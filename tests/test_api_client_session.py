"""A2: LiveStreamSession coverage + a fail-fast bug fix (red-green).

These exercise ``whisper_toggle.api_client.LiveStreamSession`` against an
in-process mock websocket server (same spirit as ``test_stream_client_errors``,
but driving the GUI-side client rather than the linux CLI client).

Bug under test (found in review):
  When the server sends an ``{"type":"error"}`` frame (or closes the socket
  before a ``final`` frame), ``_recv_loop`` set ``_failed`` but never woke the
  send loop, so the worker thread blocked on ``queue.get()`` forever. The
  session leaked a thread + connection and any waiter could stall for up to the
  90s final-timeout. The fail-fast tests below assert the worker thread
  terminates promptly; they are RED before the api_client fix and GREEN after.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whisper_toggle.api_client import LiveStreamSession


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MockWSServer:
    """Run a ``websockets`` server on its own thread + event loop."""

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


# ─── Handlers ──────────────────────────────────────────────────────────────
async def happy_handler(ws):
    async for msg in ws:
        if isinstance(msg, (bytes, bytearray)):
            await ws.send(json.dumps({"type": "partial", "text": "hello"}))
        else:
            ctrl = json.loads(msg)
            if ctrl.get("type") == "end":
                await ws.send(json.dumps({"type": "confirmed", "text": "hello"}))
                await ws.send(json.dumps({"type": "final", "text": "hello world"}))
                return


async def error_handler(ws):
    async for msg in ws:
        if isinstance(msg, (bytes, bytearray)):
            await ws.send(json.dumps({"type": "error", "error": "boom"}))
            await ws.close()
            return


async def early_close_handler(ws):
    async for msg in ws:
        if isinstance(msg, (bytes, bytearray)):
            await ws.close()
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


# ─── Characterization: happy path ────────────────────────────────────────────
def test_live_stream_session_happy_path():
    sink = _Sink()
    with MockWSServer(happy_handler) as server:
        session = sink.make(server.url)
        assert session.start() is True
        session.send_pcm(b"\x00\x00" * 1600)
        final = session.end()

    assert final == "hello world"
    assert session.final_text == "hello world"
    assert session.failed is False
    assert sink.partials == ["hello"]
    assert sink.confirmeds == ["hello"]
    assert sink.finals == ["hello world"]
    session._thread.join(timeout=5)
    assert session._thread.is_alive() is False


# ─── Bug fix (red-green): server error frame must fail fast ───────────────────
def test_server_error_frame_fails_fast_and_terminates_thread():
    sink = _Sink()
    with MockWSServer(error_handler) as server:
        session = sink.make(server.url)
        assert session.start() is True
        session.send_pcm(b"\x00\x00" * 1600)

        deadline = time.time() + 5
        while time.time() < deadline and not session.failed:
            time.sleep(0.02)
        assert session.failed is True, "server error was not surfaced"

        t0 = time.time()
        session.end()
        assert time.time() - t0 < 5, "end() blocked instead of failing fast"

        # The worker thread must actually unwind — before the fix it stays
        # blocked on queue.get() forever (leaked thread + connection).
        session._thread.join(timeout=5)
        assert session._thread.is_alive() is False, "stream worker thread leaked"
    assert sink.errors, "on_error was never called"


def test_early_close_without_final_fails_fast_and_terminates_thread():
    sink = _Sink()
    with MockWSServer(early_close_handler) as server:
        session = sink.make(server.url)
        assert session.start() is True
        session.send_pcm(b"\x00\x00" * 1600)

        # Failure must surface on its own (without the caller having to poke
        # end()); before the fix an early close left _failed False.
        deadline = time.time() + 5
        while time.time() < deadline and not session.failed:
            time.sleep(0.02)
        assert session.failed is True, "early close was not surfaced as failure"

        session._thread.join(timeout=5)
        assert session._thread.is_alive() is False, "stream worker thread leaked"
