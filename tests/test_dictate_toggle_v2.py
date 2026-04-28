from __future__ import annotations

import subprocess
from pathlib import Path


def test_dictate_toggle_v2_shell_round_trip():
    script = Path(__file__).with_suffix(".sh")
    result = subprocess.run(
        ["bash", str(script)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
