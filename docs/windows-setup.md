# Windows Setup - Whisper Toggle

Tested on Windows 11. Works with NVIDIA GPU (CUDA) or CPU-only.

## Recommended install

Use the Windows installer built from `windows/build-installer.ps1`:

```powershell
cd C:\src\Whisper-Toggle
powershell -ExecutionPolicy Bypass -File windows\build-installer.ps1
# dist\WhisperToggle-Setup-2.0.0.exe
```

The installer deploys to:

```text
%LOCALAPPDATA%\Whisper Toggle
```

It includes embedded Python, the tray app, settings GUI, local FastAPI server, and model/runtime dependencies.

## Use it

1. Start **Whisper Toggle** from Start Menu or let autostart launch it.
2. Confirm the green mic tray icon appears.
3. Put the cursor in the target app (Windows Terminal, PowerShell, browser, editor, etc.).
4. Press **Ctrl+Shift+H** to start recording.
5. Speak.
6. Press **Ctrl+Shift+H** again to stop; final text is pasted at the cursor.

## Defaults

- Hotkey: `ctrl+shift+h`
- Insertion method: clipboard + Ctrl+V after hotkey modifiers are released
- Streaming/live partials: off by default on Windows for reliability
- Win+H: optional; enable only after disabling the Windows Voice Typing launcher

## Settings

Right-click the tray icon -> **Settings...**

Settings can change:

- hotkey
- model
- device override
- autostart
- stop catch-up delay
- optional streaming/live partials
- Windows Voice Typing launcher disable helper

Saved settings reload in the tray app within about one second.

## Logs

Open from the tray menu, or directly:

```text
%LOCALAPPDATA%\Whisper Toggle\logs\whisper-toggle.log
```

## Troubleshooting

### Transcription happens but text does not paste

- Make sure the target app has focus before pressing the stop hotkey.
- Avoid releasing the hotkey extremely slowly; the app waits for Ctrl/Shift/Alt/Win to come up before pasting.
- If the focused app is elevated, Whisper Toggle may also need to run elevated.
- Check logs for `pasted ... via SendInput ctrl+v` or paste errors.

### Win+H opens Windows Voice Typing

Windows owns Win+H by default. Use **Ctrl+Shift+H** or open Settings and click **Disable Windows Voice Typing launcher**, then confirm the launcher is off in Windows Settings.

### API not ready

Check:

```powershell
Invoke-WebRequest http://127.0.0.1:8788/health -UseBasicParsing
```

If it is down, use tray -> **Restart engine** or quit/relaunch the tray app.

### CPU is slow

Expected. CPU mode works but is slower. NVIDIA CUDA is recommended for larger models.

## Uninstall

Use Windows Apps & Features, or run the uninstaller from the Start Menu group. The installer removes app files; logs/config under `%LOCALAPPDATA%\Whisper Toggle` may remain if you keep user data.
