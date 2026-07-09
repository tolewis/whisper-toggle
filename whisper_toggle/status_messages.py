"""User-facing status/notification copy."""

from __future__ import annotations


def startup_loading_notice(*, model: str, device: str, hotkey: str) -> str:
    """Explain that Whisper Toggle is alive but not ready yet.

    The model preload/smoke phase can take several seconds on CPU/GPU cold start.
    During that window the hotkey is intentionally not owned for dictation.
    """

    model_label = (model or "auto model").strip()
    device_label = (device or "auto").strip().upper()
    hotkey_label = (hotkey or "the hotkey").strip()
    return (
        f"Starting Whisper Toggle: loading {model_label} on {device_label}. "
        f"Dictation is not ready yet; wait for Ready, then press {hotkey_label}."
    )
