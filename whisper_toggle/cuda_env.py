"""Windows CUDA DLL path setup for bundled faster-whisper/CTranslate2.

CTranslate2 wheels intentionally do not bundle all CUDA DLLs. On Windows the
NVIDIA pip wheels place CUDA runtime/cuBLAS DLLs under site-packages/nvidia/*/bin,
which is not searched by LoadLibrary unless the process adds it explicitly.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path
from typing import Iterable

_DLL_HANDLES: list[object] = []
_CONFIGURED_DIRS: list[Path] = []


def _candidate_roots(extra_roots: Iterable[Path] = ()) -> list[Path]:
    roots: list[Path] = []
    for raw in site.getsitepackages():
        roots.append(Path(raw))
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")
    roots.extend(extra_roots)

    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _cuda_bin_dirs(extra_roots: Iterable[Path] = ()) -> list[Path]:
    dirs: list[Path] = []
    for root in _candidate_roots(extra_roots):
        nvidia = root / "nvidia"
        for rel in (
            "cuda_runtime/bin",
            "cublas/bin",
            "cudnn/bin",
            "cufft/bin",
            "curand/bin",
            "cusolver/bin",
            "cusparse/bin",
            "nvjitlink/bin",
        ):
            path = nvidia / rel
            if path.is_dir():
                dirs.append(path)
        ctranslate2 = root / "ctranslate2"
        if ctranslate2.is_dir():
            dirs.append(ctranslate2)

    cuda_path = os.getenv("CUDA_PATH") or os.getenv("CUDA_HOME")
    if cuda_path:
        path = Path(cuda_path) / "bin"
        if path.is_dir():
            dirs.append(path)

    toolkit_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if toolkit_root.is_dir():
        for version_dir in sorted(toolkit_root.glob("v*"), reverse=True):
            bin_dir = version_dir / "bin"
            if bin_dir.is_dir():
                dirs.append(bin_dir)

    seen: set[str] = set()
    out: list[Path] = []
    for path in dirs:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def configure_cuda_dll_paths(extra_roots: Iterable[Path] = ()) -> list[Path]:
    """Add bundled CUDA DLL directories to this process and PATH.

    Returns the directories that were discovered. Safe to call repeatedly.
    """

    dirs = _cuda_bin_dirs(extra_roots)
    existing_path = os.environ.get("PATH", "")
    path_parts = [p for p in existing_path.split(os.pathsep) if p]
    lower_path = {p.lower() for p in path_parts}

    prepend: list[str] = []
    for path in dirs:
        raw = str(path)
        if raw.lower() not in lower_path:
            prepend.append(raw)
            lower_path.add(raw.lower())

        if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
            if path not in _CONFIGURED_DIRS:
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(raw))
                    _CONFIGURED_DIRS.append(path)
                except OSError:
                    # PATH prepend still helps LoadLibrary search in C extensions.
                    pass

    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + [existing_path])

    return dirs


def has_cuda12_runtime(extra_roots: Iterable[Path] = ()) -> bool:
    """Best-effort presence check for CUDA 12 DLLs required by CTranslate2."""

    names = {"cudart64_12.dll", "cublas64_12.dll", "cublaslt64_12.dll"}
    found: set[str] = set()
    for directory in _cuda_bin_dirs(extra_roots):
        try:
            for child in directory.iterdir():
                name = child.name.lower()
                if name in names:
                    found.add(name)
        except OSError:
            continue
    return names.issubset(found)
