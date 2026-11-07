from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def quick_fingerprint(path: str | Path) -> str:
    """A cheap content fingerprint: size + first/last 1 MiB.

    Full SHA256 is intentionally not used here because preflight runs while the
    user is choosing a destination; reading every GB twice would make the dialog
    slow. The fingerprint is only used to warn about同名不同内容 and to decide
    whether an overwrite is required.
    """
    target = Path(path)
    size = target.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with target.open("rb") as handle:
        digest.update(handle.read(_CHUNK))
        if size > _CHUNK * 2:
            handle.seek(-_CHUNK, 2)
            digest.update(handle.read(_CHUNK))
    return digest.hexdigest()
