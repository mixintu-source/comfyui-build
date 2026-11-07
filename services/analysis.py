from __future__ import annotations

import sqlite3

from services.catalog import ModelCatalog
from services.queries import model_payload


def _payload(row: sqlite3.Row, target_catalog: ModelCatalog | None) -> dict:
    item = model_payload(row)
    if target_catalog is None:
        return item
    hit = target_catalog.get(item["rel_path"])
    if hit is None and not item["found"]:
        resolved = target_catalog.resolve(item["rel_path"])
        hit = resolved if resolved.found else None
    if hit is not None:
        item["in_target"] = True
        item["target_size"] = hit.size
    return item


def analyze_model(
    conn: sqlite3.Connection,
    model_id: int,
    workflow_id: int,
    target_catalog: ModelCatalog | None = None,
) -> dict | None:
    """Return the confirmation plan for migrating `model_id` out of `workflow_id`."""
    target = conn.execute(
        "SELECT id, rel_path, size, found FROM models WHERE id = ?", (model_id,)
    ).fetchone()
    if target is None:
        return None

    shared = conn.execute(
        """
        SELECT w.id, w.name
        FROM workflow_models wm
        JOIN workflows w ON w.id = wm.workflow_id
        WHERE wm.model_id = ? AND w.id != ?
        ORDER BY w.name
        """,
        (model_id, workflow_id),
    ).fetchall()

    candidates = conn.execute(
        """
        SELECT m.id, m.rel_path, m.size, m.found
        FROM workflow_models wm
        JOIN models m ON m.id = wm.model_id
        WHERE wm.workflow_id = ?
        ORDER BY m.rel_path
        """,
        (workflow_id,),
    ).fetchall()

    return {
        "target": _payload(target, target_catalog),
        "shared_with": [dict(row) for row in shared],
        "candidate_models": [_payload(row, target_catalog) for row in candidates],
    }
