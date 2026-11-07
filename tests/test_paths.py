from __future__ import annotations

from services.paths import to_posix_path


def test_windows_paths_become_forward_slash():
    assert to_posix_path(r"G:\ComfyUI-Shared\models") == "G:/ComfyUI-Shared/models"
    assert (
        to_posix_path(r"F:\Comfy-Desktop\ComfyUI (1)\user\default\workflows")
        == "F:/Comfy-Desktop/ComfyUI (1)/user/default/workflows"
    )


def test_trailing_slashes_and_duplicates_are_normalized():
    assert to_posix_path("G:/models//vae/") == "G:/models/vae"
    assert to_posix_path("G:\\") == "G:/"
    assert to_posix_path("  \"G:\\models\"  ") == "G:/models"


def test_unc_prefix_is_preserved():
    assert to_posix_path(r"\\NAS\share\models") == "//NAS/share/models"


def test_empty_path_stays_empty():
    assert to_posix_path("") == ""
    assert to_posix_path("   ") == ""
