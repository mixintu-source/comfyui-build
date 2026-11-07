from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

import settings
from services.paths import to_posix_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT NOT NULL UNIQUE,
    size     INTEGER NOT NULL DEFAULT 0,
    found    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workflow_models (
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    model_id    INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    PRIMARY KEY (workflow_id, model_id)
);

CREATE TABLE IF NOT EXISTS migrations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT NOT NULL,
    src_path TEXT NOT NULL DEFAULT '',
    dst_path TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL,
    message  TEXT NOT NULL DEFAULT '',
    ts       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations(status);
CREATE INDEX IF NOT EXISTS idx_workflow_models_model ON workflow_models(model_id);
"""


def now_str() -> str:
    """Local wall-clock timestamp; the UI is local, so the log should be too."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    settings.data_dir()
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _normalize_stored_paths(conn)


def _normalize_stored_paths(conn: sqlite3.Connection) -> None:
    """Databases created before the forward-slash rule may still hold `\\`."""
    for row in conn.execute("SELECT id, file_path FROM workflows").fetchall():
        normalized = to_posix_path(row["file_path"])
        if normalized != row["file_path"]:
            try:
                conn.execute(
                    "UPDATE workflows SET file_path = ? WHERE id = ?",
                    (normalized, row["id"]),
                )
            except sqlite3.IntegrityError:
                pass
    for row in conn.execute("SELECT id, src_path, dst_path FROM migrations").fetchall():
        src = to_posix_path(row["src_path"])
        dst = to_posix_path(row["dst_path"])
        if src != row["src_path"] or dst != row["dst_path"]:
            conn.execute(
                "UPDATE migrations SET src_path = ?, dst_path = ? WHERE id = ?",
                (src, dst, row["id"]),
            )
