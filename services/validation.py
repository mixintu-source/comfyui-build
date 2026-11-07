from __future__ import annotations

import os
import tempfile
from pathlib import Path

from schemas import PathConfig
from services.catalog import models_base
from services.mover import target_models_base
from services.paths import to_posix_path

FIELD_LABELS = {
    "comfyui_models_root": "ComfyUI 模型根目录",
    "sd_models_root": "SD 模型根目录",
    "migrate_target_root": "迁移目标根目录",
    "workflow_json_dir": "工作流 JSON 目录",
    "sd_workflow_dir": "SD 工作流目录",
}


def validate_config(config: PathConfig) -> dict:
    """Check every configured path before it is saved.

    Required roots must exist, be directories and (for the target) writable.
    Optional roots are warnings when absent, so users can keep a future SD path
    in the config without being blocked by it.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    def add(bucket: list[dict], field: str, code: str, message: str) -> None:
        bucket.append(
            {
                "field": field,
                "label": FIELD_LABELS[field],
                "code": code,
                "message": message,
            }
        )

    def check(
        field: str,
        *,
        required: bool,
        writable: bool,
        writable_is_error: bool,
    ) -> Path | None:
        raw = getattr(config, field)
        if not raw:
            if required:
                add(errors, field, "missing", "未配置")
            return None
        path = Path(raw).expanduser()
        if not path.exists():
            add(
                errors if required else warnings,
                field,
                "not_found",
                "路径不存在" if required else "路径不存在（可选目录）",
            )
            return None
        if not path.is_dir():
            add(errors, field, "not_dir", "不是目录")
            return None
        if not os.access(path, os.R_OK):
            add(errors, field, "not_readable", "目录不可读")
            return None
        if writable and not _is_writable(path):
            add(
                errors if writable_is_error else warnings,
                field,
                "not_writable",
                "目录不可写",
            )
        return path

    comfy = check(
        "comfyui_models_root",
        required=True,
        writable=False,
        writable_is_error=False,
    )
    sd = check(
        "sd_models_root",
        required=False,
        writable=False,
        writable_is_error=False,
    )
    target = check(
        "migrate_target_root",
        required=True,
        writable=True,
        writable_is_error=True,
    )
    check(
        "workflow_json_dir",
        required=False,
        writable=True,
        writable_is_error=False,
    )
    check(
        "sd_workflow_dir",
        required=False,
        writable=True,
        writable_is_error=False,
    )

    if target is not None:
        target_base = target_models_base(target).resolve()
        for field, source in (
            ("comfyui_models_root", comfy),
            ("sd_models_root", sd),
        ):
            if source is None:
                continue
            source_base = models_base(source).resolve()
            if target_base == source_base:
                add(
                    errors,
                    "migrate_target_root",
                    "same_as_source",
                    f"迁移目标与{FIELD_LABELS[field]}相同",
                )
            elif source_base in target_base.parents:
                add(
                    errors,
                    "migrate_target_root",
                    "inside_source",
                    f"迁移目标位于{FIELD_LABELS[field]}内部："
                    f"{to_posix_path(target_base)} 在 {to_posix_path(source_base)} 下",
                )

    if comfy is not None and sd is not None:
        comfy_base = models_base(comfy).resolve()
        sd_base = models_base(sd).resolve()
        if comfy_base in sd_base.parents or sd_base in comfy_base.parents:
            add(
                warnings,
                "sd_models_root",
                "overlap",
                "ComfyUI 与 SD 模型根目录存在包含关系，索引可能重复",
            )

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _is_writable(path: Path) -> bool:
    if not os.access(path, os.W_OK):
        return False
    try:
        with tempfile.NamedTemporaryFile(
            dir=path, prefix=".comfy-migrator-write-test-", suffix=".tmp"
        ):
            return True
    except OSError:
        return False
