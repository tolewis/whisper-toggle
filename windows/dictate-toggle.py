"""
dictate-toggle.py — Push-to-talk voice dictation for Windows

Press Ctrl+` to start recording, press again to transcribe and auto-paste.
Requires whisper-api running on localhost:8788.

Usage:
    python dictate-toggle.py              # Default hotkey: Ctrl+`
    python dictate-toggle.py --hotkey "ctrl+shift+h"   # Custom hotkey
"""

import sys
import time
import threading
import tempfile
import argparse
from pathlib import Path

import keyboard
import sounddevice as sd
import soundfile as sf
import numpy as np
import requests
import pyperclip

# ── Config ──────────────────────────────────────────────────────────────────
WHISPER_API = "http://127.0.0.1:8788/v1/audio/transcriptions"
DEFAULT_HOTKEY = "ctrl+`"
SAMPLE_RATE = 16000
CHANNELS = 1
WORK_DIR = Path(tempfile.gettempdir()) / "dictate-toggle"

# ── State ───────────────────────────────────────────────────────────────────
_recording = False
_stream = None
_audio_chunks: list[np.ndarray] = []
_rec_lock = threading.Lock()

# ── Notifications ───────────────────────────────────────────────────────────
_notifier = None


def _init_notifier():
    """Try to load winotify for toast notifications."""
    global _notifier
    try:
        import winotify
        _notifier = winotify
    except ImportError:
        pass


def notify(msg, title="Dictation"):
    """Windows toast notification with console fallback."""
    print(f"  [{title}] {msg}")
    if _notifier is None:
        return
    try:
        n = _notifier.Notification(
            app_id="Whisper Toggle",
            title=title,
            msg=msg,
        )
        n.show()
    except Exception:
        pass


# ── Audio callback ──────────────────────────────────────────────────────────
def _audio_callback(indata, frames, time_info, status):
    """Append each audio chunk from sounddevice."""
    _audio_chunks.append(indata.copy())


# ── Recording control ──────────────────────────────────────────────────────
def _start_recording():
    global _recording, _stream, _audio_chunks
    _audio_chunks = []
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=_audio_callback,
    )
    _stream.start()
    _recording = True
    notify("Recording...")


def _stop_recording() -> np.ndarray | None:
    """Stop recording and return audio data."""
    global _recording, _stream

    if _stream is not None:
        _stream.stop()
        _stream.close()
        _stream = None
    _recording = False

    if not _audio_chunks:
        return None
    return np.concatenate(_audio_chunks, axis=0)


# ── Transcription + paste ──────────────────────────────────────────────────
def _transcribe_and_paste(audio_data: np.ndarray):
    """Save WAV, POST to API, copy to clipboard, simulate Ctrl+V."""
    notify("Processing...")

    # Save to temp WAV
    wav_path = WORK_DIR / f"run_{int(time.time() * 1000)}.wav"
    sf.write(str(wav_path), audio_data, SAMPLE_RATE, subtype="PCM_16")

    try:
        # Skip tiny files
        if wav_path.stat().st_size < 1000:
            notify("Recording too short — ignored")
            return

        # POST to warm API
        with open(wav_path, "rb") as f:
            resp = requests.post(
                WHISPER_API,
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "small.en", "language": "en"},
                timeout=30,
            )

        if resp.status_code != 200:
            notify(f"API error: HTTP {resp.status_code}")
            return

        text = resp.json().get("text", "").strip()

        if not text:
            notify("Nothing detected")
            return

        # Clipboard + auto-paste
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.send("ctrl+v")

        preview = text[:60] + ("..." if len(text) > 60 else "")
        notify(f"{preview}  ({len(text)} chars)")

    except requests.exceptions.ConnectionError:
        notify("API not running — start whisper-api first")
    except Exception as e:
        notify(f"Error: {e}")
    finally:
        try:
            wav_path.unlink()
        except Exception:
            pass


# ── Toggle handler ──────────────────────────────────────────────────────────
def toggle():
    """Hotkey callback — start recording or stop + transcribe."""
    if not _rec_lock.acquire(blocking=False):
        return  # Already processing a toggle

    try:
        if _recording:
            audio_data = _stop_recording()
            if audio_data is None:
                notify("No audio captured")
                return
            # Release lock before slow transcription so new recording can start
            _rec_lock.release()
            _transcribe_and_paste(audio_data)
            return  # Don't release lock again
        else:
            _start_recording()
    finally:
        # Release if we haven't already (early-released for transcription path)
        if _rec_lock.locked():
            try:
                _rec_lock.release()
            except RuntimeError:
                pass


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Voice dictation toggle")
    parser.add_argument(
        "--hotkey",
        default=DEFAULT_HOTKEY,
        help=f"Global hotkey (default: {DEFAULT_HOTKEY})",
    )
    parser.add_argument(
        "--api",
        default=WHISPER_API,
        help=f"Whisper API URL (default: {WHISPER_API})",
    )
    args = parser.parse_args()

    global WHISPER_API
    WHISPER_API = args.api

    _init_notifier()
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Check API is reachable
    try:
        r = requests.get(args.api.replace("/v1/audio/transcriptions", "/health"), timeout=3)
        if r.status_code == 200:
            print(f"  API: connected ({args.api})")
        else:
            print(f"  API: responded but HTTP {r.status_code} — may not work")
    except requests.exceptions.ConnectionError:
        print(f"  API: NOT reachable at {args.api}")
        print(f"       Start the API first: start-api.bat")
        print()

    keyboard.add_hotkey(args.hotkey, toggle, suppress=True)
    print(f"  Hotkey: {args.hotkey}")
    print()
    print("  Whisper Toggle running. Press Ctrl+C to exit.")
    print()

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        if _recording and _stream:
            _stream.stop()
            _stream.close()
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
