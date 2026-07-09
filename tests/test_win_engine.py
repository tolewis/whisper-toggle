"""W5 — engine ownership/cleanup + single-instance decision logic (portable).

Two bugs:
- Abrupt tray exit orphaned the uvicorn child on :8788 because cleanup was only
  wired inline. Ownership + cleanup registration is extracted so a spawned
  engine always registers a stopper, while an adopted (already-healthy) engine
  is never owned nor killed.
- The single-instance mutex check read GetLastError without setting
  restype/argtypes/use_last_error. The 'already running' decision is extracted
  to a pure predicate here (the ctypes wiring itself is validated on jubiku).
"""

from __future__ import annotations

from whisper_toggle.win_engine import (
    ERROR_ALREADY_EXISTS,
    EnginePlan,
    already_running,
    plan_engine_start,
    register_cleanup_if_owned,
)


def test_already_running_predicate():
    assert ERROR_ALREADY_EXISTS == 183
    assert already_running(183) is True
    assert already_running(0) is False
    assert already_running(2) is False


def test_plan_spawn_when_engine_unhealthy():
    plan = plan_engine_start(api_healthy=False)
    assert isinstance(plan, EnginePlan)
    assert plan.should_spawn is True
    assert plan.owns is True


def test_plan_adopt_when_engine_already_healthy():
    """Documented behavior: adopt a running engine, do NOT own or kill it."""
    plan = plan_engine_start(api_healthy=True)
    assert plan.should_spawn is False
    assert plan.owns is False


def test_register_cleanup_when_owned():
    registered = []
    sentinel = object()

    did = register_cleanup_if_owned(True, registered.append, sentinel)

    assert did is True
    assert registered == [sentinel]


def test_no_cleanup_registered_when_adopted():
    registered = []
    sentinel = object()

    did = register_cleanup_if_owned(False, registered.append, sentinel)

    assert did is False
    assert registered == []  # never kill an engine we did not spawn


def test_spawn_path_always_registers_cleanup():
    """End-to-end of the decision: unhealthy -> spawn -> cleanup registered."""
    registered = []
    plan = plan_engine_start(api_healthy=False)
    register_cleanup_if_owned(plan.owns, registered.append, "stop_api")
    assert registered == ["stop_api"]


def test_adopt_path_registers_nothing():
    registered = []
    plan = plan_engine_start(api_healthy=True)
    register_cleanup_if_owned(plan.owns, registered.append, "stop_api")
    assert registered == []
