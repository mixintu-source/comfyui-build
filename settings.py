from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent

_data_dir: Path | None = None


def configure(data_dir: str | Path | None = None) -> Path:
    """Point the app at a data directory; safe to call repeatedly (tests do)."""
    global _data_dir
    if data_dir is None:
        data_dir = os.environ.get("COMFY_MIGRATOR_DATA_DIR") or APP_ROOT / "data"
    _data_dir = Path(data_dir).expanduser().resolve()
    _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


def data_dir() -> Path:
    if _data_dir is None:
        return configure()
    return _data_dir


def uploads_dir() -> Path:
    path = data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def web_dist() -> Path:
    return Path(os.environ.get("COMFY_MIGRATOR_WEB_DIST") or APP_ROOT / "web" / "dist").resolve()


def db_path() -> Path:
    return data_dir() / "app.db"
