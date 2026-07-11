"""Streaming-native sherpa-onnx processor for live dictation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np


SAMPLE_RATE = 16000

# Process-wide cache of loaded OnlineRecognizers, keyed by device.
_RECOGNIZER_CACHE: dict = {}


def resolve_sherpa_model_dir(env: Optional[str] = None, models_root=None) -> Path:
    """Locate the sherpa transducer model without requiring an env var.

    Priority: WHISPER_SHERPA_MODEL_DIR if set, else the first sub-directory
    under the app data ``models/`` folder that contains an ``encoder.onnx``.
    This lets the bundled/downloaded model be found automatically, so the app
    works when launched from the Start Menu (which does not always propagate a
    freshly-set user env var).
    """
    env_dir = env if env is not None else os.getenv("WHISPER_SHERPA_MODEL_DIR")
    if env_dir:
        return Path(env_dir)
    if models_root is None:
        from whisper_toggle.config import app_data_dir

        models_root = app_data_dir() / "models"
    models_root = Path(models_root)
    if models_root.is_dir():
        for sub in sorted(models_root.iterdir()):
            if sub.is_dir() and (sub / "encoder.onnx").exists():
                return sub
    raise RuntimeError(
        "No sherpa model found. Set WHISPER_SHERPA_MODEL_DIR or place a model "
        f"(with encoder.onnx) under {models_root}."
    )


class SherpaStreamProcessor:
    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        language: str,
        recognizer=None,
    ):
        self.recognizer = recognizer or self._build_recognizer(device)
        self.stream = self.recognizer.create_stream()
        self._segments: list[str] = []
        self._last_partial = ""

    def insert_pcm(self, pcm: bytes) -> float:
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0:
            return 0.0
        audio = np.frombuffer(pcm[:usable], dtype=np.int16).astype(np.float32) / 32768.0
        self.stream.accept_waveform(SAMPLE_RATE, audio)
        return float(audio.size) / SAMPLE_RATE

    def process(self) -> tuple[Optional[dict], Optional[dict]]:
        self._decode_ready()
        text = self._result_text()
        if not text:
            self._last_partial = ""
            return None, None

        if self.recognizer.is_endpoint(self.stream):
            confirmed_text = text.strip()
            self._segments.append(confirmed_text)
            self._last_partial = ""
            self.recognizer.reset(self.stream)
            return {"type": "confirmed", "text": confirmed_text}, None

        partial_text = text.strip()
        if partial_text == self._last_partial:
            return None, None
        self._last_partial = partial_text
        return None, {"type": "partial", "text": partial_text}

    def finish(self) -> str:
        if hasattr(self.stream, "input_finished"):
            self.stream.input_finished()
        self._decode_ready()
        tail = self._result_text().strip()
        if tail:
            self._segments.append(tail)
            self._last_partial = ""
            if hasattr(self.recognizer, "reset"):
                self.recognizer.reset(self.stream)
        return " ".join(part for part in self._segments if part).strip()

    def _decode_ready(self) -> None:
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def _result_text(self) -> str:
        result = self.recognizer.get_result(self.stream)
        if isinstance(result, str):
            return result.strip()
        return (getattr(result, "text", "") or "").strip()

    @staticmethod
    def _build_recognizer(device: str):
        # Cache the loaded recognizer process-wide: loading is slow (~seconds),
        # and the recognizer is stateless across streams (each connection calls
        # create_stream()), so it is safe and much faster to reuse one.
        cached = _RECOGNIZER_CACHE.get(device)
        if cached is not None:
            return cached

        import sherpa_onnx

        model_dir = resolve_sherpa_model_dir()
        encoder = model_dir / "encoder.onnx"
        decoder = model_dir / "decoder.onnx"
        joiner = model_dir / "joiner.onnx"
        tokens = model_dir / "tokens.txt"
        missing = [str(path) for path in (encoder, decoder, joiner, tokens) if not path.exists()]
        if missing:
            raise RuntimeError("missing sherpa model files: " + ", ".join(missing))

        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            num_threads=2,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cuda" if device == "cuda" else "cpu",
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=20.0,
        )
        _RECOGNIZER_CACHE[device] = recognizer
        return recognizer
