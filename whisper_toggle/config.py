"""Application config — JSON under the platform data dir."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path


# Ctrl+` is the default (native RegisterHotKey, no OS conflict). Ctrl+Shift+H is
# an alternative; Win+H is reserved by Windows voice typing.
DEFAULT_HOTKEY = "ctrl+`"
CONFIG_VERSION = "2.3.0"

# Windows 11 reserves Win+H for its voice-typing launcher and will not let an app
# reliably claim it, so it is not offered as an option and any stored value heals
# to the default.
UNSUPPORTED_HOTKEYS = frozenset({"win+h"})
STREAM_ENGINES = frozenset({"sherpa", "whisper_streaming"})


@dataclass
class AppConfig:
    hotkey: str = DEFAULT_HOTKEY
    device_override: str = "auto"
    model: str = ""  # empty = DeviceResolver default for device
    # Batch+clipboard is the reliable Windows default (matches OS voice typing paste model).
    streaming: bool = False
    autostart: bool = True
    partial_debounce_ms: int = 400
    hardware_catchup_ms: int = 250
    version: str = CONFIG_VERSION
    # Live partials require streaming; off by default until WS is stable on Windows.
    live_partials: bool = False
    stream_engine: str = "sherpa"
    hybrid_final_correct: bool = True
    suppress_hotkey: bool = True  # capture Win+H so OS voice typing does not fire
    audible_cues: bool = True  # ding on record start/stop (batch shows no text until stop)


def default_config() -> AppConfig:
    return AppConfig()


def app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Whisper Toggle"
    return Path.home() / ".local" / "share" / "whisper-toggle"


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def _type_ok(value: object, type_name: str) -> bool:
    """True if a JSON-decoded value matches a dataclass field's declared type.

    `from __future__ import annotations` makes field.type a string, so we match
    by name. bool is rejected for int fields (it is an int subclass) so a stray
    JSON `true` never lands in a numeric setting.
    """
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "str":
        return isinstance(value, str)
    if type_name == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True  # unknown/optional type: accept


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()
    if not isinstance(raw, dict):
        return default_config()

    cfg = default_config()
    field_types = {f.name: f.type for f in fields(AppConfig)}
    for key, value in raw.items():
        expected = field_types.get(key)
        if expected is None:
            continue  # unknown key
        # A wrong-typed value keeps the field's default rather than poisoning it.
        if _type_ok(value, str(expected)):
            setattr(cfg, key, value)
    # Normalize hotkey aliases
    hk = (cfg.hotkey or DEFAULT_HOTKEY).strip().lower().replace("windows+", "win+")
    if hk in UNSUPPORTED_HOTKEYS:
        hk = DEFAULT_HOTKEY
    cfg.hotkey = hk
    engine = (cfg.stream_engine or "sherpa").strip().lower()
    cfg.stream_engine = engine if engine in STREAM_ENGINES else "sherpa"
    cfg.version = CONFIG_VERSION
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> Path:
    """Persist config atomically: write a temp file in the same dir, then
    os.replace() it into place. A crash/failure mid-write leaves the previous
    good config intact instead of a truncated file."""
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    data = json.dumps(payload, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)  # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
