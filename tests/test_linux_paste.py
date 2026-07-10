"""L1 — capture/paste/type must branch on $WAYLAND_DISPLAY.

On native Wayland the X11 tools (xdotool/xprop/xclip) silently no-op, yet the
docs advertise Wayland support. The script must use wtype + wl-copy/wl-paste
under Wayland and xdotool/xclip under X11, and must degrade with a notify-send
message (never a silent no-op) when a required Wayland tool is missing.

These tests source dictate-toggle.sh (which must be import-safe, i.e. it only
runs main() when executed directly) and drive the individual helper functions
with fake tools on PATH.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux desktop tooling (bash/xdotool/wtype/wl-copy)",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "linux" / "dictate-toggle.sh"

FAKE_TOOL = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$TOOL_LOG"
# Clipboard writers read from stdin; drain it so pipes don't block.
case "$(basename "$0")" in
    wl-copy|xclip) cat >/dev/null 2>&1 || true ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    tool_log = tmp_path / "tools.log"

    def make(names):
        for name in names:
            p = fakebin / name
            p.write_text(FAKE_TOOL)
            p.chmod(0o755)

    def run(call: str, *, wayland: bool, tools, stdin: str = ""):
        make(tools)
        environ = dict(os.environ)
        environ["PATH"] = f"{fakebin}:{environ['PATH']}"
        environ["TOOL_LOG"] = str(tool_log)
        environ["WHISPER_WORK_DIR"] = str(tmp_path / "work")
        if wayland:
            environ["WAYLAND_DISPLAY"] = "wayland-0"
        else:
            environ.pop("WAYLAND_DISPLAY", None)
        script = f'source "{SCRIPT}"; set +e; {call}'
        result = subprocess.run(
            ["bash", "-c", script, "_"],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=15,
            env=environ,
        )
        log = tool_log.read_text() if tool_log.exists() else ""
        return result, log

    return run


# --- clipboard ---------------------------------------------------------------

def test_clipboard_wayland_uses_wl_copy(env):
    result, log = env(
        "copy_to_clipboard",
        wayland=True,
        tools=["wl-copy", "xclip", "notify-send"],
        stdin="hello world",
    )
    assert "wl-copy" in log
    assert "xclip" not in log


def test_clipboard_x11_uses_xclip(env):
    result, log = env(
        "copy_to_clipboard",
        wayland=False,
        tools=["wl-copy", "xclip", "notify-send"],
        stdin="hello world",
    )
    assert "xclip -selection clipboard" in log
    assert "wl-copy" not in log


# --- paste -------------------------------------------------------------------

def test_paste_wayland_uses_wtype(env):
    result, log = env(
        "paste_text",
        wayland=True,
        tools=["wtype", "xdotool", "xprop", "notify-send"],
    )
    assert "wtype" in log
    assert "xdotool key" not in log


def test_paste_x11_uses_xdotool(env):
    result, log = env(
        "paste_text",
        wayland=False,
        tools=["wtype", "xdotool", "xprop", "notify-send"],
    )
    assert "xdotool key" in log
    assert "wtype" not in log


# --- typing (partial path) ---------------------------------------------------

def test_type_wayland_uses_wtype(env):
    result, log = env(
        'type_text "hi there"',
        wayland=True,
        tools=["wtype", "xdotool", "notify-send"],
    )
    assert "wtype" in log
    assert "xdotool type" not in log


def test_type_x11_uses_xdotool(env):
    result, log = env(
        'type_text "hi there"',
        wayland=False,
        tools=["wtype", "xdotool", "notify-send"],
    )
    assert "xdotool type" in log
    assert "wtype" not in log


# --- graceful degrade (no silent no-op) --------------------------------------

def test_wayland_missing_wtype_notifies(env):
    # wtype absent under Wayland: must notify-send, not silently succeed.
    result, log = env(
        "paste_text",
        wayland=True,
        tools=["xdotool", "xprop", "notify-send"],
    )
    assert "notify-send" in log


def test_wayland_missing_wl_copy_notifies(env):
    result, log = env(
        "copy_to_clipboard",
        wayland=True,
        tools=["xclip", "notify-send"],
        stdin="hello",
    )
    assert "notify-send" in log
