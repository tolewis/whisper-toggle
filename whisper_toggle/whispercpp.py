"""whisper.cpp (Vulkan / iGPU) transcription backend for the local API.

Used when the device is ``vulkan`` — Intel/AMD integrated GPUs via whisper.cpp's
Vulkan backend, which faster-whisper/CTranslate2 cannot target. Shells out to the
built ``whisper-cli`` binary. Paths are auto-discovered but env-overridable
(WHISPER_CPP_BIN, WHISPER_CPP_MODEL); set WHISPER_VK_DISABLE_F16=1 if the iGPU
produces garbage with fp16.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional


def _candidates(*paths: Path):
    return [p for p in paths if p.exists()]


def resolve_whispercpp_bin(env: Optional[str] = None) -> str:
    b = env if env is not None else os.getenv("WHISPER_CPP_BIN")
    if b:
        return b
    found = _candidates(Path.home() / "whisper-toggle" / "whisper.cpp" / "build" / "bin" / "whisper-cli")
    return str(found[0]) if found else "whisper-cli"  # else rely on PATH


def resolve_whispercpp_model(env: Optional[str] = None) -> str:
    m = env if env is not None else os.getenv("WHISPER_CPP_MODEL")
    if m:
        return m
    root = Path.home() / "whisper-toggle" / "whisper.cpp" / "models"
    found = _candidates(root / "ggml-base.en.bin", root / "ggml-small.en.bin", root / "ggml-tiny.en.bin")
    if found:
        return str(found[0])
    raise RuntimeError("no whisper.cpp model found; set WHISPER_CPP_MODEL")


def build_command(wav_path: str, *, bin_path: str, model_path: str, language: str = "en") -> list[str]:
    # -nt no timestamps, -np no progress prints -> stdout is just the transcript.
    return [bin_path, "-m", model_path, "-f", wav_path, "-l", language, "-nt", "-np"]


def transcribe_whispercpp(
    wav_path: str,
    *,
    language: str = "en",
    bin_path: Optional[str] = None,
    model_path: Optional[str] = None,
    runner: Optional[Callable[[list[str]], str]] = None,
) -> str:
    cmd = build_command(
        wav_path,
        bin_path=bin_path or resolve_whispercpp_bin(),
        model_path=model_path or resolve_whispercpp_model(),
        language=language,
    )
    run = runner or _run
    return run(cmd).strip()


def _run(cmd: list[str]) -> str:  # pragma: no cover - real subprocess
    env = os.environ.copy()
    if os.getenv("WHISPER_VK_DISABLE_F16", "").strip().lower() in ("1", "true", "yes", "on"):
        env["GGML_VK_DISABLE_F16"] = "1"
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"whisper-cli failed ({p.returncode}): {p.stderr[-400:]}")
    return p.stdout
