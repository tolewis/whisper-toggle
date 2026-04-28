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
