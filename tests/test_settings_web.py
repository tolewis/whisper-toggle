"""Bridge logic for the webview Settings window (no pywebview / no GUI needed).

settings_web.py imports `webview` lazily (only in main()/close_window()), so the
Api bridge — read config, save config, drop the reload signal — is testable on
any platform.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "windows" / "settings_web.py"


def _load():
    spec = importlib.util.spec_from_file_location("settings_web", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def sw(monkeypatch):
    mod = _load()
    # Don't touch the real app data dir when saving in tests.
    monkeypatch.setattr(mod, "_write_signal", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "fetch_runtime", lambda: {})
    return mod


def test_save_and_get_state_roundtrip(sw, tmp_path):
    api = sw.Api(config_path=tmp_path / "config.json")
    r = api.save({
        "hotkey": "ctrl+`", "device_override": "cpu", "model": "base.en",
        "streaming": True, "live_partials": True, "autostart": False,
        "audible_cues": False, "partial_debounce_ms": 300, "hardware_catchup_ms": 200,
    })
    assert r["ok"] is True
    c = api.get_state()["config"]
    assert c["hotkey"] == "ctrl+`"
    assert c["device_override"] == "cpu"
    assert c["model"] == "base.en"
    assert c["streaming"] is True
    assert c["live_partials"] is True
    assert c["autostart"] is False
    assert c["audible_cues"] is False
    assert c["partial_debounce_ms"] == 300


def test_choices_exclude_win_h(sw, tmp_path):
    st = sw.Api(config_path=tmp_path / "config.json").get_state()
    assert "ctrl+shift+h" in st["choices"]["hotkey"]
    assert "win+h" not in st["choices"]["hotkey"]


def test_save_rejects_win_h(sw, tmp_path):
    api = sw.Api(config_path=tmp_path / "config.json")
    api.save({"hotkey": "win+h"})
    assert api.get_state()["config"]["hotkey"] != "win+h"


def test_bad_numeric_does_not_crash_save(sw, tmp_path):
    api = sw.Api(config_path=tmp_path / "config.json")
    r = api.save({"partial_debounce_ms": "oops"})
    assert r["ok"] is False  # surfaced, not swallowed into a corrupt config
