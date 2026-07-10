"""A2: LocalApiClient HTTP + one-shot streaming coverage (characterization).

Spins the real ``app.app`` on a uvicorn server in a background thread (so the
synchronous ``requests`` / ``asyncio.run`` client can drive it) with the heavy
faster-whisper model and streaming processor swapped for fakes, so these run on
any box with no GPU and no model download. Mirrors the live-server pattern in
``tests/test_stream_endpoint.py`` but exercises the GUI-side client end-to-end.
"""

from __future__ import annotations

import io
import socket
import sys
import threading
import time
import wave
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as whisper_app
from whisper_toggle.api_client import LocalApiClient


class _FakeSeg:
    def __init__(self, text: str):
        self.text = text
        self.start = 0.0
        self.end = 0.4
        self.words = None
        self.avg_logprob = -0.1
        self.no_speech_prob = 0.02


class _FakeInfo:
    language = "en"
    duration = 0.4


class _FakeBatchModel:
    def transcribe(self, path, language, vad_filter, word_timestamps):
        return iter([_FakeSeg("canned batch text")]), _FakeInfo()


class _FakeStreamProcessor:
    def __init__(self):
        self.calls = 0

    def insert_pcm(self, pcm: bytes) -> float:
        return len(pcm) / 2 / 16000

    def process(self):
        self.calls += 1
        if self.calls == 1:
            return None, {"type": "partial", "text": "canned", "start_t": 0.0, "end_t": 0.4}
        if self.calls == 2:
            return {"type": "confirmed", "text": "canned", "start_t": 0.0, "end_t": 0.4}, None
        return None, None

    def finish(self) -> str:
        return "canned stream text"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _UvicornThread:
    def __init__(self, app, port: int):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "_UvicornThread":
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not self.server.started:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("uvicorn test server did not start")
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def live_client(monkeypatch):
    monkeypatch.setattr(whisper_app, "get_model", lambda *a, **k: _FakeBatchModel())
    monkeypatch.setattr(
        whisper_app,
        "create_stream_processor",
        lambda model_name, device, compute_type, language: _FakeStreamProcessor(),
    )
    port = _free_port()
    with _UvicornThread(whisper_app.app, port):
        yield LocalApiClient(
            base_url=f"http://127.0.0.1:{port}",
            stream_url=f"ws://127.0.0.1:{port}/v1/audio/stream",
            model="fake",
            language="en",
        )


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


def test_is_healthy_true_when_server_up(live_client):
    assert live_client.is_healthy() is True


def test_is_healthy_false_when_server_down():
    # Nothing is listening on port 1 -> connection refused -> False, not raise.
    assert LocalApiClient(base_url="http://127.0.0.1:1").is_healthy() is False


def test_runtime_returns_contract(live_client):
    rt = live_client.runtime()
    assert rt.get("ok") is True
    assert rt.get("version", "").startswith("2.")
    assert "device" in rt
    assert "model" in rt
    assert "backend" in rt
    assert isinstance(rt.get("streaming"), bool)


def test_runtime_empty_dict_when_server_down():
    assert LocalApiClient(base_url="http://127.0.0.1:1").runtime() == {}


def test_batch_returns_transcribed_text(live_client):
    assert live_client.batch(_wav_bytes()) == "canned batch text"


def test_oneshot_stream_delivers_partial_confirmed_final_in_order(live_client):
    events: list[tuple[str, str]] = []
    live_client.stream(
        b"\x00\x00" * 4096,
        on_partial=lambda t: events.append(("partial", t)),
        on_confirmed=lambda t: events.append(("confirmed", t)),
        on_final=lambda t: events.append(("final", t)),
    )
    kinds = [k for k, _ in events]
    assert kinds[0] == "partial"
    assert "confirmed" in kinds
    assert kinds[-1] == "final"
    assert events[-1][1] == "canned stream text"
