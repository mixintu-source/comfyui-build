from __future__ import annotations

from pathlib import Path

from services.catalog import ModelCatalog
from services.paths import to_posix_path


def target_catalog(target_root: str) -> ModelCatalog | None:
    """Catalog of the migration target, or None when no target is configured."""
    if not target_root.strip():
        return None
    return ModelCatalog([target_root]).refresh()


def split_rel_path(rel_path: str) -> tuple[str, str]:
    """Split into the top-level category and the path shown inside it.

    `models/loras/h3/taomate.safetensors` becomes
    `("loras", "h3/taomate.safetensors")`, so the model-page tree groups by
    category (`📂 loras/`) and keeps sub-folders in the file name.
    """
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if parts and parts[0].lower() == "models":
        parts = parts[1:]
    if not parts:
        return "(root)", rel_path
    if len(parts) == 1:
        return "(root)", parts[0]
    return parts[0], "/".join(parts[1:])


def build_inventory(
    comfyui_root: str,
    sd_root: str,
    target_root: str,
    usage: dict[str, int] | None = None,
) -> dict:
    """Union of source and target model files, with migration status per file."""
    comfy = ModelCatalog([comfyui_root]).refresh()
    sd = ModelCatalog([sd_root]).refresh()
    target = ModelCatalog([target_root]).refresh()
    usage = usage or {}

    rows: dict[str, dict] = {}

    def add_source(catalog: ModelCatalog, label: str, key: str) -> None:
        for entry in catalog.entries():
            row_key = entry.rel_path.lower()
            row = rows.get(row_key)
            if row is None:
                folder, name = split_rel_path(entry.rel_path)
                row = {
                    "rel_path": entry.rel_path,
                    "name": name,
                    "folder": folder,
                    "size": entry.size,
                    "sources": [],
                    "present_in": [],
                    "in_target": False,
                    "target_size": 0,
                    "workflow_count": usage.get(row_key, 0),
                }
                rows[row_key] = row
            if label not in row["sources"]:
                row["sources"].append(label)
            if key not in row["present_in"]:
                row["present_in"].append(key)
            row["size"] = max(row["size"], entry.size)

    add_source(comfy, "ComfyUI", "comfyui")
    add_source(sd, "SD", "sd")

    target_entries = {entry.rel_path.lower(): entry for entry in target.entries()}
    for key, row in rows.items():
        entry = target_entries.get(key)
        if entry is not None:
            row["in_target"] = True
            row["target_size"] = entry.size
            row["present_in"].append("target")

    for key, entry in target_entries.items():
        if key in rows:
            continue
        folder, name = split_rel_path(entry.rel_path)
        rows[key] = {
            "rel_path": entry.rel_path,
            "name": name,
            "folder": folder,
            "size": entry.size,
            "sources": [],
            "present_in": ["target"],
            "in_target": True,
            "target_size": entry.size,
            "workflow_count": usage.get(key, 0),
        }

    for row in rows.values():
        if row["sources"] and row["in_target"]:
            row["status"] = "migrated"
        elif row["sources"]:
            row["status"] = "pending"
        else:
            row["status"] = "target_only"
        row["sources"].sort()
        row["present_in"].sort()

    ordered = sorted(rows.values(), key=lambda item: item["rel_path"].lower())
    source_rows = [row for row in ordered if row["sources"]]
    both = [row for row in source_rows if row["in_target"]]
    return {
        "summary": {
            "source_count": len(source_rows),
            "target_count": len(target_entries),
            "both_count": len(both),
            "pending_count": len(source_rows) - len(both),
            "total_count": len(ordered),
            "source_bytes": sum(row["size"] for row in source_rows),
            "target_bytes": sum(entry.size for entry in target_entries.values()),
            "roots": {
                "comfyui": _root_status(comfyui_root),
                "sd": _root_status(sd_root),
                "target": _root_status(target_root),
            },
        },
        "models": ordered,
    }


def _root_status(path: str) -> dict:
    return {
        "path": to_posix_path(path),
        "exists": bool(path) and Path(path).is_dir(),
    }
