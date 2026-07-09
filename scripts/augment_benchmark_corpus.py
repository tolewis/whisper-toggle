#!/usr/bin/env python3
"""Create deterministic noisy variants of a WAV benchmark corpus.

The goal is not to replace real microphone recordings. It provides a repeatable
stress pass between clean SAPI clips and human/live desktop validation so model
selection is less likely to overfit a pristine synthetic voice.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import wave
from pathlib import Path
from typing import Any

MAX_I16 = 32767
MIN_I16 = -32768


def read_manifest(path: Path) -> list[dict[str, Any]]:
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
        if not audio.exists():
            raise FileNotFoundError(f"manifest row {idx} audio missing: {audio}")
        clips.append({**row, "audio": audio})
    return clips


def read_wav_i16(path: Path) -> tuple[wave._wave_params, list[int]]:
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        if params.sampwidth != 2:
            raise ValueError(f"only 16-bit PCM WAV is supported: {path}")
        frames = wav.readframes(params.nframes)
    if len(frames) % 2:
        raise ValueError(f"odd 16-bit PCM byte count: {path}")
    count = len(frames) // 2
    samples = list(struct.unpack(f"<{count}h", frames))
    return params, samples


def write_wav_i16(path: Path, params: wave._wave_params, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = [max(MIN_I16, min(MAX_I16, int(round(sample)))) for sample in samples]
    data = struct.pack(f"<{len(clipped)}h", *clipped)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(params.nchannels)
        wav.setsampwidth(params.sampwidth)
        wav.setframerate(params.framerate)
        wav.writeframes(data)


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def add_white_noise(samples: list[int], *, snr_db: float, seed: int) -> list[int]:
    """Return samples with deterministic white noise at the requested SNR."""

    signal_rms = rms(samples)
    if signal_rms <= 0:
        return list(samples)
    noise_rms = signal_rms / (10 ** (snr_db / 20.0))
    rng = random.Random(seed)
    return [sample + rng.gauss(0.0, noise_rms) for sample in samples]


def augment_manifest(
    manifest_path: Path,
    out_dir: Path,
    *,
    snr_db: float,
    seed: int,
    id_suffix: str,
) -> list[dict[str, Any]]:
    clips = read_manifest(manifest_path)
    out_rows: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, start=1):
        audio = Path(clip["audio"])
        params, samples = read_wav_i16(audio)
        noisy = add_white_noise(samples, snr_db=snr_db, seed=seed + idx)
        clean_id = str(clip.get("id") or audio.stem)
        out_name = f"{clean_id}{id_suffix}.wav"
        out_audio = out_dir / out_name
        write_wav_i16(out_audio, params, noisy)
        row = {k: v for k, v in clip.items() if k != "audio"}
        row["id"] = f"{clean_id}{id_suffix}"
        row["audio"] = out_name
        row["augmentation"] = {"type": "white_noise", "snr_db": snr_db, "seed": seed + idx}
        out_rows.append(row)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(out_rows, indent=2) + "\n", encoding="utf-8")
    return out_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Input corpus manifest.json/jsonl")
    parser.add_argument("--out-dir", required=True, type=Path, help="Output directory for noisy WAVs + manifest.json")
    parser.add_argument("--snr-db", type=float, default=15.0, help="Target signal-to-noise ratio in dB")
    parser.add_argument("--seed", type=int, default=20260709, help="Base deterministic random seed")
    parser.add_argument("--id-suffix", default="-noise15", help="Suffix appended to clip ids and WAV filenames")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = augment_manifest(
        args.manifest,
        args.out_dir,
        snr_db=args.snr_db,
        seed=args.seed,
        id_suffix=args.id_suffix,
    )
    print(
        json.dumps(
            {
                "manifest": str(args.out_dir / "manifest.json"),
                "clips": len(rows),
                "snr_db": args.snr_db,
                "seed": args.seed,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
