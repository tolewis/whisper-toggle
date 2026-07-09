"""Windows keyboard + mic adapters."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("whisper-toggle.tray")


class NativeHotkeyHandle:
    """RegisterHotKey-backed global hotkey.

    The `keyboard` package's low-level suppression can leak the final key (for
    example the `h` in Ctrl+Shift+H) and can leave modifier state confused in
    Windows Terminal/tmux. RegisterHotKey lets Windows consume the chord before
    it reaches the focused app.
    """

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000

    _next_id = 0x5748
    _id_lock = threading.Lock()

    def __init__(self, hotkey: str, callback: Callable):
        self.hotkey = hotkey
        self.callback = callback
        self._ready = threading.Event()
        self._registered = False
        self._thread_id: int | None = None
        self._error: Exception | None = None
        with self._id_lock:
            self._id = NativeHotkeyHandle._next_id
            NativeHotkeyHandle._next_id += 1
        self._mods, self._vk = self._parse(hotkey)
        self._thread = threading.Thread(target=self._run, name=f"hotkey-{hotkey}", daemon=True)

    @classmethod
    def try_start(cls, hotkey: str, callback: Callable) -> "NativeHotkeyHandle | None":
        if not sys.platform.startswith("win"):
            return None
        try:
            handle = cls(hotkey, callback)
        except ValueError:
            return None
        handle._thread.start()
        if not handle._ready.wait(timeout=2.0) or not handle._registered:
            handle.stop()
            if handle._error:
                log.debug("native hotkey %s unavailable: %s", hotkey, handle._error)
            return None
        log.info("native hotkey bound: %s", hotkey)
        return handle

    @classmethod
    def _parse(cls, hotkey: str) -> tuple[int, int]:
        parts = [p.strip().lower() for p in hotkey.replace("windows+", "win+").split("+") if p.strip()]
        mods = cls.MOD_NOREPEAT
        key: str | None = None
        for part in parts:
            if part in ("ctrl", "control", "ctl"):
                mods |= cls.MOD_CONTROL
            elif part == "shift":
                mods |= cls.MOD_SHIFT
            elif part == "alt":
                mods |= cls.MOD_ALT
            elif part in ("win", "windows", "cmd", "super"):
                mods |= cls.MOD_WIN
            else:
                key = part
        if key is None:
            raise ValueError(f"missing key in hotkey {hotkey!r}")
        key_map = {
            "space": 0x20,
            "tab": 0x09,
            "enter": 0x0D,
            "return": 0x0D,
            "esc": 0x1B,
            "escape": 0x1B,
            "backspace": 0x08,
            "caps": 0x14,
            "capslock": 0x14,
            "caps lock": 0x14,
            "grave": 0xC0,
            "tilde": 0xC0,
            "`": 0xC0,
        }
        if len(key) == 1 and "a" <= key <= "z":
            vk = ord(key.upper())
        elif len(key) == 1 and "0" <= key <= "9":
            vk = ord(key)
        elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            vk = 0x70 + int(key[1:]) - 1
        elif key in key_map:
            vk = key_map[key]
        else:
            raise ValueError(f"unsupported hotkey key {key!r}")
        return mods, vk

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", POINT),
            ]

        self._thread_id = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, self._id, self._mods, self._vk):
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._registered = True
        self._ready.set()
        msg = MSG()
        try:
            while True:
                rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if rc == 0 or msg.message == self.WM_QUIT:
                    break
                if msg.message == self.WM_HOTKEY and int(msg.wParam) == self._id:
                    threading.Thread(target=self.callback, daemon=True).start()
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, self._id)
            self._registered = False

    def stop(self) -> None:
        if self._thread_id is None:
            return
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        except Exception:
            pass


class KeyboardAdapter:
    """Hotkeys + reliable text injection at the focused cursor."""

    def __init__(self):
        import keyboard

        self._keyboard = keyboard
        self._native_handles: list[NativeHotkeyHandle] = []

    def type_text(self, text: str) -> None:
        """Character typing - used for live partials only. Prefer inject_text for finals."""
        if not text:
            return
        try:
            self._wait_modifiers_up()
            if sys.platform.startswith("win"):
                self._send_unicode_text(text)
            else:
                self._keyboard.write(text, delay=0)
        except Exception as exc:  # noqa: BLE001
            log.error("type_text failed: %s", exc)
            raise

    def backspace(self, n: int) -> None:
        if n <= 0:
            return
        try:
            self._wait_modifiers_up()
            if sys.platform.startswith("win"):
                for _ in range(n):
                    self._send_vk(0x08)
            else:
                for _ in range(n):
                    self._keyboard.send("backspace")
        except Exception as exc:  # noqa: BLE001
            log.error("backspace failed: %s", exc)

    def send_paste(self) -> None:
        self._wait_modifiers_up()
        time.sleep(0.05)
        if sys.platform.startswith("win"):
            self._send_ctrl_v_sendinput()
        else:
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
        vk_control = 0x11
        vk_v = 0x56
        self._send_vk(vk_control, down=True, up=False)
        self._send_vk(vk_v, down=True, up=True)
        self._send_vk(vk_control, down=False, up=True)

    def _send_vk(self, vk: int, *, down: bool = True, up: bool = True) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        input_keyboard = 1
        keyeventf_keyup = 0x0002
        ulong_ptr = wintypes.ULONG_PTR if hasattr(wintypes, "ULONG_PTR") else ctypes.c_size_t

        class KeyBdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeyBdInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("i",)
            _fields_ = [("type", wintypes.DWORD), ("i", InputUnion)]

        events = []
        if down:
            event = Input(type=input_keyboard)
            event.ki = KeyBdInput(vk, 0, 0, 0, 0)
            events.append(event)
        if up:
            event = Input(type=input_keyboard)
            event.ki = KeyBdInput(vk, 0, keyeventf_keyup, 0, 0)
            events.append(event)
        if not events:
            return
        array_type = Input * len(events)
        sent = user32.SendInput(len(events), array_type(*events), ctypes.sizeof(Input))
        if sent != len(events):
            raise ctypes.WinError(ctypes.get_last_error())

    def _send_unicode_text(self, text: str) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        input_keyboard = 1
        keyeventf_keyup = 0x0002
        keyeventf_unicode = 0x0004
        ulong_ptr = wintypes.ULONG_PTR if hasattr(wintypes, "ULONG_PTR") else ctypes.c_size_t

        class KeyBdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeyBdInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("i",)
            _fields_ = [("type", wintypes.DWORD), ("i", InputUnion)]

        events = []
        data = text.encode("utf-16-le")
        for i in range(0, len(data), 2):
            scan = data[i] | (data[i + 1] << 8)
            down = Input(type=input_keyboard)
            down.ki = KeyBdInput(0, scan, keyeventf_unicode, 0, 0)
            up = Input(type=input_keyboard)
            up.ki = KeyBdInput(0, scan, keyeventf_unicode | keyeventf_keyup, 0, 0)
            events.extend((down, up))
        if not events:
            return
        # Keep chunks modest; SendInput can accept large arrays but smaller batches
        # avoid starving the tray thread during live partial updates.
        for start in range(0, len(events), 128):
            batch = events[start : start + 128]
            array_type = Input * len(batch)
            sent = user32.SendInput(len(batch), array_type(*batch), ctypes.sizeof(Input))
            if sent != len(batch):
                raise ctypes.WinError(ctypes.get_last_error())

    def _wait_modifiers_up(self, timeout: float = 1.0) -> None:
        """Do not inject while Ctrl/Shift/Alt/Win from the hotkey are still down."""
        deadline = time.monotonic() + timeout
        if sys.platform.startswith("win"):
            try:
                import ctypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                vks = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, lwin, rwin
                while time.monotonic() < deadline:
                    if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks):
                        return
                    time.sleep(0.02)
                log.warning("modifiers still down after wait - injecting anyway")
                return
            except Exception:
                return

        keys = ("ctrl", "shift", "alt", "left windows", "right windows", "windows")
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
        # Prefer the Windows API for ordinary global hotkeys. It consumes the
        # chord before the focused app sees the final key, avoiding stray "h"
        # and stuck modifier/caps-like state from low-level suppression hooks.
        native = NativeHotkeyHandle.try_start(hk, callback)
        if native is not None:
            self._native_handles.append(native)
            return native
        return self._keyboard.add_hotkey(hk, callback, suppress=suppress)

    def remove_hotkey(self, handle) -> None:
        try:
            if isinstance(handle, NativeHotkeyHandle):
                handle.stop()
                try:
                    self._native_handles.remove(handle)
                except ValueError:
                    pass
                return
            self._keyboard.remove_hotkey(handle)
        except Exception:
            pass

    def unhook_all(self) -> None:
        for handle in list(self._native_handles):
            handle.stop()
        self._native_handles.clear()
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
