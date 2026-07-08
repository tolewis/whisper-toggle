"""Windows keyboard + mic adapters.

Importing this module on non-Windows is OK for typing tests, but the
runtime methods require the `keyboard` / `sounddevice` packages.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np


class KeyboardAdapter:
    """Type and backspace via the `keyboard` package (Windows primary)."""

    def __init__(self):
        import keyboard  # noqa: F401

        self._keyboard = keyboard

    def type_text(self, text: str) -> None:
        if not text:
            return
        # keyboard.write handles unicode better than send for plain text
        self._keyboard.write(text, delay=0)

    def backspace(self, n: int) -> None:
        if n <= 0:
            return
        for _ in range(n):
            self._keyboard.send("backspace")

    def send_paste(self) -> None:
        time.sleep(0.05)
        self._keyboard.send("ctrl+v")

    def add_hotkey(self, hotkey: str, callback: Callable, suppress: bool = True):
        # Normalize win/windows alias
        hk = hotkey.strip().lower().replace("windows+", "win+")
        return self._keyboard.add_hotkey(hk, callback, suppress=suppress)

    def remove_hotkey(self, handle) -> None:
        try:
            self._keyboard.remove_hotkey(handle)
        except Exception:
            pass

    def unhook_all(self) -> None:
        try:
            self._keyboard.unhook_all()
        except Exception:
            pass


class MicRecorder:
    """16 kHz mono int16 recorder with optional live PCM callback."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        on_pcm: Optional[Callable[[bytes], None]] = None,
    ):
        import sounddevice as sd

        self.sd = sd
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_pcm = on_pcm
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        self._chunks = []

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            copy = indata.copy()
            with self._lock:
                self._chunks.append(copy)
            if self.on_pcm is not None:
                self.on_pcm(copy.tobytes())

        self._stream = self.sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return b""
            data = np.concatenate(self._chunks, axis=0)
            self._chunks = []
        return data.astype(np.int16).tobytes()

    def to_wav_bytes(self, pcm: bytes) -> bytes:
        import io
        import soundfile as sf

        audio = np.frombuffer(pcm, dtype=np.int16)
        buf = io.BytesIO()
        sf.write(buf, audio, self.sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()
