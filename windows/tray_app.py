#!/usr/bin/env python3
"""Whisper Toggle v2 — Windows tray product.

- Win+H owned elegantly only while engine is ready; OS voice typing restored otherwise
- Live streaming partials with batch fallback if stream fails
- System tray icon + settings GUI + file logs
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pystray

from whisper_toggle.api_client import LiveStreamSession, LocalApiClient
from whisper_toggle.config import AppConfig, app_data_dir, load_config, save_config
from whisper_toggle.controller import State
from whisper_toggle.device import resolve_device
from whisper_toggle.icons import tray_icon, write_app_icon
from whisper_toggle.logging_setup import setup_logging
from whisper_toggle.paste import LiveTextSession
from whisper_toggle.win_input import KeyboardAdapter, MicRecorder

log = setup_logging("whisper-toggle.tray")

API_HOST = "127.0.0.1"
API_PORT = 8788
API_BASE = f"http://{API_HOST}:{API_PORT}"
STREAM_URL = f"ws://{API_HOST}:{API_PORT}/v1/audio/stream"


class TrayApp:
    def __init__(self):
        self.cfg = load_config()
        save_config(self.cfg)
        self.choice = resolve_device(self.cfg.device_override, self.cfg.model)
        self.model = self.cfg.model or self.choice.model
        self.api = LocalApiClient(
            base_url=API_BASE,
            stream_url=STREAM_URL,
            model=self.model,
            open_timeout=8.0,
        )
        self.kb = KeyboardAdapter()
        self.session = LiveTextSession(self.kb)
        self.mic: MicRecorder | None = None
        self.live: LiveStreamSession | None = None
        self.api_process: subprocess.Popen | None = None
        self.state = State.STARTING
        self.status_text = "Starting..."
        self.tray: pystray.Icon | None = None
        self._hotkey_handle = None
        self._win_owner = None
        self._toggle_lock = threading.Lock()
        self._recording = False
        self._pcm_buffer = bytearray()
        self._partial_timer: threading.Timer | None = None
        self._partial_lock = threading.Lock()
        self._owns_api = False
        self._reload_path = app_data_dir() / "reload.signal"
        self._config_mtime = 0.0
        self._poll_stop = threading.Event()

        icon_path = app_data_dir() / "assets" / "icon.ico"
        try:
            write_app_icon(icon_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("icon write failed: %s", exc)

    # ── Tray ────────────────────────────────────────────────────────────
    def _set_state(self, state: State, message: str) -> None:
        self.state = state
        self.status_text = message
        log.info("state=%s %s", state.value, message)
        if self.tray:
            try:
                self.tray.icon = tray_icon(state.value)
                self.tray.title = f"Whisper Toggle — {message}"
                # Keep icon marked visible (Win11 overflow still may hide it)
                self.tray.visible = True
            except Exception as exc:  # noqa: BLE001
                log.debug("tray update failed: %s", exc)
        # Hotkey ownership follows readiness
        ready = state == State.IDLE and self.api.is_healthy()
        self._set_hotkey_ownership(ready)

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Whisper Toggle v2.0", None, enabled=False),
            pystray.MenuItem(lambda _: self.status_text, None, enabled=False),
            pystray.MenuItem(lambda _: f"Hotkey: {self.cfg.hotkey}", None, enabled=False),
            pystray.MenuItem(
                lambda _: f"Device: {self.choice.device} / {self.model}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings...", self._on_settings),
            pystray.MenuItem("Restart engine", self._on_restart_api),
            pystray.MenuItem("Open logs", self._on_open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── Engine ──────────────────────────────────────────────────────────
    def start_api(self) -> bool:
        if self.api.is_healthy():
            self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            return True

        python = sys.executable
        env = os.environ.copy()
        device = self.choice.device
        compute = self.choice.compute_type
        if device == "vulkan":
            device = "cpu"
            compute = "int8"
            log.info("vulkan detected; engine using faster-whisper cpu")

        env["WHISPER_API_DEFAULT_MODEL"] = self.model
        env["WHISPER_API_DEVICE"] = device if device in ("cuda", "cpu") else "cpu"
        env["WHISPER_API_COMPUTE_TYPE"] = compute if compute in ("int8", "float16", "float32") else "int8"
        env["WHISPER_API_LANGUAGE"] = "en"
        env["WHISPER_API_VERSION"] = "2.0.0"
        env["WHISPER_API_PRELOAD"] = "1"

        for vendor_base in (
            Path(sys.argv[0]).resolve().parent / "vendor" / "whisper_streaming",
            APP_DIR / "vendor" / "whisper_streaming",
            ROOT / "vendor" / "whisper_streaming",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Whisper Toggle" / "vendor" / "whisper_streaming",
        ):
            if (vendor_base / "whisper_online.py").exists():
                env["WHISPER_STREAMING_PATH"] = str(vendor_base)
                break

        candidates = [
            APP_DIR,
            Path(sys.argv[0]).resolve().parent,
            ROOT,
            Path(os.environ.get("LOCALAPPDATA", "")) / "Whisper Toggle",
        ]
        cwd = next((c for c in candidates if (c / "app.py").exists()), None)
        if cwd is None:
            self._set_state(State.ERROR, "app.py not found")
            return False

        self._set_state(State.STARTING, "Starting engine...")
        try:
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.api_process = subprocess.Popen(
                [python, "-m", "uvicorn", "app:app", "--host", API_HOST, "--port", str(API_PORT)],
                cwd=str(cwd),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation,
            )
            self._owns_api = True
        except Exception as exc:  # noqa: BLE001
            self._set_state(State.ERROR, f"Engine failed: {exc}")
            return False

        self._set_state(State.STARTING, "Loading model (first run may take a minute)...")
        for _ in range(240):
            if self.api_process.poll() is not None:
                if self.choice.device == "cuda":
                    log.warning("CUDA engine crashed; retrying CPU")
                    self.choice = resolve_device("cpu", self.cfg.model)
                    self.model = self.cfg.model or self.choice.model
                    self.api.model = self.model
                    return self.start_api()
                self._set_state(State.ERROR, "Engine crashed on startup")
                return False
            if self.api.is_healthy():
                rt = self.api.runtime()
                log.info("engine ready: %s", rt)
                self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
                return True
            time.sleep(1)

        self._set_state(State.ERROR, "Engine timeout")
        return False

    def stop_api(self) -> None:
        if self._owns_api and self.api_process and self.api_process.poll() is None:
            self.api_process.terminate()
            try:
                self.api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.api_process.kill()
        self.api_process = None
        self._owns_api = False

    # ── Live dictate ────────────────────────────────────────────────────
    def toggle(self) -> None:
        if not self._toggle_lock.acquire(blocking=False):
            return
        try:
            if self._recording:
                self._stop_recording()
            else:
                if self.state in (State.ERROR, State.STARTING, State.PROCESSING):
                    self._notify("Not ready yet")
                    return
                if not self.api.is_healthy():
                    self._set_state(State.ERROR, "API not ready")
                    self._set_hotkey_ownership(False)
                    return
                self._start_recording()
        finally:
            if self._toggle_lock.locked():
                try:
                    self._toggle_lock.release()
                except RuntimeError:
                    pass

    def _start_recording(self) -> None:
        self.session.clear()
        self._pcm_buffer = bytearray()
        self._set_state(State.RECORDING, "Recording... (Win+H to stop)")
        self._notify("Listening — speak now")

        use_stream = self.cfg.streaming and self.cfg.live_partials
        self.live = None
        if use_stream:
            stream_err = []

            def on_err(m: str):
                log.error("stream: %s", m)
                stream_err.append(m)

            self.live = LiveStreamSession(
                stream_url=STREAM_URL,
                model=self.model,
                language="en",
                on_partial=self._on_partial,
                on_confirmed=self._on_confirmed,
                on_final=self._on_final,
                on_error=on_err,
                open_timeout=8.0,
            )
            if not self.live.start():
                log.warning("stream connect failed; will batch at stop")
                self.live = None

        def on_pcm(data: bytes):
            self._pcm_buffer.extend(data)
            if self.live is not None and not self.live.failed:
                self.live.send_pcm(data)

        self.mic = MicRecorder(on_pcm=on_pcm)
        self.mic.start()
        self._recording = True

    def _stop_recording(self) -> None:
        self._recording = False
        catchup = max(0, int(self.cfg.hardware_catchup_ms)) / 1000.0
        if catchup:
            time.sleep(catchup)

        self._set_state(State.PROCESSING, "Processing...")
        pcm = b""
        if self.mic:
            pcm = self.mic.stop()
            self.mic = None
        if not pcm and self._pcm_buffer:
            pcm = bytes(self._pcm_buffer)

        # Prefer live stream final; fall back to batch on any failure
        if self.live is not None:
            try:
                final = self.live.end()
            except Exception as exc:  # noqa: BLE001
                log.error("live end failed: %s", exc)
                final = ""
            failed = self.live.failed
            self.live = None
            if not failed and final:
                # finalize already typed via callback; ensure idle
                if self.state == State.PROCESSING:
                    self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
                return
            if not failed and self.state == State.IDLE:
                # final callback already set idle
                return
            log.warning("stream incomplete — batch fallback")

        if not pcm or len(pcm) < 1000:
            self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            self._notify("Too short — ignored")
            return
        try:
            wav = MicRecorder().to_wav_bytes(pcm)
            text = self.api.batch(wav)
            if text:
                # Replace any partial live text with clean final
                try:
                    self.session.finalize(text)
                except Exception:
                    import pyperclip

                    pyperclip.copy(text)
                    self.kb.send_paste()
                self._notify(text[:60])
            else:
                self._notify("Nothing detected")
        except Exception as exc:  # noqa: BLE001
            log.exception("batch failed")
            self._set_state(State.ERROR, str(exc)[:60])
            return
        self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")

    def _on_partial(self, text: str) -> None:
        if not self.cfg.live_partials:
            return
        delay = max(0, int(self.cfg.partial_debounce_ms)) / 1000.0

        def apply():
            try:
                self.session.on_partial(text)
            except Exception as exc:  # noqa: BLE001
                log.error("partial type failed: %s", exc)

        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
            if delay <= 0:
                apply()
                self._partial_timer = None
            else:
                self._partial_timer = threading.Timer(delay, apply)
                self._partial_timer.daemon = True
                self._partial_timer.start()

    def _on_confirmed(self, text: str) -> None:
        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
                self._partial_timer = None
        try:
            self.session.on_confirmed(text)
        except Exception as exc:  # noqa: BLE001
            log.error("confirmed type failed: %s", exc)

    def _on_final(self, text: str) -> None:
        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
                self._partial_timer = None
        try:
            self.session.finalize(text)
            if text:
                self._notify(text[:60])
        except Exception as exc:  # noqa: BLE001
            log.error("final type failed: %s", exc)
        self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")

    # ── Hotkey ownership ────────────────────────────────────────────────
    def _set_hotkey_ownership(self, enabled: bool) -> None:
        if self._win_owner is not None:
            try:
                self._win_owner.set_enabled(enabled and self._is_win_hotkey())
            except Exception as exc:  # noqa: BLE001
                log.error("hotkey ownership toggle failed: %s", exc)

    def _is_win_hotkey(self) -> bool:
        hk = (self.cfg.hotkey or "").lower().replace("windows+", "win+").replace(" ", "")
        return hk == "win+h"

    def _bind_hotkey(self) -> None:
        # Tear down previous
        if self._hotkey_handle is not None:
            try:
                self.kb.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
            self._hotkey_handle = None
        if self._win_owner is not None:
            try:
                self._win_owner.stop()
            except Exception:
                pass
            self._win_owner = None

        hk = (self.cfg.hotkey or "win+h").lower().replace("windows+", "win+")
        self.cfg.hotkey = hk

        if self._is_win_hotkey():
            try:
                from whisper_toggle.win_hotkey import WinHotkeyOwner

                self._win_owner = WinHotkeyOwner(on_hotkey=self.toggle)
                if self._win_owner.start():
                    # Enable only when ready
                    self._set_hotkey_ownership(self.state == State.IDLE and self.api.is_healthy())
                    log.info("Win+H native ownership hook ready")
                    return
                log.error("Win+H native hook failed to start")
            except Exception as exc:  # noqa: BLE001
                log.exception("Win+H owner failed: %s", exc)

            # Fallback: keyboard lib (may not fully suppress OS voice typing)
            try:
                self._hotkey_handle = self.kb.add_hotkey("win+h", self.toggle, suppress=True)
                log.warning("using keyboard-lib win+h fallback (OS may still open voice typing)")
                return
            except Exception as exc:  # noqa: BLE001
                log.error("win+h fallback failed: %s", exc)

            # Last resort alternate
            try:
                self._hotkey_handle = self.kb.add_hotkey("ctrl+shift+h", self.toggle, suppress=True)
                self.cfg.hotkey = "ctrl+shift+h"
                save_config(self.cfg)
                self._set_state(State.IDLE, "Ready (ctrl+shift+h) — Win+H unavailable")
                self._notify("Win+H busy — using Ctrl+Shift+H")
            except Exception as exc2:  # noqa: BLE001
                self._set_state(State.ERROR, f"Hotkey failed: {exc2}")
            return

        # Non-Win hotkeys via keyboard package
        try:
            self._hotkey_handle = self.kb.add_hotkey(
                hk, self.toggle, suppress=bool(self.cfg.suppress_hotkey)
            )
            log.info("hotkey bound: %s", hk)
        except Exception as exc:  # noqa: BLE001
            log.error("hotkey bind failed for %s: %s", hk, exc)
            self._set_state(State.ERROR, f"Hotkey failed: {exc}")

    # ── Menu actions ────────────────────────────────────────────────────
    def _on_settings(self, icon=None, item=None):
        """Launch settings as a separate process — Tk is not thread-safe."""
        try:
            from whisper_toggle.config import default_config_path

            config_path = default_config_path()
            settings_py = APP_DIR / "settings_gui.py"
            if not settings_py.exists():
                # installed layout
                settings_py = Path(os.environ.get("LOCALAPPDATA", "")) / "Whisper Toggle" / "settings_gui.py"
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # Use console-less pythonw so no flash; settings still gets its own UI thread.
            py = sys.executable
            if py.lower().endswith("python.exe"):
                pyw = py[:-10] + "pythonw.exe"
                if Path(pyw).exists():
                    py = pyw
            subprocess.Popen(
                [py, str(settings_py), "--config", str(config_path)],
                cwd=str(APP_DIR if (APP_DIR / "settings_gui.py").exists() else config_path.parent),
                creationflags=creation,
            )
            log.info("opened settings process: %s", settings_py)
            self._notify("Settings opened")
        except Exception as exc:  # noqa: BLE001
            log.exception("settings launch failed")
            self._notify(f"Settings failed: {exc}")

    def _apply_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.choice = resolve_device(cfg.device_override, cfg.model)
        self.model = cfg.model or self.choice.model
        self.api.model = self.model
        self._bind_hotkey()
        log.info("config applied: hotkey=%s device=%s model=%s", cfg.hotkey, self.choice.device, self.model)

    def _on_restart_api(self, icon=None, item=None):
        threading.Thread(target=self._restart_api, daemon=True).start()

    def _restart_api(self) -> None:
        self._set_hotkey_ownership(False)
        self.stop_api()
        time.sleep(1)
        self.choice = resolve_device(self.cfg.device_override, self.cfg.model)
        self.model = self.cfg.model or self.choice.model
        self.api.model = self.model
        self.start_api()

    def _on_open_logs(self, icon=None, item=None):
        path = app_data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(path.as_uri())

    def _on_quit(self, icon=None, item=None):
        self._poll_stop.set()
        try:
            if self._recording and self.mic:
                self.mic.stop()
        except Exception:
            pass
        self._set_hotkey_ownership(False)
        if self._win_owner is not None:
            try:
                self._win_owner.stop()
            except Exception:
                pass
        try:
            self.kb.unhook_all()
        except Exception:
            pass
        self.stop_api()
        if self.tray:
            self.tray.stop()

    def _notify(self, msg: str) -> None:
        if self.tray:
            try:
                self.tray.notify(msg, "Whisper Toggle")
            except Exception:
                pass
        log.info("notify: %s", msg)

    def _poll_config_reload(self) -> None:
        """Pick up settings saved by the settings process."""
        while not self._poll_stop.wait(1.0):
            try:
                cfg_path = app_data_dir() / "config.json"
                if not cfg_path.exists():
                    continue
                mtime = cfg_path.stat().st_mtime
                signal = self._reload_path.exists()
                if signal or mtime > self._config_mtime > 0:
                    if signal:
                        try:
                            self._reload_path.unlink()
                        except OSError:
                            pass
                    new_cfg = load_config(cfg_path)
                    self._config_mtime = mtime
                    log.info("reloading config from disk: hotkey=%s", new_cfg.hotkey)
                    self._apply_config(new_cfg)
                    self._notify(f"Settings applied - hotkey {new_cfg.hotkey}")
                elif self._config_mtime <= 0:
                    self._config_mtime = mtime
            except Exception as exc:  # noqa: BLE001
                log.debug("config poll: %s", exc)

    def _startup(self, icon):
        # Ensure icon is visible ASAP
        try:
            icon.visible = True
            icon.icon = tray_icon("starting")
        except Exception:
            pass
        ok = self.start_api()
        self._bind_hotkey()
        # Always also bind a reliable backup hotkey so user is never stuck
        try:
            if (self.cfg.hotkey or "").lower() != "ctrl+shift+h":
                self.kb.add_hotkey("ctrl+shift+h", self.toggle, suppress=True)
                log.info("backup hotkey bound: ctrl+shift+h")
        except Exception as exc:  # noqa: BLE001
            log.warning("backup hotkey failed: %s", exc)
        threading.Thread(target=self._poll_config_reload, daemon=True).start()
        if ok:
            self._notify(
                f"Running. Press {self.cfg.hotkey} (backup: Ctrl+Shift+H). Green mic in tray."
            )
        else:
            self._notify("Engine failed to start - check logs. Win+H left for Windows.")
            self._set_hotkey_ownership(False)

    def run(self) -> None:
        app_data_dir().mkdir(parents=True, exist_ok=True)
        image = tray_icon("starting")
        self.tray = pystray.Icon(
            "whisper-toggle",
            image,
            "Whisper Toggle — Starting...",
            self._menu(),
        )
        self.tray.run(setup=self._startup)


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.CreateMutexW(None, False, "Local\\WhisperToggleV2")
            last = kernel32.GetLastError()
            if last == 183:
                log.warning("another instance is running")
                # Still try to toast via a short-lived message? just exit.
                return
            _ = handle
        except Exception:
            pass

    TrayApp().run()


if __name__ == "__main__":
    main()
