"""Portable engine-ownership + single-instance decision logic (no Windows APIs).

Two concerns, kept dependency-free so they are unit testable headlessly:

1. Ownership/cleanup: if the local Whisper engine is not already healthy we
   SPAWN it and OWN it, which means we must register a cleanup so an abrupt tray
   exit stops the child (otherwise uvicorn is orphaned on :8788). If the engine
   is already healthy we ADOPT it: we neither own nor kill it, because it may
   belong to another instance/user.

2. Single-instance: the Windows named-mutex probe reports ERROR_ALREADY_EXISTS
   (183) when another instance already holds it. The ctypes wiring lives in the
   tray (validated on jubiku); the decision is this pure predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

ERROR_ALREADY_EXISTS = 183


def already_running(last_error: int) -> bool:
    """True when CreateMutexW's GetLastError says the mutex already existed."""
    return last_error == ERROR_ALREADY_EXISTS


@dataclass(frozen=True)
class EnginePlan:
    should_spawn: bool
    owns: bool


def plan_engine_start(api_healthy: bool) -> EnginePlan:
    """Decide whether to spawn+own the engine or adopt an existing one.

    - engine unhealthy  -> spawn it and own it (must clean it up on exit)
    - engine healthy     -> adopt it; do not own, do not kill on exit
    """
    if api_healthy:
        return EnginePlan(should_spawn=False, owns=False)
    return EnginePlan(should_spawn=True, owns=True)


def register_cleanup_if_owned(owns: bool, register_fn: Callable, cleanup) -> bool:
    """Register ``cleanup`` via ``register_fn`` iff we own the engine.

    Returns True when a cleanup was registered. Never registers a killer for an
    engine we merely adopted.
    """
    if owns:
        register_fn(cleanup)
        return True
    return False
