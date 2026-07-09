import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "benchmark_whisper_toggle",
    Path(__file__).resolve().parents[1] / "scripts" / "benchmark_whisper_toggle.py",
)
benchmark = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(benchmark)


def test_wer_exact_match_is_zero():
    assert benchmark.wer("The quick brown fox", "the quick brown fox") == 0


def test_wer_counts_word_edits_after_normalization():
    assert benchmark.wer("the quick brown fox", "the quick fox") == 0.25


def test_summarize_empty_and_odd_values():
    assert benchmark.summarize([]) == {"min": None, "median": None, "max": None}
    assert benchmark.summarize([3.0, 1.0, 2.0]) == {"min": 1.0, "median": 2.0, "max": 3.0}
