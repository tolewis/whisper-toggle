"""Portable, crash-safe restore of Windows Voice Typing registry values.

While Whisper Toggle owns Win+H it flips a few HKCU registry values so the OS
Voice Typing launcher does not also fire. Those originals must be put back when
we relinquish the hotkey. The old design kept the originals in memory and
restored them via ``atexit`` only, so a TerminateProcess, the uninstaller's
taskkill, or a hard crash left Voice Typing disabled forever.

Fix (reconcile-on-launch): the moment we disable Voice Typing we persist the
originals to a marker file; on every launch we check for a leftover marker and,
if present, restore the originals before doing anything else.

This module is dependency-free (no ``winreg`` / ``ctypes``) so it is unit
testable on any OS. The Windows caller injects a real registry adapter and a
marker store; the reconcile/disable/restore decision logic lives here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Tuple

# (root, path, name, new_value, new_type)
RegOp = Tuple[Any, str, str, int, Any]
# (value, type)
RegValue = Tuple[Any, Any]


class RegistryPort(Protocol):
    """Minimal registry surface the reconcile logic needs."""

    def read(self, root: Any, path: str, name: str) -> Optional[RegValue]: ...
    def write(self, root: Any, path: str, name: str, value: Any, type_: Any) -> None: ...
    def delete(self, root: Any, path: str, name: str) -> None: ...


class MarkerPort(Protocol):
    """Durable store for the 'we disabled Voice Typing' record."""

    def exists(self) -> bool: ...
    def load(self) -> Optional[dict]: ...
    def save(self, data: dict) -> None: ...
    def clear(self) -> None: ...


class JsonMarkerStore:
    """Default MarkerPort: a single JSON file on disk.

    Corruption-tolerant (a garbage file loads as ``None``) and safe to clear
    when already absent, so a partially-written marker never wedges startup.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def default_marker_store() -> JsonMarkerStore:
    """Marker under the app data dir (imported lazily to stay portable)."""
    from whisper_toggle.config import app_data_dir

    return JsonMarkerStore(app_data_dir() / "voice-typing-restore.json")


def disable_voice_typing(
    registry: RegistryPort,
    marker: MarkerPort,
    ops: Iterable[RegOp],
) -> list[dict]:
    """Snapshot originals to the marker, THEN flip the values to ``new_value``.

    The marker is written before we rely on it (so even an instant crash after
    this call leaves a recoverable record). Returns the saved entries.
    """
    entries: list[dict] = []
    ops = list(ops)
    for root, path, name, _new_value, _new_type in ops:
        prev = registry.read(root, path, name)
        if prev is None:
            entries.append(
                {"root": root, "path": path, "name": name, "present": False, "prev_value": None, "prev_type": _new_type}
            )
        else:
            prev_value, prev_type = prev
            entries.append(
                {"root": root, "path": path, "name": name, "present": True, "prev_value": prev_value, "prev_type": prev_type}
            )
    # Persist BEFORE mutating, so the restore record can never lag the change.
    marker.save({"entries": entries})
    for root, path, name, new_value, new_type in ops:
        registry.write(root, path, name, new_value, new_type)
    return entries


def restore_voice_typing(registry: RegistryPort, marker: MarkerPort) -> bool:
    """Put originals back from the marker, then clear it. No-op if no marker."""
    data = marker.load()
    if not data:
        return False
    for entry in data.get("entries", []):
        root = entry["root"]
        path = entry["path"]
        name = entry["name"]
        if entry.get("present"):
            registry.write(root, path, name, entry.get("prev_value"), entry.get("prev_type"))
        else:
            # The value did not exist before we disabled it: remove it, don't
            # leave our injected value (e.g. a stray 0) behind.
            registry.delete(root, path, name)
    marker.clear()
    return True


def reconcile_on_launch(registry: RegistryPort, marker: MarkerPort) -> bool:
    """On startup, restore any leftover disable-marker from a prior run.

    Returns True if a marker existed and was reconciled, False if there was
    nothing to do.
    """
    if not marker.exists():
        return False
    return restore_voice_typing(registry, marker)
