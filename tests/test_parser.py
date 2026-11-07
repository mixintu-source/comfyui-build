from __future__ import annotations

import pytest

from services.parser import extract_model_refs, parse_workflow_bytes


def test_ui_format_extracts_widget_references_in_order():
    workflow = {
        "last_node_id": 9,
        "nodes": [
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["sd_xl_base_1.0.safetensors", "normal"],
            },
            {
                "id": 2,
                "type": "LoraLoader",
                "widgets_values": [["detail.safetensors", 0.8], "prompt ending in .pt?"],
            },
            {
                "id": 3,
                "type": "Note",
                "widgets_values": ["see https://example.com/ref.safetensors"],
            },
        ],
        "links": [],
    }

    assert extract_model_refs(workflow) == [
        "sd_xl_base_1.0.safetensors",
        "detail.safetensors",
    ]


def test_api_format_and_backslash_normalization():
    workflow = {
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "vae\\sdxl_vae.safetensors"},
        },
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "clip_l.safetensors"}},
        "5": {"class_type": "PrimitiveString", "inputs": {"value": "not-a-model.png"}},
    }

    assert extract_model_refs(workflow) == [
        "vae/sdxl_vae.safetensors",
        "clip_l.safetensors",
    ]


def test_duplicate_references_collapse():
    workflow = {"nodes": [{"widgets_values": ["a.safetensors", "a.safetensors"]}]}
    assert extract_model_refs(workflow) == ["a.safetensors"]


def test_nested_subgraph_nodes_are_walked():
    workflow = {
        "nodes": [
            {
                "type": "subgraph",
                "widgets_values": ["outer.safetensors"],
                "nodes": [
                    {
                        "type": "CheckpointLoaderSimple",
                        "widgets_values": ["inner.safetensors"],
                    }
                ],
            }
        ],
        "definitions": {
            "subgraphs": [
                {"nodes": [{"widgets_values": ["definition.safetensors"]}]}
            ]
        },
    }

    assert set(extract_model_refs(workflow)) == {
        "outer.safetensors",
        "inner.safetensors",
        "definition.safetensors",
    }


def test_parse_rejects_broken_and_non_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_workflow_bytes(b"{not json")
    with pytest.raises(ValueError, match="顶层"):
        parse_workflow_bytes(b'"just a string"')
    with pytest.raises(ValueError, match="UTF-8"):
        parse_workflow_bytes(b"\xff\xfe\x00")
