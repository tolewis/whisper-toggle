"""Windows-native Win+H ownership via low-level keyboard hook only.

RegisterHotKey is intentionally avoided (message-window setup is fragile under
pythonw). The LL hook swallows Win+H while enabled; registry best-effort
disables the OS voice-typing launcher. If we are not ready, we do nothing so
Windows Voice Typing still works.
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
from typing import Any, Callable, Optional

from whisper_toggle.win_voice_typing import (
    default_marker_store,
    disable_voice_typing,
    reconcile_on_launch,
    restore_voice_typing,
)

log = logging.getLogger("whisper-toggle.tray")

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
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


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD]
user.SetWindowsHookExW.restype = wintypes.HHOOK
user.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user.CallNextHookEx.restype = LRESULT
user.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user.UnhookWindowsHookEx.restype = wintypes.BOOL
user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user.GetMessageW.restype = ctypes.c_int
user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.PostThreadMessageW.restype = wintypes.BOOL
user.GetAsyncKeyState.argtypes = [ctypes.c_int]
user.GetAsyncKeyState.restype = wintypes.SHORT
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _voice_typing_reg_ops():
    """Registry ops as (root, path, name, new_value, new_type) 5-tuples."""
    import winreg

    return [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled", 0, winreg.REG_DWORD),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Input\Settings", "VoiceTypingEnabled", 0, winreg.REG_DWORD),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher", 0, winreg.REG_DWORD),
    ]


class _WinregAdapter:
    """Thin Windows-only RegistryPort over winreg. VALIDATE ON JUBIKU.

    The reconcile/disable/restore decision logic is unit tested via
    win_voice_typing; this adapter is the only Windows-API-touching part.
    """

    def read(self, root: Any, path: str, name: str):
        import winreg

        try:
            key = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
        except OSError:
            return None
        try:
            try:
                value, typ = winreg.QueryValueEx(key, name)
                return (value, typ)
            except OSError:
                return None
        finally:
            winreg.CloseKey(key)

    def write(self, root: Any, path: str, name: str, value: Any, type_: Any) -> None:
        import winreg

        key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
        try:
            winreg.SetValueEx(key, name, 0, type_, value)
        finally:
            winreg.CloseKey(key)

    def delete(self, root: Any, path: str, name: str) -> None:
        import winreg

        try:
            key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE)
        except OSError:
            return
        try:
            try:
                winreg.DeleteValue(key, name)
            except OSError:
                pass
        finally:
            winreg.CloseKey(key)


class WinHotkeyOwner:
    def __init__(self, on_hotkey: Callable[[], None]):
        self.on_hotkey = on_hotkey
        self._enabled = False
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._proc = None
        self._registry = _WinregAdapter()
        self._marker = default_marker_store()
        self._reconciled = False
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._last_fire = 0.0

    @property
    def active(self) -> bool:
        return bool(self._enabled and self._hook)

    def start(self) -> bool:
        # Reconcile-on-launch: if a prior run disabled OS Voice Typing and died
        # before restoring (crash / TerminateProcess / uninstaller taskkill),
        # put the saved originals back before we touch anything.
        if not self._reconciled:
            self._reconciled = True
            try:
                if reconcile_on_launch(self._registry, self._marker):
                    log.info("reconciled leftover Voice Typing marker on launch")
            except Exception as exc:  # noqa: BLE001
                log.exception("Voice Typing reconcile-on-launch failed: %s", exc)
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="win-hotkey", daemon=True)
        self._thread.start()
        ok = self._ready.wait(timeout=2.0)
        if ok:
            atexit.register(self.stop)
        else:
            log.error("Win+H owner thread failed to become ready")
        return ok

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            was = self._enabled
            self._enabled = bool(enabled)
            if self._enabled and not was:
                self._disable_os_voice_typing()
                log.info("Win+H ownership ENABLED (OS voice typing suppressed while ready)")
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

    def _fire(self) -> None:
        import time

        now = time.monotonic()
        if now - self._last_fire < 0.35:
            return  # debounce key repeat
        self._last_fire = now
        try:
            threading.Thread(target=self.on_hotkey, daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.error("hotkey callback failed: %s", exc)

    def _run(self) -> None:
        try:
            self._tid = kernel32.GetCurrentThreadId()
            self._proc = LowLevelKeyboardProc(self._callback)
            self._hook = user.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            if not self._hook:
                log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
                self._ready.set()
                return
            log.info("Win+H low-level hook installed")
            self._ready.set()

            msg = wintypes.MSG()
            while True:
                r = user.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001
            log.exception("Win+H owner thread crashed: %s", exc)
            self._ready.set()
        finally:
            if self._hook:
                try:
                    user.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
                self._hook = None

    def _callback(self, nCode, wParam, lParam):
        try:
            if nCode == HC_ACTION and self._enabled:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & LLKHF_INJECTED):
                    vk = int(kb.vkCode)
                    down = int(wParam) in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    win_held = bool(
                        (user.GetAsyncKeyState(VK_LWIN) & 0x8000)
                        or (user.GetAsyncKeyState(VK_RWIN) & 0x8000)
                    )
                    if vk == VK_H and win_held:
                        if down:
                            log.info("Win+H swallowed by LL hook")
                            self._fire()
                        return LRESULT(1)
        except Exception as exc:  # noqa: BLE001
            log.error("hook callback error: %s", exc)
        return user.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _disable_os_voice_typing(self) -> None:
        try:
            ops = _voice_typing_reg_ops()  # imports winreg; no-op off Windows
        except ImportError:
            return
        try:
            # Persists the originals to the marker BEFORE flipping values, so a
            # crash right after cannot lose them (see win_voice_typing).
            disable_voice_typing(self._registry, self._marker, ops)
        except Exception as exc:  # noqa: BLE001
            log.debug("disable voice typing failed: %s", exc)

    def _restore_os_voice_typing(self) -> None:
        try:
            restore_voice_typing(self._registry, self._marker)
        except Exception as exc:  # noqa: BLE001
            log.debug("restore voice typing failed: %s", exc)
