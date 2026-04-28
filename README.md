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

> **Platform status:** Linux is stable and working. Windows is in active development — the installer builds and runs but the tray app needs debugging. See [issues](https://github.com/tolewis/Whisper-Toggle/issues) for current status.

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

## Streaming v2

Linux now defaults to streaming transcription:

- `WHISPER_STREAMING=1` uses `WS /v1/audio/stream` for live partials and final text.
- `WHISPER_STREAMING=0` keeps the v1 batch path, `POST /v1/audio/transcriptions`.
- `WHISPER_STREAMING_ENDPOINT` defaults to `ws://127.0.0.1:8788/v1/audio/stream`.
- `WHISPER_OSD=1` shows partial and confirmed text in the Tk overlay.
- `WHISPER_OSD=0` types partial revisions in place with `xdotool`; this is available for testing but less smooth.

If the streaming WebSocket cannot connect within 1 second, the Linux toggle logs one stderr line and starts a v1 batch recording instead.

Manual smoke test for the streaming endpoint:

```bash
python3 - <<'PY'
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8788/v1/audio/stream") as ws:
        await ws.send(b"\0\0" * 16000)
        await ws.send(json.dumps({"type": "end"}))
        async for msg in ws:
            print(msg)
            if json.loads(msg).get("type") == "final":
                break

asyncio.run(main())
PY
```

Architecture diff:

- v1 records a WAV with `pw-record`, sends it to `POST /v1/audio/transcriptions`, then pastes the returned `text`.
- v2 streams 16 kHz mono PCM int16 frames to `WS /v1/audio/stream`, renders `partial` and `confirmed` messages in the OSD, then types only the `final` text.
- Both paths share the same cached `faster-whisper` `WhisperModel`; the streaming path uses ufal `whisper_streaming` LocalAgreement-2 logic around that model.

Production staging:

```bash
./scripts/deploy.sh
```

The deploy script copies files and installs pinned deps, but it does not restart the service. Cutover is explicit:

```bash
sudo systemctl restart whisper-api
sleep 2 && curl -s http://127.0.0.1:8788/v1/health || echo "FAIL"
```

## Get Started

### Linux (stable)

> Full guide: **[docs/linux-setup.md](docs/linux-setup.md)**

```bash
sudo apt install xdotool xclip x11-utils libnotify-bin curl
cp linux/dictate-toggle.sh ~/bin/ && chmod +x ~/bin/dictate-toggle.sh
```

Default hotkeys: **Super+H** and **Ctrl+\`**

### Windows (in development)

> The installer builds and runs, but the tray app and hotkey integration are still being debugged. Not ready for daily use yet. See **[docs/windows-setup.md](docs/windows-setup.md)** if you want to help test.

## What's in the Box

```
Whisper-Toggle/
├── app.py                     The brain — local Whisper API server
├── linux/                     Linux version (bash script)
├── scripts/                   Deployment staging
├── tests/                     Streaming and toggle tests
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
