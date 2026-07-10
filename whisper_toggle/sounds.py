"""Best-effort, non-blocking audible cues for dictation start/stop.

Dictation is batch by default: nothing is typed until the user stops, so there
is no visual signal that recording began. A short system sound on start and a
different one on stop (like the Windows voice-recorder ding) tells the user the
shortcut registered. Async + guarded so it never blocks or breaks dictation.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("whisper-toggle.sounds")

# Distinct native system sounds so start and stop are audibly different.
START_SOUND = "SystemAsterisk"
STOP_SOUND = "SystemExclamation"


def _play_alias(alias: str) -> None:  # pragma: no cover - Windows-only path
    import winsound

    winsound.PlaySound(
        alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    )


def _emit(alias: str) -> None:
    if not sys.platform.startswith("win"):
        return  # best-effort no-op elsewhere (could wire paplay/afplay later)
    try:
        _play_alias(alias)
    except Exception:
        log.debug("sound cue failed", exc_info=True)


def play_start() -> None:
    """Ding when recording starts."""
    _emit(START_SOUND)


def play_stop() -> None:
    """Different ding when recording stops."""
    _emit(STOP_SOUND)
