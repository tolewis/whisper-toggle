# Whisper-Toggle

Local push-to-talk voice dictation for GNOME/PipeWire desktops. Press a hotkey to start recording, press again to transcribe and auto-paste into the focused window.

Uses a warm [faster-whisper](https://github.com/SYSTRAN/faster-whisper) API server on localhost — no cold-start penalty, no cloud dependency.

## How It Works

```
Hotkey press 1 → pw-record starts capturing mic audio (16kHz mono WAV)
Hotkey press 2 → recording stops → WAV sent to local API → text pasted into focused window
```

Typical latency for a 10-second clip: **~1.5–2.5s** (stop + transcribe + paste).

## Components

| File | Purpose |
|------|---------|
| `dictate-toggle.sh` | Main script — toggle recording, transcribe via API, auto-paste |
| `app.py` | FastAPI server wrapping faster-whisper (OpenAI-compatible endpoint) |
| `whisper-api.service` | systemd user unit to keep the API warm |

## Requirements

- **GNOME** desktop (Wayland or X11)
- **PipeWire** (`pw-record`)
- **NVIDIA GPU** with CUDA (for fast inference; CPU fallback possible)
- Python packages: `faster-whisper`, `fastapi`, `uvicorn`
- System packages: `xdotool`, `xclip`, `xprop`, `notify-send`, `curl`

## Setup

### 1. Install system dependencies

```bash
sudo apt install xdotool xclip x11-utils libnotify-bin curl
```

### 2. Create Python venv and install packages

```bash
python3 -m venv ~/.venvs/whisper
~/.venvs/whisper/bin/pip install faster-whisper fastapi uvicorn
```

### 3. Deploy the API server

```bash
mkdir -p ~/.local/share/whisper-api
cp app.py ~/.local/share/whisper-api/
cp whisper-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whisper-api
```

Verify it's running:

```bash
curl http://127.0.0.1:8788/health
# → {"ok":true}
```

### 4. Install the dictation script

```bash
cp dictate-toggle.sh ~/bin/
chmod +x ~/bin/dictate-toggle.sh
```

### 5. Bind hotkeys (GNOME)

Unbind Super+H from minimize (if needed):

```bash
gsettings set org.gnome.desktop.wm.keybindings minimize "['']"
```

Register custom keybindings:

```bash
# Add keybinding paths
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/', \
    '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/']"

# Super+H
SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
PATH0="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
gsettings set "${SCHEMA}:${PATH0}" name "Dictation Toggle"
gsettings set "${SCHEMA}:${PATH0}" command "$HOME/bin/dictate-toggle.sh"
gsettings set "${SCHEMA}:${PATH0}" binding "<Super>h"

# Ctrl+` (alternative)
PATH1="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"
gsettings set "${SCHEMA}:${PATH1}" name "Dictation Toggle (Alt)"
gsettings set "${SCHEMA}:${PATH1}" command "$HOME/bin/dictate-toggle.sh"
gsettings set "${SCHEMA}:${PATH1}" binding "<Ctrl>grave"
```

## Usage

1. Press **Super+H** (or **Ctrl+\`**) — notification says "Recording..."
2. Speak
3. Press the hotkey again — notification says "Processing..."
4. Transcribed text is pasted into the focused window and copied to clipboard

The script auto-detects terminals (gnome-terminal, kitty, Alacritty, etc.) and uses Ctrl+Shift+V instead of Ctrl+V.

## Configuration

Edit the top of `dictate-toggle.sh` to change:

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_API` | `http://127.0.0.1:8788/v1/audio/transcriptions` | API endpoint |
| `WORK_DIR` | `/tmp/dictate-toggle` | Temp files location |
| `NOTIFY_ID` | `991337` | Fixed notification ID (for replace behavior) |
| `KILL_TIMEOUT` | `1` | Seconds to wait for pw-record graceful shutdown |

To change the Whisper model or switch to CPU, edit the environment variables in `whisper-api.service`.

## License

MIT
