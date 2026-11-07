from __future__ import annotations

from fastapi import APIRouter

import config_store
from db import get_conn
from services.catalog import ModelCatalog
from services.queries import file_modified_at

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def stats() -> dict:
    config = config_store.get()
    catalog = ModelCatalog(
        [config.comfyui_models_root, config.sd_models_root]
    ).refresh()
    entries = catalog.entries()
    target_entries = (
        ModelCatalog([config.migrate_target_root]).refresh().entries()
        if config.migrate_target_root
        else []
    )
    source_keys = {entry.rel_path.lower() for entry in entries}
    target_keys = {entry.rel_path.lower() for entry in target_entries}

    with get_conn() as conn:
        workflow_count = conn.execute("SELECT COUNT(*) AS c FROM workflows").fetchone()["c"]
        migrated = conn.execute(
            "SELECT COUNT(*) AS c FROM migrations WHERE status = 'success'"
        ).fetchone()["c"]
        failed = conn.execute(
            "SELECT COUNT(*) AS c FROM migrations WHERE status = 'failed'"
        ).fetchone()["c"]
        recent_migrations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT rel_path, status, message, ts
                FROM migrations ORDER BY id DESC LIMIT 8
                """
            ).fetchall()
        ]
        recent_workflows = [
            {
                **dict(row),
                "modified_at": file_modified_at(row["file_path"]),
            }
            for row in conn.execute(
                """
                SELECT w.id, w.name, w.file_path, w.imported_at,
                       (SELECT COUNT(*) FROM workflow_models wm WHERE wm.workflow_id = w.id)
                           AS model_count
                FROM workflows w ORDER BY w.id DESC LIMIT 8
                """
            ).fetchall()
        ]

    return {
        "total_models": len(entries),
        "total_workflows": workflow_count,
        "migrated_count": migrated,
        "failed_count": failed,
        "model_bytes": sum(entry.size for entry in entries),
        "target_models": len(target_entries),
        "target_bytes": sum(entry.size for entry in target_entries),
        "models_in_target": len(source_keys & target_keys),
        "recent_migrations": recent_migrations,
        "recent_workflows": recent_workflows,
    }
