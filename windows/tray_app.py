#!/usr/bin/env python3
"""Whisper Toggle v2 - Windows tray product.

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
from whisper_toggle.live_overlay import LivePreviewOverlay
from whisper_toggle.logging_setup import setup_logging
from whisper_toggle.paste import LiveTextSession
from whisper_toggle.status_messages import startup_loading_notice
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
        self._backup_hotkey_handle = None
        self._win_owner = None
        self._toggle_lock = threading.Lock()
        self._recording = False
        self._pcm_buffer = bytearray()
        self._partial_timer: threading.Timer | None = None
        self._partial_lock = threading.Lock()
        self.preview = LivePreviewOverlay()
        self._preview_stop = threading.Event()
        self._preview_thread: threading.Thread | None = None
        self._preview_batch_lock = threading.Lock()
        self._preview_confirmed = ""
        self._owns_api = False
        self._reload_path = app_data_dir() / "reload.signal"
        self._quit_path = app_data_dir() / "quit.signal"
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
                self.tray.title = f"Whisper Toggle - {message}"
                # Keep icon marked visible (Win11 overflow still may hide it)
                self.tray.visible = True
            except Exception as exc:  # noqa: BLE001
                log.debug("tray update failed: %s", exc)
        # Hotkey ownership follows readiness
        ready = state == State.IDLE and self.api.is_healthy()
        self._set_hotkey_ownership(ready)

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Whisper Toggle v2.0.3", None, enabled=False),
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
            pystray.MenuItem("Exit Whisper Toggle", self._on_quit),
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
        env["WHISPER_API_VERSION"] = "2.0.3"
        env["WHISPER_API_PRELOAD"] = "1"
        env["WHISPER_API_REQUIRE_SMOKE"] = "1"

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
        self._notify(
            startup_loading_notice(
                model=self.model,
                device=device,
                hotkey=self.cfg.hotkey,
            )
        )
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
        self._preview_confirmed = ""
        self.live = None
        self._set_state(State.RECORDING, f"Recording... ({self.cfg.hotkey} to stop)")

        def on_pcm(data: bytes):
            self._pcm_buffer.extend(data)
            live = self.live
            if live is not None and not live.failed:
                live.send_pcm(data)

        # Start the microphone before any toast or streaming setup. Windows
        # notifications and WebSocket connects can be slow; they must never delay
        # the first captured word.
        self.mic = MicRecorder(on_pcm=on_pcm)
        self.mic.start()
        self._recording = True
        log.info("recording started")

        if self.cfg.live_partials:
            self._start_preview("Listening…")

        # Streaming is optional. Start it in the background and backfill the PCM
        # already captured so semi-live mode does not delay recording start. If
        # the streaming stack fails, a slower batch-preview loop keeps the live
        # proofing UI useful without touching the focused app.
        if self.cfg.live_partials and self.cfg.streaming:
            threading.Thread(target=self._start_live_stream, daemon=True).start()
        elif self.cfg.live_partials:
            self._start_batch_preview_loop()

    def _start_live_stream(self) -> None:
        def on_err(m: str):
            log.error("stream: %s", m)
            if self._recording and self.cfg.live_partials:
                self._start_batch_preview_loop()

        stream = LiveStreamSession(
            stream_url=STREAM_URL,
            model=self.model,
            language="en",
            on_partial=self._on_partial,
            on_confirmed=self._on_confirmed,
            on_final=self._on_final,
            on_error=on_err,
            open_timeout=3.0,
        )
        if not stream.start():
            log.warning("stream connect failed; will batch at stop")
            return
        if not self._recording:
            try:
                stream.end()
            except Exception:
                pass
            return
        self.live = stream
        backlog = bytes(self._pcm_buffer)
        if backlog:
            stream.send_pcm(backlog)
        log.info("live stream connected")

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
        if self.cfg.live_partials:
            self._preview_stop.set()
            self._update_preview("Processing…")

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
            log.warning("stream incomplete - batch fallback")

        if not pcm or len(pcm) < 1000:
            if self.cfg.live_partials:
                self._stop_preview(delay=0.5)
            self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
            self._notify("Too short - ignored")
            return
        try:
            wav = MicRecorder().to_wav_bytes(pcm)
            with self._preview_batch_lock:
                text = self.api.batch(wav)
            if text:
                log.info("batch text (%d chars): %s", len(text), text[:120])
                # Always inject via clipboard+Ctrl+V (works in PowerShell/Terminal)
                try:
                    self.kb.inject_text(text)
                except Exception:
                    log.exception("inject_text failed; trying session.finalize")
                    try:
                        self.session.finalize(text)
                    except Exception:
                        log.exception("finalize also failed")
                        self._notify("Transcribed but paste failed - text on clipboard")
                        try:
                            import pyperclip

                            pyperclip.copy(text)
                        except Exception:
                            pass
                        self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")
                        return
                self.session.clear()
                if self.cfg.live_partials:
                    self._update_preview(text)
                    self._stop_preview(delay=1.5)
                self._notify(text[:60])
            else:
                if self.cfg.live_partials:
                    self._stop_preview(delay=0.5)
                self._notify("Nothing detected")
        except Exception as exc:  # noqa: BLE001
            log.exception("batch failed")
            if self.cfg.live_partials:
                self._stop_preview(delay=0.5)
            self._set_state(State.ERROR, str(exc)[:60])
            return
        self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")

    def _on_partial(self, text: str) -> None:
        if not self.cfg.live_partials:
            return
        delay = max(0, int(self.cfg.partial_debounce_ms)) / 1000.0

        def apply():
            preview = self._join_preview(self._preview_confirmed, text)
            self._update_preview(preview or "Listening…")

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
        text = (text or "").strip()
        if not text:
            return
        if text.startswith(self._preview_confirmed):
            self._preview_confirmed = text
        else:
            self._preview_confirmed = self._join_preview(self._preview_confirmed, text)
        self._update_preview(self._preview_confirmed)

    def _on_final(self, text: str) -> None:
        with self._partial_lock:
            if self._partial_timer is not None:
                self._partial_timer.cancel()
                self._partial_timer = None
        try:
            self.session.finalize(text)
            if text:
                self._update_preview(text)
                self._stop_preview(delay=1.5)
                self._notify(text[:60])
            else:
                self._stop_preview(delay=0.5)
        except Exception as exc:  # noqa: BLE001
            log.error("final type failed: %s", exc)
            self._stop_preview(delay=0.5)
        self._set_state(State.IDLE, f"Ready ({self.cfg.hotkey})")

    def _join_preview(self, left: str, right: str) -> str:
        left = (left or "").strip()
        right = (right or "").strip()
        if not left:
            return right
        if not right:
            return left
        if right.startswith(left):
            return right
        if left.endswith((" ", "\n")) or right.startswith((" ", "\n")):
            return left + right
        return left + " " + right

    def _start_preview(self, text: str = "Listening…") -> None:
        self._preview_stop.clear()
        self.preview.start()
        self._update_preview(text)

    def _update_preview(self, text: str) -> None:
        try:
            self.preview.update(text)
        except Exception as exc:  # noqa: BLE001
            log.debug("preview update failed: %s", exc)

    def _stop_preview(self, delay: float = 0.0) -> None:
        self._preview_stop.set()

        def stop() -> None:
            if delay > 0:
                time.sleep(delay)
            try:
                self.preview.stop()
            except Exception:
                pass

        threading.Thread(target=stop, daemon=True).start()

    def _start_batch_preview_loop(self) -> None:
        if self._preview_thread and self._preview_thread.is_alive():
            return
        self._preview_thread = threading.Thread(target=self._batch_preview_loop, daemon=True)
        self._preview_thread.start()

    def _batch_preview_loop(self) -> None:
        # A reliable, slower fallback for Windows when the streaming websocket
        # stack fails. It updates the overlay only; final insertion still uses
        # the normal batch result on stop.
        interval = max(3.0, int(self.cfg.partial_debounce_ms or 0) / 1000.0)
        min_bytes = 16000 * 2  # 1s of 16 kHz mono int16
        last_text = ""
        last_len = 0
        while not self._preview_stop.wait(interval):
            if not self._recording:
                return
            pcm = bytes(self._pcm_buffer)
            if len(pcm) < min_bytes or len(pcm) == last_len:
                continue
            last_len = len(pcm)
            if not self._preview_batch_lock.acquire(blocking=False):
                continue
            try:
                wav = MicRecorder().to_wav_bytes(pcm)
                text = self.api.batch(wav)
                if text and text != last_text and self._recording:
                    last_text = text
                    self._update_preview(text)
            except Exception as exc:  # noqa: BLE001
                log.debug("batch preview failed: %s", exc)
                return
            finally:
                self._preview_batch_lock.release()

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

    def _ensure_backup_hotkey(self) -> None:
        """Keep Ctrl+Shift+H available whenever the primary hotkey is different."""
        if (self.cfg.hotkey or "").lower() == "ctrl+shift+h":
            if self._backup_hotkey_handle is not None:
                try:
                    self.kb.remove_hotkey(self._backup_hotkey_handle)
                except Exception:
                    pass
                self._backup_hotkey_handle = None
            return
        if self._backup_hotkey_handle is not None:
            return
        try:
            self._backup_hotkey_handle = self.kb.add_hotkey("ctrl+shift+h", self.toggle, suppress=True)
            log.info("backup hotkey bound: ctrl+shift+h")
        except Exception as exc:  # noqa: BLE001
            log.warning("backup hotkey failed: %s", exc)

    def _bind_hotkey(self) -> None:
        # Tear down previous
        if self._hotkey_handle is not None:
            try:
                self.kb.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
            self._hotkey_handle = None
        if self._backup_hotkey_handle is not None:
            try:
                self.kb.remove_hotkey(self._backup_hotkey_handle)
            except Exception:
                pass
            self._backup_hotkey_handle = None
        if self._win_owner is not None:
            try:
                self._win_owner.stop()
            except Exception:
                pass
            self._win_owner = None

        hk = (self.cfg.hotkey or "ctrl+shift+h").lower().replace("windows+", "win+")
        self.cfg.hotkey = hk

        if self._is_win_hotkey():
            try:
                from whisper_toggle.win_hotkey import WinHotkeyOwner

                self._win_owner = WinHotkeyOwner(on_hotkey=self.toggle)
                if self._win_owner.start():
                    # Enable only when ready
                    self._set_hotkey_ownership(self.state == State.IDLE and self.api.is_healthy())
                    log.info("Win+H native ownership hook ready")
                    self._ensure_backup_hotkey()
                    return
                log.error("Win+H native hook failed to start")
            except Exception as exc:  # noqa: BLE001
                log.exception("Win+H owner failed: %s", exc)

            # Do not fall back to a keyboard-lib Win+H hook: Windows 11 often
            # lets the focused app receive the trailing "h" and/or opens Voice
            # Typing anyway. If the native owner is unavailable, keep Windows'
            # default behavior intact and switch Whisper Toggle to Ctrl+Shift+H.
            try:
                self._hotkey_handle = self.kb.add_hotkey("ctrl+shift+h", self.toggle, suppress=True)
                self.cfg.hotkey = "ctrl+shift+h"
                save_config(self.cfg)
                self._set_state(State.IDLE, "Ready (ctrl+shift+h) - Win+H unavailable")
                self._notify("Win+H busy - using Ctrl+Shift+H")
            except Exception as exc2:  # noqa: BLE001
                self._set_state(State.ERROR, f"Hotkey failed: {exc2}")
            return

        # Non-Win hotkeys via keyboard package
        try:
            self._hotkey_handle = self.kb.add_hotkey(
                hk, self.toggle, suppress=bool(self.cfg.suppress_hotkey)
            )
            log.info("hotkey bound: %s", hk)
            self._ensure_backup_hotkey()
        except Exception as exc:  # noqa: BLE001
            log.error("hotkey bind failed for %s: %s", hk, exc)
            self._set_state(State.ERROR, f"Hotkey failed: {exc}")

    # ── Menu actions ────────────────────────────────────────────────────
    def _on_settings(self, icon=None, item=None):
        """Launch settings as a separate process - Tk is not thread-safe."""
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
            self._win_owner = None
        if self._backup_hotkey_handle is not None:
            try:
                self.kb.remove_hotkey(self._backup_hotkey_handle)
            except Exception:
                pass
            self._backup_hotkey_handle = None
        try:
            self.kb.unhook_all()
        except Exception:
            pass
        self.stop_api()
        if self.tray:
            self.tray.stop()

    def _notify(self, msg: str) -> None:
        # Toast delivery on Windows can block or lag behind reality. Never let a
        # notification delay recording, stopping, or pasting.
        log.info("notify: %s", msg)
        if not self.tray:
            return

        def _send():
            try:
                self.tray.notify(msg, "Whisper Toggle")
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True).start()

    def _poll_config_reload(self) -> None:
        """Pick up settings saved by the settings process."""
        while not self._poll_stop.wait(1.0):
            try:
                if self._quit_path.exists():
                    try:
                        self._quit_path.unlink()
                    except OSError:
                        pass
                    log.info("quit requested by settings")
                    self._on_quit()
                    return

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
        # Always keep a reliable backup hotkey so the user is never stuck.
        self._ensure_backup_hotkey()
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
            "Whisper Toggle - Starting...",
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
