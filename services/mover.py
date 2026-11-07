from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable, Protocol


class UnsafePathError(ValueError):
    pass


class CopyCancelled(Exception):
    pass


class CancelSignal(Protocol):
    def is_set(self) -> bool: ...


def target_models_base(target_root: str | Path) -> Path:
    """The target root may be a ComfyUI install dir or its `models/` dir."""
    root = Path(target_root).expanduser()
    return root if root.name.lower() == "models" else root / "models"


def target_path(target_root: str | Path, rel_path: str) -> Path:
    """Build the destination path and refuse anything outside the target root."""
    raw = str(rel_path).strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise UnsafePathError(f"非法相对路径：{rel_path!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafePathError(f"非法相对路径：{rel_path!r}")
    if ":" in parts[0]:  # C:, \\?\ and other drive tricks
        raise UnsafePathError(f"非法相对路径：{rel_path!r}")
    if parts[0].lower() != "models":
        raise UnsafePathError(f"路径必须以 models/ 开头：{rel_path!r}")
    if len(parts) < 2:
        raise UnsafePathError(f"路径缺少文件名：{rel_path!r}")

    base = target_models_base(target_root)
    base_resolved = base.resolve()
    destination = (base / Path(*parts[1:])).resolve()
    try:
        destination.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"目标路径越界：{rel_path!r}") from exc
    if destination == base_resolved:
        raise UnsafePathError("目标路径不能是根目录本身")
    return destination


def copy_atomic(source: str | Path, destination: str | Path) -> str:
    """Copy `source` to `destination` atomically, never touching the source.

    Returns one of "copied" / "replaced" / "same". The temporary file lives in
    the destination directory so `os.replace` stays on the same volume.
    """
    src = Path(source)
    dst = Path(destination)
    if src.resolve() == dst.resolve():
        return "same"

    replaced = dst.exists()
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        shutil.copy2(src, temp)
        os.replace(temp, dst)
    finally:
        temp.unlink(missing_ok=True)
    return "replaced" if replaced else "copied"


def copy_with_progress(
    source: str | Path,
    destination: str | Path,
    on_progress: Callable[[int, int], None],
    cancel_signal: CancelSignal,
    chunk_size: int = 4 * 1024 * 1024,
) -> str:
    """Chunked atomic copy with progress and cancellation.

    The source is never touched. If cancelled, the temp file is removed and the
    existing destination (if any) is left intact.
    """
    src = Path(source)
    dst = Path(destination)
    if src.resolve() == dst.resolve():
        return "same"

    replaced = dst.exists()
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.part")
    total = src.stat().st_size
    copied = 0
    try:
        with src.open("rb") as reader, temp.open("wb") as writer:
            while True:
                if cancel_signal.is_set():
                    raise CopyCancelled()
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                on_progress(copied, total)
        if cancel_signal.is_set():
            raise CopyCancelled()
        shutil.copystat(src, temp)
        os.replace(temp, dst)
    finally:
        temp.unlink(missing_ok=True)
    return "replaced" if replaced else "copied"
