from __future__ import annotations

import os
from pathlib import Path

from whisper_toggle import cuda_env


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _isolate_site(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cuda_env.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(cuda_env.site, "getusersitepackages", lambda: str(tmp_path / "user-site"))
    monkeypatch.setattr(cuda_env.sys, "prefix", str(tmp_path / "prefix"))


def test_configure_cuda_dll_paths_prepends_nvidia_bins(tmp_path, monkeypatch):
    _isolate_site(monkeypatch, tmp_path)
    runtime = tmp_path / "nvidia" / "cuda_runtime" / "bin"
    cublas = tmp_path / "nvidia" / "cublas" / "bin"
    runtime.mkdir(parents=True)
    cublas.mkdir(parents=True)
    monkeypatch.setenv("PATH", "ORIGINAL")

    dirs = cuda_env.configure_cuda_dll_paths([tmp_path])

    assert runtime in dirs
    assert cublas in dirs
    path_parts = [p for p in os.environ["PATH"].split(os.pathsep) if p]
    assert path_parts[:2] == [str(runtime), str(cublas)]
    assert path_parts[-1] == "ORIGINAL"


def test_has_cuda12_runtime_detects_required_dlls(tmp_path, monkeypatch):
    _isolate_site(monkeypatch, tmp_path)
    _touch(tmp_path / "nvidia" / "cuda_runtime" / "bin" / "cudart64_12.dll")
    _touch(tmp_path / "nvidia" / "cublas" / "bin" / "cublas64_12.dll")
    _touch(tmp_path / "nvidia" / "cublas" / "bin" / "cublasLt64_12.dll")

    assert cuda_env.has_cuda12_runtime([tmp_path]) is True
