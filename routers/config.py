from __future__ import annotations

from fastapi import APIRouter, HTTPException

import config_store
from schemas import PathConfig
from services.validation import validate_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def read_config() -> dict:
    return config_store.get().model_dump()


@router.post("")
def write_config(config: PathConfig) -> dict:
    result = validate_config(config)
    if not result["ok"]:
        raise HTTPException(
            422,
            detail={
                "message": "配置有误，未保存",
                "errors": result["errors"],
                "warnings": result["warnings"],
            },
        )
    return config_store.save(config).model_dump()


@router.post("/validate")
def check_config(config: PathConfig) -> dict:
    return validate_config(config)
