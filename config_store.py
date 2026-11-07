from __future__ import annotations

import json
import os

import settings
from schemas import PathConfig


def _config_file():
    return settings.data_dir() / "config.json"


def get() -> PathConfig:
    path = _config_file()
    if not path.is_file():
        return PathConfig()
    try:
        return PathConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        # A corrupt config should not brick the app; fall back to defaults.
        return PathConfig()


def save(config: PathConfig) -> PathConfig:
    path = _config_file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return config
