from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODEL_EXTENSIONS = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".onnx",
    ".sft",
)

_MAX_REF_LENGTH = 512
_URL_PREFIXES = ("http://", "https://", "data:", "ftp://")


def extract_model_refs(document: Any) -> list[str]:
    """Return model-looking strings from a parsed ComfyUI workflow.

    Handles both graph formats:
      * UI format:   {"nodes": [{"widgets_values": [...]}, ...]}
      * API format:  {"3": {"class_type": ..., "inputs": {"ckpt_name": ...}}}

    The walk is deliberately format-agnostic: any string that ends in a known
    model extension is a reference. First-seen order is preserved so imports
    read the same way the workflow does.
    """
    found: dict[str, None] = {}
    _walk(document, found)
    return list(found)


def parse_workflow_bytes(content: bytes) -> dict:
    """Decode and parse a workflow file, raising ValueError on bad input."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是 UTF-8 编码的 JSON") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：第 {exc.lineno} 行 {exc.msg}") from exc
    if not isinstance(document, (dict, list)):
        raise ValueError("工作流 JSON 顶层必须是对象或数组")
    return document


def parse_workflow_file(path: str | Path) -> list[str]:
    document = parse_workflow_bytes(Path(path).read_bytes())
    return extract_model_refs(document)


def _walk(value: Any, found: dict[str, None]) -> None:
    if isinstance(value, str):
        _consider(value, found)
    elif isinstance(value, dict):
        for child in value.values():
            _walk(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _walk(child, found)


def _consider(value: str, found: dict[str, None]) -> None:
    text = value.strip()
    if not text or len(text) > _MAX_REF_LENGTH:
        return
    lower = text.lower()
    if "://" in lower or lower.startswith(_URL_PREFIXES):
        return
    if not lower.endswith(MODEL_EXTENSIONS):
        return
    if "\n" in text or "\r" in text or "\x00" in text:
        return
    normalized = text.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    found.setdefault(normalized, None)
