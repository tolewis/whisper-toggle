#!/usr/bin/env python3
"""Benchmark the local Whisper Toggle API with a fixed WAV file.

This measures the part we can test deterministically without desktop input:
model/runtime readiness, batch transcription latency, optional streaming latency,
and word error rate against an expected phrase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import requests
import websockets


_WORD_RE = re.compile(r"[a-z0-9']+")


def _normalize_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, aw in enumerate(a, start=1):
        cur = [i]
        for j, bw in enumerate(b, start=1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (0 if aw == bw else 1),
                )
            )
        prev = cur
    return prev[-1]


def wer(expected: str, actual: str) -> float | None:
    ref = _normalize_words(expected)
    hyp = _normalize_words(actual)
    if not ref:
        return None
    return _edit_distance(ref, hyp) / len(ref)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        return frames / float(rate or 1)


def read_pcm16_mono_16k(path: Path) -> tuple[bytes, float]:
    """Return 16 kHz mono int16 PCM, using a tiny numpy resampler if needed."""

    try:
        import soundfile as sf
    except ImportError:
        sf = None  # type: ignore[assignment]

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        duration = frames / float(rate or 1)
        if channels == 1 and sample_width == 2 and rate == 16000:
            return wav.readframes(frames), duration

    if sf is None:
        raise RuntimeError(
            "stream benchmark requires 16 kHz mono PCM WAV, or install soundfile for resampling"
        )

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    duration = len(mono) / float(rate or 1)
    if rate != 16000:
        target_len = max(1, int(round(duration * 16000)))
        src_x = np.linspace(0.0, duration, num=len(mono), endpoint=False)
        dst_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
        mono = np.interp(dst_x, src_x, mono).astype(np.float32)
    pcm = np.clip(mono, -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)
    return pcm_i16.tobytes(), duration


def get_runtime(base_url: str, timeout: float) -> dict[str, Any]:
    r = requests.get(f"{base_url.rstrip('/')}/v1/runtime", timeout=timeout)
    r.raise_for_status()
    return r.json()


def batch_once(base_url: str, audio_path: Path, model: str, language: str, expected: str) -> dict[str, Any]:
    started = time.perf_counter()
    with audio_path.open("rb") as fh:
        r = requests.post(
            f"{base_url.rstrip('/')}/v1/audio/transcriptions",
            files={"file": (audio_path.name, fh, "audio/wav")},
            data={"model": model, "language": language},
            timeout=180,
        )
    elapsed = time.perf_counter() - started
    r.raise_for_status()
    text = (r.json().get("text") or "").strip()
    return {
        "elapsed_sec": round(elapsed, 3),
        "text": text,
        "wer": wer(expected, text),
    }


async def stream_once(
    stream_url: str,
    audio_path: Path,
    model: str,
    language: str,
    expected: str,
    chunk_ms: int,
    realtime: bool,
) -> dict[str, Any]:
    pcm, audio_sec = read_pcm16_mono_16k(audio_path)
    bytes_per_ms = 16000 * 2 / 1000.0
    chunk_size = max(320, int(bytes_per_ms * chunk_ms))
    chunk_size -= chunk_size % 2
    sep = "&" if "?" in stream_url else "?"
    url = f"{stream_url}{sep}model={model}&language={language}"

    started = time.perf_counter()
    first_frame_sec: float | None = None
    first_partial_sec: float | None = None
    first_confirmed_sec: float | None = None
    final_sec: float | None = None
    final_text = ""
    frames: list[dict[str, Any]] = []

    async with websockets.connect(
        url,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=8 * 1024 * 1024,
    ) as ws:
        async def recv_loop() -> None:
            nonlocal first_frame_sec, first_partial_sec, first_confirmed_sec, final_sec, final_text
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                now = time.perf_counter() - started
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")
                text = (payload.get("text") or "").strip()
                frames.append({"at_sec": round(now, 3), "type": kind, "text": text})
                if first_frame_sec is None:
                    first_frame_sec = now
                if kind == "partial" and first_partial_sec is None:
                    first_partial_sec = now
                if kind == "confirmed" and first_confirmed_sec is None:
                    first_confirmed_sec = now
                if kind == "final":
                    final_sec = now
                    final_text = text
                    return

        recv_task = asyncio.create_task(recv_loop())
        for i in range(0, len(pcm), chunk_size):
            await ws.send(pcm[i : i + chunk_size])
            if realtime:
                await asyncio.sleep(chunk_size / bytes_per_ms / 1000.0)
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(recv_task, timeout=120)

    return {
        "audio_sec": round(audio_sec, 3),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "first_frame_sec": None if first_frame_sec is None else round(first_frame_sec, 3),
        "first_partial_sec": None if first_partial_sec is None else round(first_partial_sec, 3),
        "first_confirmed_sec": None if first_confirmed_sec is None else round(first_confirmed_sec, 3),
        "final_sec": None if final_sec is None else round(final_sec, 3),
        "final_text": final_text,
        "wer": wer(expected, final_text),
        "frames": frames,
    }


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"min": round(min(values), 3), "median": round(median, 3), "max": round(max(values), 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark a running Whisper Toggle API")
    parser.add_argument("--audio", required=True, type=Path, help="WAV file to transcribe")
    parser.add_argument("--expected", default="", help="expected transcript for WER")
    parser.add_argument("--base-url", default="http://127.0.0.1:8788")
    parser.add_argument("--stream-url", default="ws://127.0.0.1:8788/v1/audio/stream")
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--language", default="en")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--stream", action="store_true", help="also benchmark /v1/audio/stream")
    parser.add_argument("--chunk-ms", type=int, default=250)
    parser.add_argument("--no-realtime", action="store_true", help="send stream audio as fast as possible")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if not args.audio.exists():
        raise SystemExit(f"audio file not found: {args.audio}")

    result: dict[str, Any] = {
        "audio": str(args.audio),
        "audio_sec": round(wav_duration(args.audio), 3),
        "expected": args.expected,
        "base_url": args.base_url,
        "stream_url": args.stream_url,
        "model": args.model,
        "language": args.language,
        "runs": args.runs,
        "runtime": get_runtime(args.base_url, timeout=10),
        "batch": [],
        "stream": [],
    }

    for _ in range(args.runs):
        result["batch"].append(batch_once(args.base_url, args.audio, args.model, args.language, args.expected))

    if args.stream:
        for _ in range(args.runs):
            result["stream"].append(
                asyncio.run(
                    stream_once(
                        args.stream_url,
                        args.audio,
                        args.model,
                        args.language,
                        args.expected,
                        args.chunk_ms,
                        realtime=not args.no_realtime,
                    )
                )
            )

    result["summary"] = {
        "batch_elapsed_sec": summarize([row["elapsed_sec"] for row in result["batch"]]),
        "batch_wer": summarize([row["wer"] for row in result["batch"] if row["wer"] is not None]),
        "stream_final_sec": summarize([row["final_sec"] for row in result["stream"] if row["final_sec"] is not None]),
        "stream_first_partial_sec": summarize(
            [row["first_partial_sec"] for row in result["stream"] if row["first_partial_sec"] is not None]
        ),
        "stream_wer": summarize([row["wer"] for row in result["stream"] if row["wer"] is not None]),
    }

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
