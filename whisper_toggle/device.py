"""Capability-based device selection for NVIDIA, Vulkan (Intel), and CPU."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable, Optional


VALID_OVERRIDES = frozenset({"auto", "cuda", "cpu", "vulkan"})


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    backend: str
    detail: str
    vram_mb: Optional[int] = None


@dataclass(frozen=True)
class DeviceChoice:
    device: str
    backend: str
    compute_type: str
    model: str
    override: str
    detail: str
    vram_mb: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _default_probe_cuda() -> ProbeResult:
    if shutil.which("nvidia-smi") is None:
        return ProbeResult(ok=False, backend="cuda", detail="nvidia-smi not found")
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        # Sum across GPUs; use first line for primary
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        vram = int(float(lines[0])) if lines else None
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(ok=False, backend="cuda", detail=f"nvidia-smi failed: {exc}")

    cuda_dll_detail = "cuda DLL path probe not run"
    try:
        from .cuda_env import configure_cuda_dll_paths, has_cuda12_runtime

        configure_cuda_dll_paths()
        cuda_dll_detail = "cuda12 runtime DLLs present" if has_cuda12_runtime() else "cuda12 runtime DLLs not found"
    except Exception as exc:  # noqa: BLE001
        cuda_dll_detail = f"cuda DLL path probe soft-fail: {exc}"

    # Optional deeper probe: try importing ctranslate2 cuda — soft fail to still report nvidia present.
    # The install/runtime smoke consumes a real transcription and catches missing cuBLAS/cudart.
    try:
        import ctranslate2  # type: ignore

        if hasattr(ctranslate2, "get_cuda_device_count"):
            n = int(ctranslate2.get_cuda_device_count())
            if n <= 0:
                return ProbeResult(
                    ok=False,
                    backend="cuda",
                    detail=f"ctranslate2 reports 0 CUDA devices; {cuda_dll_detail}",
                    vram_mb=vram,
                )
    except Exception as exc:  # noqa: BLE001
        # nvidia-smi present is still a strong signal; smoke/load will catch hard failures.
        return ProbeResult(
            ok=True,
            backend="cuda",
            detail=f"nvidia-smi ok; {cuda_dll_detail}; ctranslate2 probe soft-fail: {exc}",
            vram_mb=vram,
        )

    return ProbeResult(ok=True, backend="cuda", detail=f"cuda available; {cuda_dll_detail}", vram_mb=vram)


def _default_probe_vulkan() -> ProbeResult:
    """Detect whisper.cpp Vulkan binary + runtime (Intel Iris path).

    Presence of `whisper-cli`/`whisper-stream` with a vulkan build, or an explicit
    WHISPER_VULKAN_BIN env, marks the path available. Full model load is deferred.
    """

    explicit = os.getenv("WHISPER_VULKAN_BIN", "").strip()
    candidates = []
    if explicit:
        candidates.append(explicit)
    for name in ("whisper-cli", "whisper-cli.exe", "main", "whisper"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # Also look in common app-local paths
    local = os.getenv("LOCALAPPDATA") or os.getenv("HOME") or ""
    for rel in (
        "Whisper Toggle/vulkan/whisper-cli.exe",
        "Whisper Toggle/vulkan/whisper-cli",
        ".local/share/whisper-toggle/vulkan/whisper-cli",
    ):
        path = os.path.join(local, rel)
        if os.path.isfile(path):
            candidates.append(path)

    for bin_path in candidates:
        try:
            # Help text often mentions ggml backends
            proc = subprocess.run(
                [bin_path, "-h"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            blob = (proc.stdout or "") + (proc.stderr or "")
            if "vulkan" in blob.lower() or os.getenv("WHISPER_FORCE_VULKAN") == "1":
                return ProbeResult(
                    ok=True,
                    backend="vulkan",
                    detail=f"vulkan binary: {bin_path}",
                )
            # If user pointed WHISPER_VULKAN_BIN, trust it
            if explicit and bin_path == explicit:
                return ProbeResult(
                    ok=True,
                    backend="vulkan",
                    detail=f"explicit WHISPER_VULKAN_BIN: {bin_path}",
                )
        except Exception:
            continue

    return ProbeResult(ok=False, backend="vulkan", detail="no vulkan whisper binary")


class DeviceResolver:
    def __init__(
        self,
        probe_cuda: Optional[Callable[[], ProbeResult]] = None,
        probe_vulkan: Optional[Callable[[], ProbeResult]] = None,
        override: str = "auto",
        model_override: str = "",
    ):
        self.probe_cuda = probe_cuda or _default_probe_cuda
        self.probe_vulkan = probe_vulkan or _default_probe_vulkan
        self.override = (override or "auto").strip().lower()
        self.model_override = (model_override or "").strip()

    def resolve(self) -> DeviceChoice:
        override = self.override if self.override in VALID_OVERRIDES else "auto"

        if override == "cpu":
            return self._cpu("override=cpu")
        if override == "cuda":
            cuda = self.probe_cuda()
            if cuda.ok:
                return self._cuda(cuda)
            return self._cpu(f"override=cuda failed: {cuda.detail}")
        if override == "vulkan":
            vulkan = self.probe_vulkan()
            if vulkan.ok:
                return self._vulkan(vulkan)
            return self._cpu(f"override=vulkan failed: {vulkan.detail}")

        # auto
        cuda = self.probe_cuda()
        if cuda.ok:
            return self._cuda(cuda)
        vulkan = self.probe_vulkan()
        if vulkan.ok:
            return self._vulkan(vulkan)
        return self._cpu("auto -> cpu")

    def _model_for(self, device: str) -> str:
        if self.model_override:
            return self.model_override
        if device == "cuda":
            return "small.en"
        # cpu / vulkan — prefer base.en for latency on modest GPUs
        return "base.en"

    def _cuda(self, probe: ProbeResult) -> DeviceChoice:
        return DeviceChoice(
            device="cuda",
            backend="faster-whisper",
            compute_type="int8",
            model=self._model_for("cuda"),
            override=self.override if self.override in VALID_OVERRIDES else "auto",
            detail=probe.detail,
            vram_mb=probe.vram_mb,
        )

    def _vulkan(self, probe: ProbeResult) -> DeviceChoice:
        return DeviceChoice(
            device="vulkan",
            backend="whisper.cpp",
            compute_type="vulkan",
            model=self._model_for("vulkan"),
            override=self.override if self.override in VALID_OVERRIDES else "auto",
            detail=probe.detail,
            vram_mb=probe.vram_mb,
        )

    def _cpu(self, detail: str) -> DeviceChoice:
        return DeviceChoice(
            device="cpu",
            backend="faster-whisper",
            compute_type="int8",
            model=self._model_for("cpu"),
            override=self.override if self.override in VALID_OVERRIDES else "auto",
            detail=detail,
            vram_mb=None,
        )


def resolve_device(override: str = "auto", model_override: str = "") -> DeviceChoice:
    return DeviceResolver(override=override, model_override=model_override).resolve()


if __name__ == "__main__":
    import json

    choice = resolve_device(os.getenv("WHISPER_DEVICE_OVERRIDE", "auto"))
    print(json.dumps(choice.to_dict(), indent=2))
