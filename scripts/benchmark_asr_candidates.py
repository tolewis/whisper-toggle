#!/usr/bin/env python3
"""Benchmark local ASR candidate models directly.

This is intentionally below the tray/foreground layer. It answers: if a model is
loaded and kept warm, how fast and accurate is the actual transcription core?

Currently implemented backends:
- faster-whisper / CTranslate2
- sherpa-onnx online transducer (streaming file replay)

Future backends should preserve this JSON shape so results can be compared with
whisper.cpp, Moonshine, etc.
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


def read_wave_float32(path: Path) -> tuple[Any, int]:
    """Read a mono 16-bit PCM WAV as numpy float32 in [-1, 1]."""

    import numpy as np

    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1:
            raise ValueError(f"expected mono WAV for sherpa-onnx benchmark: {path}")
        if wav.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM WAV for sherpa-onnx benchmark: {path}")
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples_i16 = np.frombuffer(frames, dtype=np.int16)
    return samples_i16.astype(np.float32) / 32768.0, sample_rate


def _first_existing(base: Path, patterns: list[str], label: str) -> Path:
    for pattern in patterns:
        matches = sorted(base.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"could not find {label} in {base}; tried {', '.join(patterns)}")


def sherpa_online_transducer_model_paths(model_dir: Path) -> dict[str, Path]:
    """Resolve common sherpa-onnx online transducer model filenames."""

    if not model_dir.exists():
        raise FileNotFoundError(f"sherpa-onnx model directory not found: {model_dir}")
    if not model_dir.is_dir():
        raise ValueError(f"sherpa-onnx model path must be a directory: {model_dir}")
    return {
        "tokens": _first_existing(model_dir, ["tokens.txt", "*tokens.txt"], "tokens"),
        "encoder": _first_existing(model_dir, ["encoder*.int8.onnx", "encoder*.onnx"], "encoder"),
        "decoder": _first_existing(model_dir, ["decoder*.int8.onnx", "decoder*.onnx"], "decoder"),
        "joiner": _first_existing(model_dir, ["joiner*.int8.onnx", "joiner*.onnx"], "joiner"),
    }


def recognizer_result_text(result: Any) -> str:
    text = getattr(result, "text", result)
    return str(text or "").strip()


def transcribe_sherpa_online(
    recognizer: Any,
    audio: Path,
    *,
    chunk_sec: float,
    tail_padding_sec: float,
) -> dict[str, Any]:
    """Replay a WAV through a sherpa-onnx online recognizer as fast as possible."""

    import numpy as np

    samples, sample_rate = read_wave_float32(audio)
    stream = recognizer.create_stream()
    chunk_samples = max(1, int(sample_rate * chunk_sec))
    started = time.perf_counter()
    first_partial_sec: float | None = None
    first_partial_audio_sec: float | None = None
    first_partial_text = ""
    audio_consumed_sec = 0.0

    def decode_ready() -> None:
        nonlocal first_partial_sec, first_partial_audio_sec, first_partial_text
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
            text = recognizer_result_text(recognizer.get_result(stream))
            if text and first_partial_sec is None:
                first_partial_sec = time.perf_counter() - started
                first_partial_audio_sec = audio_consumed_sec
                first_partial_text = text

    for start in range(0, len(samples), chunk_samples):
        end = min(len(samples), start + chunk_samples)
        stream.accept_waveform(sample_rate, samples[start:end])
        audio_consumed_sec = end / sample_rate
        decode_ready()

    if tail_padding_sec > 0:
        tail = np.zeros(int(tail_padding_sec * sample_rate), dtype=np.float32)
        stream.accept_waveform(sample_rate, tail)
        audio_consumed_sec = (len(samples) + len(tail)) / sample_rate
        decode_ready()

    stream.input_finished()
    decode_ready()
    elapsed = time.perf_counter() - started
    text = recognizer_result_text(recognizer.get_result(stream))
    return {
        "text": text,
        "elapsed_sec": elapsed,
        "first_partial_sec": first_partial_sec,
        "first_partial_audio_sec": first_partial_audio_sec,
        "first_partial_text": first_partial_text,
    }


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


def run_one_sherpa_transcription(
    *,
    recognizer: Any,
    clip: dict[str, Any],
    chunk_sec: float,
    tail_padding_sec: float,
    run_index: int,
    phase: str,
) -> dict[str, Any]:
    output = transcribe_sherpa_online(
        recognizer,
        clip["audio"],
        chunk_sec=chunk_sec,
        tail_padding_sec=tail_padding_sec,
    )
    elapsed = float(output["elapsed_sec"])
    audio_sec = float(clip["audio_sec"])
    first_partial_sec = output.get("first_partial_sec")
    first_partial_audio_sec = output.get("first_partial_audio_sec")
    return {
        "phase": phase,
        "run_index": run_index,
        "clip_id": clip["id"],
        "audio": str(clip["audio"]),
        "audio_sec": round(audio_sec, 4),
        "elapsed_sec": round(elapsed, 4),
        "rtf": round(elapsed / audio_sec, 4),
        "first_partial_sec": round(first_partial_sec, 4) if first_partial_sec is not None else None,
        "first_partial_audio_sec": round(first_partial_audio_sec, 4) if first_partial_audio_sec is not None else None,
        "first_partial_text": output.get("first_partial_text") or "",
        "wer": wer(str(clip["expected"]), str(output["text"])),
        "dictation_wer": dictation_wer(str(clip["expected"]), str(output["text"])),
        "text": output["text"],
    }


def benchmark_sherpa_onnx_online_model(
    *,
    model_name: str,
    clips: list[dict[str, Any]],
    provider: str,
    num_threads: int,
    decoding_method: str,
    max_active_paths: int,
    chunk_sec: float,
    tail_padding_sec: float,
    runs: int,
    warmup_runs: int,
) -> dict[str, Any]:
    import sherpa_onnx

    model_dir = Path(model_name).expanduser()
    paths = sherpa_online_transducer_model_paths(model_dir)
    before_mem = nvidia_memory_used_mb()
    load_started = time.perf_counter()
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(paths["tokens"]),
        encoder=str(paths["encoder"]),
        decoder=str(paths["decoder"]),
        joiner=str(paths["joiner"]),
        num_threads=num_threads,
        provider=provider,
        sample_rate=16000,
        feature_dim=80,
        decoding_method=decoding_method,
        max_active_paths=max_active_paths,
    )
    load_sec = time.perf_counter() - load_started
    after_load_mem = nvidia_memory_used_mb()

    warmups: list[dict[str, Any]] = []
    first_clip = clips[0]
    for idx in range(1, warmup_runs + 1):
        warmups.append(
            run_one_sherpa_transcription(
                recognizer=recognizer,
                clip=first_clip,
                chunk_sec=chunk_sec,
                tail_padding_sec=tail_padding_sec,
                run_index=idx,
                phase="warmup",
            )
        )

    measurements: list[dict[str, Any]] = []
    for run_idx in range(1, runs + 1):
        for clip in clips:
            measurements.append(
                run_one_sherpa_transcription(
                    recognizer=recognizer,
                    clip=clip,
                    chunk_sec=chunk_sec,
                    tail_padding_sec=tail_padding_sec,
                    run_index=run_idx,
                    phase="measure",
                )
            )

    after_runs_mem = nvidia_memory_used_mb()
    del recognizer
    gc.collect()

    return {
        "backend": "sherpa-onnx-online",
        "model": model_name,
        "model_dir": str(model_dir),
        "model_files": {key: str(value) for key, value in paths.items()},
        "provider": provider,
        "num_threads": num_threads,
        "decoding_method": decoding_method,
        "max_active_paths": max_active_paths,
        "chunk_sec": chunk_sec,
        "tail_padding_sec": tail_padding_sec,
        "ok": True,
        "load_sec": round(load_sec, 4),
        "warmup": warmups,
        "runs": measurements,
        "summary": {
            "elapsed_sec": summarize([row["elapsed_sec"] for row in measurements]),
            "rtf": summarize([row["rtf"] for row in measurements]),
            "first_partial_sec": summarize(
                [row["first_partial_sec"] for row in measurements if row["first_partial_sec"] is not None]
            ),
            "first_partial_audio_sec": summarize(
                [
                    row["first_partial_audio_sec"]
                    for row in measurements
                    if row["first_partial_audio_sec"] is not None
                ]
            ),
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
                "first_partial_sec": summarize(
                    [
                        row["first_partial_sec"]
                        for row in measurements
                        if row["clip_id"] == clip["id"] and row["first_partial_sec"] is not None
                    ]
                ),
                "first_partial_audio_sec": summarize(
                    [
                        row["first_partial_audio_sec"]
                        for row in measurements
                        if row["clip_id"] == clip["id"] and row["first_partial_audio_sec"] is not None
                    ]
                ),
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


def benchmark_model_safe(backend: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        if backend == "sherpa-onnx-online":
            return benchmark_sherpa_onnx_online_model(**kwargs)
        return benchmark_faster_whisper_model(**kwargs)
    except Exception as exc:  # noqa: BLE001 - benchmark should continue across failed candidates
        row: dict[str, Any] = {
            "backend": backend,
            "model": kwargs["model_name"],
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        for key in ("device", "compute_type", "provider", "num_threads"):
            if key in kwargs:
                row[key] = kwargs[key]
        return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ASR candidate models")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", type=Path)
    source.add_argument("--manifest", type=Path, help="JSON array or JSONL rows: audio, expected, optional id")
    parser.add_argument("--expected", default="", help="expected transcript for --audio mode")
    parser.add_argument(
        "--models",
        required=True,
        help=(
            "comma-separated model names. For faster-whisper use names like "
            "tiny.en,base.en,small.en. For sherpa-onnx-online use model directories."
        ),
    )
    parser.add_argument("--backend", choices=["faster-whisper", "sherpa-onnx-online"], default="faster-whisper")
    parser.add_argument("--device", default="cuda", help="faster-whisper device")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type")
    parser.add_argument("--language", default="en", help="faster-whisper language")
    parser.add_argument("--beam-size", type=int, default=1, help="faster-whisper beam size")
    parser.add_argument("--vad-filter", action="store_true", help="faster-whisper VAD filter")
    parser.add_argument("--sherpa-provider", default=None, help="sherpa-onnx provider: cpu or cuda")
    parser.add_argument("--sherpa-num-threads", type=int, default=1)
    parser.add_argument("--sherpa-decoding-method", default="greedy_search")
    parser.add_argument("--sherpa-max-active-paths", type=int, default=4)
    parser.add_argument("--sherpa-chunk-sec", type=float, default=0.32)
    parser.add_argument("--sherpa-tail-padding-sec", type=float, default=0.66)
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
        if args.backend == "sherpa-onnx-online":
            provider = args.sherpa_provider or ("cuda" if args.device == "cuda" else "cpu")
            kwargs = {
                "model_name": model_name,
                "clips": clips,
                "provider": provider,
                "num_threads": args.sherpa_num_threads,
                "decoding_method": args.sherpa_decoding_method,
                "max_active_paths": args.sherpa_max_active_paths,
                "chunk_sec": args.sherpa_chunk_sec,
                "tail_padding_sec": args.sherpa_tail_padding_sec,
                "runs": args.runs,
                "warmup_runs": args.warmup_runs,
            }
        else:
            kwargs = {
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
        result["models"].append(benchmark_model_safe(args.backend, kwargs))

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    failures = [row for row in result["models"] if not row.get("ok")]
    return 1 if failures and len(failures) == len(result["models"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
