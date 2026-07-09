"""L2 — streaming recorder availability is gated with a real batch fallback.

Streaming recorded with `arecord` (ALSA) while the docs install only pipewire
tools. When `arecord` is missing the script died with a misleading
"pw-record failed to start" and never fell back to the batch path. The fix
resolves the streaming recorder up front and, when it is unavailable, cleanly
takes the batch path (pw-record + POST). By default streaming now prefers
pw-record so a pipewire-only install works without ALSA at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux desktop tooling (bash/pw-record/arecord)",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "linux" / "dictate-toggle.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else "/usr/bin/bash"

# Real tools the start path needs; arecord is deliberately excluded so it is
# genuinely absent from PATH.
REAL_TOOLS = [
    "dirname", "mkdir", "rm", "sleep", "flock", "mkfifo",
    "cat", "seq", "head", "date", "stat",
]

MOCK_PW_RECORD = r"""#!/bin/bash
trap 'exit 0' INT TERM
last="${@: -1}"
if [[ "$last" == "-" ]]; then
    # streaming: headerless raw PCM to stdout
    while true; do
        for _ in $(seq 1 800); do printf '\0\0'; done
        sleep 0.05
    done
fi
# batch: write a >1KB WAV-ish file then idle
printf 'RIFF' > "$last"
head -c 4096 /dev/zero >> "$last"
while true; do sleep 1; done
"""

MOCK_NOTIFY = "#!/bin/bash\nexit 0\n"
# A stand-in stream client that just stays alive (as a real, connected client
# would), so the *current* code reaches its misleading die() instead of the
# unrelated dual-death fallback.
MOCK_CLIENT = "#!/bin/bash\nsleep 5\n"


@pytest.fixture
def sandbox(tmp_path):
    bind = tmp_path / "bin"
    bind.mkdir()
    for name in REAL_TOOLS:
        path = shutil.which(name)
        assert path, f"missing host tool: {name}"
        os.symlink(path, bind / name)

    (bind / "pw-record").write_text(MOCK_PW_RECORD)
    (bind / "notify-send").write_text(MOCK_NOTIFY)
    for f in ("pw-record", "notify-send"):
        (bind / f).chmod(0o755)

    client = tmp_path / "mock_client.sh"
    client.write_text(MOCK_CLIENT)
    client.chmod(0o755)

    work = tmp_path / "work"

    out_file = tmp_path / "stdout.txt"
    err_file = tmp_path / "stderr.txt"

    def run(extra_env):
        env = {
            "PATH": str(bind),
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "WHISPER_STREAMING": "1",
            "WHISPER_OSD": "0",
            "WHISPER_WORK_DIR": str(work),
            "WHISPER_STREAM_CLIENT": str(client),
            "WHISPER_STREAMING_ENDPOINT": "ws://127.0.0.1:1/x",
        }
        env.update(extra_env)
        # Redirect to files, not pipes: the recorder is backgrounded and
        # inherits stdout/stderr; a captured pipe would keep the test blocked
        # after the (hotkey-style) script itself exits.
        with open(out_file, "w") as out, open(err_file, "w") as err:
            proc = subprocess.run(
                [BASH, str(SCRIPT)],
                env=env,
                stdout=out,
                stderr=err,
                timeout=20,
            )
        stdout = out_file.read_text()
        stderr = err_file.read_text()
        mode = ""
        mode_file = work / "mode"
        if mode_file.exists():
            mode = mode_file.read_text().strip()
        return proc, stdout, stderr, mode

    return run


def test_missing_streaming_recorder_falls_back_to_batch(sandbox):
    # Force the legacy ALSA streaming recorder, which is absent from PATH.
    proc, stdout, stderr, mode = sandbox({"WHISPER_STREAM_RECORDER": "arecord"})

    assert "pw-record failed to start" not in stderr, stderr
    assert "arecord: command not found" not in stderr, stderr
    assert proc.returncode == 0, stdout + stderr
    assert mode == "batch", (mode, stderr)


def test_auto_prefers_pw_record_for_streaming(sandbox):
    # With pw-record available and no override, streaming stays on pw-record
    # (pipewire-only install works without any ALSA tooling).
    proc, stdout, stderr, mode = sandbox({})

    assert proc.returncode == 0, stdout + stderr
    assert mode == "stream", (mode, stderr)
