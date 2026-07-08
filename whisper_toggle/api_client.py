"""Local Whisper API client — health, batch, and streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Callable, Optional

import requests

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore

log = logging.getLogger("whisper-toggle.api")


class LocalApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8788",
        stream_url: str = "ws://127.0.0.1:8788/v1/audio/stream",
        model: str = "small.en",
        language: str = "en",
        open_timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.stream_url = stream_url
        self.model = model
        self.language = language
        self.open_timeout = open_timeout

    def is_healthy(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def runtime(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/v1/runtime", timeout=2)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    def batch(self, pcm_wav_bytes: bytes) -> str:
        """POST a WAV file's bytes (caller provides full WAV)."""
        r = requests.post(
            f"{self.base_url}/v1/audio/transcriptions",
            files={"file": ("audio.wav", pcm_wav_bytes, "audio/wav")},
            data={"model": self.model, "language": self.language},
            timeout=120,
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()

    def stream(
        self,
        pcm: bytes,
        on_partial: Callable[[str], None],
        on_confirmed: Callable[[str], None],
        on_final: Callable[[str], None],
    ) -> None:
        """Send a full PCM buffer as one stream session (int16 mono 16k)."""
        if websockets is None:
            raise RuntimeError("websockets package required for streaming")

        async def _run():
            url = self.stream_url
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}model={self.model}&language={self.language}"
            async with websockets.connect(
                url,
                open_timeout=self.open_timeout,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            ) as ws:
                chunk = 8192
                for i in range(0, len(pcm), chunk):
                    await ws.send(pcm[i : i + chunk])
                    await asyncio.sleep(0)
                await ws.send(json.dumps({"type": "end"}))
                async for message in ws:
                    if isinstance(message, bytes):
                        continue
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    kind = payload.get("type")
                    text = (payload.get("text") or "").strip()
                    if kind == "partial":
                        on_partial(text)
                    elif kind == "confirmed":
                        on_confirmed(text)
                    elif kind == "final":
                        on_final(text)
                        return

        asyncio.run(_run())


class LiveStreamSession:
    """Open a streaming WS and push PCM while the user is speaking."""

    def __init__(
        self,
        stream_url: str,
        model: str,
        language: str,
        on_partial: Callable[[str], None],
        on_confirmed: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Optional[Callable[[str], None]] = None,
        open_timeout: float = 5.0,
    ):
        if websockets is None:
            raise RuntimeError("websockets package required for streaming")
        self.stream_url = stream_url
        self.model = model
        self.language = language
        self.on_partial = on_partial
        self.on_confirmed = on_confirmed
        self.on_final = on_final
        self.on_error = on_error or (lambda _m: None)
        self.open_timeout = open_timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._queue: Optional[asyncio.Queue] = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._failed = False
        self._final_text = ""

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def final_text(self) -> str:
        return self._final_text

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        ok = self._ready.wait(timeout=self.open_timeout + 3)
        return bool(ok) and not self._failed and not self._done.is_set()

    def send_pcm(self, data: bytes) -> None:
        if not data or self._failed or self._loop is None or self._queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except Exception:
            pass

    def end(self) -> str:
        """Signal end-of-audio; wait for final. Returns final text if any."""
        if self._failed:
            self._done.set()
            return self._final_text
        if self._loop is not None and self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, {"type": "end"})
            except Exception as exc:  # noqa: BLE001
                log.warning("end queue failed: %s", exc)
                self._failed = True
                self._done.set()
        self._done.wait(timeout=90)
        return self._final_text

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            self.on_error(str(exc))
            self._ready.set()
            self._done.set()

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        url = self.stream_url
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}model={self.model}&language={self.language}"
        try:
            async with websockets.connect(
                url,
                open_timeout=self.open_timeout,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            ) as ws:
                self._ready.set()
                recv_task = asyncio.create_task(self._recv_loop(ws))
                try:
                    while True:
                        item = await self._queue.get()
                        if isinstance(item, dict):
                            await ws.send(json.dumps(item))
                            if item.get("type") == "end":
                                try:
                                    await asyncio.wait_for(recv_task, timeout=90)
                                except asyncio.TimeoutError:
                                    self._failed = True
                                    self.on_error("stream final timeout")
                                self._done.set()
                                return
                        else:
                            await ws.send(item)
                finally:
                    if not recv_task.done():
                        recv_task.cancel()
                        try:
                            await recv_task
                        except Exception:
                            pass
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            self.on_error(str(exc))
            self._ready.set()
            self._done.set()

    async def _recv_loop(self, ws) -> None:
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")
                text = (payload.get("text") or "").strip()
                if kind == "partial":
                    self.on_partial(text)
                elif kind == "confirmed":
                    self.on_confirmed(text)
                elif kind == "final":
                    self._final_text = text
                    self.on_final(text)
                    return
                elif kind == "error":
                    self._failed = True
                    self.on_error(payload.get("error") or "stream error")
                    return
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            self.on_error(str(exc))
