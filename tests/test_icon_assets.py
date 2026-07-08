from pathlib import Path

from whisper_toggle.icons import tray_icon, write_app_icon


def test_tray_state_images_generate():
    for state in ("idle", "recording", "processing", "starting", "error"):
        img = tray_icon(state)
        assert img.size == (64, 64)
        assert img.mode == "RGBA"


def test_icon_ico_write(tmp_path: Path):
    path = write_app_icon(tmp_path / "icon.ico")
    assert path.exists()
    assert path.stat().st_size > 100
    # Repo asset should exist after generate step
    repo_icon = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
    assert repo_icon.exists(), "assets/icon.ico missing — run icon generate"


def test_repo_icon_ico_exists():
    repo_icon = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
    assert repo_icon.exists()
