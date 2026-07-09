"""Windows keyboard + mic adapters."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("whisper-toggle.tray")


class KeyboardAdapter:
    """Hotkeys + reliable text injection at the focused cursor."""

    def __init__(self):
        import keyboard

        self._keyboard = keyboard

    def type_text(self, text: str) -> None:
        """Character typing - used for live partials only. Prefer inject_text for finals."""
        if not text:
            return
        try:
            self._wait_modifiers_up()
            self._keyboard.write(text, delay=0)
        except Exception as exc:  # noqa: BLE001
            log.error("type_text failed: %s", exc)
            raise

    def backspace(self, n: int) -> None:
        if n <= 0:
            return
        try:
            self._wait_modifiers_up()
            for _ in range(n):
                self._keyboard.send("backspace")
        except Exception as exc:  # noqa: BLE001
            log.error("backspace failed: %s", exc)

    def send_paste(self) -> None:
        self._wait_modifiers_up()
        time.sleep(0.05)
        self._keyboard.send("ctrl+v")

    def inject_text(self, text: str, restore_clipboard: bool = True) -> None:
        """Most reliable path: clipboard + Ctrl+V after hotkey keys are released.

        This is how most Windows dictation tools insert text and works in
        PowerShell, Windows Terminal, Notepad, browsers, etc.
        """
        if not text:
            return
        import pyperclip

        self._wait_modifiers_up()
        previous = None
        if restore_clipboard:
            try:
                previous = pyperclip.paste()
            except Exception:
                previous = None

        try:
            pyperclip.copy(text)
        except Exception as exc:  # noqa: BLE001
            log.error("clipboard copy failed: %s - falling back to write()", exc)
            self.type_text(text)
            return

        time.sleep(0.10)
        pasted = False
        try:
            self._send_ctrl_v_sendinput()
            pasted = True
            log.info("pasted %d chars via SendInput ctrl+v", len(text))
        except Exception as exc:  # noqa: BLE001
            log.warning("SendInput paste failed: %s", exc)

        if not pasted:
            try:
                self._keyboard.send("ctrl+v")
                pasted = True
                log.info("pasted %d chars via keyboard ctrl+v", len(text))
            except Exception as exc:  # noqa: BLE001
                log.error("keyboard ctrl+v failed: %s", exc)

        if not pasted:
            try:
                self.type_text(text)
                log.info("injected %d chars via write()", len(text))
            except Exception:
                log.exception("all inject methods failed")
                raise

        if restore_clipboard and previous is not None:
            # Restore previous clipboard after a beat so paste can complete
            def _restore():
                time.sleep(0.4)
                try:
                    pyperclip.copy(previous)
                except Exception:
                    pass

            threading.Thread(target=_restore, daemon=True).start()

    def _send_ctrl_v_sendinput(self) -> None:
        """Low-level Ctrl+V independent of the keyboard hook stack."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        input_keyboard = 1
        keyeventf_keyup = 0x0002
        vk_control = 0x11
        vk_v = 0x56

        class KeyBdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeyBdInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("i",)
            _fields_ = [("type", wintypes.DWORD), ("i", InputUnion)]

        def key(vk: int, *, up: bool = False) -> None:
            event = Input(type=input_keyboard)
            event.ki = KeyBdInput(vk, 0, keyeventf_keyup if up else 0, 0, None)
            sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))
            if sent != 1:
                raise ctypes.WinError(ctypes.get_last_error())

        key(vk_control)
        key(vk_v)
        key(vk_v, up=True)
        key(vk_control, up=True)

    def _wait_modifiers_up(self, timeout: float = 1.0) -> None:
        """Do not inject while Ctrl/Shift/Alt/Win from the hotkey are still down."""
        keys = ("ctrl", "shift", "alt", "left windows", "right windows", "windows")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not any(self._keyboard.is_pressed(k) for k in keys):
                    return
            except Exception:
                return
            time.sleep(0.02)
        log.warning("modifiers still down after wait - injecting anyway")

    def add_hotkey(self, hotkey: str, callback: Callable, suppress: bool = True):
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
