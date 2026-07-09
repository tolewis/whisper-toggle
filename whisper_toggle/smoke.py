"""Install/runtime smoke checks for Whisper Toggle.

The critical Windows failure mode is a model object that appears loaded while the
first real encode crashes because CUDA DLLs (for example cublas64_12.dll) are not
on the process DLL search path. These checks consume a tiny transcription so CUDA
kernels and model weights are actually exercised.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from .config import load_config
from .cuda_env import configure_cuda_dll_paths, has_cuda12_runtime
from .device import resolve_device


def _write_silence_wav(path: Path, sample_rate: int = 16000, seconds: float = 1.0) -> None:
    samples = max(1, int(sample_rate * seconds))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * samples)


def smoke_transcribe(
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: str = "en",
    seconds: float = 1.0,
) -> dict[str, Any]:
    """Load and execute a minimal faster-whisper transcription."""

    started = time.monotonic()
    cuda_dirs = configure_cuda_dll_paths()

    # Import after configure_cuda_dll_paths so Windows can resolve CUDA DLLs.
    from faster_whisper import WhisperModel

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _write_silence_wav(tmp_path, seconds=seconds)
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            str(tmp_path),
            language=language,
            vad_filter=False,
            beam_size=1,
        )
        # Consume the generator. CUDA failures often appear only here.
        text = "".join(segment.text for segment in segments).strip()
        elapsed = round(time.monotonic() - started, 3)
        return {
            "ok": True,
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "language": getattr(info, "language", language) or language,
            "text_preview": text[:80],
            "elapsed_sec": elapsed,
            "cuda_dirs": [str(p) for p in cuda_dirs],
            "cuda12_runtime_present": has_cuda12_runtime(),
        }
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def smoke_from_config() -> dict[str, Any]:
    cfg = load_config()
    choice = resolve_device(cfg.device_override, cfg.model)
    device = choice.device
    compute = choice.compute_type
    if device == "vulkan":
        # Windows v2 package does not ship whisper.cpp Vulkan yet; use the same
        # fallback as tray_app.py.
        device = "cpu"
        compute = "int8"
    model = cfg.model or choice.model
    return smoke_transcribe(
        model_name=model,
        device=device if device in ("cuda", "cpu") else "cpu",
        compute_type=compute if compute in ("int8", "float16", "float32") else "int8",
        language="en",
    ) | {"resolved_choice": choice.to_dict()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Whisper Toggle install/runtime smoke test")
    parser.add_argument("--from-config", action="store_true", help="resolve model/device from app config")
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="en")
    parser.add_argument("--log", default="", help="optional JSON output path")
    args = parser.parse_args(argv)

    try:
        if args.from_config:
            result = smoke_from_config()
        else:
            result = smoke_transcribe(
                model_name=args.model,
                device=args.device,
                compute_type=args.compute_type,
                language=args.language,
            )
        code = 0
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "path": os.environ.get("PATH", ""),
        }
        code = 1

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if args.log:
        path = Path(args.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":  # pragma: no cover - exercised by installer/manual smoke
    raise SystemExit(main())
