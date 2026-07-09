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
augment_corpus = _load_script("augment_benchmark_corpus")


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


def test_dictation_wer_treats_common_number_spellings_as_equivalent():
    expected = "July ninth at ten thirty AM and remind me in fifteen minutes"
    actual = "July 9 at 10 30 a.m and remind me in 15 minutes"

    assert asr_candidates.wer(expected, actual) > 0
    assert asr_candidates.dictation_wer(expected, actual) == 0


def test_sherpa_online_transducer_model_paths_prefer_int8(tmp_path):
    for name in [
        "tokens.txt",
        "encoder-epoch-1.onnx",
        "encoder-epoch-1.int8.onnx",
        "decoder-epoch-1.onnx",
        "joiner-epoch-1.onnx",
        "joiner-epoch-1.int8.onnx",
    ]:
        (tmp_path / name).write_text("", encoding="utf-8")

    paths = asr_candidates.sherpa_online_transducer_model_paths(tmp_path)

    assert paths["tokens"].name == "tokens.txt"
    assert paths["encoder"].name == "encoder-epoch-1.int8.onnx"
    assert paths["decoder"].name == "decoder-epoch-1.onnx"
    assert paths["joiner"].name == "joiner-epoch-1.int8.onnx"


def test_recognizer_result_text_accepts_string_or_text_object():
    class Result:
        text = " hello "

    assert asr_candidates.recognizer_result_text(" hi ") == "hi"
    assert asr_candidates.recognizer_result_text(Result()) == "hello"


def _write_test_wav(path: Path, frames: bytes = b"\x00\x00" * 1600) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(frames)


def test_asr_manifest_loads_json_array_and_relative_audio(tmp_path):
    wav_path = tmp_path / "clip.wav"
    _write_test_wav(wav_path)
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


def test_augment_corpus_writes_deterministic_noisy_manifest(tmp_path):
    wav_path = tmp_path / "clip.wav"
    frames = b"\xe8\x03" * 1600  # constant non-silent i16 sample value 1000
    _write_test_wav(wav_path, frames)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"id": "clip", "audio": "clip.wav", "expected": "hello world"}]),
        encoding="utf-8",
    )

    out_dir = tmp_path / "noise"
    rows = augment_corpus.augment_manifest(manifest, out_dir, snr_db=15.0, seed=7, id_suffix="-noise15")

    assert rows == [
        {
            "id": "clip-noise15",
            "expected": "hello world",
            "audio": "clip-noise15.wav",
            "augmentation": {"type": "white_noise", "snr_db": 15.0, "seed": 8},
        }
    ]
    assert (out_dir / "clip-noise15.wav").exists()
    assert json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")) == rows
    assert augment_corpus.read_wav_i16(out_dir / "clip-noise15.wav")[1] != [1000] * 1600
