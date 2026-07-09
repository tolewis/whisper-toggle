#!/usr/bin/env python3
"""Benchmark local ASR candidate models directly.

This is intentionally below the tray/foreground layer. It answers: if a model is
loaded and kept warm, how fast and accurate is the actual transcription core?

Currently implemented backend:
- faster-whisper / CTranslate2

Future backends should preserve this JSON shape so results can be compared with
sherpa-onnx, whisper.cpp, Moonshine, etc.
"""

from __future__ import annotations

import argparse
import gc
import json
import re
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any


_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, aw in enumerate(a, start=1):
        cur = [i]
        for j, bw in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (aw != bw)))
        prev = cur
    return prev[-1]


def wer(expected: str, actual: str) -> float | None:
    ref = normalize_words(expected)
    if not ref:
        return None
    return edit_distance(ref, normalize_words(actual)) / len(ref)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate() or 1)


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"min": round(min(values), 4), "median": round(median, 4), "max": round(max(values), 4)}


def nvidia_memory_used_mb() -> list[int] | None:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return [int(line.strip()) for line in out.splitlines() if line.strip()]
    except Exception:
        return None


def transcribe_faster_whisper(
    model: Any,
    audio: Path,
    *,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> str:
    segments, _info = model.transcribe(
        str(audio),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    return "".join(segment.text for segment in segments).strip()


def configure_runtime_for_backend() -> None:
    """Apply project-specific runtime setup when run from an installed tree."""

    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        installed_root = Path(local_app) / "Whisper Toggle"
        if (installed_root / "whisper_toggle").exists() and str(installed_root) not in sys.path:
            sys.path.insert(0, str(installed_root))
    try:
        from whisper_toggle.cuda_env import configure_cuda_dll_paths

        configure_cuda_dll_paths()
    except Exception:
        # The benchmark must also work outside the Whisper Toggle package; CPU
        # runs and non-Windows environments do not need this setup.
        pass


def benchmark_faster_whisper_model(
    *,
    model_name: str,
    audio: Path,
    expected: str,
    device: str,
    compute_type: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
    runs: int,
    warmup_runs: int,
    audio_sec: float,
) -> dict[str, Any]:
    configure_runtime_for_backend()
    from faster_whisper import WhisperModel

    before_mem = nvidia_memory_used_mb()
    load_started = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    load_sec = time.perf_counter() - load_started
    after_load_mem = nvidia_memory_used_mb()

    warmups: list[dict[str, Any]] = []
    for _ in range(warmup_runs):
        started = time.perf_counter()
        text = transcribe_faster_whisper(
            model,
            audio,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        elapsed = time.perf_counter() - started
        warmups.append(
            {
                "elapsed_sec": round(elapsed, 4),
                "rtf": round(elapsed / audio_sec, 4),
                "wer": wer(expected, text),
                "text": text,
            }
        )

    measurements: list[dict[str, Any]] = []
    for _ in range(runs):
        started = time.perf_counter()
        text = transcribe_faster_whisper(
            model,
            audio,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        elapsed = time.perf_counter() - started
        measurements.append(
            {
                "elapsed_sec": round(elapsed, 4),
                "rtf": round(elapsed / audio_sec, 4),
                "wer": wer(expected, text),
                "text": text,
            }
        )

    after_runs_mem = nvidia_memory_used_mb()
    del model
    gc.collect()

    return {
        "backend": "faster-whisper",
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "language": language,
        "beam_size": beam_size,
        "vad_filter": vad_filter,
        "ok": True,
        "load_sec": round(load_sec, 4),
        "warmup": warmups,
        "runs": measurements,
        "summary": {
            "elapsed_sec": summarize([row["elapsed_sec"] for row in measurements]),
            "rtf": summarize([row["rtf"] for row in measurements]),
            "wer": summarize([row["wer"] for row in measurements if row["wer"] is not None]),
        },
        "nvidia_memory_used_mb": {
            "before_load": before_mem,
            "after_load": after_load_mem,
            "after_runs": after_runs_mem,
        },
    }


def benchmark_model_safe(kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return benchmark_faster_whisper_model(**kwargs)
    except Exception as exc:  # noqa: BLE001 - benchmark should continue across failed candidates
        return {
            "backend": "faster-whisper",
            "model": kwargs["model_name"],
            "device": kwargs["device"],
            "compute_type": kwargs["compute_type"],
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ASR candidate models")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--expected", default="")
    parser.add_argument(
        "--models",
        required=True,
        help="comma-separated faster-whisper model names, e.g. tiny.en,base.en,small.en,distil-large-v3",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="en")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--vad-filter", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if not args.audio.exists():
        raise SystemExit(f"audio file not found: {args.audio}")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("--models did not contain any model names")

    audio_sec = wav_duration(args.audio)
    result = {
        "schema": "teamlewis/whisper-toggle-asr-benchmark@1",
        "audio": str(args.audio),
        "audio_sec": round(audio_sec, 4),
        "expected": args.expected,
        "models": [],
    }

    for model_name in models:
        result["models"].append(
            benchmark_model_safe(
                {
                    "model_name": model_name,
                    "audio": args.audio,
                    "expected": args.expected,
                    "device": args.device,
                    "compute_type": args.compute_type,
                    "language": args.language,
                    "beam_size": args.beam_size,
                    "vad_filter": args.vad_filter,
                    "runs": args.runs,
                    "warmup_runs": args.warmup_runs,
                    "audio_sec": audio_sec,
                }
            )
        )

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    failures = [row for row in result["models"] if not row.get("ok")]
    return 1 if failures and len(failures) == len(result["models"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
