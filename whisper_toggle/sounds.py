"""Best-effort, non-blocking audible cues for dictation start/stop.

Dictation is batch by default: nothing is typed until the user stops, so there
is no visual signal that recording began. A short, calm sound on start and a
different one on stop tells the user the shortcut registered. Async + guarded so
it never blocks or breaks dictation.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("whisper-toggle.sounds")

# Gentle Windows Media chimes instead of the abrupt default ding. Playing the
# full WAV with SND_ASYNC (rather than a short system alias) avoids the clipped
# cut-off and sounds calmer.
_MEDIA = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media")
START_SOUND = os.path.join(_MEDIA, "chimes.wav")   # soft ascending chime
STOP_SOUND = os.path.join(_MEDIA, "chord.wav")     # soft descending chord


def _play_sound(sound: str) -> None:  # pragma: no cover - Windows-only path
    import winsound

    if os.path.exists(sound):
        winsound.PlaySound(
            sound, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
    else:  # fall back to a system alias if the media file is missing
        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)


def _emit(sound: str) -> None:
    if not sys.platform.startswith("win"):
        return  # best-effort no-op elsewhere
    try:
        _play_sound(sound)
    except Exception:
        log.debug("sound cue failed", exc_info=True)


def play_start() -> None:
    """Calm chime when recording starts."""
    _emit(START_SOUND)


def play_stop() -> None:
    """Different calm chime when recording stops."""
    _emit(STOP_SOUND)
