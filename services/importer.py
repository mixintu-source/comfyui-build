from __future__ import annotations

import sqlite3
from pathlib import Path

from db import now_str
from services.catalog import ModelCatalog, ResolvedModel
from services.paths import to_posix_path


def register_workflow(
    conn: sqlite3.Connection,
    catalog: ModelCatalog,
    name: str,
    file_path: str | Path,
    refs: list[str],
    target_catalog: ModelCatalog | None = None,
) -> int:
    """Persist one workflow and its model links inside the caller's transaction.

    Importing the same `file_path` twice replaces the links, so re-scanning a
    changed file is idempotent.
    """
    path_text = to_posix_path(Path(file_path).resolve())
    existing = conn.execute(
        "SELECT id FROM workflows WHERE file_path = ?", (path_text,)
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
            "INSERT INTO workflows (name, file_path, imported_at) VALUES (?, ?, ?)",
            (name, path_text, now_str()),
        )
        workflow_id = int(cursor.lastrowid)
    else:
        workflow_id = int(existing["id"])
        conn.execute(
            "UPDATE workflows SET name = ?, imported_at = ? WHERE id = ?",
            (name, now_str(), workflow_id),
        )
        conn.execute(
            "DELETE FROM workflow_models WHERE workflow_id = ?", (workflow_id,)
        )

    linked: set[str] = set()
    for ref in refs:
        resolved = catalog.resolve(ref)
        if not resolved.found and target_catalog is not None:
            # Keep the real target rel_path instead of a "未找到" placeholder, so
            # the tree groups it under the right folder and shows "已在目标".
            target = target_catalog.resolve(ref)
            if target.found:
                resolved = ResolvedModel(
                    rel_path=target.rel_path,
                    abs_path=None,
                    size=target.size,
                    found=False,
                    mtime=target.mtime,
                )
        key = resolved.rel_path.lower()
        if key in linked:
            continue
        linked.add(key)
        model_id = _upsert_model(conn, resolved)
        conn.execute(
            "INSERT OR IGNORE INTO workflow_models (workflow_id, model_id) VALUES (?, ?)",
            (workflow_id, model_id),
        )
    return workflow_id


def _upsert_model(conn: sqlite3.Connection, model: ResolvedModel) -> int:
    conn.execute(
        """
        INSERT INTO models (rel_path, size, found) VALUES (?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET size = excluded.size, found = excluded.found
        """,
        (model.rel_path, model.size, 1 if model.found else 0),
    )
    row = conn.execute(
        "SELECT id FROM models WHERE rel_path = ?", (model.rel_path,)
    ).fetchone()
    if row is None:  # pragma: no cover - the insert above guarantees a row
        raise RuntimeError(f"模型写入失败：{model.rel_path}")
    return int(row["id"])
