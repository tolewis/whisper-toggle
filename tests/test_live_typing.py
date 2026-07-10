"""Append-only live-streaming diff."""

from __future__ import annotations

from whisper_toggle.live_typing import next_to_type


def test_first_words():
    assert next_to_type("", "hello") == "hello"


def test_cumulative_server_text():
    assert next_to_type("the quick", "the quick brown") == " brown"
    assert next_to_type("the quick brown", "the quick brown fox") == " fox"


def test_incremental_server_segments():
    assert next_to_type("the quick", "brown fox") == " brown fox"


def test_no_leading_space_when_typed_ends_with_space():
    assert next_to_type("the quick ", "brown") == "brown"


def test_duplicate_confirmed_types_nothing():
    assert next_to_type("the quick brown", "brown") == ""
    assert next_to_type("the quick brown", "the quick brown") == ""


def test_empty_confirmed_types_nothing():
    assert next_to_type("anything", "") == ""
    assert next_to_type("anything", "   ") == ""


def test_full_stream_appends_without_backspace():
    typed = ""
    for seg in ["Hello", "Hello world", "Hello world, this", "Hello world, this is live"]:
        typed += next_to_type(typed, seg)
    assert typed == "Hello world, this is live"
