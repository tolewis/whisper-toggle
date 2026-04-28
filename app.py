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
- Default response: {"text": "..."}  (back-compat)
- When the caller asks for word-level timestamps (via OpenAI's
  `timestamp_granularities=word` or faster-whisper's `word_timestamps=true`)
  return a verbose_json payload with segments + words.

Notes:
- Model name is taken from the multipart field `model` if provided.
  Default is SMALL English-only (good balance for latency on modest GPUs).
- Compute config is intentionally conservative: int8 on CUDA.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional, List

from fastapi import FastAPI, File, Form, UploadFile

from faster_whisper import WhisperModel


def env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


DEFAULT_MODEL = env("WHISPER_API_DEFAULT_MODEL", "small.en")
DEFAULT_DEVICE = env("WHISPER_API_DEVICE", "cuda")  # set to cpu if needed
DEFAULT_COMPUTE = env("WHISPER_API_COMPUTE_TYPE", "int8")
DEFAULT_LANG = env("WHISPER_API_LANGUAGE", "en")
OPENAI_MODEL_ALIASES = {
    "whisper-1": DEFAULT_MODEL,
}

app = FastAPI(title="Local Whisper API", version="0.2.0")

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


def _truthy(v: Optional[str]) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "word", "segment")


@app.post("/v1/audio/transcriptions")
def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    # ─── OpenAI-compatible options ─────────────────────────────────
    response_format: Optional[str] = Form(None),  # text | json | verbose_json (default text-in-json)
    timestamp_granularities: Optional[List[str]] = Form(None),  # ["word"], ["segment"]
    # ─── faster-whisper direct flag (accepted for convenience) ─────
    word_timestamps: Optional[str] = Form(None),
    # ─── VAD toggle (default on for back-compat) ───────────────────
    vad_filter: Optional[str] = Form(None),
):
    """OpenAI-like transcription endpoint.

    Accepts multipart/form-data with at least:
      - file=@audio.wav

    Optional fields (all form-data):
      - model=<model-id>                    (default: small.en)
      - language=<lang>                     (default: en)
      - response_format=verbose_json        (return segments/words payload)
      - timestamp_granularities=word        (OpenAI-compat, request word timings)
      - word_timestamps=true                (faster-whisper direct, same effect)
      - vad_filter=false                    (disable VAD, default true)

    Returns:
      - Default:           {"text": "..."}
      - verbose_json OR when word/segment timestamps are requested:
        {
          "text": "...",
          "language": "en",
          "duration": 10.04,
          "segments": [
             {"id": 0, "start": 0.0, "end": 2.9, "text": "...",
              "words": [{"start": 0.0, "end": 0.16, "word": "Watch", "probability": 0.98}, ...]}
          ],
          "words": [{"start": 0.0, "end": 0.16, "word": "Watch", "probability": 0.98}, ...]
        }
    """

    requested_model = (model or "").strip()
    model_name = OPENAI_MODEL_ALIASES.get(requested_model, requested_model or DEFAULT_MODEL)
    device = DEFAULT_DEVICE
    compute_type = DEFAULT_COMPUTE
    lang = language or DEFAULT_LANG

    want_words = (
        _truthy(word_timestamps)
        or (timestamp_granularities is not None and any(str(g).lower() == "word" for g in timestamp_granularities))
        or (response_format or "").strip().lower() == "verbose_json"
    )
    use_vad = True if vad_filter is None else _truthy(vad_filter)

    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "audio.wav")[1], delete=True) as tmp:
        tmp.write(file.file.read())
        tmp.flush()

        whisper = get_model(model_name, device, compute_type)
        segments_iter, info = whisper.transcribe(
            tmp.name,
            language=lang,
            vad_filter=use_vad,
            word_timestamps=want_words,
        )

        if not want_words:
            # Back-compat path: flat text only.
            text = "".join([s.text for s in segments_iter]).strip()
            return {"text": text}

        # Verbose path: build segments + aggregated words list.
        out_segments = []
        out_words = []
        for idx, s in enumerate(segments_iter):
            seg_words = []
            for w in (s.words or []):
                wd = {
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": w.word,
                    "probability": round(float(w.probability or 0.0), 3),
                }
                seg_words.append(wd)
                out_words.append(wd)
            out_segments.append({
                "id": idx,
                "seek": 0,
                "start": round(float(s.start), 3),
                "end": round(float(s.end), 3),
                "text": s.text,
                "words": seg_words,
                "avg_logprob": round(float(getattr(s, "avg_logprob", 0.0) or 0.0), 4),
                "no_speech_prob": round(float(getattr(s, "no_speech_prob", 0.0) or 0.0), 4),
            })

        full_text = "".join(seg["text"] for seg in out_segments).strip()

        return {
            "text": full_text,
            "language": getattr(info, "language", lang) or lang,
            "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 3),
            "segments": out_segments,
            "words": out_words,
            "task": "transcribe",
        }
