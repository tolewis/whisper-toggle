# Linux Setup — Whisper-Toggle

Tested on Ubuntu 24.04, GNOME, PipeWire, GTX 1060 6GB.

## Prerequisites

- GNOME desktop (Wayland or X11)
- PipeWire audio (`pw-record` available)
- NVIDIA GPU with CUDA (or set `WHISPER_API_DEVICE=cpu`)
- Python 3.9+

## Installation

### 1. System packages

```bash
sudo apt install xdotool xclip x11-utils libnotify-bin curl pipewire-tools
```

### 2. Python venv

```bash
python3 -m venv ~/.venvs/whisper
~/.venvs/whisper/bin/pip install faster-whisper fastapi uvicorn
```

### 3. API server

```bash
mkdir -p ~/.local/share/whisper-api
cp app.py ~/.local/share/whisper-api/

mkdir -p ~/.config/systemd/user
cp linux/whisper-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whisper-api
```

Verify:

```bash
curl http://127.0.0.1:8788/health
# → {"ok":true}
```

### 4. Dictation script

```bash
cp linux/dictate-toggle.sh ~/bin/
chmod +x ~/bin/dictate-toggle.sh
```

### 5. Keybindings

Unbind Super+H from minimize:

```bash
gsettings set org.gnome.desktop.wm.keybindings minimize "['']"
```

Register hotkeys:

```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/', \
    '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/']"

SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

# Super+H
P0="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
gsettings set "${SCHEMA}:${P0}" name "Dictation Toggle"
gsettings set "${SCHEMA}:${P0}" command "$HOME/bin/dictate-toggle.sh"
gsettings set "${SCHEMA}:${P0}" binding "<Super>h"

# Ctrl+`
P1="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"
gsettings set "${SCHEMA}:${P1}" name "Dictation Toggle (Alt)"
gsettings set "${SCHEMA}:${P1}" command "$HOME/bin/dictate-toggle.sh"
gsettings set "${SCHEMA}:${P1}" binding "<Ctrl>grave"
```

## Usage

1. **Super+H** (or **Ctrl+\`**) — "Recording..." notification
2. Speak
3. Press hotkey again — "Processing..." → transcribed text auto-pasted + clipboard

Terminal windows (gnome-terminal, kitty, Alacritty, etc.) are auto-detected and use Ctrl+Shift+V.

## Troubleshooting

### Hotkey doesn't trigger

```bash
# Verify keybindings exist
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings

# Check Super+H isn't still bound to minimize
gsettings get org.gnome.desktop.wm.keybindings minimize
# Should be ['']
```

### Second press doesn't stop recording

Ensure `dictate-toggle.sh` line 173 has `9>&-`:

```bash
pw-record --rate 16000 --channels 1 --format s16 "$WAV_FILE" 9>&- &
```

Manual cleanup:

```bash
kill $(cat /tmp/dictate-toggle/rec.pid)
rm -f /tmp/dictate-toggle/rec.pid /tmp/dictate-toggle/current.wav
```

### API not responding

```bash
systemctl --user status whisper-api
journalctl --user -u whisper-api --no-pager -n 20
# Restart:
systemctl --user restart whisper-api
```

### No audio captured

```bash
# Test mic
timeout 3 pw-record --rate 16000 --channels 1 --format s16 /tmp/test.wav
ls -la /tmp/test.wav  # Should be >30KB

# Check PipeWire default source
wpctl status | head -30
```

## Service Management

```bash
systemctl --user status whisper-api      # Status
systemctl --user restart whisper-api     # Restart (reloads model)
systemctl --user stop whisper-api        # Stop (frees VRAM)
journalctl --user -u whisper-api -f      # Live logs
```

## Uninstall

```bash
systemctl --user disable --now whisper-api
rm ~/bin/dictate-toggle.sh
rm -rf ~/.local/share/whisper-api
rm ~/.config/systemd/user/whisper-api.service
systemctl --user daemon-reload
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "[]"
gsettings set org.gnome.desktop.wm.keybindings minimize "['<Super>h']"
```
