"""W1 — Win+H hook-ownership policy (portable, no Windows APIs).

Regression: the old tray gate was ``state == IDLE and api_healthy`` which
dropped ownership the instant recording started, so the 2nd Win+H press hit
Windows Voice Typing instead of stopping the recording. The hook must stay
OWNED through RECORDING and PROCESSING while the engine is healthy.
"""

from __future__ import annotations

import pytest

from whisper_toggle.controller import State
from whisper_toggle.win_policy import should_own_hotkey


@pytest.mark.parametrize(
    "state,healthy,expected",
    [
        # Healthy engine: own through the whole record/process cycle.
        (State.IDLE, True, True),
        (State.RECORDING, True, True),
        (State.PROCESSING, True, True),
        # Not-ready states never own, even when healthy.
        (State.ERROR, True, False),
        (State.STARTING, True, False),
        # Unhealthy engine: never own regardless of state.
        (State.IDLE, False, False),
        (State.RECORDING, False, False),
        (State.PROCESSING, False, False),
        (State.ERROR, False, False),
        (State.STARTING, False, False),
    ],
)
def test_should_own_hotkey_truth_table(state, healthy, expected):
    assert should_own_hotkey(state, healthy) is expected


def test_recording_keeps_ownership_regression():
    """The core bug: RECORDING must keep ownership True.

    The old gate (``state == IDLE``) would have returned False here, releasing
    the Win+H hook mid-recording and letting the stop press open Voice Typing.
    """
    old_gate = (State.RECORDING == State.IDLE)  # what the buggy code computed
    assert old_gate is False
    assert should_own_hotkey(State.RECORDING, True) is True
    assert should_own_hotkey(State.PROCESSING, True) is True


def test_accepts_str_state_values():
    """State is a str-enum; the policy also tolerates raw string states."""
    assert should_own_hotkey("recording", True) is True
    assert should_own_hotkey("error", True) is False
    assert should_own_hotkey("starting", True) is False
    assert should_own_hotkey("idle", False) is False
