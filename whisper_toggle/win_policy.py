"""Portable decision logic for the Windows tray (no Windows APIs imported).

Kept dependency-free so it is unit-testable headlessly on Linux/CI. The tray
app (``windows/tray_app.py``) imports these helpers and wires them to the real
Windows hooks.
"""

from __future__ import annotations

from whisper_toggle.controller import State

# States in which the engine is NOT usable, so we must relinquish the Win+H
# hook and let Windows Voice Typing behave normally.
_NOT_READY = frozenset({State.ERROR.value, State.STARTING.value})


def _state_value(state) -> str:
    """Normalize a ``State`` enum or raw string to its lowercase value."""
    if isinstance(state, State):
        return state.value
    return str(state).strip().lower()


def should_own_hotkey(state, api_healthy: bool) -> bool:
    """Return True when Whisper Toggle should OWN (swallow) the Win+H hotkey.

    Contract: the hook stays owned through the entire capture cycle
    (RECORDING and PROCESSING), not just IDLE, so the second Win+H press is
    still intercepted and routed to ``toggle()`` to STOP recording rather than
    launching Windows Voice Typing.

    Ownership is granted when the engine is healthy AND the state is not one of
    the not-ready states (ERROR / STARTING). This replaces the buggy
    ``state == IDLE and api_healthy`` gate that dropped ownership the moment
    recording began.
    """
    if not api_healthy:
        return False
    return _state_value(state) not in _NOT_READY
