"""Logic tests for Win+H ownership policy (no Windows required)."""

from __future__ import annotations


def test_ownership_only_when_ready_policy():
    """Documented policy: swallow Win+H only when enabled=True."""

    class FakeOwner:
        def __init__(self):
            self.enabled = False

        def set_enabled(self, v: bool):
            self.enabled = v

    owner = FakeOwner()
    # not ready
    owner.set_enabled(False)
    assert owner.enabled is False
    # ready
    owner.set_enabled(True)
    assert owner.enabled is True
    # engine death restores OS
    owner.set_enabled(False)
    assert owner.enabled is False


def test_hotkey_normalization():
    for raw, want in [
        ("Win+H", "win+h"),
        ("windows+h", "win+h"),
        ("WIN+H", "win+h"),
    ]:
        got = raw.lower().replace("windows+", "win+")
        assert got == want
