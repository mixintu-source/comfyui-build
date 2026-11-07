from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from services.catalog import ModelCatalog
from services.tree import group_models


def file_modified_at(file_path: str) -> str:
    try:
        return datetime.fromtimestamp(Path(file_path).stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except OSError:
        return ""


def model_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "rel_path": row["rel_path"],
        "size": row["size"],
        "found": bool(row["found"]),
        "in_target": False,
        "target_size": 0,
    }


def _linked_models(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    rows = conn.execute(
        """
        SELECT wm.workflow_id AS workflow_id, m.id, m.rel_path, m.size, m.found
        FROM workflow_models wm
        JOIN models m ON m.id = wm.model_id
        ORDER BY m.rel_path
        """
    ).fetchall()
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["workflow_id"])].append(model_payload(row))
    return grouped


def _enrich(models: list[dict], target_catalog: ModelCatalog | None) -> None:
    if target_catalog is None:
        return
    for model in models:
        hit = target_catalog.get(model["rel_path"])
        if hit is None and not model["found"]:
            # A placeholder ("未找到/<name>") may still exist in the target.
            resolved = target_catalog.resolve(model["rel_path"])
            hit = resolved if resolved.found else None
        if hit is not None:
            model["in_target"] = True
            model["target_size"] = hit.size


def list_workflows(
    conn: sqlite3.Connection, target_catalog: ModelCatalog | None = None
) -> list[dict]:
    workflows = conn.execute(
        "SELECT id, name, file_path, imported_at FROM workflows ORDER BY id DESC"
    ).fetchall()
    models_by_workflow = _linked_models(conn)
    result = []
    for workflow in workflows:
        models = models_by_workflow.get(int(workflow["id"]), [])
        _enrich(models, target_catalog)
        item = dict(workflow)
        item["modified_at"] = file_modified_at(workflow["file_path"])
        item["models"] = models
        item["tree"] = group_models(models)
        result.append(item)
    return result


def get_workflow(
    conn: sqlite3.Connection,
    workflow_id: int,
    target_catalog: ModelCatalog | None = None,
) -> dict | None:
    workflow = conn.execute(
        "SELECT id, name, file_path, imported_at FROM workflows WHERE id = ?",
        (workflow_id,),
    ).fetchone()
    if workflow is None:
        return None
    models = _linked_models(conn).get(workflow_id, [])
    _enrich(models, target_catalog)
    item = dict(workflow)
    item["modified_at"] = file_modified_at(workflow["file_path"])
    item["models"] = models
    item["tree"] = group_models(models)
    return item
