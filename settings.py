from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent

_data_dir: Path | None = None


def _fallback_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "ComfyUIMigrator"
    return Path.home() / ".comfyui-migrator"


def _default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / "data"
        try:
            portable.mkdir(parents=True, exist_ok=True)
            probe = portable / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return portable
        except OSError:
            return _fallback_data_dir()
    return APP_ROOT / "data"


def configure(data_dir: str | Path | None = None) -> Path:
    """Point the app at a data directory; safe to call repeatedly (tests do)."""
    global _data_dir
    if data_dir is None:
        data_dir = os.environ.get("COMFY_MIGRATOR_DATA_DIR") or _default_data_dir()
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
    if os.environ.get("COMFY_MIGRATOR_WEB_DIST"):
        return Path(os.environ["COMFY_MIGRATOR_WEB_DIST"]).resolve()
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = bundle / "web" / "dist"
        if candidate.is_dir():
            return candidate
    return APP_ROOT / "web" / "dist"


def db_path() -> Path:
    return data_dir() / "app.db"

