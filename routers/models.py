from __future__ import annotations

from fastapi import APIRouter

import config_store
from db import get_conn
from services.inventory import build_inventory

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models() -> dict:
    """Union of source and target model inventories, with migration status."""
    config = config_store.get()
    with get_conn() as conn:
        usage = {
            row["rel_path"].lower(): row["c"]
            for row in conn.execute(
                """
                SELECT m.rel_path AS rel_path, COUNT(DISTINCT wm.workflow_id) AS c
                FROM models m
                JOIN workflow_models wm ON wm.model_id = m.id
                GROUP BY m.rel_path
                """
            ).fetchall()
        }
    return build_inventory(
        config.comfyui_models_root,
        config.sd_models_root,
        config.migrate_target_root,
        usage,
    )
