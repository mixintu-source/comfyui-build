from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

import config_store
import settings
from db import get_conn
from services.catalog import ModelCatalog
from services.importer import register_workflow
from services.inventory import target_catalog
from services.parser import extract_model_refs, parse_workflow_bytes
from services.queries import get_workflow

router = APIRouter(prefix="/api/upload", tags=["upload"])

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"com{i}" for i in range(1, 10)
} | {f"lpt{i}" for i in range(1, 10)}


@router.post("")
async def upload_workflow(file: UploadFile = File(...)) -> dict:
    original = file.filename or "workflow.json"
    if not original.lower().endswith(".json"):
        raise HTTPException(400, "仅支持 .json 工作流文件")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 64 MB 上限")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 64 MB 上限")
    try:
        document = parse_workflow_bytes(content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    config = config_store.get()
    destination_dir = (
        Path(config.workflow_json_dir).expanduser()
        if config.workflow_json_dir
        else settings.uploads_dir()
    )
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"工作流目录不可写：{exc}") from exc

    save_path = _unique_path(destination_dir, _safe_filename(original))
    try:
        save_path.write_bytes(content)
    except OSError as exc:
        raise HTTPException(500, f"保存工作流失败：{exc}") from exc

    catalog = ModelCatalog([config.comfyui_models_root, config.sd_models_root])
    target = target_catalog(config.migrate_target_root)
    try:
        with get_conn() as conn:
            workflow_id = register_workflow(
                conn,
                catalog,
                save_path.name,
                save_path,
                extract_model_refs(document),
                target,
            )
            workflow = get_workflow(conn, workflow_id, target)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    return {"workflow": workflow, "model_count": len(workflow["models"])}


def _safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = _UNSAFE.sub("_", base).strip(" .")
    if not base:
        base = "workflow.json"
    if base.split(".")[0].lower() in _RESERVED:
        base = f"_{base}"
    if not base.lower().endswith(".json"):
        base += ".json"
    return base


def _unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(409, "同名文件过多，请先清理工作流目录")
