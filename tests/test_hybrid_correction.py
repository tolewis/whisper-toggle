"""Hybrid final correction diff."""

from __future__ import annotations

from whisper_toggle.live_typing import hybrid_correction


def apply_correction(live: str, batch: str) -> str:
    backspaces, append = hybrid_correction(live, batch)
    kept = live[: len(live) - backspaces] if backspaces else live
    return kept + append


def test_identical_strings_need_no_correction():
    assert hybrid_correction("hello world", "hello world") == (0, "")


def test_empty_live_types_entire_batch():
    assert hybrid_correction("", "hello world") == (0, "hello world")


def test_empty_batch_deletes_live_text():
    assert hybrid_correction("hello world", "") == (11, "")


def test_replaces_divergent_suffix_from_longest_common_prefix():
    assert hybrid_correction("hello wrld", "hello world") == (3, "orld")


def test_all_different_replaces_everything():
    assert hybrid_correction("rough", "accurate") == (5, "accurate")


def test_correction_transforms_live_to_batch_for_common_cases():
    cases = [
        ("the quick", "the quick brown"),
        ("the kwik brown", "the quick brown fox"),
        ("", "dictated text"),
        ("extra words", ""),
        ("same", "same"),
    ]
    for live, batch in cases:
        assert apply_correction(live, batch) == batch
