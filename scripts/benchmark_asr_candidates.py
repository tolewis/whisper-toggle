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
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any


_WORD_RE = re.compile(r"[a-z0-9']+")
_DICTATION_TOKEN_RE = re.compile(r"[a-z]+|\d+", re.IGNORECASE)
_NUMBER_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
}


def normalize_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def normalize_dictation_words(text: str) -> list[str]:
    """Normalize common dictation-equivalent tokens for fairer WER.

    ASR often writes spoken numbers as digits ("fifteen" -> "15") and times as
    `10.30am`. These are acceptable dictation outputs but basic WER counts them
    as errors. Keep this conservative: only normalize common one-token numbers
    and split digit/letter runs.
    """

    raw = _DICTATION_TOKEN_RE.findall((text or "").lower())
    mapped = [_NUMBER_WORDS.get(token, token) for token in raw]
    out: list[str] = []
    i = 0
    while i < len(mapped):
        if mapped[i : i + 2] == ["a", "m"]:
            out.append("am")
            i += 2
            continue
        if mapped[i : i + 2] == ["p", "m"]:
            out.append("pm")
            i += 2
            continue
        out.append(mapped[i])
        i += 1
    return out


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


def dictation_wer(expected: str, actual: str) -> float | None:
    ref = normalize_dictation_words(expected)
    if not ref:
        return None
    return edit_distance(ref, normalize_dictation_words(actual)) / len(ref)


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


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Load a JSON array or JSONL benchmark manifest.

    Each row must contain at least:
      {"audio": "path.wav", "expected": "transcript"}
    Optional: `id`.
    Relative audio paths resolve relative to the manifest file.
    """

    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ValueError(f"empty manifest: {path}")
    if raw.startswith("["):
        rows = json.loads(raw)
    else:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError("manifest must be a JSON array or JSONL rows")
    clips: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"manifest row {idx} is not an object")
        audio = Path(str(row.get("audio") or ""))
        if not audio.is_absolute():
            audio = path.parent / audio
        expected = str(row.get("expected") or "")
        if not audio.exists():
            raise FileNotFoundError(f"manifest row {idx} audio missing: {audio}")
        clips.append(
            {
                "id": str(row.get("id") or audio.stem or f"clip-{idx}"),
                "audio": audio,
                "expected": expected,
                "audio_sec": wav_duration(audio),
            }
        )
    if not clips:
        raise ValueError(f"manifest contains no clips: {path}")
    return clips


def single_clip(audio: Path, expected: str) -> list[dict[str, Any]]:
    if not audio.exists():
        raise FileNotFoundError(f"audio file not found: {audio}")
    return [
        {
            "id": audio.stem,
            "audio": audio,
            "expected": expected,
            "audio_sec": wav_duration(audio),
        }
    ]


def run_one_transcription(
    *,
    model: Any,
    clip: dict[str, Any],
    language: str,
    beam_size: int,
    vad_filter: bool,
    run_index: int,
    phase: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    text = transcribe_faster_whisper(
        model,
        clip["audio"],
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    elapsed = time.perf_counter() - started
    audio_sec = float(clip["audio_sec"])
    return {
        "phase": phase,
        "run_index": run_index,
        "clip_id": clip["id"],
        "audio": str(clip["audio"]),
        "audio_sec": round(audio_sec, 4),
        "elapsed_sec": round(elapsed, 4),
        "rtf": round(elapsed / audio_sec, 4),
        "wer": wer(str(clip["expected"]), text),
        "dictation_wer": dictation_wer(str(clip["expected"]), text),
        "text": text,
    }


def benchmark_faster_whisper_model(
    *,
    model_name: str,
    clips: list[dict[str, Any]],
    device: str,
    compute_type: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
    runs: int,
    warmup_runs: int,
) -> dict[str, Any]:
    configure_runtime_for_backend()
    from faster_whisper import WhisperModel

    before_mem = nvidia_memory_used_mb()
    load_started = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    load_sec = time.perf_counter() - load_started
    after_load_mem = nvidia_memory_used_mb()

    warmups: list[dict[str, Any]] = []
    first_clip = clips[0]
    for idx in range(1, warmup_runs + 1):
        warmups.append(
            run_one_transcription(
                model=model,
                clip=first_clip,
                language=language,
                beam_size=beam_size,
                vad_filter=vad_filter,
                run_index=idx,
                phase="warmup",
            )
        )

    measurements: list[dict[str, Any]] = []
    for run_idx in range(1, runs + 1):
        for clip in clips:
            measurements.append(
                run_one_transcription(
                    model=model,
                    clip=clip,
                    language=language,
                    beam_size=beam_size,
                    vad_filter=vad_filter,
                    run_index=run_idx,
                    phase="measure",
                )
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
            "dictation_wer": summarize(
                [row["dictation_wer"] for row in measurements if row["dictation_wer"] is not None]
            ),
        },
        "by_clip": {
            clip["id"]: {
                "elapsed_sec": summarize(
                    [row["elapsed_sec"] for row in measurements if row["clip_id"] == clip["id"]]
                ),
                "rtf": summarize([row["rtf"] for row in measurements if row["clip_id"] == clip["id"]]),
                "wer": summarize(
                    [row["wer"] for row in measurements if row["clip_id"] == clip["id"] and row["wer"] is not None]
                ),
                "dictation_wer": summarize(
                    [
                        row["dictation_wer"]
                        for row in measurements
                        if row["clip_id"] == clip["id"] and row["dictation_wer"] is not None
                    ]
                ),
            }
            for clip in clips
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", type=Path)
    source.add_argument("--manifest", type=Path, help="JSON array or JSONL rows: audio, expected, optional id")
    parser.add_argument("--expected", default="", help="expected transcript for --audio mode")
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

    clips = load_manifest(args.manifest) if args.manifest else single_clip(args.audio, args.expected)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("--models did not contain any model names")

    result = {
        "schema": "teamlewis/whisper-toggle-asr-benchmark@1",
        "clips": [
            {
                "id": clip["id"],
                "audio": str(clip["audio"]),
                "audio_sec": round(float(clip["audio_sec"]), 4),
                "expected": clip["expected"],
            }
            for clip in clips
        ],
        "models": [],
    }
    if len(clips) == 1:
        # Back-compatible convenience fields for older one-clip reports.
        result["audio"] = result["clips"][0]["audio"]
        result["audio_sec"] = result["clips"][0]["audio_sec"]
        result["expected"] = result["clips"][0]["expected"]

    for model_name in models:
        result["models"].append(
            benchmark_model_safe(
                {
                    "model_name": model_name,
                    "clips": clips,
                    "device": args.device,
                    "compute_type": args.compute_type,
                    "language": args.language,
                    "beam_size": args.beam_size,
                    "vad_filter": args.vad_filter,
                    "runs": args.runs,
                    "warmup_runs": args.warmup_runs,
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
