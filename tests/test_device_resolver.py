"""DeviceResolver unit tests — no GPU required (injected probes)."""

from __future__ import annotations

from whisper_toggle.device import DeviceChoice, DeviceResolver, ProbeResult


def test_prefers_cuda_when_smoke_passes():
    def probe_cuda():
        return ProbeResult(ok=True, backend="cuda", detail="smoke ok", vram_mb=6144)

    def probe_vulkan():
        return ProbeResult(ok=False, backend="vulkan", detail="not tried")

    choice = DeviceResolver(
        probe_cuda=probe_cuda,
        probe_vulkan=probe_vulkan,
        override="auto",
    ).resolve()
    assert choice.device == "cuda"
    assert choice.backend == "faster-whisper"
    assert choice.compute_type == "int8"
    assert choice.model == "small.en"


def test_falls_back_cpu_when_cuda_load_fails():
    def probe_cuda():
        return ProbeResult(ok=False, backend="cuda", detail="no module")

    def probe_vulkan():
        return ProbeResult(ok=False, backend="vulkan", detail="missing")

    choice = DeviceResolver(
        probe_cuda=probe_cuda,
        probe_vulkan=probe_vulkan,
        override="auto",
    ).resolve()
    assert choice.device == "cpu"
    assert choice.backend == "faster-whisper"
    assert choice.model == "base.en"
    assert choice.compute_type in ("int8", "float32")


def test_iris_like_host_selects_vulkan_when_available():
    def probe_cuda():
        return ProbeResult(ok=False, backend="cuda", detail="no nvidia")

    def probe_vulkan():
        return ProbeResult(ok=True, backend="vulkan", detail="iris xe", vram_mb=2048)

    choice = DeviceResolver(
        probe_cuda=probe_cuda,
        probe_vulkan=probe_vulkan,
        override="auto",
    ).resolve()
    assert choice.device == "vulkan"
    assert choice.backend == "whisper.cpp"
    assert choice.model in ("base.en", "small.en")


def test_model_tier_small_on_cuda_base_on_cpu():
    cuda = DeviceResolver(
        probe_cuda=lambda: ProbeResult(ok=True, backend="cuda", detail="ok", vram_mb=8000),
        probe_vulkan=lambda: ProbeResult(ok=False, backend="vulkan", detail="n/a"),
        override="auto",
    ).resolve()
    cpu = DeviceResolver(
        probe_cuda=lambda: ProbeResult(ok=False, backend="cuda", detail="n/a"),
        probe_vulkan=lambda: ProbeResult(ok=False, backend="vulkan", detail="n/a"),
        override="auto",
    ).resolve()
    assert cuda.model == "small.en"
    assert cpu.model == "base.en"


def test_config_override_wins():
    choice = DeviceResolver(
        probe_cuda=lambda: ProbeResult(ok=True, backend="cuda", detail="ok", vram_mb=6000),
        probe_vulkan=lambda: ProbeResult(ok=True, backend="vulkan", detail="ok"),
        override="cpu",
    ).resolve()
    assert choice.device == "cpu"
    assert choice.backend == "faster-whisper"
    assert choice.model == "base.en"
    assert choice.override == "cpu"


def test_probe_result_is_json_serializable():
    choice = DeviceChoice(
        device="cuda",
        backend="faster-whisper",
        compute_type="int8",
        model="small.en",
        override="auto",
        detail="smoke ok",
        vram_mb=6144,
    )
    payload = choice.to_dict()
    assert payload["device"] == "cuda"
    assert payload["model"] == "small.en"
    assert isinstance(payload["vram_mb"], int)


def test_invalid_override_falls_back_to_auto():
    choice = DeviceResolver(
        probe_cuda=lambda: ProbeResult(ok=False, backend="cuda", detail="n/a"),
        probe_vulkan=lambda: ProbeResult(ok=False, backend="vulkan", detail="n/a"),
        override="potato",
    ).resolve()
    assert choice.device == "cpu"
