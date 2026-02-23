# Windows Setup — Whisper-Toggle

Tested on Windows 11. Works with NVIDIA GPU (CUDA) or CPU-only.

## Prerequisites

- Windows 11 (Windows 10 should work but untested)
- Python 3.9+ ([python.org](https://www.python.org/downloads/) — check "Add to PATH" during install)
- NVIDIA GPU with CUDA drivers (optional — CPU fallback works, just slower)

## Quick Install

```powershell
git clone https://github.com/tolewis/Whisper-Toggle.git
cd Whisper-Toggle
powershell -ExecutionPolicy Bypass -File windows\install.ps1
```

The installer:
1. Creates a Python venv at `%LOCALAPPDATA%\whisper-venv`
2. Installs all dependencies (faster-whisper, sounddevice, keyboard, etc.)
3. Detects NVIDIA GPU and installs CUDA PyTorch if present
4. Deploys files to `%LOCALAPPDATA%\whisper-toggle`

## Manual Install

If you prefer to install manually or the script doesn't work:

### 1. Create venv

```powershell
python -m venv $env:LOCALAPPDATA\whisper-venv
```

### 2. Install dependencies

```powershell
$pip = "$env:LOCALAPPDATA\whisper-venv\Scripts\pip"

# API server
& $pip install faster-whisper fastapi uvicorn

# CUDA PyTorch (skip if no NVIDIA GPU)
& $pip install torch --index-url https://download.pytorch.org/whl/cu121

# Dictation script
& $pip install keyboard sounddevice soundfile numpy requests pyperclip winotify
```

### 3. Deploy files

```powershell
$appDir = "$env:LOCALAPPDATA\whisper-toggle"
mkdir $appDir -Force
copy app.py $appDir\
copy windows\dictate-toggle.py $appDir\
copy windows\start-api.bat $appDir\
```

## Running

Two terminals needed — one for the API, one for the dictation toggle.

### Terminal 1: Start the API

```powershell
cd $env:LOCALAPPDATA\whisper-toggle
.\start-api.bat
```

Wait for `Uvicorn running on http://127.0.0.1:8788`. First launch downloads the Whisper model (~500MB) and loads it into VRAM. Subsequent starts are fast.

**No NVIDIA GPU?** Edit `start-api.bat` and change `WHISPER_API_DEVICE=cuda` to `WHISPER_API_DEVICE=cpu`. Transcription will be slower (~10-15s for a 10s clip) but works.

### Terminal 2: Start dictation

```powershell
& "$env:LOCALAPPDATA\whisper-venv\Scripts\python" "$env:LOCALAPPDATA\whisper-toggle\dictate-toggle.py"
```

You should see:

```
  API: connected (http://127.0.0.1:8788/v1/audio/transcriptions)
  Hotkey: ctrl+`
  Whisper Toggle running. Press Ctrl+C to exit.
```

### Use it

1. Press **Ctrl+\`** — "Recording..." toast notification
2. Speak
3. Press **Ctrl+\`** again — "Processing..." → text auto-pasted into focused window

## Custom Hotkey

If Ctrl+\` conflicts with another app (VS Code uses it for the terminal):

```powershell
& "$env:LOCALAPPDATA\whisper-venv\Scripts\python" "$env:LOCALAPPDATA\whisper-toggle\dictate-toggle.py" --hotkey "ctrl+shift+h"
```

Other examples: `ctrl+shift+space`, `f9`, `ctrl+alt+v`

## Auto-Start on Boot (Optional)

### API server via Task Scheduler

1. Open Task Scheduler (`taskschd.msc`)
2. Create Basic Task → "Whisper API"
3. Trigger: "When I log on"
4. Action: Start a program
   - Program: `%LOCALAPPDATA%\whisper-venv\Scripts\python.exe`
   - Arguments: `-m uvicorn app:app --host 127.0.0.1 --port 8788`
   - Start in: `%LOCALAPPDATA%\whisper-toggle`
5. Check "Open the Properties dialog" → Settings → uncheck "Stop the task if it runs longer than 3 days"

### Dictation toggle via Startup folder

1. Press Win+R → `shell:startup`
2. Create a shortcut:
   - Target: `%LOCALAPPDATA%\whisper-venv\Scripts\pythonw.exe %LOCALAPPDATA%\whisper-toggle\dictate-toggle.py`
   - Name: "Whisper Toggle"

Using `pythonw.exe` (not `python.exe`) runs it without a console window.

## Troubleshooting

### "API not running"

Make sure Terminal 1 is running `start-api.bat` and shows `Uvicorn running`. Test:

```powershell
curl http://127.0.0.1:8788/health
# → {"ok":true}
```

### "No audio captured"

Check Windows sound settings → Input → correct microphone selected as default.

Test in Python:

```python
import sounddevice as sd
print(sd.query_devices())  # Should show your mic
```

### Hotkey doesn't work

The `keyboard` library uses a low-level hook. Some things that can interfere:

- **Run as Administrator** if the focused app is elevated (e.g., Task Manager)
- **Antivirus** may block keyboard hooks — add an exception for Python
- **Other hotkey apps** (AutoHotKey, etc.) may capture the key first

### CUDA out of memory

```powershell
nvidia-smi  # Check VRAM usage
```

If VRAM is tight, edit `start-api.bat`:
- Change model to `tiny.en` (~1GB VRAM)
- Or switch to `WHISPER_API_DEVICE=cpu`

### Slow transcription on CPU

Expected. CPU mode takes ~10-15s for a 10s clip. For faster results, use a CUDA-capable GPU or a smaller model (`tiny.en`).

## Uninstall

```powershell
# Remove application files
Remove-Item -Recurse "$env:LOCALAPPDATA\whisper-toggle"

# Remove venv
Remove-Item -Recurse "$env:LOCALAPPDATA\whisper-venv"

# Remove Task Scheduler tasks (if created)
# Open taskschd.msc and delete "Whisper API"

# Remove startup shortcut (if created)
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Whisper Toggle.lnk"
```
