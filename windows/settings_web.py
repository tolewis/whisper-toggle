"""Modern Settings window rendered with pywebview (native OS WebView2 on Win11).

Replaces the old tkinter settings. The UI is HTML/CSS/JS; Python exposes a small
bridge (``Api``) that reads/writes the same config.json and drops the same
reload/quit signal files the tray already watches, so nothing else has to change.

Launch: ``pythonw settings_web.py`` (the tray spawns it as a subprocess).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

# Make the shared package importable when run as a loose script from the install
# dir (same pattern the tray uses).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whisper_toggle.config import (  # noqa: E402
    AppConfig,
    STREAM_ENGINES,
    UNSUPPORTED_HOTKEYS,
    app_data_dir,
    default_config_path,
    load_config,
    save_config,
)
from whisper_toggle.logging_setup import setup_logging  # noqa: E402

log = setup_logging("whisper-toggle.settings")

RUNTIME_URL = "http://127.0.0.1:8788/v1/runtime"

HOTKEY_CHOICES = ["ctrl+`", "ctrl+shift+h", "f9"]
DEVICE_CHOICES = ["auto", "cuda", "cpu", "vulkan"]
MODEL_CHOICES = ["", "tiny.en", "base.en", "small.en", "medium.en"]
STREAM_ENGINE_CHOICES = ["sherpa", "whisper_streaming"]


def _write_signal(name: str) -> None:
    path = app_data_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def fetch_runtime() -> dict:
    try:
        with urllib.request.urlopen(RUNTIME_URL, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


class Api:
    """JS <-> Python bridge exposed to the webview as ``window.pywebview.api``."""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or default_config_path()

    def get_state(self) -> dict:
        cfg = load_config(self.config_path)
        rt = fetch_runtime()
        ready = bool(rt.get("ok"))
        return {
            "config": {
                "hotkey": cfg.hotkey,
                "device_override": cfg.device_override,
                "model": cfg.model,
                "streaming": cfg.streaming,
                "live_partials": cfg.live_partials,
                "stream_engine": cfg.stream_engine,
                "hybrid_final_correct": cfg.hybrid_final_correct,
                "autostart": cfg.autostart,
                "audible_cues": cfg.audible_cues,
                "partial_debounce_ms": cfg.partial_debounce_ms,
                "hardware_catchup_ms": cfg.hardware_catchup_ms,
            },
            "runtime": {
                "ready": ready,
                "version": rt.get("version", ""),
                "device": rt.get("device", ""),
                "model": rt.get("model", ""),
            },
            "choices": {
                "hotkey": HOTKEY_CHOICES,
                "device": DEVICE_CHOICES,
                "model": MODEL_CHOICES,
                "stream_engine": STREAM_ENGINE_CHOICES,
            },
        }

    def save(self, payload: dict) -> dict:
        try:
            cfg = load_config(self.config_path)
            hk = str(payload.get("hotkey", cfg.hotkey)).strip().lower()
            if hk and hk not in UNSUPPORTED_HOTKEYS:
                cfg.hotkey = hk
            cfg.device_override = str(payload.get("device_override", cfg.device_override))
            cfg.model = str(payload.get("model", cfg.model))
            cfg.streaming = bool(payload.get("streaming", cfg.streaming))
            cfg.live_partials = bool(payload.get("live_partials", cfg.live_partials))
            stream_engine = str(payload.get("stream_engine", cfg.stream_engine)).strip().lower()
            cfg.stream_engine = stream_engine if stream_engine in STREAM_ENGINES else "sherpa"
            cfg.hybrid_final_correct = bool(
                payload.get("hybrid_final_correct", cfg.hybrid_final_correct)
            )
            cfg.autostart = bool(payload.get("autostart", cfg.autostart))
            cfg.audible_cues = bool(payload.get("audible_cues", cfg.audible_cues))
            cfg.partial_debounce_ms = int(payload.get("partial_debounce_ms", cfg.partial_debounce_ms))
            cfg.hardware_catchup_ms = int(payload.get("hardware_catchup_ms", cfg.hardware_catchup_ms))
            save_config(cfg, self.config_path)
            _write_signal("reload.signal")
            log.info("settings saved: hotkey=%s streaming=%s cues=%s",
                     cfg.hotkey, cfg.streaming, cfg.audible_cues)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            log.exception("settings save failed")
            return {"ok": False, "error": str(exc)}

    def open_logs(self) -> None:
        try:
            os.startfile(str(app_data_dir() / "logs"))  # type: ignore[attr-defined]
        except Exception:
            log.debug("open_logs failed", exc_info=True)

    def restart_engine(self) -> None:
        _write_signal("restart.signal")

    def close_window(self) -> None:
        try:
            import webview
            for w in webview.windows:
                w.destroy()
        except Exception:
            log.debug("close failed", exc_info=True)


def _tk_fallback() -> None:
    """Last resort so Settings always opens even if the webview backend is
    missing/broken on this machine."""
    try:
        import settings_gui  # loose script in the same dir (on sys.path)

        settings_gui.main([])
    except Exception:
        log.exception("tk settings fallback failed")


def main() -> None:
    try:
        import webview
    except Exception:
        log.exception("pywebview unavailable - falling back to tk settings")
        _tk_fallback()
        return

    html = (Path(__file__).resolve().parent / "settings_web.html").read_text(encoding="utf-8")
    api = Api()
    try:
        webview.create_window(
            "Whisper Toggle — Settings",
            html=html,
            js_api=api,
            width=900,
            height=840,
            resizable=False,
            background_color="#14161a",
        )
        webview.start()
    except Exception:
        log.exception("webview failed to start - falling back to tk settings")
        _tk_fallback()


if __name__ == "__main__":
    main()
