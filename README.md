# Whisper Toggle

```
 __        ___     _                       _____                 _
 \ \      / / |__ (_)___ _ __   ___ _ __  |_   _|__   __ _  __ _| | ___
  \ \ /\ / /| '_ \| / __| '_ \ / _ \ '__|  | |/ _ \ / _` |/ _` | |/ _ \
   \ V  V / | | | | \__ \ |_) |  __/ |     | | (_) | (_| | (_| | |  __/
    \_/\_/  |_| |_|_|___/ .__/ \___|_|     |_|\___/ \__, |\__, |_|\___|
                         |_|                          |___/ |___/
```

**Talk to your computer. It types for you - private, local, and reliably pasted at the cursor.**

## v2.0

| Platform | Status |
|----------|--------|
| **Windows** | Product tray app + settings GUI + Ctrl+Shift+H default + optional Win+H/live partials |
| **Linux** | Stable batch + streaming (existing) |

### Windows highlights
- **Hotkey: Ctrl+Shift+H by default** - reliable on Windows 11; Win+H is optional after disabling the Windows voice typing launcher
- **Reliable paste** - final text is inserted with clipboard + Ctrl+V after hotkey modifiers are released, which works in Windows Terminal/PowerShell/tmux
- **Optional live partials** - available in Settings, but batch+paste is the safe Windows default
- **Tray icon + settings** - device, model, delays, restart engine, open logs
- **Device auto-select** - NVIDIA CUDA when available, else CPU (Intel Iris path)
- **One installer** - embedded Python, no terminal workflow

### How it feels
```
  Press Ctrl+Shift+H      Listening starts
  Speak naturally         Local Whisper transcribes
  Press Ctrl+Shift+H      Final text pastes into the focused app
```

## Architecture

```
Tray / hotkey  ->  mic PCM  ->  local FastAPI (faster-whisper)
                     |              |
                     |              +- POST /v1/audio/transcriptions  (default batch)
                     |              +- WS   /v1/audio/stream          (optional live)
                     +- clipboard + Ctrl+V injection at focused cursor
```

Shared library: `whisper_toggle/` (device resolver, controller, live paste, icons).

## Develop (server)

```bash
# use the whisper venv
source ~/.venvs/whisper/bin/activate
pip install -r requirements-dev.txt Pillow
PYTHONPATH=. pytest tests/ -q
```

## Build Windows installer (jubiku)

```powershell
cd C:\path\to\Whisper-Toggle
powershell -ExecutionPolicy Bypass -File windows\build-installer.ps1
# → dist\WhisperToggle-Setup-2.0.0.exe
```

Requires Inno Setup 6 on the build host (present on jubiku).

## Linux (stable)

See [docs/linux-setup.md](docs/linux-setup.md). Hotkeys Super+H / Ctrl+`.

## Requirements

- Windows 10/11 or Linux
- Microphone
- NVIDIA GPU recommended; CPU works (slower) - including Intel Iris Xe

## Version History

| Version | Date | What's new |
|---------|------|-----------|
| **2.0.x** | 2026-07 | Windows product: reliable Ctrl+Shift+H default, tray GUI, settings, clipboard paste injection, optional Win+H/live partials |
| **2.0.0** | 2026-07 | Windows product: Win+H, live partials, tray GUI, icon, DeviceResolver, installer 2.0 |
| **1.0** | Feb 2026 | Warm API, auto-paste, Windows + Linux foundations |
| 0.1–0.3 | 2025 | Early prototypes. Archived. |

## License

MIT

---

*Built by [Tim Lewis](https://github.com/tolewis). Transcription powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper).*
