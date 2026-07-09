import importlib.util
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
