"""Config load/save tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whisper_toggle.config import (
    AppConfig,
    CONFIG_VERSION,
    default_config,
    load_config,
    save_config,
)


def test_default_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = default_config()
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.hotkey == cfg.hotkey
    assert loaded.device_override == "auto"
    assert loaded.streaming is False
    assert loaded.live_partials is False
    # Version tracks the package, not a hardcoded literal, so a version bump
    # does not break this test for a non-behavioral reason.
    assert loaded.version == CONFIG_VERSION


def test_hotkey_aliases_normalized_on_load(tmp_path: Path):
    """'windows+' alias and case/space are normalized when a config is loaded.

    Uses a non-reserved key (win+space) so this exercises aliasing, not the
    Win+H unsupported-hotkey fallback covered separately.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "Windows+Space"}), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.hotkey == "win+space"


def test_load_missing_returns_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.hotkey == "ctrl+shift+h"
    assert cfg.streaming is False


def test_audible_cues_defaults_on_and_roundtrips(tmp_path: Path):
    assert default_config().audible_cues is True
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg.audible_cues = False
    save_config(cfg, path)
    assert load_config(path).audible_cues is False


def test_win_h_is_unsupported_and_falls_back_to_default(tmp_path: Path):
    """Windows 11 reserves Win+H for OS voice typing; it cannot be reliably
    claimed, so a win+h config self-heals to the default rather than binding a
    hotkey the user 'still can't use'."""
    from whisper_toggle.config import DEFAULT_HOTKEY

    for raw in ("win+h", "Windows+H", "WIN+H"):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"hotkey": raw}), encoding="utf-8")
        cfg = load_config(path)
        assert cfg.hotkey == DEFAULT_HOTKEY


# ── A4: type validation on load ────────────────────────────────────────────

def test_load_rejects_wrong_typed_values(tmp_path: Path):
    """A hand-edited/corrupt value of the wrong type must not poison the config.

    Before the fix, load_config did a blind setattr, so a string in an int
    field loaded cleanly and blew up later at int(...). Now the field keeps its
    default when the JSON type does not match.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "partial_debounce_ms": "not-a-number",  # bad int
                "streaming": "false",  # bad bool (string, not JSON bool)
                "hardware_catchup_ms": 500,  # valid int -> kept
                "hotkey": "ctrl+alt+j",  # valid str -> kept
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert isinstance(cfg.partial_debounce_ms, int)
    assert cfg.partial_debounce_ms == AppConfig().partial_debounce_ms  # default
    assert cfg.streaming is False and isinstance(cfg.streaming, bool)
    assert cfg.hardware_catchup_ms == 500  # good value survives
    assert cfg.hotkey == "ctrl+alt+j"


def test_load_rejects_bool_in_int_field(tmp_path: Path):
    """JSON true must not slip into an int field (bool is an int subclass)."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"partial_debounce_ms": True}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.partial_debounce_ms == AppConfig().partial_debounce_ms
    assert not isinstance(cfg.partial_debounce_ms, bool)


# ── A4: atomic save ─────────────────────────────────────────────────────────

def test_save_is_atomic_on_serialization_failure(tmp_path: Path, monkeypatch):
    """A failure mid-write must leave the previous good config intact, not a
    half-written/truncated file."""
    path = tmp_path / "config.json"
    good = default_config()
    good.hotkey = "ctrl+shift+h"
    save_config(good, path)
    original = path.read_text(encoding="utf-8")

    # Force the serialized write to blow up after the good file already exists.
    import whisper_toggle.config as config_mod

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.os, "replace", boom)
    with pytest.raises(OSError):
        save_config(AppConfig(hotkey="ctrl+shift+k"), path)

    # The original file is untouched and still valid JSON.
    assert path.read_text(encoding="utf-8") == original
    assert load_config(path).hotkey == "ctrl+shift+h"
    # No leftover temp files in the directory.
    assert list(tmp_path.glob("*.tmp*")) == []


def test_save_no_partial_file_left_behind(tmp_path: Path):
    path = tmp_path / "config.json"
    save_config(default_config(), path)
    assert path.exists()
    assert list(tmp_path.iterdir()) == [path]  # only the config, no temp residue
