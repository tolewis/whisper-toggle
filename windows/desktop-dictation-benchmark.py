#!/usr/bin/env python3
"""Interactive desktop dictation benchmark target.

Run this in the active Windows console session (for example with a Scheduled Task
using /IT). It opens a focused Tk text box, sends either Win+H (Windows Voice
Typing) or Ctrl+Shift+H (Whisper Toggle), plays a fixed WAV through the default
speakers, and records what text appears in the box.

This is intentionally a desktop test, not a unit test: it measures hotkey,
microphone routing, OS dictation UI, and text insertion behavior together.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import threading
import time
import wave
import winsound
from pathlib import Path
import tkinter as tk


user32 = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_H = 0x48
VK_ESCAPE = 0x1B


ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


def _key(vk: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def send_keys_down_up(vks: list[int]) -> None:
    if user32 is None:
        raise RuntimeError("SendInput is only available on Windows")
    events = [_key(vk, False) for vk in vks] + [_key(vk, True) for vk in reversed(vks)]
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate() or 1)


def play_wav(path: Path, result: dict, started: float) -> None:
    result["audio_start_sec"] = round(time.perf_counter() - started, 3)
    winsound.PlaySound(str(path), winsound.SND_FILENAME)
    result["audio_end_sec"] = round(time.perf_counter() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Desktop dictation benchmark target")
    parser.add_argument("--mode", choices=("windows", "whisper-toggle"), required=True)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--warmup-sec", type=float, default=2.0)
    parser.add_argument("--windows-ready-sec", type=float, default=2.5)
    parser.add_argument("--whisper-ready-sec", type=float, default=0.5)
    parser.add_argument("--tail-sec", type=float, default=7.0)
    args = parser.parse_args()

    started = time.perf_counter()
    result: dict = {
        "ok": False,
        "mode": args.mode,
        "audio": str(args.audio),
        "audio_sec": round(wav_duration(args.audio), 3),
        "events": [],
        "first_text_sec": None,
        "final_text": "",
    }

    root = tk.Tk()
    root.title(f"Whisper Toggle benchmark - {args.mode}")
    root.geometry("900x260+120+120")
    root.attributes("-topmost", True)
    text = tk.Text(root, font=("Segoe UI", 18), undo=False)
    text.pack(fill="both", expand=True)
    text.insert("1.0", "")

    last_text = ""

    finished = False

    def record_event(name: str, **extra) -> None:
        payload = {"at_sec": round(time.perf_counter() - started, 3), "event": name}
        payload.update(extra)
        result["events"].append(payload)

    def finish(error: str | None = None) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        result["final_text"] = text.get("1.0", "end-1c")
        result["elapsed_sec"] = round(time.perf_counter() - started, 3)
        if error:
            result["error"] = error
        result["ok"] = bool(result["final_text"].strip()) and not error
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        root.destroy()

    def safe(fn):
        def _wrapped() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - benchmark must report GUI callback failures
                record_event("error", error=str(exc), error_type=type(exc).__name__)
                finish(f"{type(exc).__name__}: {exc}")

        return _wrapped

    def poll_text() -> None:
        nonlocal last_text
        current = text.get("1.0", "end-1c")
        if current != last_text:
            now = round(time.perf_counter() - started, 3)
            if current.strip() and result["first_text_sec"] is None:
                result["first_text_sec"] = now
            record_event("text_changed", text=current)
            last_text = current
        if not finished:
            root.after(100, poll_text)

    def focus_target() -> None:
        root.deiconify()
        root.lift()
        root.focus_force()
        text.focus_force()
        record_event("focused")

    def start_sequence() -> None:
        focus_target()
        if args.mode == "windows":
            record_event("send_hotkey", hotkey="win+h")
            send_keys_down_up([VK_LWIN, VK_H])
            delay = args.windows_ready_sec
        else:
            record_event("send_hotkey", hotkey="ctrl+shift+h:start")
            send_keys_down_up([VK_CONTROL, VK_SHIFT, VK_H])
            delay = args.whisper_ready_sec
        root.after(int(delay * 1000), safe(start_audio))

    def start_audio() -> None:
        record_event("audio_play_start")
        threading.Thread(target=play_wav, args=(args.audio, result, started), daemon=True).start()
        audio_ms = int(result["audio_sec"] * 1000)
        if args.mode == "whisper-toggle":
            root.after(audio_ms + 800, safe(stop_whisper_toggle))
        else:
            root.after(audio_ms + int(args.tail_sec * 1000), safe(stop_windows))

    def stop_whisper_toggle() -> None:
        record_event("send_hotkey", hotkey="ctrl+shift+h:stop")
        send_keys_down_up([VK_CONTROL, VK_SHIFT, VK_H])
        root.after(int(args.tail_sec * 1000), safe(finish))

    def stop_windows() -> None:
        record_event("send_key", key="escape")
        send_keys_down_up([VK_ESCAPE])
        root.after(1000, safe(finish))

    root.after(100, poll_text)
    root.after(int(args.warmup_sec * 1000), safe(start_sequence))
    root.mainloop()
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
