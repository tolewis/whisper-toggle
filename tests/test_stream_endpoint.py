from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import sys

import httpx
import pytest
import uvicorn
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as whisper_app


class FakeStreamProcessor:
    def __init__(self):
        self.calls = 0
        self.bytes_seen = 0

    def insert_pcm(self, pcm: bytes) -> float:
        self.bytes_seen += len(pcm)
        return len(pcm) / 2 / 16000

    def process(self):
        self.calls += 1
        if self.calls == 1:
            return None, {"type": "partial", "text": "hello", "start_t": 0.0, "end_t": 0.4}
        if self.calls == 2:
            return (
                {"type": "confirmed", "text": "hello", "start_t": 0.0, "end_t": 0.4},
                {"type": "partial", "text": "world", "start_t": 0.4, "end_t": 0.8},
            )
        return None, None

    def finish(self) -> str:
        return "hello world"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def live_server(monkeypatch):
    monkeypatch.setattr(
        whisper_app,
        "create_stream_processor",
        lambda model_name, device, compute_type, language: FakeStreamProcessor(),
    )
    port = free_port()
    config = uvicorn.Config(
        whisper_app.app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        log_level="error",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    async with httpx.AsyncClient() as client:
        for _ in range(50):
            try:
                response = await client.get(f"http://127.0.0.1:{port}/health")
                if response.status_code == 200:
                    break
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("test server did not start")

    yield f"ws://127.0.0.1:{port}/v1/audio/stream"

    server.should_exit = True
    await task


class _FakeWS:
    """Minimal stand-in for a Starlette WebSocket for send-path unit tests."""

    def __init__(self, application_state, send_exc=None):
        self.application_state = application_state
        self.send_exc = send_exc
        self.sent = []

    async def send_text(self, text):
        if self.send_exc is not None:
            raise self.send_exc
        self.sent.append(text)


@pytest.mark.anyio
async def test_send_json_swallows_send_after_close_runtimeerror():
    """Reproduce the production race: application_state still reads CONNECTED,
    but the transport closed (client keepalive ping timeout), so send_text
    raises the Starlette 'Unexpected ASGI message ... after sending
    websocket.close' RuntimeError. _send_json must treat this as a disconnect
    (return False), never propagate it."""
    exc = RuntimeError(
        "Unexpected ASGI message 'websocket.send', after sending "
        "'websocket.close' or response already completed."
    )
    ws = _FakeWS(whisper_app.WebSocketState.CONNECTED, send_exc=exc)
    ok = await whisper_app._send_json(ws, {"type": "partial", "text": "x"})
    assert ok is False


@pytest.mark.anyio
async def test_send_json_returns_false_when_not_connected():
    ws = _FakeWS(whisper_app.WebSocketState.DISCONNECTED)
    ok = await whisper_app._send_json(ws, {"type": "partial", "text": "x"})
    assert ok is False
    assert ws.sent == []


@pytest.mark.anyio
async def test_send_json_returns_true_on_success():
    ws = _FakeWS(whisper_app.WebSocketState.CONNECTED)
    ok = await whisper_app._send_json(ws, {"type": "partial", "text": "hi"})
    assert ok is True
    assert ws.sent and "hi" in ws.sent[0]


@pytest.mark.anyio
async def test_stream_endpoint_survives_client_disconnect_midstream(live_server, caplog):
    """Client goes away mid-stream: the endpoint must terminate cleanly with no
    unhandled RuntimeError logged, and the server must keep serving."""
    import logging

    caplog.set_level(logging.ERROR)
    async with websockets.connect(live_server) as websocket:
        await websocket.send((b"\x00\x00" * 1600))
        await asyncio.wait_for(websocket.recv(), timeout=2)
        # Abruptly drop the connection without an "end" control frame.
        await websocket.close()

    await asyncio.sleep(0.3)

    # The server survived and still serves a fresh session end-to-end.
    async with websockets.connect(live_server) as websocket:
        await websocket.send((b"\x00\x00" * 1600))
        await asyncio.wait_for(websocket.recv(), timeout=2)
        await websocket.send('{"type":"end"}')
        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=2)
            if '"type":"final"' in message.replace(" ", ""):
                break

    assert "Unexpected ASGI message" not in caplog.text
    assert "stream endpoint failed" not in caplog.text


@pytest.mark.anyio
async def test_stream_endpoint_emits_partial_confirmed_final_in_order(live_server):
    async with websockets.connect(live_server) as websocket:
        await websocket.send((b"\x00\x00" * 1600))
        first = await asyncio.wait_for(websocket.recv(), timeout=2)
        assert '"type":"partial"' in first.replace(" ", "")

        await asyncio.sleep(0.3)
        await websocket.send((b"\x01\x00" * 1600))
        messages = [await asyncio.wait_for(websocket.recv(), timeout=2)]
        messages.append(await asyncio.wait_for(websocket.recv(), timeout=2))
        assert any('"type":"confirmed"' in item.replace(" ", "") for item in messages)

        await websocket.send('{"type":"end"}')
        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=2)
            if '"type":"final"' in message.replace(" ", ""):
                assert "hello world" in message
                break
