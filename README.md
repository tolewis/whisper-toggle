# Whisper-Toggle

Local push-to-talk voice dictation. Press a hotkey to start recording, press again to transcribe and auto-paste into the focused window.

Runs a warm [faster-whisper](https://github.com/SYSTRAN/faster-whisper) API on localhost — no cold-start penalty, no cloud dependency, no API keys.

**Platforms:** Linux (GNOME/PipeWire) · Windows 11

## How It Works

```
Hotkey press 1  →  mic starts recording (16kHz mono WAV)
Hotkey press 2  →  recording stops → WAV sent to local API → text pasted
```

Typical latency for a 10-second clip: **~1.5–2.5s** (stop → transcribe → paste).

## Architecture

```
┌──────────────┐     ┌────────────────────┐     ┌─────────────────────────┐
│  Hotkey       │     │  dictate-toggle    │     │  whisper-api (:8788)    │
│  (OS-level)   │────▶│  .sh (Linux)       │────▶│  faster-whisper         │
│               │     │  .py (Windows)     │     │  small.en · CUDA int8   │
└──────────────┘     │  record → POST     │     │  model warm in VRAM     │
                      │  → clipboard       │     └─────────────────────────┘
                      │  → auto-paste      │
                      └────────────────────┘
```

The API server (`app.py`) is shared across platforms. Platform-specific scripts handle recording, hotkey binding, and paste.

## Quick Start

### Linux

```bash
# Install deps, deploy service, bind hotkeys
# Full guide: docs/linux-setup.md
sudo apt install xdotool xclip x11-utils libnotify-bin curl
cp linux/dictate-toggle.sh ~/bin/ && chmod +x ~/bin/dictate-toggle.sh
```

Default hotkeys: **Super+H** and **Ctrl+\`**

### Windows

```powershell
# Run the installer (creates venv, installs deps, deploys files)
# Full guide: docs/windows-setup.md
powershell -ExecutionPolicy Bypass -File windows\install.ps1
```

Default hotkey: **Ctrl+\`**

## Repo Structure

```
Whisper-Toggle/
├── README.md                  This file
├── app.py                     Shared API server (OpenAI-compatible)
├── linux/
│   ├── dictate-toggle.sh      Linux entry point (bash, pw-record, xdotool)
│   └── whisper-api.service    systemd user unit
├── windows/
│   ├── dictate-toggle.py      Windows entry point (keyboard, sounddevice)
│   ├── requirements.txt       Python dependencies
│   ├── start-api.bat          API server launcher
│   └── install.ps1            Automated installer
└── docs/
    ├── linux-setup.md         Full Linux setup guide
    └── windows-setup.md       Full Windows setup guide
```

## Platform Comparison

| Component | Linux | Windows |
|-----------|-------|---------|
| Recording | `pw-record` (PipeWire) | `sounddevice` (PortAudio/WASAPI) |
| Hotkey | GNOME custom keybinding | `keyboard` library (global hook) |
| Paste | `xdotool key ctrl+v` | `keyboard.send('ctrl+v')` |
| Clipboard | `xclip` | `pyperclip` |
| Notifications | `notify-send` | `winotify` (toast) |
| API persistence | systemd user unit | `start-api.bat` (manual or Task Scheduler) |
| Terminal detection | Yes (xprop WM_CLASS) | No (Ctrl+V works everywhere) |

## API Server

Both platforms share `app.py` — a FastAPI wrapper around faster-whisper that implements the OpenAI `/v1/audio/transcriptions` endpoint.

```bash
# Health check
curl http://127.0.0.1:8788/health

# Manual transcription
curl -X POST http://127.0.0.1:8788/v1/audio/transcriptions \
  -F "file=@audio.wav" -F "model=small.en" -F "language=en"
```

Environment variables (set in systemd unit or `start-api.bat`):

| Variable | Default | Options |
|----------|---------|---------|
| `WHISPER_API_DEFAULT_MODEL` | `small.en` | `tiny.en`, `base.en`, `small.en`, `medium.en` |
| `WHISPER_API_DEVICE` | `cuda` | `cuda`, `cpu` |
| `WHISPER_API_COMPUTE_TYPE` | `int8` | `int8`, `float16`, `float32` |
| `WHISPER_API_LANGUAGE` | `en` | Any Whisper-supported language code |

## Version History

| Version | Date | What changed |
|---------|------|-------------|
| **v1.0** | 2026-02-23 | Full rewrite. Warm API, PipeWire, auto-paste, Windows support. |
| v0.3 | 2025 | whisper-hotkey.sh + whisper-transcribe.py (cold model, broken) |
| v0.2 | 2025 | whisper_toggle.sh variants (ALSA, no API) |
| v0.1 | 2025 | whisper_clip.sh (first prototype) |

## License

MIT
