"""A1: direct contract tests for POST /v1/audio/transcriptions.

The main OpenAI-compatible HTTP surface had zero direct coverage. The real
faster-whisper model is replaced with a fake so these run on any box (no GPU,
no model download); they pin the response shapes, the option plumbing
(word timestamps / verbose_json / VAD toggle), and temp-file cleanup.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as whisper_app


class _FakeWord:
    def __init__(self, word, start, end, prob):
        self.word = word
        self.start = start
        self.end = end
        self.probability = prob


class _FakeSeg:
    def __init__(self, text, start, end, words=None):
        self.text = text
        self.start = start
        self.end = end
        self.words = words
        self.avg_logprob = -0.12
        self.no_speech_prob = 0.03


class _FakeInfo:
    language = "en"
    duration = 1.234


class _FakeModel:
    """Records the kwargs the endpoint passes to transcribe()."""

    def __init__(self, captured: dict):
        self.captured = captured

    def transcribe(self, path, language, vad_filter, word_timestamps):
        self.captured.update(
            path=path, language=language,
            vad_filter=vad_filter, word_timestamps=word_timestamps,
        )
        words = None
        if word_timestamps:
            words = [_FakeWord("Watch", 0.0, 0.16, 0.98), _FakeWord(" out", 0.16, 0.40, 0.91)]
        return iter([_FakeSeg("Watch out", 0.0, 0.40, words=words)]), _FakeInfo()


@pytest.fixture
def captured():
    return {}


@pytest.fixture
def client(monkeypatch, captured):
    model = _FakeModel(captured)
    monkeypatch.setattr(whisper_app, "get_model", lambda *_a, **_k: model)
    return TestClient(whisper_app.app)


def _post(client, **fields):
    files = {"file": ("audio.wav", b"RIFFfake-wav-bytes", "audio/wav")}
    return client.post("/v1/audio/transcriptions", files=files, data=fields)


def test_default_returns_plain_text_and_vad_on(client, captured):
    r = _post(client)
    assert r.status_code == 200
    assert r.json() == {"text": "Watch out"}
    # Back-compat defaults: VAD on, no word timestamps.
    assert captured["vad_filter"] is True
    assert captured["word_timestamps"] is False


def test_vad_filter_false_disables_vad(client, captured):
    r = _post(client, vad_filter="false")
    assert r.status_code == 200
    assert captured["vad_filter"] is False


def test_verbose_json_returns_segments_and_words(client, captured):
    r = _post(client, response_format="verbose_json")
    assert r.status_code == 200
    body = r.json()
    assert captured["word_timestamps"] is True  # verbose_json implies words
    assert body["text"] == "Watch out"
    assert body["language"] == "en"
    assert body["duration"] == 1.234
    assert len(body["segments"]) == 1
    seg = body["segments"][0]
    assert seg["id"] == 0
    assert [w["word"] for w in seg["words"]] == ["Watch", " out"]
    assert body["words"][0]["probability"] == 0.98
    assert body["task"] == "transcribe"


def test_timestamp_granularities_word_triggers_words(client, captured):
    r = _post(client, timestamp_granularities="word")
    assert r.status_code == 200
    assert captured["word_timestamps"] is True
    assert "words" in r.json()


def test_word_timestamps_direct_flag(client, captured):
    r = _post(client, word_timestamps="true")
    assert r.status_code == 200
    assert captured["word_timestamps"] is True


def test_temp_file_is_cleaned_up(client, captured):
    r = _post(client)
    assert r.status_code == 200
    # The endpoint writes the upload to a temp file then unlinks it in finally.
    assert captured["path"]
    assert not os.path.exists(captured["path"]), "temp upload file was left behind"
