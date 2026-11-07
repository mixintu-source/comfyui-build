"""Create a small, realistic demo tree and import it into the local database.

Run from the project root:  .venv\\Scripts\\python scripts\\seed_demo.py
The script is idempotent: model files are overwritten, workflows are re-imported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_store  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402
from schemas import PathConfig  # noqa: E402
from services.catalog import ModelCatalog  # noqa: E402
from services.importer import register_workflow  # noqa: E402
from services.paths import to_posix_path  # noqa: E402
from services.parser import extract_model_refs  # noqa: E402

DEMO = ROOT / "demo"
COMFY = DEMO / "ComfyUI"
SD = DEMO / "SD"
TARGET = DEMO / "target"

MODELS = {
    "ComfyUI/models/diffusion_models/trellis_2_int8_convrot.safetensors": 1024 * 1024,
    "ComfyUI/models/diffusion_models/pixal3d_int8_convrot.safetensors": 1024 * 1024 + 512 * 1024,
    "ComfyUI/models/vae/trellis_2_shape_vae_bf16.safetensors": 256 * 1024,
    "ComfyUI/models/vae/trellis_2_texture_vae_bf16.safetensors": 256 * 1024,
    "ComfyUI/models/clip_vision/dino_v3_L_naf_fp32.safetensors": 512 * 1024,
    "ComfyUI/models/background_removal/birefnet.safetensors": 128 * 1024,
    "ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors": 2 * 1024 * 1024,
    "SD/models/checkpoints/sd15_base.safetensors": 1024 * 1024,
}

TRELLIS_WORKFLOW = {
    "last_node_id": 7,
    "last_link_id": 9,
    "nodes": [
        {
            "id": 1,
            "type": "CheckpointLoaderSimple",
            "widgets_values": ["trellis_2_int8_convrot.safetensors"],
        },
        {
            "id": 2,
            "type": "UnetLoaderGGUF",
            "widgets_values": ["pixal3d_int8_convrot.safetensors"],
        },
        {
            "id": 3,
            "type": "VAELoader",
            "widgets_values": ["trellis_2_shape_vae_bf16.safetensors"],
        },
        {
            "id": 4,
            "type": "VAELoader",
            "widgets_values": ["trellis_2_texture_vae_bf16.safetensors"],
        },
        {
            "id": 5,
            "type": "CLIPVisionLoader",
            "widgets_values": ["dino_v3_L_naf_fp32.safetensors"],
        },
        {
            "id": 6,
            "type": "BackgroundRemoval",
            "widgets_values": ["birefnet.safetensors", "alpha"],
        },
        {
            "id": 7,
            "type": "Note",
            "widgets_values": ["提示：本工作流的所有权重都应位于 models/ 目录。"],
        },
    ],
    "links": [],
}

MULTI_VIEWS_WORKFLOW = {
    "last_node_id": 4,
    "nodes": [
        {
            "id": 1,
            "type": "CheckpointLoaderSimple",
            "widgets_values": ["trellis_2_int8_convrot.safetensors"],
        },
        {
            "id": 2,
            "type": "VAELoader",
            "widgets_values": ["trellis_2_shape_vae_bf16.safetensors"],
        },
        {
            "id": 3,
            "type": "CLIPVisionLoader",
            "widgets_values": ["dino_v3_L_naf_fp32.safetensors"],
        },
    ],
    "links": [],
}

SD_WORKFLOW = {
    "3": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd15_base.safetensors"},
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a photo of a red apple", "clip": ["3", 1]},
    },
}


def _write_models() -> None:
    for relative, size in MODELS.items():
        path = DEMO / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size != size:
            path.write_bytes(b"\0" * size)


def _write_workflows() -> list[Path]:
    comfy_workflows = COMFY / "user" / "default" / "workflows"
    sd_workflows = SD / "workflows"
    comfy_workflows.mkdir(parents=True, exist_ok=True)
    sd_workflows.mkdir(parents=True, exist_ok=True)

    files = [
        (comfy_workflows / "3d_pixal3d_trellis2_image_to_model.json", TRELLIS_WORKFLOW),
        (comfy_workflows / "3d_pixal3d_multi_views.json", MULTI_VIEWS_WORKFLOW),
        (sd_workflows / "sd_basic.json", SD_WORKFLOW),
    ]
    for path, document in files:
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return [path for path, _ in files]


def main() -> None:
    settings.configure()
    db.init_schema()
    TARGET.mkdir(parents=True, exist_ok=True)
    _write_models()
    workflows = _write_workflows()

    config = PathConfig(
        comfyui_models_root=to_posix_path(COMFY),
        sd_models_root=to_posix_path(SD),
        migrate_target_root=to_posix_path(TARGET),
        workflow_json_dir=to_posix_path(COMFY / "user" / "default" / "workflows"),
        sd_workflow_dir=to_posix_path(SD / "workflows"),
    )
    config_store.save(config)

    catalog = ModelCatalog([config.comfyui_models_root, config.sd_models_root])
    with db.get_conn() as conn:
        for path in workflows:
            document = json.loads(path.read_text(encoding="utf-8"))
            register_workflow(
                conn,
                catalog,
                path.name,
                path,
                extract_model_refs(document),
            )

    print(f"演示数据已就绪：{len(workflows)} 个工作流，{len(catalog.entries())} 个模型")
    print(f"配置写入：{settings.data_dir() / 'config.json'}")
    print("启动：.\\.venv\\Scripts\\python -m uvicorn main:app --port 8765")


if __name__ == "__main__":
    main()
