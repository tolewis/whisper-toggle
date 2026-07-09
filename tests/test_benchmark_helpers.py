import importlib.util
import json
import wave
from pathlib import Path


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


benchmark = _load_script("benchmark_whisper_toggle")
asr_candidates = _load_script("benchmark_asr_candidates")


def test_wer_exact_match_is_zero():
    assert benchmark.wer("The quick brown fox", "the quick brown fox") == 0


def test_wer_counts_word_edits_after_normalization():
    assert benchmark.wer("the quick brown fox", "the quick fox") == 0.25


def test_summarize_empty_and_odd_values():
    assert benchmark.summarize([]) == {"min": None, "median": None, "max": None}
    assert benchmark.summarize([3.0, 1.0, 2.0]) == {"min": 1.0, "median": 2.0, "max": 3.0}


def test_asr_candidate_helpers_match_basic_wer_and_summary_contract():
    assert asr_candidates.wer("one two three", "one two") == 1 / 3
    assert asr_candidates.summarize([0.3, 0.1]) == {"min": 0.1, "median": 0.2, "max": 0.3}


def test_asr_manifest_loads_json_array_and_relative_audio(tmp_path):
    wav_path = tmp_path / "clip.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"id": "clip", "audio": "clip.wav", "expected": "hello world"}]),
        encoding="utf-8",
    )

    clips = asr_candidates.load_manifest(manifest)

    assert clips[0]["id"] == "clip"
    assert clips[0]["audio"] == wav_path
    assert clips[0]["expected"] == "hello world"
    assert round(clips[0]["audio_sec"], 1) == 0.1
