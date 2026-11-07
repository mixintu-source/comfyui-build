from __future__ import annotations

import re
from pathlib import Path

_DUPLICATE_SLASH = re.compile(r"/{2,}")
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:/$")


def to_posix_path(value: str | Path) -> str:
    """Normalize a Windows or POSIX path for storage and display.

    Stored paths always use forward slashes, even on Windows: SQLite/JSON stay
    readable, URLs and logs stay consistent, and `pathlib` accepts them natively.
    UNC prefixes (`//server/share`) are preserved.
    """
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return ""

    text = text.replace("\\", "/")
    unc = text.startswith("//")
    if unc:
        text = text[2:]
    text = _DUPLICATE_SLASH.sub("/", text)
    if unc:
        text = "//" + text

    if text.endswith("/") and not _DRIVE_ROOT.match(text) and text != "/":
        text = text.rstrip("/")
    return text


def join_posix(base: str | Path, *parts: str) -> str:
    """Join path parts with forward slashes without touching the filesystem."""
    pieces = [to_posix_path(base), *(to_posix_path(part) for part in parts)]
    return to_posix_path("/".join(piece for piece in pieces if piece))
