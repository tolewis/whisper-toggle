"""Application config — JSON under the platform data dir."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


# Ctrl+Shift+H is the reliable default on Windows 11 (Win+H is owned by OS voice typing).
DEFAULT_HOTKEY = "ctrl+shift+h"
CONFIG_VERSION = "2.0.0"


@dataclass
class AppConfig:
    hotkey: str = DEFAULT_HOTKEY
    device_override: str = "auto"
    model: str = ""  # empty = DeviceResolver default for device
    streaming: bool = True
    autostart: bool = True
    partial_debounce_ms: int = 400
    hardware_catchup_ms: int = 250
    version: str = CONFIG_VERSION
    # Live typing: type confirmed immediately; debounce partial revisions
    live_partials: bool = True
    suppress_hotkey: bool = True  # capture Win+H so OS voice typing does not fire


def default_config() -> AppConfig:
    return AppConfig()


def app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Whisper Toggle"
    return Path.home() / ".local" / "share" / "whisper-toggle"


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()

    cfg = default_config()
    valid = {f.name for f in fields(AppConfig)}
    for key, value in raw.items():
        if key in valid:
            setattr(cfg, key, value)
    # Normalize hotkey aliases
    hk = (cfg.hotkey or DEFAULT_HOTKEY).strip().lower().replace("windows+", "win+")
    cfg.hotkey = hk
    cfg.version = CONFIG_VERSION
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
