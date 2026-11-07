from __future__ import annotations

from fastapi import APIRouter, HTTPException

import config_store
from db import get_conn
from services.analysis import analyze_model
from services.inventory import target_catalog

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.get("")
def analyze(model_id: int, workflow_id: int) -> dict:
    config = config_store.get()
    with get_conn() as conn:
        plan = analyze_model(
            conn,
            model_id,
            workflow_id,
            target_catalog(config.migrate_target_root),
        )
    if plan is None:
        raise HTTPException(404, "模型不存在")
    return plan
