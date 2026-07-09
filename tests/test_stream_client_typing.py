"""L1 (client half) — stream_ws_client typing must branch on $WAYLAND_DISPLAY.

The partial-typing path used `xdotool type` unconditionally, which silently
no-ops on native Wayland. It must use wtype under Wayland and xdotool under
X11, and degrade (notify / stderr) rather than silently no-op when the tool
is missing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT = REPO_ROOT / "linux" / "stream_ws_client.py"


def _load():
    spec = importlib.util.spec_from_file_location("stream_ws_client_typing", CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(monkeypatch):
    mod = _load()
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._calls = calls  # type: ignore[attr-defined]
    return mod


def _set_tools(mod, monkeypatch, available):
    import shutil

    def fake_which(name):
        return f"/usr/bin/{name}" if name in available else None

    monkeypatch.setattr(mod.shutil, "which", fake_which)


def test_type_text_wayland_uses_wtype(client, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _set_tools(client, monkeypatch, {"wtype", "xdotool"})
    client.type_text("hello")
    tools = [c[0] for c in client._calls]
    assert "wtype" in tools
    assert "xdotool" not in tools


def test_type_text_x11_uses_xdotool(client, monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _set_tools(client, monkeypatch, {"wtype", "xdotool"})
    client.type_text("hello")
    tools = [c[0] for c in client._calls]
    assert "xdotool" in tools
    assert "wtype" not in tools


def test_revise_text_wayland_backspaces_with_wtype(client, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _set_tools(client, monkeypatch, {"wtype", "xdotool"})
    client.revise_text("ab", "cd")
    # Every subprocess call in the revise path must go through wtype, not xdotool.
    tools = {c[0] for c in client._calls}
    assert tools == {"wtype"}
    joined = " ".join(" ".join(c) for c in client._calls)
    assert "BackSpace" in joined
    assert "cd" in joined


def test_type_text_wayland_missing_wtype_does_not_use_xdotool(client, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    # wtype unavailable; notify-send available so degrade can announce.
    _set_tools(client, monkeypatch, {"notify-send"})
    client.type_text("hello")
    tools = [c[0] for c in client._calls]
    # Never silently fall through to xdotool on Wayland.
    assert "xdotool" not in tools
    # Not a silent no-op: it attempted to notify.
    assert "notify-send" in tools
