#!/usr/bin/env python3
"""Whisper Toggle v2 — Windows tray product.

- Default hotkey: Win+H (overrides Windows voice typing when suppress=True)
- Live streaming partials typed into the focused field while speaking
- System tray icon + small settings GUI
- Local engine subprocess with DeviceResolver (cuda / vulkan / cpu)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

# Allow running from repo or installed layout
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pystray
from PIL import Image

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
        save_config(self.cfg)  # ensure config exists
        self.choice = resolve_device(self.cfg.device_override, self.cfg.model)
        self.model = self.cfg.model or self.choice.model
        self.api = LocalApiClient(base_url=API_BASE, stream_url=STREAM_URL, model=self.model)
        self.kb = KeyboardAdapter()
        self.session = LiveTextSession(self.kb)
        self.mic: MicRecorder | None = None
        self.live: LiveStreamSession | None = None
        self.api_process: subprocess.Popen | None = None
        self.state = State.STARTING
        self.status_text = "Starting..."
        self.tray: pystray.Icon | None = None
        self._hotkey_handle = None
        self._toggle_lock = threading.Lock()
        self._recording = False
        # Debounce partial UI/typing
        self._partial_timer: threading.Timer | None = None
        self._partial_lock = threading.Lock()
        self._confirmed_bits: list[str] = []

        # Ensure icon asset exists for shortcuts
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
            self.tray.icon = tray_icon(state.value)
            self.tray.title = f"Whisper Toggle — {message}"

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Whisper Toggle v2.0", None, enabled=False),
            pystray.MenuItem(lambda _: self.status_text, None, enabled=False),
            pystray.MenuItem(
                lambda _: f"Hotkey: {self.cfg.hotkey}",
                None,
                enabled=False,
            ),
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

    # ── Engine subprocess ───────────────────────────────────────────────
    def start_api(self) -> bool:
        if self.api.is_healthy():
            self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            return True

        python = sys.executable
        # Prefer bundled python layout
        bundled = Path(sys.executable).resolve()
        env = os.environ.copy()
        # Map vulkan → cpu for faster-whisper engine; vulkan binary path is future backend
        device = self.choice.device
        compute = self.choice.compute_type
        if device == "vulkan":
            # Engine still uses faster-whisper until whisper.cpp adapter is wired for live WS.
            # DeviceResolver still reports vulkan availability for the GUI; runtime falls back
            # to CPU int8 which is always correct, then we can swap backend later.
            device = "cpu"
            compute = "int8"
            log.info("vulkan detected; engine using faster-whisper cpu until cpp adapter ships")

        env["WHISPER_API_DEFAULT_MODEL"] = self.model
        env["WHISPER_API_DEVICE"] = device if device in ("cuda", "cpu") else "cpu"
        env["WHISPER_API_COMPUTE_TYPE"] = compute if compute in ("int8", "float16", "float32") else "int8"
        env["WHISPER_API_LANGUAGE"] = "en"
        env["WHISPER_API_VERSION"] = "2.0.0"
        # Prefer staged vendor copy for streaming (ufal whisper_streaming)
        for vendor_base in (
            Path(sys.argv[0]).resolve().parent / "vendor" / "whisper_streaming",
            ROOT / "vendor" / "whisper_streaming",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Whisper Toggle" / "vendor" / "whisper_streaming",
        ):
            if (vendor_base / "whisper_online.py").exists():
                env["WHISPER_STREAMING_PATH"] = str(vendor_base)
                break

        # app.py location: install dir or repo root
        app_py_dir = Path(sys.argv[0]).resolve().parent
        candidates = [
            app_py_dir,
            app_py_dir.parent,
            ROOT,
            Path(os.environ.get("LOCALAPPDATA", "")) / "Whisper Toggle",
        ]
        cwd = None
        for c in candidates:
            if (c / "app.py").exists():
                cwd = c
                break
        if cwd is None:
            self._set_state(State.ERROR, "app.py not found")
            log.error("app.py not found in %s", candidates)
            return False

        self._set_state(State.STARTING, "Starting engine...")
        try:
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.api_process = subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app:app",
                    "--host",
                    API_HOST,
                    "--port",
                    str(API_PORT),
                ],
                cwd=str(cwd),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_state(State.ERROR, f"Engine failed: {exc}")
            return False

        self._set_state(State.STARTING, "Loading model...")
        for _ in range(180):
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
        if self.api_process and self.api_process.poll() is None:
            self.api_process.terminate()
            try:
                self.api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.api_process.kill()
        self.api_process = None

    # ── Live dictate ────────────────────────────────────────────────────
    def toggle(self) -> None:
        if not self._toggle_lock.acquire(blocking=False):
            return
        try:
            if self._recording:
                self._stop_recording()
            else:
                if self.state in (State.ERROR, State.STARTING, State.PROCESSING):
                    self._notify("Not ready")
                    return
                if not self.api.is_healthy():
                    self._set_state(State.ERROR, "API not ready")
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
        self._confirmed_bits = []
        self._set_state(State.RECORDING, "Recording... (Win+H to stop)")
        self._notify("Listening...")

        use_stream = self.cfg.streaming and self.cfg.live_partials

        if use_stream:
            self.live = LiveStreamSession(
                stream_url=STREAM_URL,
                model=self.model,
                language="en",
                on_partial=self._on_partial,
                on_confirmed=self._on_confirmed,
                on_final=self._on_final,
                on_error=lambda m: log.error("stream: %s", m),
            )
            if not self.live.start():
                log.warning("stream connect failed; falling back to batch at stop")
                self.live = None

        def on_pcm(data: bytes):
            if self.live is not None:
                self.live.send_pcm(data)

        self.mic = MicRecorder(on_pcm=on_pcm if self.live else None)
        self.mic.start()
        self._recording = True

    def _stop_recording(self) -> None:
        self._recording = False
        # Hardware catch-up — let USB/Bluetooth mics flush
        catchup = max(0, int(self.cfg.hardware_catchup_ms)) / 1000.0
        if catchup:
            time.sleep(catchup)

        self._set_state(State.PROCESSING, "Processing...")
        pcm = b""
        if self.mic:
            pcm = self.mic.stop()
            self.mic = None

        if self.live is not None:
            # End stream; final callback commits text
            try:
                self.live.end()
            except Exception as exc:  # noqa: BLE001
                log.error("live end failed: %s", exc)
            self.live = None
            if self.state == State.PROCESSING:
                self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            return

        # Batch fallback
        if not pcm or len(pcm) < 1000:
            self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            self._notify("Too short — ignored")
            return
        try:
            assert self.mic is None
            wav = MicRecorder().to_wav_bytes(pcm)
            text = self.api.batch(wav)
            if text:
                # Prefer clipboard paste for batch to avoid partial session mismatch
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

    # ── Menu actions ────────────────────────────────────────────────────
    def _on_settings(self, icon=None, item=None):
        def run():
            try:
                from settings_gui import open_settings
            except ImportError:
                from windows.settings_gui import open_settings  # type: ignore

            open_settings(
                self.cfg,
                self.api.runtime(),
                on_save=self._apply_config,
                on_restart_api=lambda: threading.Thread(target=self._restart_api, daemon=True).start(),
                on_open_logs=self._on_open_logs,
            )

        threading.Thread(target=run, daemon=True).start()

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
        try:
            if self._recording and self.mic:
                self.mic.stop()
        except Exception:
            pass
        self.kb.unhook_all()
        self.stop_api()
        if self.tray:
            self.tray.stop()

    def _notify(self, msg: str) -> None:
        if self.tray:
            try:
                self.tray.notify(msg, "Whisper Toggle")
            except Exception:
                pass

    def _bind_hotkey(self) -> None:
        if self._hotkey_handle is not None:
            self.kb.remove_hotkey(self._hotkey_handle)
            self._hotkey_handle = None
        hk = self.cfg.hotkey
        suppress = bool(self.cfg.suppress_hotkey)
        try:
            self._hotkey_handle = self.kb.add_hotkey(hk, self.toggle, suppress=suppress)
            log.info("hotkey bound: %s suppress=%s", hk, suppress)
        except Exception as exc:  # noqa: BLE001
            log.error("hotkey bind failed for %s: %s", hk, exc)
            # Fallback
            try:
                self._hotkey_handle = self.kb.add_hotkey("ctrl+shift+h", self.toggle, suppress=True)
                self.cfg.hotkey = "ctrl+shift+h"
                self._set_state(State.IDLE, "Ready (ctrl+shift+h) — Win+H bind failed")
            except Exception as exc2:  # noqa: BLE001
                self._set_state(State.ERROR, f"Hotkey failed: {exc2}")

    def _startup(self, icon):
        self.start_api()
        self._bind_hotkey()
        # Best-effort: remind user Win+H is ours
        self._notify(f"Ready — press {self.cfg.hotkey} to dictate")

    def run(self) -> None:
        app_data_dir().mkdir(parents=True, exist_ok=True)
        self.tray = pystray.Icon(
            "whisper-toggle",
            tray_icon("starting"),
            "Whisper Toggle — Starting...",
            self._menu(),
        )
        self.tray.run(setup=self._startup)


def main():
    # Single-instance mutex on Windows
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            name = "Local\\WhisperToggleV2"
            handle = kernel32.CreateMutexW(None, False, name)
            last = kernel32.GetLastError()
            # ERROR_ALREADY_EXISTS = 183
            if last == 183:
                log.warning("another instance is running")
                return
            _ = handle
        except Exception:
            pass

    TrayApp().run()


if __name__ == "__main__":
    main()
