from __future__ import annotations

from collections.abc import Iterable, Mapping

ROOT_FOLDER = "(root)"


def group_models(models: Iterable[Mapping]) -> list[dict]:
    """Group model rows into folders for the tree view.

    Input rows need `rel_path`; `id`, `size` and `found` are passed through when
    present. Paths are normalized (backslashes, leading `models/`, empty parts).
    """
    folders: dict[str, list[dict]] = {}
    for model in models:
        raw = str(model.get("rel_path") or "").replace("\\", "/")
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if parts and parts[0].lower() == "models":
            parts = parts[1:]
        name = parts[-1] if parts else raw
        folder = "/".join(parts[:-1]) or ROOT_FOLDER
        folders.setdefault(folder, []).append(
            {
                "id": model.get("id"),
                "name": name,
                "rel_path": raw,
                "size": int(model.get("size") or 0),
                "found": bool(model.get("found", 1)),
                "in_target": bool(model.get("in_target", False)),
                "target_size": int(model.get("target_size") or 0),
            }
        )
    return [
        {"folder": folder, "files": sorted(files, key=lambda item: item["name"].lower())}
        for folder, files in sorted(folders.items())
    ]
