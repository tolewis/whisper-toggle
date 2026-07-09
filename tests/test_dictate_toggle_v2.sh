#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

MOCK_BIN="$TMP/bin"
mkdir -p "$MOCK_BIN" "$TMP/work"

cat > "$MOCK_BIN/notify-send" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$MOCK_BIN/notify-send"

cat > "$MOCK_BIN/xclip" <<'SH'
#!/usr/bin/env bash
cat >> "$XCLIP_LOG"
printf '\n' >> "$XCLIP_LOG"
exit 0
SH
chmod +x "$MOCK_BIN/xclip"

cat > "$MOCK_BIN/xprop" <<'SH'
#!/usr/bin/env bash
echo "WM_CLASS(STRING) = ${MOCK_WM_CLASS:-\"test\", \"test\"}"
SH
chmod +x "$MOCK_BIN/xprop"

cat > "$MOCK_BIN/xdotool" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$XDOTOOL_LOG"
case "${1:-}" in
    getactivewindow)
        echo 123
        ;;
    getwindowname)
        echo "${MOCK_WINDOW_TITLE:-Test Window}"
        ;;
esac
exit 0
SH
chmod +x "$MOCK_BIN/xdotool"

cat > "$MOCK_BIN/pw-record" <<'SH'
#!/usr/bin/env bash
trap 'exit 0' INT TERM
if printf '%s\n' "$*" | grep -q -- '--raw'; then
    while true; do
        for _ in $(seq 1 1600); do
            printf '\0\0'
        done
        sleep 0.05
    done
fi
out="${@: -1}"
python3 - "$out" <<'PY'
import sys
from pathlib import Path
Path(sys.argv[1]).write_bytes(b"RIFF" + b"\0" * 4096)
PY
while true; do sleep 1; done
SH
chmod +x "$MOCK_BIN/pw-record"

PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

cat > "$TMP/mock_ws.py" <<'PY'
from __future__ import annotations

import asyncio
import json
import os

import websockets


async def handler(websocket):
    binary_count = 0
    async for message in websocket:
        if isinstance(message, bytes):
            binary_count += 1
            if binary_count == 1:
                await websocket.send(json.dumps({"type": "partial", "text": "hello", "start_t": 0.0, "end_t": 0.5}))
            elif binary_count == 2:
                await websocket.send(json.dumps({"type": "confirmed", "text": "hello", "start_t": 0.0, "end_t": 0.5}))
        else:
            payload = json.loads(message)
            if payload.get("type") == "end":
                await websocket.send(json.dumps({"type": "final", "text": "hello world", "duration": 0.8}))
                return


async def main():
    port = int(os.environ["MOCK_WS_PORT"])
    async with websockets.serve(handler, "127.0.0.1", port):
        await asyncio.Future()


asyncio.run(main())
PY

MOCK_WS_PORT="$PORT" python3 "$TMP/mock_ws.py" &
SERVER_PID=$!
sleep 0.3

# Force the X11 code path deterministically regardless of the host session.
unset WAYLAND_DISPLAY

export PATH="$MOCK_BIN:$PATH"
export XDOTOOL_LOG="$TMP/xdotool.log"
export XCLIP_LOG="$TMP/xclip.log"
export WHISPER_WORK_DIR="$TMP/work"
export WHISPER_STREAMING=1
export WHISPER_STREAMING_ENDPOINT="ws://127.0.0.1:$PORT"
export WHISPER_OSD=1
export WHISPER_OSD_COMMAND="cat > '$TMP/osd.log'"
export WHISPER_STREAM_CLIENT="$REPO_ROOT/linux/stream_ws_client.py"

"$REPO_ROOT/linux/dictate-toggle.sh"
sleep 0.5
"$REPO_ROOT/linux/dictate-toggle.sh"

grep -q '^hello world$' "$XCLIP_LOG"
grep -q 'key --clearmodifiers ctrl+v' "$XDOTOOL_LOG"
grep -q '"type": "partial"' "$TMP/osd.log"
grep -q '"type": "final"' "$TMP/osd.log"

: > "$XDOTOOL_LOG"
export MOCK_WM_CLASS='"gnome-terminal", "Gnome-terminal"'
export MOCK_WINDOW_TITLE='tmux on server'
"$REPO_ROOT/linux/dictate-toggle.sh"
sleep 0.5
"$REPO_ROOT/linux/dictate-toggle.sh"

grep -q 'key --clearmodifiers ctrl+shift+v' "$XDOTOOL_LOG"
