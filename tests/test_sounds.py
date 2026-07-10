"""Audible start/stop cue helper — platform-guarded, non-blocking, never raises."""

from __future__ import annotations

import whisper_toggle.sounds as sounds


def test_play_start_stop_use_distinct_aliases(monkeypatch):
    calls = []
    monkeypatch.setattr(sounds, "_emit", lambda alias: calls.append(alias))
    sounds.play_start()
    sounds.play_stop()
    assert calls == [sounds.START_SOUND, sounds.STOP_SOUND]
    assert sounds.START_SOUND != sounds.STOP_SOUND


def test_emit_is_noop_off_windows(monkeypatch):
    played = []
    monkeypatch.setattr(sounds, "_play_alias", lambda a: played.append(a))
    monkeypatch.setattr(sounds.sys, "platform", "linux")
    sounds._emit("SystemAsterisk")
    assert played == []  # never touches the player off-Windows


def test_emit_calls_player_on_windows(monkeypatch):
    played = []
    monkeypatch.setattr(sounds.sys, "platform", "win32")
    monkeypatch.setattr(sounds, "_play_alias", lambda a: played.append(a))
    sounds._emit("SystemAsterisk")
    assert played == ["SystemAsterisk"]


def test_emit_swallows_player_errors(monkeypatch):
    monkeypatch.setattr(sounds.sys, "platform", "win32")

    def boom(_a):
        raise RuntimeError("no audio device")

    monkeypatch.setattr(sounds, "_play_alias", boom)
    sounds._emit("SystemAsterisk")  # must not raise
