"""W2 — the uninstaller must kill ONLY this app, never every pythonw.exe.

The prior [UninstallRun] ran ``taskkill /F /IM pythonw.exe`` which terminates
EVERY pythonw process on the machine (other tray apps, Jupyter, etc.). Uninstall
must instead stop only processes running from this install directory ({app}).

This is a static lint over installer.iss so it runs headlessly on Linux/CI.
"""

from __future__ import annotations

import re
from pathlib import Path

ISS = Path(__file__).resolve().parents[1] / "windows" / "installer.iss"


def _iss_text() -> str:
    return ISS.read_text(encoding="utf-8", errors="replace")


def test_installer_exists():
    assert ISS.exists(), f"installer.iss not found at {ISS}"


def test_no_bare_image_wide_kill():
    """No image-wide taskkill of pythonw/python — that nukes unrelated apps."""
    text = _iss_text().lower()
    # A bare /IM <image> kill (no PID / no path filter) is the dangerous form.
    assert "/im pythonw.exe" not in text, "installer must not taskkill /IM pythonw.exe (kills ALL pythonw)"
    assert "/im python.exe" not in text, "installer must not taskkill /IM python.exe (kills ALL python)"


def test_uninstall_targets_this_install_only():
    """Uninstall must scope the kill to processes under {app} (this install)."""
    text = _iss_text()
    # Isolate the [UninstallRun] section (up to the next [Section] or EOF).
    m = re.search(r"\[UninstallRun\](.*?)(\n\[[A-Za-z]|\Z)", text, re.DOTALL)
    assert m, "installer.iss has no [UninstallRun] section"
    section = m.group(1)
    lower = section.lower()
    # Must reference the install dir and filter by the executable's real path,
    # so only our own pythonw/python under {app} is stopped.
    assert "{app}" in section, "[UninstallRun] must scope to the {app} install dir"
    assert "executablepath" in lower, "[UninstallRun] must filter processes by ExecutablePath under {app}"
    # Sanity: the old image-wide form must not survive inside this section.
    assert "/im pythonw.exe" not in lower
