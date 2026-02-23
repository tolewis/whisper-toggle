# Whisper Toggle

```
 __        ___     _                       _____                 _
 \ \      / / |__ (_)___ _ __   ___ _ __  |_   _|__   __ _  __ _| | ___
  \ \ /\ / /| '_ \| / __| '_ \ / _ \ '__|  | |/ _ \ / _` |/ _` | |/ _ \
   \ V  V / | | | | \__ \ |_) |  __/ |     | | (_) | (_| | (_| | |  __/
    \_/\_/  |_| |_|_|___/ .__/ \___|_|     |_|\___/ \__, |\__, |_|\___|
                         |_|                          |___/ |___/
```

**Talk to your computer. It types for you.**

Whisper Toggle turns your voice into text — instantly, privately, in any app. No cloud. No subscription. No sending your voice to anyone. Everything runs on your machine.

Press a hotkey, say what you want, press it again. Your words appear wherever your cursor is. Discord, Google Docs, your code editor, a terminal — doesn't matter. It just works.

## Why You'll Love This

- **It's fast.** ~2 seconds from the moment you stop talking to text on screen.
- **It's private.** Your voice never leaves your computer. No accounts, no API keys, no data collection.
- **It works everywhere.** Any app, any text field. If you can type there, you can dictate there.
- **It's free.** Open source, forever. No trial period, no premium tier.

## How It Works

```
  Press Ctrl+`         🎙️ "Recording..."
  Say your thing
  Press Ctrl+` again   ⚡ "Processing..."
  Text appears ✨       Right where your cursor was
```

Under the hood, it uses [OpenAI's Whisper](https://github.com/openai/whisper) speech recognition model running locally on your GPU (or CPU). A small server keeps the model loaded in memory so transcription is near-instant.

## Get Started

### Windows

> Full guide: **[docs/windows-setup.md](docs/windows-setup.md)**

```powershell
git clone https://github.com/tolewis/Whisper-Toggle.git
cd Whisper-Toggle
powershell -ExecutionPolicy Bypass -File windows\install.ps1
```

Default hotkey: **Ctrl+\`** (the backtick key, above Tab)

### Linux

> Full guide: **[docs/linux-setup.md](docs/linux-setup.md)**

```bash
sudo apt install xdotool xclip x11-utils libnotify-bin curl
cp linux/dictate-toggle.sh ~/bin/ && chmod +x ~/bin/dictate-toggle.sh
```

Default hotkeys: **Super+H** and **Ctrl+\`**

## What's in the Box

```
Whisper-Toggle/
├── app.py                     The brain — local Whisper API server
├── linux/                     Linux version (bash script)
├── windows/                   Windows version (Python + installer)
└── docs/                      Setup guides for each platform
```

## Requirements

- **An NVIDIA GPU** makes it fast (~2 sec). No GPU? It still works on CPU, just slower (~10-15 sec).
- **Python 3.9+** for the transcription server.
- **A microphone.** Obviously.

## FAQ

**Will this work with my Bluetooth headset / USB mic / webcam mic?**
Yes. Whatever Windows or Linux sees as your default microphone, Whisper Toggle uses it.

**Does it work in games / full-screen apps?**
The hotkey is system-wide, so yes — as long as the game doesn't capture all keyboard input.

**Can I change the hotkey?**
Yes. On Windows: `dictate-toggle.py --hotkey "ctrl+shift+h"` (or whatever combo you want). On Linux: edit the GNOME keybinding.

**How accurate is it?**
Whisper is remarkably good. It handles accents, mumbling, and background noise better than most cloud services. Punctuation and capitalization are automatic.

**How much disk space does it need?**
About 3-4 GB total (Python environment + Whisper model). The model downloads automatically on first run.

## Version History

| Version | Date | What's new |
|---------|------|-----------|
| **1.0** | Feb 2026 | Full rewrite. Warm API, auto-paste, Windows + Linux support. |
| 0.1–0.3 | 2025 | Early prototypes. Slow, unreliable. Archived. |

## License

MIT — do whatever you want with it.

---

*Built by [Tim Lewis](https://github.com/tolewis). Transcription powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper).*
