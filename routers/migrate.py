from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

import config_store
from db import get_conn, now_str
from schemas import MigrateRequest, PreflightRequest
from services.catalog import ModelCatalog, ResolvedModel
from services.fingerprint import quick_fingerprint
from services.mover import UnsafePathError, copy_atomic, target_path
from services.paths import to_posix_path
from services.preflight import plan_migration
from services.tasks import tasks

router = APIRouter(prefix="/api/migrate", tags=["migrate"])

_ROOT_FIELDS = {
    "comfyui": "comfyui_models_root",
    "sd": "sd_models_root",
    "target": "migrate_target_root",
}
_ROOT_LABELS = {
    "comfyui": "ComfyUI 模型根目录",
    "sd": "SD 模型根目录",
    "target": "迁移目标根目录",
}

_COPY_MESSAGES = {
    "copied": "已复制到目标目录",
    "replaced": "目标已存在，已覆盖",
    "same": "源与目标为同一文件，已跳过",
}
_MOVE_MESSAGES = {
    "copied": "已剪贴到目标目录（源文件已删除）",
    "replaced": "已剪贴并覆盖目标文件（源文件已删除）",
    "same": "源与目标为同一文件，未删除源文件",
}

_SKIP_COPY = "目标已存在且内容一致，已跳过复制"
_SKIP_MOVE = "目标已存在且内容一致，源文件已剪贴删除"
_SAME_PATH = "源与目标为同一位置，已跳过"
_SAME_PATH_MOVE = "源与目标为同一位置，未删除源文件"
_ALREADY_THERE = "文件已在目标目录，无需迁移"


@router.post("/tasks")
def create_task(request: MigrateRequest) -> dict:
    config = config_store.get()
    destination_root = getattr(config, _ROOT_FIELDS[request.destination]).strip()
    if not destination_root:
        raise HTTPException(
            400, f"请先配置{_ROOT_LABELS[request.destination]}"
        )
    if request.move and not config.allow_delete_source:
        raise HTTPException(403, "未开启「允许删除源文件」，当前只允许复制迁移")
    plan = plan_migration(
        request.rel_paths,
        destination_root,
        [
            config.comfyui_models_root,
            config.sd_models_root,
            config.migrate_target_root,
        ],
        request.destination,
        request.overwrite,
    )
    if not plan["ok"]:
        raise HTTPException(409, plan["reason"])
    task_id = tasks.create(
        config,
        request.rel_paths,
        request.destination,
        request.move,
        plan,
        overwrite=request.overwrite,
    )
    return {"task_id": task_id}


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task.snapshot()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    if not tasks.cancel(task_id):
        raise HTTPException(404, "任务不存在或已结束")
    return {"ok": True}


@router.post("/preflight")
def preflight(request: PreflightRequest) -> dict:
    config = config_store.get()
    destination_root = getattr(config, _ROOT_FIELDS[request.destination]).strip()
    if not destination_root:
        raise HTTPException(
            400, f"请先配置{_ROOT_LABELS[request.destination]}"
        )
    return plan_migration(
        request.rel_paths,
        destination_root,
        [
            config.comfyui_models_root,
            config.sd_models_root,
            config.migrate_target_root,
        ],
        request.destination,
        request.overwrite,
    )


@router.post("")
def execute_migration(request: MigrateRequest) -> dict:
    config = config_store.get()
    destination_key = request.destination
    destination_root = getattr(config, _ROOT_FIELDS[destination_key]).strip()
    if not destination_root:
        raise HTTPException(
            400, f"请先配置{_ROOT_LABELS[destination_key]}"
        )
    if request.move and not config.allow_delete_source:
        raise HTTPException(
            403,
            "未开启「允许删除源文件」，当前只允许复制迁移",
        )

    source_roots = [
        config.comfyui_models_root,
        config.sd_models_root,
        config.migrate_target_root,
    ]
    plan = plan_migration(
        request.rel_paths,
        destination_root,
        source_roots,
        destination_key,
        request.overwrite,
    )
    if not plan["ok"]:
        raise HTTPException(409, plan["reason"])

    # The source may be any of the three roots: a target-only model can be
    # copied back to ComfyUI/SD, and a model can be moved between sources.
    catalog = ModelCatalog(source_roots)
    destinations = ModelCatalog([destination_root]).refresh()

    results: list[dict] = []
    with get_conn() as conn:
        for rel_path in request.rel_paths:
            result = _migrate_one(
                conn,
                catalog,
                destinations,
                rel_path,
                destination_key,
                destination_root,
                request.move,
                request.overwrite,
            )
            results.append(result)
            conn.commit()  # keep completed files visible even if a later one dies
    return {"results": results}


def _migrate_one(
    conn: sqlite3.Connection,
    catalog: ModelCatalog,
    destinations: ModelCatalog,
    rel_path: str,
    destination_key: str,
    destination_root: str,
    move: bool,
    overwrite: bool,
) -> dict:
    timestamp = now_str()
    resolved = catalog.resolve(rel_path)
    source = to_posix_path(resolved.abs_path) if resolved.abs_path else ""
    destination = ""
    status = "failed"
    message = "未找到源文件，请检查模型根目录配置"

    if resolved.found and resolved.abs_path is not None:
        try:
            destination_path = target_path(destination_root, resolved.rel_path)
            destination = to_posix_path(destination_path)
        except (OSError, UnsafePathError, ValueError) as exc:
            message = str(exc)
        else:
            target_hit = destinations.get(resolved.rel_path)
            same_path = resolved.abs_path.resolve() == destination_path.resolve()

            same_content = False
            if target_hit is not None and target_hit.size == resolved.size:
                try:
                    same_content = quick_fingerprint(
                        resolved.abs_path
                    ) == quick_fingerprint(target_hit.abs_path)
                except OSError:
                    same_content = False

            if same_path:
                status = "success"
                if destination_key == "target":
                    message = _ALREADY_THERE
                else:
                    message = _SAME_PATH_MOVE if move else _SAME_PATH
            elif target_hit is not None and same_content and not overwrite:
                status = "success"
                message = _SKIP_COPY
                if move:
                    error = _delete_source(resolved.abs_path)
                    if error:
                        status = "failed"
                        message = f"目标已存在，但删除源文件失败：{error}"
                    else:
                        message = _SKIP_MOVE
                _heal_model_row(conn, rel_path, resolved)
            elif target_hit is not None and not same_content and not overwrite:
                status = "failed"
                message = "目标已存在不同版本，未勾选覆盖，源文件与目标均未改动"
            else:
                try:
                    outcome = copy_atomic(resolved.abs_path, destination_path)
                except (OSError, UnsafePathError, ValueError) as exc:
                    message = str(exc)
                else:
                    status = "success"
                    message = _COPY_MESSAGES[outcome]
                    if move:
                        error = _delete_source(resolved.abs_path)
                        if error:
                            status = "failed"
                            message = f"已复制到目标，但删除源文件失败：{error}"
                        else:
                            message = _MOVE_MESSAGES[outcome]
                    _heal_model_row(conn, rel_path, resolved)

    conn.execute(
        """
        INSERT INTO migrations (rel_path, src_path, dst_path, status, message, ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (rel_path, source, destination, status, message, timestamp),
    )
    return {
        "rel_path": rel_path,
        "src_path": source,
        "dst_path": destination,
        "destination": destination_key,
        "move": move,
        "overwrite": overwrite,
        "status": status,
        "message": message,
    }


def _delete_source(path) -> str:
    try:
        path.unlink()
    except OSError as exc:
        return str(exc)
    return ""


def _heal_model_row(
    conn: sqlite3.Connection, requested: str, resolved: ResolvedModel
) -> None:
    """A placeholder row can be upgraded once its file actually exists."""
    if not resolved.found:
        return
    if resolved.rel_path != requested:
        try:
            conn.execute(
                "UPDATE models SET rel_path = ?, size = ?, found = 1 WHERE rel_path = ?",
                (resolved.rel_path, resolved.size, requested),
            )
        except sqlite3.IntegrityError:
            pass  # real row already exists under that path
    conn.execute(
        "UPDATE models SET size = ?, found = 1 WHERE rel_path = ?",
        (resolved.size, resolved.rel_path),
    )
