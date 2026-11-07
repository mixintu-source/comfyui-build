from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from services.paths import to_posix_path


class PathConfig(BaseModel):
    comfyui_models_root: str = ""
    sd_models_root: str = ""
    migrate_target_root: str = ""
    workflow_json_dir: str = ""
    sd_workflow_dir: str = ""
    allow_delete_source: bool = False

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, value: object) -> object:
        if isinstance(value, str):
            # Users paste paths straight from Explorer; quotes and backslashes
            # are normalized so the rest of the app only sees forward slashes.
            return to_posix_path(value)
        return value


class MigrateRequest(BaseModel):
    rel_paths: list[str] = Field(min_length=1, max_length=500)
    destination: Literal["comfyui", "sd", "target"] = "target"
    move: bool = False
    overwrite: bool = False

    @field_validator("rel_paths")
    @classmethod
    def _non_empty(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("rel_paths 不能为空")
        return cleaned


class PreflightRequest(BaseModel):
    rel_paths: list[str] = Field(min_length=1, max_length=500)
    destination: Literal["comfyui", "sd", "target"] = "target"
    overwrite: bool = False

    @field_validator("rel_paths")
    @classmethod
    def _non_empty(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("rel_paths 不能为空")
        return cleaned


class ImportPathRequest(BaseModel):
    path: str = ""

    @field_validator("path", mode="before")
    @classmethod
    def _clean_path(cls, value: object) -> object:
        if isinstance(value, str):
            return to_posix_path(value)
        return value


class ScanImportRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=2000)

    @field_validator("paths")
    @classmethod
    def _clean_paths(cls, values: list[str]) -> list[str]:
        cleaned = [to_posix_path(value) for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("paths 不能为空")
        return cleaned
