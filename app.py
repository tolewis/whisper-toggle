#!/usr/bin/env python3
"""Whisper API (OpenAI-compatible) - local, self-hosted.

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

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, List

import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

try:
    from whisper_toggle.cuda_env import configure_cuda_dll_paths, has_cuda12_runtime

    configure_cuda_dll_paths()
except Exception:
    # CUDA path setup must never prevent CPU-only startup.
    pass

from faster_whisper import WhisperModel

try:
    from whisper_toggle.logging_setup import setup_logging

    log = setup_logging("whisper-toggle.api")
except Exception:  # pragma: no cover - logging must never block API startup
    import logging

    log = logging.getLogger("whisper-toggle.api")


def env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


DEFAULT_MODEL = env("WHISPER_API_DEFAULT_MODEL", "small.en")
DEFAULT_DEVICE = env("WHISPER_API_DEVICE", "cuda")  # set to cpu if needed
DEFAULT_COMPUTE = env("WHISPER_API_COMPUTE_TYPE", "int8")
DEFAULT_LANG = env("WHISPER_API_LANGUAGE", "en")
STREAM_SAMPLE_RATE = int(env("WHISPER_STREAM_SAMPLE_RATE", "16000"))
STREAM_PARTIAL_INTERVAL = float(env("WHISPER_STREAM_PARTIAL_INTERVAL", "0.25"))
STREAM_BUFFER_TRIM_SEC = float(env("WHISPER_STREAM_BUFFER_TRIM_SEC", "15"))
OPENAI_MODEL_ALIASES = {
    "whisper-1": DEFAULT_MODEL,
}

APP_VERSION = env("WHISPER_API_VERSION", "2.0.4")
app = FastAPI(title="Local Whisper API", version=APP_VERSION)


@app.on_event("startup")
def _preload_model_if_requested():
    """Optional warm-load/smoke so first dictation is not a cold start.

    A plain WhisperModel constructor is not enough on CUDA: missing cuBLAS can
    surface only when the first encode runs. When WHISPER_API_REQUIRE_SMOKE=1,
    startup consumes a tiny silence transcription and lets uvicorn exit on any
    CUDA/model failure.
    """
    if env("WHISPER_API_PRELOAD", "0").strip() not in ("1", "true", "yes", "on"):
        return
    try:
        model = get_model(DEFAULT_MODEL, DEFAULT_DEVICE, DEFAULT_COMPUTE)
        if env("WHISPER_API_REQUIRE_SMOKE", "0").strip() in ("1", "true", "yes", "on"):
            _smoke_model(model)
    except Exception:
        log.exception("model preload/smoke failed")
        if env("WHISPER_API_REQUIRE_SMOKE", "0").strip() in ("1", "true", "yes", "on"):
            raise


def _smoke_model(model: WhisperModel) -> None:
    """Run one tiny transcription so CUDA/model failures happen at startup."""
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with wave.open(tmp_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(STREAM_SAMPLE_RATE)
            wav.writeframes(b"\x00\x00" * STREAM_SAMPLE_RATE)
        segments, _info = model.transcribe(
            tmp_path,
            language=DEFAULT_LANG,
            vad_filter=False,
            beam_size=1,
        )
        # Consume the generator: CTranslate2 encode errors surface here.
        _ = "".join(segment.text for segment in segments)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

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


@app.get("/v1/runtime")
def runtime():
    """Report active device/model for tray GUI and install acceptance."""
    backend = "faster-whisper"
    if DEFAULT_DEVICE in ("vulkan", "whisper.cpp"):
        backend = "whisper.cpp"
    try:
        cuda12_runtime_present = has_cuda12_runtime() if DEFAULT_DEVICE == "cuda" else None
    except Exception:
        cuda12_runtime_present = None
    return {
        "ok": True,
        "version": APP_VERSION,
        "device": DEFAULT_DEVICE,
        "compute_type": DEFAULT_COMPUTE,
        "model": DEFAULT_MODEL,
        "language": DEFAULT_LANG,
        "backend": backend,
        "streaming": True,
        "stream_sample_rate": STREAM_SAMPLE_RATE,
        "cuda12_runtime_present": cuda12_runtime_present,
        "models_cached": [list(k) for k in _model_cache.keys()],
    }


def _truthy(v: Optional[str]) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "word", "segment")


def _load_whisper_online_module():
    """Load ufal whisper_streaming's whisper_online module.

    The upstream repository is not a Python package, so deploy.sh clones the
    pinned source under vendor/whisper_streaming. Normal imports are tried first
    so developer installs can still provide the module on PYTHONPATH.
    """

    for module_name in ("whisper_online", "whisper_streaming.whisper_online"):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            pass

    candidates = []
    configured = os.getenv("WHISPER_STREAMING_PATH")
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(__file__).resolve().parent / "vendor" / "whisper_streaming")

    for base in candidates:
        source = base / "whisper_online.py"
        if not source.exists():
            continue
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        spec = importlib.util.spec_from_file_location("whisper_online", source)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("whisper_online", module)
        spec.loader.exec_module(module)
        return module

    raise RuntimeError(
        "whisper_streaming is not installed. Run scripts/deploy.sh to install "
        "the pinned ufal whisper_streaming source."
    )


class StreamingASRProcessor:
    def __init__(self, model_name: str, device: str, compute_type: str, language: str):
        whisper_online = _load_whisper_online_module()
        shared_model = get_model(model_name, device, compute_type)

        class SharedFasterWhisperASR(whisper_online.FasterWhisperASR):
            def __init__(self, lan: str, model: WhisperModel):
                self._shared_model = model
                super().__init__(lan, modelsize=model_name)

            def load_model(self, modelsize=None, cache_dir=None, model_dir=None):
                return self._shared_model

        asr = SharedFasterWhisperASR(language, shared_model)
        self.online = whisper_online.OnlineASRProcessor(
            asr,
            buffer_trimming=("segment", STREAM_BUFFER_TRIM_SEC),
        )
        self.confirmed_text = ""

    def insert_pcm(self, pcm: bytes) -> float:
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0:
            return 0.0
        audio = np.frombuffer(pcm[:usable], dtype=np.int16).astype(np.float32) / 32768.0
        self.online.insert_audio_chunk(audio)
        return float(audio.size) / STREAM_SAMPLE_RATE

    def process(self) -> tuple[Optional[dict], Optional[dict]]:
        start_t, end_t, text = self.online.process_iter()
        confirmed = None
        if text:
            self.confirmed_text = (self.confirmed_text + text).strip()
            confirmed = {
                "type": "confirmed",
                "text": text.strip(),
                "start_t": round(float(start_t or 0.0), 3),
                "end_t": round(float(end_t or 0.0), 3),
            }

        p_start, p_end, p_text = self.online.to_flush(self.online.transcript_buffer.complete())
        partial = None
        if p_text:
            partial = {
                "type": "partial",
                "text": p_text.strip(),
                "start_t": round(float(p_start or 0.0), 3),
                "end_t": round(float(p_end or 0.0), 3),
            }
        return confirmed, partial

    def finish(self) -> str:
        _start_t, _end_t, final_flush = self.online.finish()
        return (self.confirmed_text + final_flush).strip()


def create_stream_processor(model_name: str, device: str, compute_type: str, language: str):
    return StreamingASRProcessor(model_name, device, compute_type, language)


async def _send_json(websocket: WebSocket, payload: dict):
    if websocket.application_state == WebSocketState.CONNECTED:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))


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

    # Windows cannot open a NamedTemporaryFile that is still held by this process
    # (PermissionError from PyAV). Write, close, then transcribe, then delete.
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    try:
        tmp.write(file.file.read())
        tmp.flush()
        tmp.close()

        whisper = get_model(model_name, device, compute_type)
        segments_iter, info = whisper.transcribe(
            tmp_path,
            language=lang,
            vad_filter=use_vad,
            word_timestamps=want_words,
        )

        if not want_words:
            text = "".join([s.text for s in segments_iter]).strip()
            return {"text": text}

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
    finally:
        try:
            if not tmp.file.closed:
                tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.websocket("/v1/audio/stream")
async def audio_stream(websocket: WebSocket):
    await websocket.accept()

    model_name = OPENAI_MODEL_ALIASES.get(
        websocket.query_params.get("model", ""),
        websocket.query_params.get("model") or DEFAULT_MODEL,
    )
    lang = websocket.query_params.get("language") or DEFAULT_LANG
    try:
        processor = create_stream_processor(model_name, DEFAULT_DEVICE, DEFAULT_COMPUTE, lang)
    except Exception as exc:  # noqa: BLE001
        log.exception("stream processor init failed")
        await _send_json(websocket, {"type": "error", "error": f"stream_init_failed: {exc}"})
        await websocket.close()
        return

    audio_duration = 0.0
    last_partial_at = 0.0
    last_partial_text = ""

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                audio_duration += processor.insert_pcm(message["bytes"] or b"")
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"] or "{}")
                except json.JSONDecodeError:
                    await _send_json(websocket, {"type": "error", "error": "invalid_json"})
                    continue
                if control.get("type") == "end":
                    break
                await _send_json(websocket, {"type": "error", "error": "unknown_control"})
                continue

            now = time.monotonic()
            if now - last_partial_at < STREAM_PARTIAL_INTERVAL:
                continue

            confirmed, partial = await asyncio.to_thread(processor.process)
            last_partial_at = now
            if confirmed is not None:
                await _send_json(websocket, confirmed)
            if partial is not None and partial["text"] != last_partial_text:
                last_partial_text = partial["text"]
                await _send_json(websocket, partial)

        confirmed, partial = await asyncio.to_thread(processor.process)
        if confirmed is not None:
            await _send_json(websocket, confirmed)
        if partial is not None and partial["text"] != last_partial_text:
            await _send_json(websocket, partial)

        final_text = await asyncio.to_thread(processor.finish)
        await _send_json(
            websocket,
            {
                "type": "final",
                "text": final_text,
                "duration": round(audio_duration, 3),
            },
        )
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("stream endpoint failed")
        try:
            await _send_json(websocket, {"type": "error", "error": str(exc)})
        except Exception:
            pass
    finally:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
