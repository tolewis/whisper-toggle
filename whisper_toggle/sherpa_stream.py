"""Streaming-native sherpa-onnx processor for live dictation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np


SAMPLE_RATE = 16000


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
        return (getattr(result, "text", "") or "").strip()

    @staticmethod
    def _build_recognizer(device: str):
        import sherpa_onnx

        model_dir_raw = os.getenv("WHISPER_SHERPA_MODEL_DIR")
        if not model_dir_raw:
            raise RuntimeError("WHISPER_SHERPA_MODEL_DIR must point to a sherpa transducer model")
        model_dir = Path(model_dir_raw)
        encoder = model_dir / "encoder.onnx"
        decoder = model_dir / "decoder.onnx"
        joiner = model_dir / "joiner.onnx"
        tokens = model_dir / "tokens.txt"
        missing = [str(path) for path in (encoder, decoder, joiner, tokens) if not path.exists()]
        if missing:
            raise RuntimeError("missing sherpa model files: " + ", ".join(missing))

        return sherpa_onnx.OnlineRecognizer.from_transducer(
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
