from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from db import get_conn

router = APIRouter(prefix="/api/logs", tags=["logs"])

_VALID_STATUSES = {"success", "failed", "pending"}


@router.get("")
def list_logs(status: str = "", limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    sql = "SELECT * FROM migrations"
    params: list[object] = []
    if status in _VALID_STATUSES:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


@router.delete("/{log_id}")
def delete_log(log_id: int) -> dict:
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM migrations WHERE id = ?", (log_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "日志不存在")
    return {"ok": True}


@router.delete("")
def clear_logs(status: str = "") -> dict:
    with get_conn() as conn:
        if status in _VALID_STATUSES:
            conn.execute("DELETE FROM migrations WHERE status = ?", (status,))
        else:
            conn.execute("DELETE FROM migrations")
    return {"ok": True}
