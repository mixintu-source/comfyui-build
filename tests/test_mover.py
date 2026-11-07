from __future__ import annotations

import threading

import pytest

from services.mover import (
    CopyCancelled,
    UnsafePathError,
    copy_atomic,
    copy_with_progress,
    target_path,
)


def test_copy_atomic_creates_dirs_and_keeps_source(tmp_path):
    source = tmp_path / "source" / "model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"weights")
    destination = tmp_path / "target" / "models" / "vae" / "model.safetensors"

    assert copy_atomic(source, destination) == "copied"
    assert destination.read_bytes() == b"weights"
    assert source.read_bytes() == b"weights"


def test_copy_atomic_replaces_and_reports(tmp_path):
    source = tmp_path / "model.safetensors"
    source.write_bytes(b"new")
    destination = tmp_path / "target" / "models" / "model.safetensors"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")

    assert copy_atomic(source, destination) == "replaced"
    assert destination.read_bytes() == b"new"


def test_copy_atomic_same_file_is_a_noop(tmp_path):
    source = tmp_path / "models" / "model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"weights")

    assert copy_atomic(source, source) == "same"


def test_target_path_stays_inside_root(tmp_path):
    destination = target_path(tmp_path / "target", "models/vae/x.safetensors")
    assert destination == (tmp_path / "target" / "models" / "vae" / "x.safetensors").resolve()


def test_target_root_may_be_the_models_dir(tmp_path):
    destination = target_path(tmp_path / "target" / "models", "models/vae/x.safetensors")
    assert destination == (tmp_path / "target" / "models" / "vae" / "x.safetensors").resolve()


@pytest.mark.parametrize(
    "rel_path",
    [
        "models/../evil.safetensors",
        "models/vae/../../evil.safetensors",
        "C:/models/evil.safetensors",
        "/models/evil.safetensors",
        "notmodels/evil.safetensors",
        "models",
        "",
    ],
)
def test_target_path_rejects_escapes(tmp_path, rel_path):
    with pytest.raises(UnsafePathError):
        target_path(tmp_path / "target", rel_path)


def test_failed_copy_leaves_no_temp_file(tmp_path):
    destination = tmp_path / "target" / "models" / "missing.safetensors"
    with pytest.raises(FileNotFoundError):
        copy_atomic(tmp_path / "missing.safetensors", destination)
    assert list(destination.parent.glob("*.part")) == []


def test_cancelled_copy_does_not_leave_a_target(tmp_path):
    source = tmp_path / "models" / "big.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x" * (1024 * 1024))
    destination = tmp_path / "target" / "models" / "big.safetensors"
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(CopyCancelled):
        copy_with_progress(source, destination, lambda copied, total: None, cancel)
    assert not destination.exists()
    assert list(destination.parent.glob("*.part")) == []
