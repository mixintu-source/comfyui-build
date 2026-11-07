from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from services.parser import MODEL_EXTENSIONS

UNRESOLVED_FOLDER = "(未找到)"
_SKIP_DIRS = {".git", "node_modules", "__pycache__"}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DUPLICATE_SLASH = re.compile(r"/{2,}")


@dataclass(frozen=True)
class ResolvedModel:
    rel_path: str
    abs_path: Path | None
    size: int
    found: bool
    mtime: float = 0.0


def models_base(root: str | Path) -> Path:
    """A configured root may be the ComfyUI install dir or the models dir itself."""
    base = Path(root).expanduser()
    nested = base / "models"
    if nested.is_dir():
        return nested
    return base


def normalize_ref(ref: str) -> str:
    """Normalize a workflow reference into a comparable posix string."""
    text = str(ref).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return _DUPLICATE_SLASH.sub("/", text)


def rel_path_for(ref: str) -> str:
    """Map a reference to its storage key, or "" when it is unsafe to use as a path."""
    text = normalize_ref(ref)
    if not text:
        return ""
    if text.startswith("/") or _DRIVE_RE.match(text):
        return ""
    if ".." in Path(text).parts:
        return ""
    if text == "models":
        return ""
    return text if text.startswith("models/") else f"models/{text}"


def model_name(ref: str) -> str:
    text = normalize_ref(ref).rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


class ModelCatalog:
    """An index of model files across configured roots.

    Roots are ordered by priority: ComfyUI first, then SD. Files are indexed by
    both their `models/...` relative path and their basename, so a workflow that
    only stores `foo.safetensors` can still be resolved to the right folder.
    """

    def __init__(self, roots: Sequence[str | Path] | Iterable[str | Path]):
        self._roots = [Path(root).expanduser() for root in roots if str(root).strip()]
        self._by_rel: dict[str, ResolvedModel] = {}
        self._by_name: dict[str, list[ResolvedModel]] = {}
        self._loaded = False

    def refresh(self) -> "ModelCatalog":
        self._by_rel.clear()
        self._by_name.clear()
        for root in self._roots:
            base = models_base(root)
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
                for filename in filenames:
                    if not filename.lower().endswith(MODEL_EXTENSIONS):
                        continue
                    absolute = Path(dirpath) / filename
                    try:
                        stat = absolute.stat()
                    except OSError:
                        continue
                    size = stat.st_size
                    try:
                        relative = absolute.relative_to(base).as_posix()
                    except ValueError:
                        continue
                    rel_path = f"models/{relative}"
                    key = rel_path.lower()
                    if key in self._by_rel:
                        continue  # ComfyUI root wins over SD for the same rel path
                    model = ResolvedModel(
                        rel_path=rel_path,
                        abs_path=absolute.resolve(),
                        size=size,
                        found=True,
                        mtime=stat.st_mtime,
                    )
                    self._by_rel[key] = model
                    self._by_name.setdefault(filename.lower(), []).append(model)
        self._loaded = True
        return self

    def entries(self) -> list[ResolvedModel]:
        self._ensure_loaded()
        return list(self._by_rel.values())

    def get(self, rel_path: str) -> ResolvedModel | None:
        """Exact lookup by `models/...` path (no basename fallback)."""
        self._ensure_loaded()
        key = rel_path_for(rel_path)
        if not key:
            return None
        return self._by_rel.get(key.lower())

    def resolve(self, ref: str) -> ResolvedModel:
        """Resolve a reference to a concrete model, or an explicit placeholder."""
        self._ensure_loaded()
        text = normalize_ref(ref)

        rel_key = rel_path_for(text)
        if rel_key:
            hit = self._by_rel.get(rel_key.lower())
            if hit is not None:
                return hit

        name = model_name(text)
        candidates = self._by_name.get(name.lower(), []) if name else []
        if candidates:
            # Deterministic tie-break: shortest path, then lexicographic.
            return min(candidates, key=lambda item: (len(item.rel_path), item.rel_path))

        safe_name = name or "unknown"
        return ResolvedModel(
            rel_path=f"models/{UNRESOLVED_FOLDER}/{safe_name}",
            abs_path=None,
            size=0,
            found=False,
        )

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()
