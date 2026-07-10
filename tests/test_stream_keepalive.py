"""S2 — streaming keepalive survives long recordings.

Two concerns:
  1. A multi-second processing/finish gap on the server must not trip the
     client's keepalive and close the socket with 1011. The client therefore
     uses a generous ``ping_timeout`` so a slow ``finish()`` does not look like
     a dead peer. This is asserted by capturing the kwargs the client passes to
     ``websockets.connect``.
  2. If the server *does* close mid-stream (e.g. it sends 1011
     "keepalive ping timeout"), the client must surface a clean, fail-fast
     failure and tear its worker thread down rather than hanging or crashing.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whisper_toggle import api_client
from whisper_toggle.api_client import LiveStreamSession


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MockWSServer:
    def __init__(self, handler):
        self._handler = handler
        self.port = _free_port()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._stop = self.loop.create_future()

        async def _main():
            async with websockets.serve(self._handler, "127.0.0.1", self.port):
                self._ready.set()
                await self._stop

        try:
            self.loop.run_until_complete(_main())
        finally:
            self.loop.close()

    def __enter__(self) -> "MockWSServer":
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("mock ws server did not start")
        return self

    def __exit__(self, *exc) -> None:
        if self.loop is not None and self._stop is not None:
            self.loop.call_soon_threadsafe(
                lambda: self._stop.done() or self._stop.set_result(None)
            )
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/audio/stream"


async def slow_finish_handler(ws):
    """Simulate a long transcription: pause before the final frame."""
    async for msg in ws:
        if isinstance(msg, (bytes, bytearray)):
            await ws.send(json.dumps({"type": "partial", "text": "hi"}))
        else:
            ctrl = json.loads(msg)
            if ctrl.get("type") == "end":
                await asyncio.sleep(1.0)  # long finish()
                await ws.send(json.dumps({"type": "final", "text": "hi there"}))
                return


async def keepalive_1011_handler(ws):
    """Server drops the connection mid-stream with a 1011 close (the exact
    'keepalive ping timeout' scenario seen in production logs)."""
    async for msg in ws:
        if isinstance(msg, (bytes, bytearray)):
            await ws.close(code=1011, reason="keepalive ping timeout")
            return


class _Sink:
    def __init__(self):
        self.partials: list[str] = []
        self.confirmeds: list[str] = []
        self.finals: list[str] = []
        self.errors: list[str] = []

    def make(self, url: str) -> LiveStreamSession:
        return LiveStreamSession(
            stream_url=url,
            model="fake",
            language="en",
            on_partial=self.partials.append,
            on_confirmed=self.confirmeds.append,
            on_final=self.finals.append,
            on_error=self.errors.append,
            open_timeout=3.0,
        )


def test_client_ping_timeout_is_generous_for_long_finish():
    """The client must not ping-timeout a peer that is busy transcribing for a
    few seconds. Guards the ping_timeout regression."""
    captured: dict = {}
    real_connect = websockets.connect

    def spy(url, **kwargs):
        captured.update(kwargs)
        return real_connect(url, **kwargs)

    sink = _Sink()
    with MockWSServer(slow_finish_handler) as server:
        with patch.object(api_client.websockets, "connect", spy):
            session = sink.make(server.url)
            assert session.start() is True
            session.send_pcm(b"\x00\x00" * 1600)
            final = session.end()

    assert final == "hi there"
    assert session.failed is False
    # A slow (1s) finish must not have tripped the keepalive.
    assert captured.get("ping_timeout") is not None
    assert captured["ping_timeout"] >= 30, captured
    assert captured.get("ping_interval") is not None


def test_server_1011_close_fails_fast_and_terminates_thread():
    """A server 1011 (keepalive ping timeout) mid-stream close surfaces a clean
    failure and the worker thread unwinds — no hang, no crash."""
    sink = _Sink()
    with MockWSServer(keepalive_1011_handler) as server:
        session = sink.make(server.url)
        assert session.start() is True
        session.send_pcm(b"\x00\x00" * 1600)

        deadline = time.time() + 5
        while time.time() < deadline and not session.failed:
            time.sleep(0.02)
        assert session.failed is True, "server 1011 close was not surfaced"

        t0 = time.time()
        session.end()
        assert time.time() - t0 < 5, "end() blocked instead of failing fast"

        session._thread.join(timeout=5)
        assert session._thread.is_alive() is False, "stream worker thread leaked"
    assert sink.errors, "on_error was never called on 1011"
