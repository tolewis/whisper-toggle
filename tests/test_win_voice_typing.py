"""W3 — crash-safe Voice Typing restore via reconcile-on-launch (portable).

The old design saved the original registry values in memory and restored them
via ``atexit`` only. ``atexit`` does not run on TerminateProcess, the
uninstaller's taskkill, or a hard crash, so Windows Voice Typing was left
disabled forever after any abnormal exit.

Fix: persist the originals to a marker the instant we disable Voice Typing, and
RECONCILE ON LAUNCH — if a marker survived from a prior run, restore the
originals before doing anything else. These pure functions use injected
registry + marker ports so they run headlessly.
"""

from __future__ import annotations

import pytest

from whisper_toggle.win_voice_typing import (
    JsonMarkerStore,
    disable_voice_typing,
    reconcile_on_launch,
    restore_voice_typing,
)

# Opaque stand-ins for winreg.HKEY_CURRENT_USER and winreg.REG_DWORD.
HKCU = 0x80000001
REG_DWORD = 4

# (root, path, name, new_value, new_type) — mirrors the real disable ops.
OPS = [
    (HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled", 0, REG_DWORD),
    (HKCU, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher", 0, REG_DWORD),
]


class FakeRegistry:
    """Dict-backed registry: key = (root, path, name) -> (value, type)."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.calls: list[tuple] = []

    def read(self, root, path, name):
        self.calls.append(("read", root, path, name))
        return self.store.get((root, path, name))

    def write(self, root, path, name, value, type_):
        self.calls.append(("write", root, path, name, value, type_))
        self.store[(root, path, name)] = (value, type_)

    def delete(self, root, path, name):
        self.calls.append(("delete", root, path, name))
        self.store.pop((root, path, name), None)


class FakeMarker:
    def __init__(self):
        self.data = None

    def exists(self):
        return self.data is not None

    def load(self):
        return self.data

    def save(self, data):
        self.data = data

    def clear(self):
        self.data = None


def test_disable_persists_marker_and_writes_zeros():
    reg = FakeRegistry(
        {
            (HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled"): (1, REG_DWORD),
            # EnableLauncher is ABSENT (read returns None).
        }
    )
    marker = FakeMarker()

    disable_voice_typing(reg, marker, OPS)

    # Values were flipped to 0.
    assert reg.store[(HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled")] == (0, REG_DWORD)
    assert reg.store[(HKCU, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher")] == (0, REG_DWORD)
    # Marker was persisted with the originals (present=True/False recorded).
    assert marker.exists()
    entries = marker.load()["entries"]
    by_name = {e["name"]: e for e in entries}
    assert by_name["IsVoiceTypingKeyEnabled"]["present"] is True
    assert by_name["IsVoiceTypingKeyEnabled"]["prev_value"] == 1
    assert by_name["EnableLauncher"]["present"] is False


def test_reconcile_restores_leftover_marker():
    """A marker left by a crashed prior run is restored on next launch."""
    # Registry currently reflects the disabled (0) state from the dead run.
    reg = FakeRegistry(
        {
            (HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled"): (0, REG_DWORD),
            (HKCU, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher"): (0, REG_DWORD),
        }
    )
    marker = FakeMarker()
    marker.save(
        {
            "entries": [
                {
                    "root": HKCU,
                    "path": r"Software\Microsoft\Input\Settings",
                    "name": "IsVoiceTypingKeyEnabled",
                    "present": True,
                    "prev_value": 1,
                    "prev_type": REG_DWORD,
                },
                {
                    "root": HKCU,
                    "path": r"Software\Microsoft\Input\Settings\VoiceTyping",
                    "name": "EnableLauncher",
                    "present": False,
                    "prev_value": None,
                    "prev_type": REG_DWORD,
                },
            ]
        }
    )

    reconciled = reconcile_on_launch(reg, marker)

    assert reconciled is True
    # Original value restored...
    assert reg.store[(HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled")] == (1, REG_DWORD)
    # ...and the previously-absent value deleted, not left at 0.
    assert (HKCU, r"Software\Microsoft\Input\Settings\VoiceTyping", "EnableLauncher") not in reg.store
    # Marker cleared so the next launch is a no-op.
    assert not marker.exists()


def test_reconcile_noop_without_marker():
    reg = FakeRegistry({(HKCU, "p", "n"): (1, REG_DWORD)})
    marker = FakeMarker()

    reconciled = reconcile_on_launch(reg, marker)

    assert reconciled is False
    # Registry untouched; no writes/deletes issued.
    assert reg.store == {(HKCU, "p", "n"): (1, REG_DWORD)}
    assert not any(c[0] in ("write", "delete") for c in reg.calls)


def test_full_cycle_disable_then_restore_is_identity():
    original = {
        (HKCU, r"Software\Microsoft\Input\Settings", "IsVoiceTypingKeyEnabled"): (1, REG_DWORD),
    }
    reg = FakeRegistry(dict(original))
    marker = FakeMarker()

    disable_voice_typing(reg, marker, OPS)
    assert reg.store != original  # zeros written, EnableLauncher created

    restored = restore_voice_typing(reg, marker)
    assert restored is True
    assert reg.store == original  # back to exactly the original state
    assert not marker.exists()


def test_json_marker_store_roundtrip(tmp_path):
    path = tmp_path / "sub" / "voice-typing-restore.json"
    store = JsonMarkerStore(path)
    assert store.exists() is False
    assert store.load() is None

    payload = {"entries": [{"name": "X", "present": True, "prev_value": 1, "prev_type": REG_DWORD}]}
    store.save(payload)
    assert store.exists() is True
    assert store.load() == payload

    store.clear()
    assert store.exists() is False
    # Clearing a missing marker is a safe no-op.
    store.clear()


def test_json_marker_store_survives_corruption(tmp_path):
    path = tmp_path / "voice-typing-restore.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = JsonMarkerStore(path)
    # A corrupt marker loads as None rather than raising.
    assert store.load() is None
