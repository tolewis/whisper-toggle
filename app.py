#!/usr/bin/env python3
"""Whisper API (OpenAI-compatible) — local, self-hosted.

Implements:
  POST /v1/audio/transcriptions

Intended to be compatible enough for tools that can talk to:
  https://api.openai.com/v1/audio/transcriptions

This server uses faster-whisper (CTranslate2) under the hood.

Design goals:
- Bind to 127.0.0.1 only (local machine)
- No auth by default
- Fast cold-start + predictable behavior
- Minimal response payload: {"text": "..."}

Notes:
- Model name is taken from the multipart field `model` if provided.
  Default is SMALL English-only (good balance for latency on modest GPUs).
- Compute config is intentionally conservative: int8 on CUDA.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile

from faster_whisper import WhisperModel


def env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


DEFAULT_MODEL = env("WHISPER_API_DEFAULT_MODEL", "small.en")
DEFAULT_DEVICE = env("WHISPER_API_DEVICE", "cuda")  # set to cpu if needed
DEFAULT_COMPUTE = env("WHISPER_API_COMPUTE_TYPE", "int8")
DEFAULT_LANG = env("WHISPER_API_LANGUAGE", "en")

app = FastAPI(title="Local Whisper API", version="0.1.0")

# Simple in-process cache so repeated calls don't reload weights.
_model_cache: dict[tuple[str, str, str], WhisperModel] = {}


def get_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    key = (model_name, device, compute_type)
    m = _model_cache.get(key)
    if m is None:
        m = WhisperModel(model_name, device=device, compute_type=compute_type)
        _model_cache[key] = m
    return m


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/audio/transcriptions")
def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
):
    """OpenAI-like transcription endpoint.

    Accepts multipart/form-data with at least:
      - file=@audio.wav

    Optional fields:
      - model=<model-id>
      - language=<lang>

    Returns:
      {"text": "..."}
    """

    model_name = model or DEFAULT_MODEL
    device = DEFAULT_DEVICE
    compute_type = DEFAULT_COMPUTE
    lang = language or DEFAULT_LANG

    # Persist to a temp file; faster-whisper expects a file path.
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "audio.wav")[1], delete=True) as tmp:
        tmp.write(file.file.read())
        tmp.flush()

        whisper = get_model(model_name, device, compute_type)
        segments, _info = whisper.transcribe(
            tmp.name,
            language=lang,
            vad_filter=True,
        )
        text = "".join([s.text for s in segments]).strip()

    return {"text": text}
