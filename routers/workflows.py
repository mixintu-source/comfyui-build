from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

import config_store
from db import get_conn
from schemas import ImportPathRequest, PathConfig, ScanImportRequest
from services.catalog import ModelCatalog
from services.importer import register_workflow
from services.inventory import target_catalog
from services.parser import extract_model_refs, parse_workflow_bytes
from services.paths import to_posix_path
from services.queries import get_workflow, list_workflows

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

MAX_WORKFLOW_BYTES = 64 * 1024 * 1024


def _workflow_dirs(config: PathConfig) -> list[Path]:
    directories: list[Path] = []
    seen: set[str] = set()
    for raw in (config.workflow_json_dir, config.sd_workflow_dir):
        if not raw:
            continue
        directory = Path(raw).expanduser()
        key = str(directory.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        directories.append(directory)
    return directories


def _registered_paths() -> set[str]:
    with get_conn() as conn:
        return {
            row["file_path"]
            for row in conn.execute("SELECT file_path FROM workflows").fetchall()
        }


@router.get("")
def get_workflows() -> list[dict]:
    with get_conn() as conn:
        return list_workflows(
            conn, target_catalog(config_store.get().migrate_target_root)
        )


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int, delete_file: bool = False) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "工作流不存在")
        cursor = conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "工作流不存在")
        # Models are shared entities; only drop the ones nobody references anymore.
        conn.execute(
            "DELETE FROM models WHERE id NOT IN (SELECT model_id FROM workflow_models)"
        )
        file_deleted = False
        if delete_file:
            path = Path(row["file_path"])
            if path.is_file():
                try:
                    path.unlink()
                except OSError as exc:
                    raise HTTPException(
                        500, f"记录未删除：无法删除本地 JSON 文件（{exc}）"
                    ) from exc
                file_deleted = True
    return {"ok": True, "file_deleted": file_deleted}


def _reveal_folder(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


@router.post("/{workflow_id}/open")
def open_workflow_folder(workflow_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "工作流不存在")
    path = Path(row["file_path"])
    if not path.is_file():
        raise HTTPException(404, "工作流文件不存在")
    try:
        _reveal_folder(path.parent)
    except OSError as exc:
        raise HTTPException(500, f"无法打开目录：{exc}") from exc
    return {"ok": True, "path": to_posix_path(path.parent)}


@router.get("/scan/preview")
def scan_preview() -> dict:
    config = config_store.get()
    directories = _workflow_dirs(config)
    if not directories:
        raise HTTPException(400, "请先在路径配置中设置工作流 JSON 目录")

    existing = _registered_paths()
    files: list[dict] = []
    seen: set[str] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            if not path.is_file():
                continue
            resolved = to_posix_path(path.resolve())
            key = resolved.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            files.append(
                {
                    "name": path.name,
                    "path": resolved,
                    "size": size,
                    "imported": resolved in existing,
                }
            )
    files.sort(key=lambda item: (item["imported"], item["path"].lower()))
    return {
        "files": files,
        "directories": [to_posix_path(directory.resolve()) for directory in directories],
    }


@router.post("/scan/import")
def scan_import(request: ScanImportRequest) -> dict:
    config = config_store.get()
    directories = _workflow_dirs(config)
    if not directories:
        raise HTTPException(400, "请先在路径配置中设置工作流 JSON 目录")
    allowed_roots = [directory.resolve() for directory in directories]

    catalog = ModelCatalog([config.comfyui_models_root, config.sd_models_root])
    targets = target_catalog(config.migrate_target_root)
    imported: list[str] = []
    failed: list[dict] = []
    seen: set[str] = set()

    with get_conn() as conn:
        for raw in request.paths:
            path = Path(raw).expanduser()
            if not path.is_absolute() or not path.is_file():
                failed.append({"file": raw, "error": "文件不存在或不是绝对路径"})
                continue
            resolved_path = path.resolve()
            if not any(
                resolved_path == root or root in resolved_path.parents
                for root in allowed_roots
            ):
                raise HTTPException(400, "只能导入已配置工作流目录下的文件")
            if resolved_path.suffix.lower() != ".json":
                failed.append({"file": raw, "error": "仅支持 .json"})
                continue
            key = str(resolved_path).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                document = parse_workflow_bytes(path.read_bytes())
                register_workflow(
                    conn,
                    catalog,
                    path.name,
                    resolved_path,
                    extract_model_refs(document),
                    targets,
                )
            except (OSError, ValueError) as exc:
                failed.append({"file": raw, "error": str(exc)})
                continue
            imported.append(path.name)

    return {
        "imported": imported,
        "imported_count": len(imported),
        "failed": failed,
    }


@router.post("/import-path")
def import_path(request: ImportPathRequest) -> dict:
    raw = request.path.strip()
    if not raw:
        raise HTTPException(400, "请输入工作流 JSON 的绝对路径")
    path = Path(raw).expanduser()
    if ".." in path.parts:
        raise HTTPException(400, "路径中不允许包含 ..")
    if not path.is_absolute():
        raise HTTPException(400, "请输入绝对路径")
    if path.suffix.lower() != ".json":
        raise HTTPException(400, "仅支持 .json 工作流文件")
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    try:
        if path.stat().st_size > MAX_WORKFLOW_BYTES:
            raise HTTPException(413, "文件超过 64 MB 上限")
        document = parse_workflow_bytes(path.read_bytes())
    except OSError as exc:
        raise HTTPException(400, f"读取失败：{exc}") from exc

    config = config_store.get()
    catalog = ModelCatalog([config.comfyui_models_root, config.sd_models_root])
    targets = target_catalog(config.migrate_target_root)
    with get_conn() as conn:
        workflow_id = register_workflow(
            conn,
            catalog,
            path.name,
            path,
            extract_model_refs(document),
            targets,
        )
        workflow = get_workflow(conn, workflow_id, targets)
    return {"workflow": workflow, "model_count": len(workflow["models"])}


@router.post("/scan")
def scan_workflow_dirs() -> dict:
    config = config_store.get()
    directories = _workflow_dirs(config)
    if not directories:
        raise HTTPException(400, "请先在路径配置中设置工作流 JSON 目录")

    catalog = ModelCatalog([config.comfyui_models_root, config.sd_models_root])
    targets = target_catalog(config.migrate_target_root)
    imported: list[str] = []
    failed: list[dict] = []
    skipped = 0

    with get_conn() as conn:
        existing = {
            row["file_path"]
            for row in conn.execute("SELECT file_path FROM workflows").fetchall()
        }
        seen_files: set[str] = set()
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.json")):
                if not path.is_file():
                    continue
                resolved = to_posix_path(path.resolve())
                key = resolved.lower()
                if key in seen_files:
                    continue
                seen_files.add(key)
                if resolved in existing:
                    skipped += 1
                    continue
                try:
                    document = parse_workflow_bytes(path.read_bytes())
                    refs = extract_model_refs(document)
                    register_workflow(conn, catalog, path.name, resolved, refs, targets)
                except (OSError, ValueError) as exc:
                    failed.append({"file": str(path), "error": str(exc)})
                    continue
                imported.append(path.name)
                existing.add(resolved)

    return {
        "imported": imported,
        "imported_count": len(imported),
        "skipped": skipped,
        "failed": failed,
    }
