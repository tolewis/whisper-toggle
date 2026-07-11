"""whisper.cpp (vulkan) backend — path resolution + command + runner."""

from __future__ import annotations

from whisper_toggle.whispercpp import (
    build_command,
    resolve_whispercpp_bin,
    resolve_whispercpp_model,
    transcribe_whispercpp,
)


def test_resolve_bin_env_override():
    assert resolve_whispercpp_bin(env="/x/whisper-cli") == "/x/whisper-cli"


def test_resolve_model_env_override():
    assert resolve_whispercpp_model(env="/x/ggml.bin") == "/x/ggml.bin"


def test_build_command():
    assert build_command("a.wav", bin_path="wc", model_path="m.bin", language="en") == [
        "wc", "-m", "m.bin", "-f", "a.wav", "-l", "en", "-nt", "-np",
    ]


def test_transcribe_uses_runner_and_strips():
    seen = {}
    def fake_runner(cmd):
        seen["cmd"] = cmd
        return "  hello world \n"
    out = transcribe_whispercpp("a.wav", bin_path="wc", model_path="m.bin", runner=fake_runner)
    assert out == "hello world"
    assert seen["cmd"][0] == "wc" and "a.wav" in seen["cmd"]
