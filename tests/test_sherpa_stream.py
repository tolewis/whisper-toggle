"""Sherpa streaming processor and stream-engine factory tests."""

from __future__ import annotations

import numpy as np

import app as whisper_app
from whisper_toggle.sherpa_stream import SherpaStreamProcessor


class FakeResult:
    def __init__(self, text: str):
        self.text = text


class FakeStream:
    def __init__(self):
        self.accepted = []
        self.finished = False

    def accept_waveform(self, sample_rate: int, samples):
        self.accepted.append((sample_rate, np.asarray(samples).copy()))

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    def __init__(self):
        self.stream = FakeStream()
        self.text = ""
        self.endpoint = False
        self.decode_calls = 0
        self.reset_calls = 0
        self.result_as_string = False

    def create_stream(self):
        return self.stream

    def is_ready(self, stream):
        return self.decode_calls == 0

    def decode_stream(self, stream):
        self.decode_calls += 1

    def get_result(self, stream):
        if self.result_as_string:
            return self.text
        return FakeResult(self.text)

    def is_endpoint(self, stream):
        return self.endpoint

    def reset(self, stream):
        self.reset_calls += 1
        self.endpoint = False


def test_sherpa_insert_pcm_feeds_16k_float_audio():
    recognizer = FakeRecognizer()
    processor = SherpaStreamProcessor("model", "cpu", "int8", "en", recognizer=recognizer)
    seconds = processor.insert_pcm((np.array([0, 32767, -32768], dtype=np.int16)).tobytes())
    assert seconds == 3 / 16000
    sample_rate, samples = recognizer.stream.accepted[0]
    assert sample_rate == 16000
    np.testing.assert_allclose(samples, np.array([0, 32767, -32768]) / 32768.0)


def test_sherpa_process_returns_partial_then_confirmed_on_endpoint():
    recognizer = FakeRecognizer()
    processor = SherpaStreamProcessor("model", "cpu", "int8", "en", recognizer=recognizer)
    recognizer.text = "hello"
    confirmed, partial = processor.process()
    assert confirmed is None
    assert partial == {"type": "partial", "text": "hello"}

    recognizer.text = "hello world"
    recognizer.endpoint = True
    confirmed, partial = processor.process()
    assert confirmed == {"type": "confirmed", "text": "hello world"}
    assert partial is None
    assert recognizer.reset_calls == 1


def test_sherpa_process_accepts_real_string_result_shape():
    recognizer = FakeRecognizer()
    recognizer.result_as_string = True
    processor = SherpaStreamProcessor("model", "cpu", "int8", "en", recognizer=recognizer)

    recognizer.text = "hello"

    assert processor.process() == (None, {"type": "partial", "text": "hello"})


def test_sherpa_finish_flushes_and_returns_accumulated_text():
    recognizer = FakeRecognizer()
    processor = SherpaStreamProcessor("model", "cpu", "int8", "en", recognizer=recognizer)
    recognizer.text = "hello"
    recognizer.endpoint = True
    processor.process()

    recognizer.text = "tail"
    assert processor.finish() == "hello tail"
    assert recognizer.stream.finished is True


def test_create_stream_processor_defaults_to_sherpa(monkeypatch):
    class FakeSherpa:
        pass

    class FakeWhisperStreaming:
        pass

    monkeypatch.delenv("WHISPER_STREAM_ENGINE", raising=False)
    monkeypatch.setattr(whisper_app, "SherpaStreamProcessor", lambda *args: FakeSherpa())
    monkeypatch.setattr(
        whisper_app,
        "StreamingASRProcessor",
        lambda *args: FakeWhisperStreaming(),
    )
    assert isinstance(whisper_app.create_stream_processor("m", "cpu", "int8", "en"), FakeSherpa)


def test_create_stream_processor_uses_whisper_streaming_fallback(monkeypatch):
    class FakeSherpa:
        pass

    class FakeWhisperStreaming:
        pass

    monkeypatch.setenv("WHISPER_STREAM_ENGINE", "whisper_streaming")
    monkeypatch.setattr(whisper_app, "SherpaStreamProcessor", lambda *args: FakeSherpa())
    monkeypatch.setattr(
        whisper_app,
        "StreamingASRProcessor",
        lambda *args: FakeWhisperStreaming(),
    )
    assert isinstance(
        whisper_app.create_stream_processor("m", "cpu", "int8", "en"),
        FakeWhisperStreaming,
    )


def test_resolve_sherpa_model_dir_env_override():
    from whisper_toggle.sherpa_stream import resolve_sherpa_model_dir
    assert str(resolve_sherpa_model_dir(env=r"C:\some\model")) == r"C:\some\model"


def test_resolve_sherpa_model_dir_auto_discovers_encoder(tmp_path):
    from whisper_toggle.sherpa_stream import resolve_sherpa_model_dir
    models = tmp_path / "models"
    good = models / "sherpa-en"
    good.mkdir(parents=True)
    (good / "encoder.onnx").write_bytes(b"x")
    assert resolve_sherpa_model_dir(env="", models_root=models) == good


def test_resolve_sherpa_model_dir_raises_when_missing(tmp_path):
    import pytest
    from whisper_toggle.sherpa_stream import resolve_sherpa_model_dir
    with pytest.raises(RuntimeError):
        resolve_sherpa_model_dir(env="", models_root=tmp_path / "nope")
