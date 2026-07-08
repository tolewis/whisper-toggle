"""Windows-native Win+H ownership.

Design:
- Claim Win+H with a low-level keyboard hook that *swallows* the chord only while
  Whisper Toggle is healthy and ready.
- If we cannot claim / are not ready, do nothing — Windows Voice Typing keeps Win+H.
- Also best-effort toggle the OS "voice typing launcher" registry while we own the key,
  restored on release.

This is intentionally separate from the `keyboard` package so Win+ combos are reliable.
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
from typing import Callable, Optional

log = logging.getLogger("whisper-toggle.tray")  # share tray handlers

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
VK_H = 0x48
VK_LWIN = 0x5B
VK_RWIN = 0x5C
HC_ACTION = 0
LLKHF_INJECTED = 0x00000010

user = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelKeyboardProc,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user.SetWindowsHookExW.restype = wintypes.HHOOK
user.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user.CallNextHookEx.restype = LRESULT
user.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user.UnhookWindowsHookEx.restype = wintypes.BOOL
user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user.GetMessageW.restype = wintypes.BOOL
user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.GetAsyncKeyState.argtypes = [ctypes.c_int]
user.GetAsyncKeyState.restype = wintypes.SHORT

WM_QUIT = 0x0012


def _voice_typing_reg_paths():
    import winreg

    return [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Input\Settings", "EnableHwkbTextPrediction"),
        # Voice typing launcher preference (best-effort; ignored if missing)
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher"),
    ]


class WinHotkeyOwner:
    """Own Win+H while ready; release cleanly so OS voice typing returns."""

    def __init__(self, on_hotkey: Callable[[], None]):
        self.on_hotkey = on_hotkey
        self._enabled = False  # only swallow when True (engine healthy)
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._proc = None  # keep callback alive
        self._win_down = False
        self._armed = False  # saw win+h down while enabled
        self._saved_reg: list[tuple] = []
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._hook is not None and self._enabled

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="win-hotkey", daemon=True)
        self._thread.start()
        # Give the hook a moment to install
        for _ in range(50):
            if self._hook is not None:
                atexit.register(self.stop)
                return True
            threading.Event().wait(0.05)
        log.error("failed to install Win+H hook")
        return False

    def set_enabled(self, enabled: bool) -> None:
        """Enable swallowing only when Whisper Toggle can actually dictate."""
        with self._lock:
            was = self._enabled
            self._enabled = bool(enabled)
            if self._enabled and not was:
                self._disable_os_voice_typing()
                log.info("Win+H ownership ENABLED (OS voice typing suppressed while we are ready)")
            elif not self._enabled and was:
                self._restore_os_voice_typing()
                log.info("Win+H ownership DISABLED (OS voice typing restored)")

    def stop(self) -> None:
        self.set_enabled(False)
        if self._tid:
            try:
                user.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._hook:
            try:
                user.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None

    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        self._proc = LowLevelKeyboardProc(self._callback)
        self._hook = user.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
            return
        log.info("Win+H low-level hook installed")
        msg = wintypes.MSG()
        while user.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user.TranslateMessage(ctypes.byref(msg))
            user.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _callback(self, nCode, wParam, lParam):
        try:
            if nCode == HC_ACTION:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.flags & LLKHF_INJECTED:
                    return user.CallNextHookEx(self._hook, nCode, wParam, lParam)

                vk = kb.vkCode
                down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                if vk in (VK_LWIN, VK_RWIN):
                    self._win_down = down
                    # never swallow bare Win
                    return user.CallNextHookEx(self._hook, nCode, wParam, lParam)

                # Detect Win held via async state too (more reliable than tracking)
                win_held = (
                    self._win_down
                    or (user.GetAsyncKeyState(VK_LWIN) & 0x8000)
                    or (user.GetAsyncKeyState(VK_RWIN) & 0x8000)
                )

                if vk == VK_H and win_held:
                    if not self._enabled:
                        # Not ready — let Windows Voice Typing handle it
                        return user.CallNextHookEx(self._hook, nCode, wParam, lParam)
                    # Swallow both down and up so OS never sees the chord
                    if down:
                        self._armed = True
                        try:
                            threading.Thread(target=self.on_hotkey, daemon=True).start()
                        except Exception as exc:  # noqa: BLE001
                            log.error("hotkey callback failed: %s", exc)
                    if up:
                        self._armed = False
                    return 1  # non-zero = eat the event
        except Exception as exc:  # noqa: BLE001
            log.error("hook callback error: %s", exc)
        return user.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _disable_os_voice_typing(self) -> None:
        """Best-effort: turn off launcher while we own Win+H; remember prior values."""
        try:
            import winreg
        except ImportError:
            return
        self._saved_reg = []
        for root, path, name in _voice_typing_reg_paths():
            try:
                key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
            except OSError:
                continue
            try:
                try:
                    prev, typ = winreg.QueryValueEx(key, name)
                except OSError:
                    prev, typ = None, winreg.REG_DWORD
                self._saved_reg.append((root, path, name, prev, typ))
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)
            except OSError as exc:
                log.debug("reg set failed %s\\%s: %s", path, name, exc)
            finally:
                winreg.CloseKey(key)

    def _restore_os_voice_typing(self) -> None:
        try:
            import winreg
        except ImportError:
            return
        for root, path, name, prev, typ in self._saved_reg:
            try:
                key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE)
                if prev is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except OSError:
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
                else:
                    winreg.SetValueEx(key, name, 0, typ, prev)
                winreg.CloseKey(key)
            except OSError as exc:
                log.debug("reg restore failed %s\\%s: %s", path, name, exc)
        self._saved_reg = []
