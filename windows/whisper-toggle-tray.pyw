"""
whisper-toggle-tray.pyw — Whisper Toggle system tray application

All-in-one: starts the Whisper API server, listens for global hotkey,
records audio, transcribes via local API, pastes into focused window.

Lives in the system tray. Green = ready, red = recording, yellow = processing.
"""

import subprocess
import threading
import time
import sys
import os
import shutil
import tempfile
from pathlib import Path

import keyboard
import sounddevice as sd
import soundfile as sf
import numpy as np
import requests
import pyperclip
import pystray
from PIL import Image, ImageDraw

# ── Paths ───────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
PYTHON_DIR = APP_DIR / "python"
PYTHON_EXE = PYTHON_DIR / "python.exe"
API_SCRIPT = APP_DIR / "app.py"
WORK_DIR = Path(tempfile.gettempdir()) / "whisper-toggle"

# ── Config ──────────────────────────────────────────────────────────────────
API_HOST = "127.0.0.1"
API_PORT = 8788
API_URL = f"http://{API_HOST}:{API_PORT}"
TRANSCRIBE_URL = f"{API_URL}/v1/audio/transcriptions"
HEALTH_URL = f"{API_URL}/health"
HOTKEY = "ctrl+`"
SAMPLE_RATE = 16000
CHANNELS = 1

# ── Icon colors ─────────────────────────────────────────────────────────────
COLOR_IDLE = (76, 175, 80)       # green
COLOR_RECORDING = (244, 67, 54)  # red
COLOR_PROCESSING = (255, 193, 7) # amber
COLOR_STARTING = (66, 165, 245)  # blue
COLOR_ERROR = (158, 158, 158)    # gray


def make_icon(color, ring=False):
    """Draw a filled circle tray icon. Ring = recording pulse effect."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if ring:
        draw.ellipse([2, 2, 62, 62], outline=color, width=4)
        draw.ellipse([12, 12, 52, 52], fill=color)
    else:
        draw.ellipse([6, 6, 58, 58], fill=color)
    return img


def detect_gpu():
    """Check if NVIDIA GPU is available."""
    return shutil.which("nvidia-smi") is not None


class WhisperToggle:
    def __init__(self):
        self.api_process = None
        self.recording = False
        self.stream = None
        self.audio_chunks = []
        self.rec_lock = threading.Lock()
        self.state = "starting"
        self.tray = None
        self.has_gpu = detect_gpu()
        self.status_text = "Starting..."

    # ── Tray icon ───────────────────────────────────────────────────────
    def _update_tray(self, state, status):
        self.state = state
        self.status_text = status
        if self.tray:
            colors = {
                "idle": COLOR_IDLE,
                "recording": COLOR_RECORDING,
                "processing": COLOR_PROCESSING,
                "starting": COLOR_STARTING,
                "error": COLOR_ERROR,
            }
            color = colors.get(state, COLOR_ERROR)
            self.tray.icon = make_icon(color, ring=(state == "recording"))
            self.tray.title = f"Whisper Toggle — {status}"

    def _get_status_label(self, _=None):
        return self.status_text

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Whisper Toggle", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: self.status_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: f"Hotkey: {HOTKEY}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda _: f"GPU: {'CUDA' if self.has_gpu else 'CPU'}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart API", self._on_restart_api),
            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── API server management ───────────────────────────────────────────
    def start_api(self):
        """Start the Whisper API as a subprocess."""
        # Check if API is already running (another instance, or manual start)
        if self._api_healthy():
            self._update_tray("idle", f"Ready ({HOTKEY})")
            return True

        if not PYTHON_EXE.exists():
            # Fall back to system Python
            python = shutil.which("python") or shutil.which("python3")
            if not python:
                self._update_tray("error", "Python not found")
                return False
        else:
            python = str(PYTHON_EXE)

        env = os.environ.copy()
        env["WHISPER_API_DEFAULT_MODEL"] = "small.en"
        env["WHISPER_API_DEVICE"] = "cuda" if self.has_gpu else "cpu"
        env["WHISPER_API_COMPUTE_TYPE"] = "int8" if self.has_gpu else "float32"
        env["WHISPER_API_LANGUAGE"] = "en"

        self._update_tray("starting", "Starting API...")

        try:
            self.api_process = subprocess.Popen(
                [python, "-m", "uvicorn", "app:app",
                 "--host", API_HOST, "--port", str(API_PORT)],
                cwd=str(APP_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            self._update_tray("error", f"API failed: {e}")
            return False

        # Wait for health (model download + load can take a while on first run)
        self._update_tray("starting", "Loading model...")
        for i in range(120):  # 2 minutes max
            if self.api_process.poll() is not None:
                # Process died — retry with CPU if we tried CUDA
                if self.has_gpu and env["WHISPER_API_DEVICE"] == "cuda":
                    self.has_gpu = False
                    return self.start_api()
                self._update_tray("error", "API crashed on startup")
                return False
            if self._api_healthy():
                self._update_tray("idle", f"Ready ({HOTKEY})")
                return True
            time.sleep(1)

        self._update_tray("error", "API timeout")
        return False

    def stop_api(self):
        if self.api_process and self.api_process.poll() is None:
            self.api_process.terminate()
            try:
                self.api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.api_process.kill()
        self.api_process = None

    def _api_healthy(self):
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _on_restart_api(self, icon=None, item=None):
        threading.Thread(target=self._restart_api_thread, daemon=True).start()

    def _restart_api_thread(self):
        self.stop_api()
        time.sleep(1)
        self.start_api()

    # ── Audio recording ─────────────────────────────────────────────────
    def _audio_callback(self, indata, frames, time_info, status):
        self.audio_chunks.append(indata.copy())

    def _start_recording(self):
        self.audio_chunks = []
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=self._audio_callback,
        )
        self.stream.start()
        self.recording = True
        self._update_tray("recording", "Recording...")
        self._notify("Recording...")

    def _stop_recording(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.recording = False

        if not self.audio_chunks:
            return None
        return np.concatenate(self.audio_chunks, axis=0)

    # ── Transcription + paste ───────────────────────────────────────────
    def _transcribe_and_paste(self, audio_data):
        self._update_tray("processing", "Processing...")
        self._notify("Processing...")

        wav_path = WORK_DIR / f"run_{int(time.time() * 1000)}.wav"
        sf.write(str(wav_path), audio_data, SAMPLE_RATE, subtype="PCM_16")

        try:
            if wav_path.stat().st_size < 1000:
                self._update_tray("idle", f"Ready ({HOTKEY})")
                self._notify("Too short — ignored")
                return

            with open(wav_path, "rb") as f:
                resp = requests.post(
                    TRANSCRIBE_URL,
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"model": "small.en", "language": "en"},
                    timeout=30,
                )

            if resp.status_code != 200:
                self._update_tray("error", f"API error: HTTP {resp.status_code}")
                return

            text = resp.json().get("text", "").strip()

            if not text:
                self._update_tray("idle", f"Ready ({HOTKEY})")
                self._notify("Nothing detected")
                return

            pyperclip.copy(text)
            time.sleep(0.05)
            keyboard.send("ctrl+v")

            preview = text[:50] + ("..." if len(text) > 50 else "")
            self._update_tray("idle", f"Ready ({HOTKEY})")
            self._notify(f"{preview}  ({len(text)} chars)")

        except requests.exceptions.ConnectionError:
            self._update_tray("error", "API not responding")
            self._notify("API connection lost")
        except Exception as e:
            self._update_tray("error", str(e)[:40])
        finally:
            try:
                wav_path.unlink()
            except Exception:
                pass

    # ── Toggle handler ──────────────────────────────────────────────────
    def toggle(self):
        if not self.rec_lock.acquire(blocking=False):
            return

        try:
            if self.recording:
                audio_data = self._stop_recording()
                if audio_data is None:
                    self._update_tray("idle", f"Ready ({HOTKEY})")
                    self._notify("No audio captured")
                    return
                self.rec_lock.release()
                self._transcribe_and_paste(audio_data)
                return
            else:
                if self.state == "error" or self.state == "starting":
                    self._notify("API not ready")
                    return
                self._start_recording()
        finally:
            if self.rec_lock.locked():
                try:
                    self.rec_lock.release()
                except RuntimeError:
                    pass

    # ── Notifications ───────────────────────────────────────────────────
    def _notify(self, msg):
        if self.tray:
            try:
                self.tray.notify(msg, "Whisper Toggle")
            except Exception:
                pass

    # ── Lifecycle ───────────────────────────────────────────────────────
    def _on_quit(self, icon=None, item=None):
        if self.recording and self.stream:
            self.stream.stop()
            self.stream.close()
        keyboard.unhook_all()
        self.stop_api()
        if self.tray:
            self.tray.stop()

    def _startup(self, icon):
        """Runs in a thread after tray.run() starts the message loop."""
        self.start_api()
        keyboard.add_hotkey(HOTKEY, self.toggle, suppress=True)

    def run(self):
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        self.tray = pystray.Icon(
            "whisper-toggle",
            make_icon(COLOR_STARTING),
            "Whisper Toggle — Starting...",
            self._build_menu(),
        )

        # start_api + hotkey registration happen in a background thread
        # so the tray icon appears immediately
        self.tray.run(setup=self._startup)


if __name__ == "__main__":
    app = WhisperToggle()
    app.run()
