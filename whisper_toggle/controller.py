"""Push-to-talk controller: idle ⇄ recording → processing → idle/error."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Protocol


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    STARTING = "starting"
    ERROR = "error"


class AudioPort(Protocol):
    def start(self) -> None: ...
    def stop(self) -> bytes: ...


class ApiPort(Protocol):
    def is_healthy(self) -> bool: ...
    def stream(
        self,
        pcm: bytes,
        on_partial: Callable[[str], None],
        on_confirmed: Callable[[str], None],
        on_final: Callable[[str], None],
    ) -> None: ...
    def batch(self, pcm: bytes) -> str: ...


class TyperPort(Protocol):
    def set_live_text(self, confirmed: str, partial: str) -> None: ...
    def commit_final(self, text: str) -> None: ...
    def clear_session(self) -> None: ...


@dataclass
class ControllerConfig:
    streaming: bool = True
    min_audio_bytes: int = 1000  # ~30ms of int16 mono is tiny; 1000B ≈ 31ms
    partial_debounce_ms: int = 400
    hardware_catchup_ms: int = 250


class Controller:
    def __init__(
        self,
        api: ApiPort,
        audio: AudioPort,
        typer: TyperPort,
        config: Optional[ControllerConfig] = None,
        on_state: Optional[Callable[[State, str], None]] = None,
    ):
        self.api = api
        self.audio = audio
        self.typer = typer
        self.config = config or ControllerConfig()
        self.on_state = on_state or (lambda _s, _m: None)
        self.state = State.IDLE
        self._lock = threading.Lock()
        self._confirmed = ""
        self._partial = ""
        self._partial_timer: Optional[threading.Timer] = None
        self._partial_lock = threading.Lock()

    def _set_state(self, state: State, message: str = "") -> None:
        self.state = state
        self.on_state(state, message)

    def toggle(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self.state == State.PROCESSING:
                return
            if self.state == State.RECORDING:
                self._stop_and_process()
                return
            # idle / error / starting → try start
            if not self.api.is_healthy():
                self._set_state(State.ERROR, "API not ready")
                return
            self._start_recording()
        finally:
            if self._lock.locked():
                try:
                    self._lock.release()
                except RuntimeError:
                    pass

    def _start_recording(self) -> None:
        self._confirmed = ""
        self._partial = ""
        self.typer.clear_session()
        self.audio.start()
        self._set_state(State.RECORDING, "Recording...")

    def _stop_and_process(self) -> None:
        # Hardware catch-up: let the mic/USB stack flush tail samples
        catchup = max(0, int(self.config.hardware_catchup_ms)) / 1000.0
        if catchup:
            time.sleep(catchup)
        pcm = self.audio.stop()
        if not pcm or len(pcm) < self.config.min_audio_bytes:
            self._set_state(State.IDLE, "Too short — ignored")
            return

        self._set_state(State.PROCESSING, "Processing...")
        # Release toggle lock for the duration of transcription so we don't
        # hard-deadlock; re-check state for re-entry protection via PROCESSING.
        self._lock.release()
        try:
            if self.config.streaming:
                self._run_stream(pcm)
            else:
                self._run_batch(pcm)
        except Exception as exc:  # noqa: BLE001
            self._set_state(State.ERROR, str(exc)[:80])
        finally:
            if self.state == State.PROCESSING:
                self._set_state(State.IDLE, "Ready")
            # Re-acquire only if still owned by us — toggle() finally also releases.
            # We already released above; ensure finally of toggle doesn't double-release.
            self._lock.acquire(blocking=False)

    def _run_batch(self, pcm: bytes) -> None:
        text = (self.api.batch(pcm) or "").strip()
        if text:
            self.typer.commit_final(text)
        self._set_state(State.IDLE, "Ready")

    def _run_stream(self, pcm: bytes) -> None:
        # For the v2 Windows path, stream can also be fed live while recording.
        # This stop-path handles the recorded buffer as a single stream session
        # (and is what unit tests exercise). Live path uses feed_* methods.
        def on_partial(text: str) -> None:
            self._schedule_partial(text)

        def on_confirmed(text: str) -> None:
            self._flush_partial_timer()
            self._confirmed = self._merge_confirmed(self._confirmed, text)
            self._partial = ""
            self.typer.set_live_text(self._confirmed, "")

        def on_final(text: str) -> None:
            self._flush_partial_timer()
            final = (text or "").strip() or self._confirmed
            self.typer.commit_final(final)
            self._confirmed = final
            self._partial = ""

        self.api.stream(pcm, on_partial, on_confirmed, on_final)
        self._set_state(State.IDLE, "Ready")

    def _merge_confirmed(self, prev: str, chunk: str) -> str:
        chunk = (chunk or "").strip()
        if not chunk:
            return prev
        if not prev:
            return chunk
        if chunk.startswith(prev):
            return chunk
        # Incremental append
        if prev.endswith(chunk):
            return prev
        joiner = "" if prev.endswith(" ") or chunk.startswith(" ") else " "
        return (prev + joiner + chunk).strip()

    def _apply_partial(self, text: str) -> None:
        self._partial = (text or "").strip()
        self.typer.set_live_text(self._confirmed, self._partial)

    def _schedule_partial(self, text: str) -> None:
        delay = max(0, int(self.config.partial_debounce_ms)) / 1000.0

        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
                self._partial_timer = None
            if delay <= 0:
                self._apply_partial(text)
                return
            self._partial_timer = threading.Timer(delay, self._apply_partial, args=(text,))
            self._partial_timer.daemon = True
            self._partial_timer.start()

    def _flush_partial_timer(self) -> None:
        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
                self._partial_timer = None
