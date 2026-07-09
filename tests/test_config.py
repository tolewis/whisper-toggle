"""Config load/save tests."""

from __future__ import annotations

from pathlib import Path

from whisper_toggle.config import AppConfig, default_config, load_config, save_config


def test_default_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = default_config()
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.hotkey == cfg.hotkey
    assert loaded.device_override == "auto"
    assert loaded.streaming is False
    assert loaded.live_partials is False
    assert loaded.version == "2.0.0"


def test_hotkey_validation():
    cfg = default_config()
    assert cfg.hotkey.lower().replace(" ", "") == "ctrl+shift+h"
    cfg2 = AppConfig(
        hotkey="ctrl+shift+h",
        device_override="cpu",
        model="",
        streaming=True,
        autostart=True,
        partial_debounce_ms=400,
        hardware_catchup_ms=250,
        version="2.0.0",
    )
    assert cfg2.hotkey == "ctrl+shift+h"


def test_load_missing_returns_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.hotkey == "ctrl+shift+h"
    assert cfg.streaming is False
