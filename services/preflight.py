from __future__ import annotations

import shutil

from services.catalog import ModelCatalog
from services.fingerprint import quick_fingerprint
from services.paths import to_posix_path


def _fmt_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    number = float(max(0, value))
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.0f} {units[index]}" if index == 0 else f"{number:.1f} {units[index]}"


def plan_migration(
    rel_paths: list[str],
    destination_root: str,
    source_roots: list[str],
    destination_key: str = "target",
    overwrite: bool = False,
) -> dict:
    """Estimate bytes needed and describe same-name target conflicts."""
    source_pairs = [
        (key, ModelCatalog([root]).refresh())
        for key, root in zip(("comfyui", "sd", "target"), source_roots)
        if root.strip()
    ]
    destination = ModelCatalog([destination_root]).refresh()
    files: list[dict] = []
    need_bytes = 0
    missing = 0
    requires_overwrite = 0
    target_exists_count = 0
    risk_messages: list[str] = []

    for rel_path in rel_paths:
        chosen = None
        for key, catalog in source_pairs:
            resolved = catalog.resolve(rel_path)
            if resolved.found:
                chosen = (key, resolved)
                break
        if chosen is None:
            missing += 1
            files.append(
                {
                    "rel_path": rel_path,
                    "size": 0,
                    "action": "missing",
                    "reason": "源文件未找到",
                    "source_root": "",
                    "source_path": "",
                    "same_location": False,
                    "target_exists": False,
                    "target_size": 0,
                    "target_mtime": 0,
                    "same_content": False,
                    "requires_overwrite": False,
                    "risk_messages": [],
                }
            )
            continue

        source_key, resolved = chosen
        same_location = source_key == destination_key
        hit = destination.get(resolved.rel_path)
        target_exists = hit is not None
        target_size = hit.size if hit else 0
        target_mtime = hit.mtime if hit else 0.0
        same_content = False
        risks: list[str] = []

        if hit is not None:
            target_exists_count += 1
            if hit.size != resolved.size:
                risks.append(
                    f"大小不同：源 {_fmt_bytes(resolved.size)}，目标 {_fmt_bytes(hit.size)}"
                )
            else:
                try:
                    same_content = quick_fingerprint(
                        resolved.abs_path
                    ) == quick_fingerprint(hit.abs_path)
                except OSError as exc:
                    risks.append(f"内容特征读取失败：{exc}")
                if not same_content:
                    risks.append("同名文件内容特征不同（首尾 1 MB 快速指纹）")
                elif abs(hit.mtime - resolved.mtime) > 2:
                    risks.append("大小与内容特征一致，但修改时间不同")

        requires = target_exists and not same_content
        if requires:
            requires_overwrite += 1

        if hit is not None and same_content and not overwrite:
            action = "skip"
            reason = "目标已有相同内容"
        elif requires and not overwrite:
            action = "blocked"
            reason = "目标已存在不同版本，需要勾选覆盖"
        elif same_location and same_content and not overwrite:
            action = "skip"
            reason = "目标位置与源位置相同"
        else:
            action = "copy"
            reason = ""
            need_bytes += resolved.size

        if requires:
            risk_messages.extend(risks)
        elif hit is not None and risks:
            risk_messages.extend(risks)

        files.append(
            {
                "rel_path": resolved.rel_path,
                "size": resolved.size,
                "action": action,
                "reason": reason,
                "source_root": source_key,
                "source_path": to_posix_path(resolved.abs_path or ""),
                "same_location": same_location,
                "target_exists": target_exists,
                "target_size": target_size,
                "target_mtime": target_mtime,
                "same_content": same_content,
                "requires_overwrite": requires,
                "risk_messages": risks,
            }
        )

    try:
        free_bytes = shutil.disk_usage(destination_root).free
        disk_ok = True
        reason = ""
    except OSError as exc:
        free_bytes = 0
        disk_ok = False
        reason = f"目标目录不可用：{exc}"

    if disk_ok and need_bytes > free_bytes:
        disk_ok = False
        reason = (
            f"目标磁盘空间不足：需要 {_fmt_bytes(need_bytes)}，"
            f"可用 {_fmt_bytes(free_bytes)}"
        )

    return {
        "ok": disk_ok,
        "reason": reason,
        "need_bytes": need_bytes,
        "free_bytes": free_bytes,
        "missing_count": missing,
        "missing": missing > 0,
        "destination": to_posix_path(destination_root),
        "destination_key": destination_key,
        "overwrite": overwrite,
        "requires_overwrite": requires_overwrite > 0,
        "different_count": requires_overwrite,
        "target_exists_count": target_exists_count,
        "risk_messages": list(dict.fromkeys(risk_messages)),
        "files": files,
    }
