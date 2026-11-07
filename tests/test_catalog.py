from __future__ import annotations

from pathlib import Path

from services.catalog import ModelCatalog, models_base, rel_path_for


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_models_base_detects_comfyui_root_and_models_dir(tmp_path):
    comfy = tmp_path / "ComfyUI"
    _write(comfy / "models" / "vae" / "x.safetensors", b"x")
    assert models_base(comfy) == comfy / "models"
    assert models_base(comfy / "models") == comfy / "models"


def test_catalog_resolves_bare_names_subdirs_and_prefixes(tmp_path):
    comfy = tmp_path / "ComfyUI"
    sd = tmp_path / "SD"
    _write(comfy / "models" / "diffusion_models" / "trellis.safetensors", b"a" * 10)
    _write(comfy / "models" / "vae" / "shape.safetensors", b"b" * 20)
    _write(comfy / "models" / "checkpoints" / "base.safetensors", b"c" * 30)
    _write(sd / "models" / "diffusion_models" / "sd_only.safetensors", b"d" * 40)

    catalog = ModelCatalog([comfy, sd]).refresh()

    assert len(catalog.entries()) == 4
    assert catalog.resolve("base.safetensors").rel_path == "models/checkpoints/base.safetensors"
    assert (
        catalog.resolve("diffusion_models/trellis.safetensors").rel_path
        == "models/diffusion_models/trellis.safetensors"
    )
    assert (
        catalog.resolve("models\\vae\\shape.safetensors").rel_path
        == "models/vae/shape.safetensors"
    )
    assert catalog.resolve("sd_only.safetensors").rel_path == "models/diffusion_models/sd_only.safetensors"
    assert catalog.resolve("sd_only.safetensors").size == 40


def test_catalog_prefers_comfyui_when_relative_paths_collide(tmp_path):
    comfy = tmp_path / "ComfyUI"
    sd = tmp_path / "SD"
    _write(comfy / "models" / "vae" / "same.safetensors", b"comfy")
    _write(sd / "models" / "vae" / "same.safetensors", b"sd")

    catalog = ModelCatalog([comfy, sd]).refresh()

    resolved = catalog.resolve("vae/same.safetensors")
    assert resolved.rel_path == "models/vae/same.safetensors"
    assert resolved.size == len(b"comfy")


def test_catalog_ambiguous_basename_picks_shortest_path(tmp_path):
    comfy = tmp_path / "ComfyUI"
    _write(comfy / "models" / "a" / "dup.safetensors", b"1")
    _write(comfy / "models" / "long_folder_name" / "dup.safetensors", b"2")

    resolved = ModelCatalog([comfy]).resolve("dup.safetensors")
    assert resolved.rel_path == "models/a/dup.safetensors"


def test_unresolved_reference_keeps_a_placeholder(tmp_path):
    catalog = ModelCatalog([tmp_path / "missing"]).refresh()
    resolved = catalog.resolve("nowhere/missing.safetensors")

    assert resolved.found is False
    assert resolved.abs_path is None
    assert resolved.rel_path == "models/(未找到)/missing.safetensors"


def test_rel_path_for_rejects_escapes():
    assert rel_path_for("vae/x.safetensors") == "models/vae/x.safetensors"
    assert rel_path_for("models/vae/x.safetensors") == "models/vae/x.safetensors"
    assert rel_path_for("../x.safetensors") == ""
    assert rel_path_for("C:/models/x.safetensors") == ""
