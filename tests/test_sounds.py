"""Audible start/stop cue helper — platform-guarded, non-blocking, never raises."""

from __future__ import annotations

import whisper_toggle.sounds as sounds


def test_play_start_stop_use_distinct_sounds(monkeypatch):
    calls = []
    monkeypatch.setattr(sounds, "_emit", lambda sound: calls.append(sound))
    sounds.play_start()
    sounds.play_stop()
    assert calls == [sounds.START_SOUND, sounds.STOP_SOUND]
    assert sounds.START_SOUND != sounds.STOP_SOUND


def test_emit_is_noop_off_windows(monkeypatch):
    played = []
    monkeypatch.setattr(sounds, "_play_sound", lambda s: played.append(s))
    monkeypatch.setattr(sounds.sys, "platform", "linux")
    sounds._emit(sounds.START_SOUND)
    assert played == []  # never touches the player off-Windows


def test_emit_calls_player_on_windows(monkeypatch):
    played = []
    monkeypatch.setattr(sounds.sys, "platform", "win32")
    monkeypatch.setattr(sounds, "_play_sound", lambda s: played.append(s))
    sounds._emit(sounds.START_SOUND)
    assert played == [sounds.START_SOUND]


def test_emit_swallows_player_errors(monkeypatch):
    monkeypatch.setattr(sounds.sys, "platform", "win32")

    def boom(_s):
        raise RuntimeError("no audio device")

    monkeypatch.setattr(sounds, "_play_sound", boom)
    sounds._emit(sounds.START_SOUND)  # must not raise
