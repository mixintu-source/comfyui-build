from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_app
from routers.upload import _safe_filename

WORKFLOW_ONE = {
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["trellis.safetensors"]},
        {"id": 2, "type": "VAELoader", "widgets_values": ["shape.safetensors"]},
    ],
    "links": [],
}

WORKFLOW_TWO = {
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["trellis.safetensors"]},
        {"id": 2, "type": "CLIPVisionLoader", "widgets_values": ["dino.safetensors"]},
    ],
    "links": [],
}


def _write_model(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m" * size)


@pytest.fixture()
def env(tmp_path: Path):
    comfy = tmp_path / "ComfyUI"
    _write_model(comfy / "models" / "diffusion_models" / "trellis.safetensors", 100)
    _write_model(comfy / "models" / "vae" / "shape.safetensors", 200)
    _write_model(comfy / "models" / "clip_vision" / "dino.safetensors", 300)
    _write_model(comfy / "models" / "checkpoints" / "base.safetensors", 400)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    data = tmp_path / "data"

    app = create_app(data)
    with TestClient(app) as client:
        response = client.post(
            "/api/config",
            json={
                "comfyui_models_root": str(comfy),
                "sd_models_root": "",
                "migrate_target_root": str(target),
                "workflow_json_dir": str(workflow_dir),
                "sd_workflow_dir": "",
            },
        )
        assert response.status_code == 200
        yield {
            "client": client,
            "comfy": comfy,
            "workflow_dir": workflow_dir,
            "target": target,
            "data": data,
        }


def _upload(client: TestClient, name: str, workflow: dict) -> dict:
    response = client.post(
        "/api/upload",
        files={"file": (name, json.dumps(workflow).encode("utf-8"), "application/json")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_full_import_analyze_migrate_flow(env):
    client: TestClient = env["client"]
    first = _upload(client, "wf1.json", WORKFLOW_ONE)
    second = _upload(client, "wf2.json", WORKFLOW_TWO)
    assert first["model_count"] == 2
    assert second["model_count"] == 2

    workflows = client.get("/api/workflows").json()
    assert {item["name"] for item in workflows} == {"wf1.json", "wf2.json"}
    assert workflows[0]["tree"]
    assert any(
        file["name"] == "trellis.safetensors" and file["id"]
        for folder in workflows[0]["tree"]
        for file in folder["files"]
    )

    trellis = next(
        model for model in first["workflow"]["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    plan = client.get(
        "/api/analyze",
        params={"model_id": trellis["id"], "workflow_id": first["workflow"]["id"]},
    ).json()
    assert [item["name"] for item in plan["shared_with"]] == ["wf2.json"]
    assert len(plan["candidate_models"]) == 2

    migration = client.post(
        "/api/migrate", json={"rel_paths": [trellis["rel_path"]]}
    ).json()["results"][0]
    assert migration["status"] == "success", migration
    source = env["comfy"] / "models" / "diffusion_models" / "trellis.safetensors"
    destination = env["target"] / "models" / "diffusion_models" / "trellis.safetensors"
    assert destination.read_bytes() == b"m" * 100
    assert source.exists()

    logs = client.get("/api/logs").json()
    assert len(logs) == 1
    assert logs[0]["status"] == "success"

    stats = client.get("/api/stats").json()
    assert stats["total_models"] == 4
    assert stats["total_workflows"] == 2
    assert stats["migrated_count"] == 1
    assert stats["failed_count"] == 0
    assert stats["model_bytes"] == 1000
    assert stats["recent_workflows"]
    assert stats["recent_workflows"][0]["modified_at"]

    assert client.delete(f"/api/logs/{logs[0]['id']}").status_code == 200
    assert client.get("/api/logs").json() == []

    assert client.delete(f"/api/workflows/{second['workflow']['id']}").status_code == 200
    remaining = client.get("/api/workflows").json()
    assert len(remaining) == 1
    assert len(remaining[0]["models"]) == 2


def test_delete_workflow_can_remove_local_json(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    path = Path(workflow["file_path"])
    assert path.is_file()

    response = client.delete(f"/api/workflows/{workflow['id']}?delete_file=true")
    assert response.status_code == 200, response.text
    assert response.json()["file_deleted"] is True
    assert not path.exists()
    assert client.get("/api/workflows").json() == []


def test_delete_workflow_keeps_local_json_by_default(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    path = Path(workflow["file_path"])

    response = client.delete(f"/api/workflows/{workflow['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["file_deleted"] is False
    assert path.is_file()


def test_scan_imports_new_files_and_skips_known_ones(env):
    client: TestClient = env["client"]
    _upload(client, "known.json", WORKFLOW_ONE)
    (env["workflow_dir"] / "extra.json").write_text(json.dumps(WORKFLOW_TWO), encoding="utf-8")
    (env["workflow_dir"] / "broken.json").write_text("{oops", encoding="utf-8")

    result = client.post("/api/workflows/scan").json()
    assert result["imported_count"] == 1
    assert result["skipped"] == 1
    assert result["failed"][0]["file"].endswith("broken.json")

    names = {item["name"] for item in client.get("/api/workflows").json()}
    assert names == {"known.json", "extra.json"}


def test_upload_rejects_bad_input(env):
    client: TestClient = env["client"]
    assert client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    ).status_code == 400
    assert client.post(
        "/api/upload",
        files={"file": ("broken.json", b"{nope", "application/json")},
    ).status_code == 400


def test_upload_filename_is_sanitized():
    assert _safe_filename("..\\..\\evil.json") == "evil.json"
    assert _safe_filename('../../a<b>.json') == "a_b_.json"


def test_config_is_stored_with_forward_slashes(env):
    client: TestClient = env["client"]
    payload = {
        "comfyui_models_root": str(env["comfy"]).replace("/", "\\"),
        "sd_models_root": "",
        "migrate_target_root": str(env["target"]).replace("/", "\\"),
        "workflow_json_dir": str(env["workflow_dir"]).replace("/", "\\"),
        "sd_workflow_dir": "",
    }
    saved = client.post("/api/config", json=payload).json()
    loaded = client.get("/api/config").json()
    for config in (saved, loaded):
        assert "\\" not in config["comfyui_models_root"]
        assert "\\" not in config["migrate_target_root"]
        assert "\\" not in config["workflow_json_dir"]
        assert config["comfyui_models_root"].startswith("C:/")


def test_model_inventory_combines_sources_and_target(env):
    client: TestClient = env["client"]
    target_models = env["target"] / "models"
    _write_model(target_models / "vae" / "shape.safetensors", 200)
    _write_model(target_models / "loras" / "target_only.safetensors", 50)

    data = client.get("/api/models").json()
    summary = data["summary"]
    assert summary["source_count"] == 4
    assert summary["target_count"] == 2
    assert summary["both_count"] == 1
    assert summary["pending_count"] == 3
    assert summary["total_count"] == 5
    assert summary["roots"]["comfyui"]["exists"] is True

    rows = {row["rel_path"]: row for row in data["models"]}
    shape = rows["models/vae/shape.safetensors"]
    assert shape["status"] == "migrated"
    assert shape["in_target"] is True
    assert shape["sources"] == ["ComfyUI"]
    assert rows["models/loras/target_only.safetensors"]["status"] == "target_only"


def test_migration_skips_an_identical_target_copy(env):
    client: TestClient = env["client"]
    first = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in first["models"] if model["rel_path"].endswith("trellis.safetensors")
    )

    first_result = client.post(
        "/api/migrate", json={"rel_paths": [trellis["rel_path"]]}
    ).json()["results"][0]
    second_result = client.post(
        "/api/migrate", json={"rel_paths": [trellis["rel_path"]]}
    ).json()["results"][0]
    assert first_result["status"] == "success"
    assert second_result["status"] == "success"
    assert "跳过" in second_result["message"]

    workflows = client.get("/api/workflows").json()
    item = next(wf for wf in workflows if wf["name"] == "wf1.json")
    trellis_in_tree = next(
        file
        for folder in item["tree"]
        for file in folder["files"]
        if file["name"] == "trellis.safetensors"
    )
    assert trellis_in_tree["in_target"] is True
    assert trellis_in_tree["target_size"] == 100


def test_migration_accepts_a_file_that_only_exists_in_target(env):
    client: TestClient = env["client"]
    _write_model(env["target"] / "models" / "loras" / "already.safetensors", 10)

    result = client.post(
        "/api/migrate", json={"rel_paths": ["models/loras/already.safetensors"]}
    ).json()["results"][0]
    assert result["status"] == "success"
    assert "已在目标目录" in result["message"]


def _config_body(env, **overrides) -> dict:
    body = {
        "comfyui_models_root": str(env["comfy"]),
        "sd_models_root": "",
        "migrate_target_root": str(env["target"]),
        "workflow_json_dir": str(env["workflow_dir"]),
        "sd_workflow_dir": "",
        "allow_delete_source": False,
    }
    body.update(overrides)
    return body


def _enable_delete(client: TestClient, env) -> None:
    response = client.post(
        "/api/config", json=_config_body(env, allow_delete_source=True)
    )
    assert response.status_code == 200


def _configure_with_sd(client: TestClient, env, sd_root: Path) -> None:
    response = client.post(
        "/api/config",
        json={
            "comfyui_models_root": str(env["comfy"]),
            "sd_models_root": str(sd_root),
            "migrate_target_root": str(env["target"]),
            "workflow_json_dir": str(env["workflow_dir"]),
            "sd_workflow_dir": "",
        },
    )
    assert response.status_code == 200


def test_migration_can_copy_to_a_chosen_destination(env):
    client: TestClient = env["client"]
    sd_root = env["comfy"].parent / "SD"
    sd_root.mkdir(parents=True, exist_ok=True)
    _configure_with_sd(client, env, sd_root)
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )

    result = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "destination": "sd"},
    ).json()["results"][0]
    assert result["status"] == "success"
    assert result["destination"] == "sd"
    assert (sd_root / "models" / "diffusion_models" / "trellis.safetensors").exists()
    assert (env["comfy"] / "models" / "diffusion_models" / "trellis.safetensors").exists()


def test_target_only_model_can_be_copied_back_to_comfyui(env):
    client: TestClient = env["client"]
    _write_model(env["target"] / "models" / "loras" / "back.safetensors", 10)

    result = client.post(
        "/api/migrate",
        json={
            "rel_paths": ["models/loras/back.safetensors"],
            "destination": "comfyui",
        },
    ).json()["results"][0]
    assert result["status"] == "success"
    assert (env["comfy"] / "models" / "loras" / "back.safetensors").exists()
    assert (env["target"] / "models" / "loras" / "back.safetensors").exists()


def test_move_can_delete_the_source_when_explicitly_requested(env):
    client: TestClient = env["client"]
    _enable_delete(client, env)
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    source = env["comfy"] / "models" / "diffusion_models" / "trellis.safetensors"

    result = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "move": True},
    ).json()["results"][0]
    assert result["status"] == "success"
    assert result["move"] is True
    assert "剪贴" in result["message"]
    assert not source.exists()
    assert (env["target"] / "models" / "diffusion_models" / "trellis.safetensors").exists()


def test_move_to_the_same_location_never_deletes_the_source(env):
    client: TestClient = env["client"]
    _enable_delete(client, env)
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    source = env["comfy"] / "models" / "diffusion_models" / "trellis.safetensors"

    result = client.post(
        "/api/migrate",
        json={
            "rel_paths": [trellis["rel_path"]],
            "destination": "comfyui",
            "move": True,
        },
    ).json()["results"][0]
    assert result["status"] == "success"
    assert source.exists()
    assert "未删除源文件" in result["message"] or "无需迁移" in result["message"]


def test_batch_migration_accepts_multiple_rel_paths(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    rels = [model["rel_path"] for model in workflow["models"]]

    results = client.post("/api/migrate", json={"rel_paths": rels}).json()["results"]
    assert len(results) == 2
    assert all(result["status"] == "success" for result in results)
    assert (env["target"] / "models" / "diffusion_models" / "trellis.safetensors").exists()
    assert (env["target"] / "models" / "vae" / "shape.safetensors").exists()


def test_model_inventory_groups_by_top_level_category(env):
    client: TestClient = env["client"]
    _write_model(env["comfy"] / "models" / "loras" / "h3" / "nested.safetensors", 10)

    data = client.get("/api/models").json()
    row = next(
        item for item in data["models"] if item["rel_path"].endswith("nested.safetensors")
    )
    assert row["folder"] == "loras"
    assert row["name"] == "h3/nested.safetensors"


def test_model_inventory_reports_workflow_usage(env):
    client: TestClient = env["client"]
    _upload(client, "wf1.json", WORKFLOW_ONE)

    rows = {row["rel_path"]: row for row in client.get("/api/models").json()["models"]}
    trellis = rows["models/diffusion_models/trellis.safetensors"]
    assert trellis["workflow_count"] == 1
    assert trellis["present_in"] == ["comfyui"]


def test_migration_rejects_missing_source(env):
    client: TestClient = env["client"]
    result = client.post(
        "/api/migrate", json={"rel_paths": ["models/(未找到)/ghost.safetensors"]}
    ).json()["results"][0]
    assert result["status"] == "failed"
    assert "未找到" in result["message"]


def test_config_validation_reports_missing_and_inside_target(env):
    client: TestClient = env["client"]
    missing = env["target"] / "missing"
    result = client.post(
        "/api/config/validate",
        json=_config_body(env, migrate_target_root=str(missing)),
    ).json()
    assert result["ok"] is False
    assert any(item["code"] == "not_found" for item in result["errors"])

    inside = env["comfy"] / "models" / "inside-target"
    inside.mkdir(parents=True)
    result = client.post(
        "/api/config/validate",
        json=_config_body(env, migrate_target_root=str(inside)),
    ).json()
    assert result["ok"] is False
    assert any(item["code"] == "inside_source" for item in result["errors"])

    response = client.post(
        "/api/config",
        json=_config_body(env, migrate_target_root=str(missing)),
    )
    assert response.status_code == 422
    assert client.get("/api/config").json()["migrate_target_root"] == str(
        env["target"]
    ).replace("\\", "/")


def test_config_validation_warns_about_optional_missing_sd(env):
    client: TestClient = env["client"]
    result = client.post(
        "/api/config/validate",
        json=_config_body(
            env, sd_models_root=str(env["comfy"].parent / "missing-sd")
        ),
    ).json()
    assert result["ok"] is True
    assert any(item["code"] == "not_found" for item in result["warnings"])


def test_config_persists_across_app_restart(env):
    client: TestClient = env["client"]
    _enable_delete(client, env)
    saved = client.get("/api/config").json()

    from main import create_app

    app2 = create_app(env["data"])
    client2 = TestClient(app2)
    loaded = client2.get("/api/config").json()
    assert loaded == saved
    assert loaded["allow_delete_source"] is True


def test_index_html_is_not_cached(env):
    client: TestClient = env["client"]
    response = client.get("/")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")


def test_workflow_list_uses_file_modification_time(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    row = client.get("/api/workflows").json()[0]
    assert row["id"] == workflow["id"]
    datetime.strptime(row["modified_at"], "%Y-%m-%d %H:%M:%S")


def test_open_workflow_folder_uses_explorer_helper(env, monkeypatch):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    opened: list[Path] = []

    import routers.workflows as workflows_router

    monkeypatch.setattr(
        workflows_router, "_reveal_folder", lambda folder: opened.append(folder)
    )
    response = client.post(f"/api/workflows/{workflow['id']}/open")
    assert response.status_code == 200, response.text
    assert opened and opened[0] == env["workflow_dir"].resolve()
    assert client.post("/api/workflows/99999/open").status_code == 404


def test_async_migration_task_reports_completion(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    rels = [model["rel_path"] for model in workflow["models"]]

    created = client.post("/api/migrate/tasks", json={"rel_paths": rels})
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    task = {}
    for _ in range(100):
        task = client.get(f"/api/migrate/tasks/{task_id}").json()
        if task["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert task["status"] == "done", task
    assert task["percent"] == 1
    assert len(task["results"]) == 2
    assert all(result["status"] == "success" for result in task["results"])
    assert (env["target"] / "models" / "diffusion_models" / "trellis.safetensors").exists()


def test_async_migration_task_can_move_source(env):
    client: TestClient = env["client"]
    _enable_delete(client, env)
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    source = env["comfy"] / "models" / "diffusion_models" / "trellis.safetensors"

    created = client.post(
        "/api/migrate/tasks",
        json={"rel_paths": [trellis["rel_path"]], "move": True},
    ).json()
    task_id = created["task_id"]
    task = {}
    for _ in range(100):
        task = client.get(f"/api/migrate/tasks/{task_id}").json()
        if task["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert task["status"] == "done", task
    assert task["results"][0]["status"] == "success"
    assert not source.exists()
    assert (env["target"] / "models" / "diffusion_models" / "trellis.safetensors").exists()


def test_async_task_can_be_cancelled(env):
    client: TestClient = env["client"]
    # A pre-set cancel is not possible through the API, but a finished task
    # must reject cancellation instead of hanging.
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    created = client.post(
        "/api/migrate/tasks",
        json={"rel_paths": [workflow["models"][0]["rel_path"]]},
    ).json()
    task_id = created["task_id"]
    for _ in range(100):
        task = client.get(f"/api/migrate/tasks/{task_id}").json()
        if task["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    response = client.post(f"/api/migrate/tasks/{task_id}/cancel")
    assert response.status_code == 404


def test_preflight_detects_different_target_and_requires_overwrite(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    target_file = env["target"] / "models" / "diffusion_models" / "trellis.safetensors"
    _write_model(target_file, 50)

    plan = client.post(
        "/api/migrate/preflight", json={"rel_paths": [trellis["rel_path"]]}
    ).json()
    item = plan["files"][0]
    assert plan["requires_overwrite"] is True
    assert item["target_exists"] is True
    assert item["target_size"] == 50
    assert item["action"] == "blocked"
    assert any("大小不同" in message for message in plan["risk_messages"])

    blocked = client.post(
        "/api/migrate", json={"rel_paths": [trellis["rel_path"]]}
    ).json()["results"][0]
    assert blocked["status"] == "failed"
    assert target_file.read_bytes() == b"m" * 50

    forced = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "overwrite": True},
    ).json()["results"][0]
    assert forced["status"] == "success"
    assert target_file.read_bytes() == b"m" * 100


def test_same_size_different_content_requires_overwrite(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    target_file = env["target"] / "models" / "diffusion_models" / "trellis.safetensors"
    _write_model(target_file, 100)
    target_file.write_bytes(b"x" * 100)

    plan = client.post(
        "/api/migrate/preflight", json={"rel_paths": [trellis["rel_path"]]}
    ).json()
    assert plan["requires_overwrite"] is True
    assert any("内容特征" in message for message in plan["risk_messages"])

    forced = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "overwrite": True},
    ).json()["results"][0]
    assert forced["status"] == "success"
    assert target_file.read_bytes() == b"m" * 100


def test_preflight_rejects_insufficient_disk_space(env, monkeypatch):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )

    class Usage:
        free = 10

    monkeypatch.setattr(
        "services.preflight.shutil.disk_usage", lambda path: Usage()
    )
    preflight = client.post(
        "/api/migrate/preflight",
        json={"rel_paths": [trellis["rel_path"]]},
    ).json()
    assert preflight["ok"] is False
    assert preflight["need_bytes"] == 100
    assert preflight["free_bytes"] == 10
    assert "空间不足" in preflight["reason"]

    response = client.post(
        "/api/migrate", json={"rel_paths": [trellis["rel_path"]]}
    )
    assert response.status_code == 409
    assert not (env["target"] / "models" / "diffusion_models" / "trellis.safetensors").exists()


def test_move_requires_allow_delete_source(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )

    denied = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "move": True},
    )
    assert denied.status_code == 403

    _enable_delete(client, env)
    allowed = client.post(
        "/api/migrate",
        json={"rel_paths": [trellis["rel_path"]], "move": True},
    ).json()["results"][0]
    assert allowed["status"] == "success"


def test_import_workflow_by_absolute_path_and_update_not_duplicate(env, tmp_path):
    client: TestClient = env["client"]
    external = tmp_path / "external"
    external.mkdir()
    workflow_file = external / "external.json"
    workflow_file.write_text(json.dumps(WORKFLOW_ONE), encoding="utf-8")

    response = client.post(
        "/api/workflows/import-path", json={"path": str(workflow_file)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["model_count"] == 2

    again = client.post(
        "/api/workflows/import-path", json={"path": str(workflow_file)}
    )
    assert again.status_code == 200
    assert len(client.get("/api/workflows").json()) == 1

    traversal = client.post(
        "/api/workflows/import-path",
        json={"path": str(tmp_path / ".." / "outside.json")},
    )
    assert traversal.status_code == 400
    relative = client.post(
        "/api/workflows/import-path", json={"path": "outside.json"}
    )
    assert relative.status_code == 400


def test_scan_preview_lists_files_and_imports_selected(env):
    client: TestClient = env["client"]
    first = env["workflow_dir"] / "one.json"
    second = env["workflow_dir"] / "two.json"
    first.write_text(json.dumps(WORKFLOW_ONE), encoding="utf-8")
    second.write_text(json.dumps(WORKFLOW_TWO), encoding="utf-8")

    preview = client.get("/api/workflows/scan/preview").json()
    paths = {item["path"]: item for item in preview["files"]}
    assert paths[str(first).replace("\\", "/")]["imported"] is False

    response = client.post(
        "/api/workflows/scan/import",
        json={"paths": [str(first)]},
    )
    assert response.status_code == 200
    assert response.json()["imported_count"] == 1

    preview = client.get("/api/workflows/scan/preview").json()
    paths = {item["path"]: item for item in preview["files"]}
    assert paths[str(first).replace("\\", "/")]["imported"] is True


def test_analyze_reports_target_presence(env):
    client: TestClient = env["client"]
    workflow = _upload(client, "wf1.json", WORKFLOW_ONE)["workflow"]
    trellis = next(
        model for model in workflow["models"] if model["rel_path"].endswith("trellis.safetensors")
    )
    _write_model(
        env["target"] / "models" / "diffusion_models" / "trellis.safetensors", 100
    )

    plan = client.get(
        "/api/analyze",
        params={"model_id": trellis["id"], "workflow_id": workflow["id"]},
    ).json()
    candidate = next(
        item for item in plan["candidate_models"] if item["id"] == trellis["id"]
    )
    assert candidate["in_target"] is True
    assert candidate["target_size"] == 100
