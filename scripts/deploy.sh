#!/usr/bin/env bash
#
# Stage Whisper Toggle files for production. This does not restart systemd.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${WHISPER_API_DIR:-/home/tlewis/.local/share/whisper-api}"
BIN_DIR="${WHISPER_BIN_DIR:-/home/tlewis/bin}"
VENV_DIR="${WHISPER_API_VENV:-/home/tlewis/.venvs/whisper}"
STREAMING_REPO="${WHISPER_STREAMING_REPO:-https://github.com/ufal/whisper_streaming.git}"
STREAMING_COMMIT="${WHISPER_STREAMING_COMMIT:-6da90b44b7e50d79695e68166d2a2c7609c75abb}"
VENDOR_DIR="$API_DIR/vendor/whisper_streaming"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Missing venv python: $VENV_DIR/bin/python" >&2
    exit 1
fi

mkdir -p "$API_DIR" "$BIN_DIR" "$(dirname "$VENDOR_DIR")"

"$VENV_DIR/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt"

tmp_clone="$(mktemp -d)"
trap 'rm -rf "$tmp_clone"' EXIT
git clone --quiet "$STREAMING_REPO" "$tmp_clone/whisper_streaming"
git -C "$tmp_clone/whisper_streaming" checkout --quiet "$STREAMING_COMMIT"
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"
cp "$tmp_clone/whisper_streaming/whisper_online.py" "$VENDOR_DIR/whisper_online.py"
cp "$tmp_clone/whisper_streaming/line_packet.py" "$VENDOR_DIR/line_packet.py"
cp "$tmp_clone/whisper_streaming/silero_vad_iterator.py" "$VENDOR_DIR/silero_vad_iterator.py"
cp "$tmp_clone/whisper_streaming/LICENSE" "$VENDOR_DIR/LICENSE"

cp "$REPO_ROOT/app.py" "$API_DIR/app.py"
cp "$REPO_ROOT/linux/dictate-toggle.sh" "$BIN_DIR/dictate-toggle.sh"
cp "$REPO_ROOT/linux/osd_overlay.py" "$BIN_DIR/whisper-osd.py"
cp "$REPO_ROOT/linux/stream_ws_client.py" "$BIN_DIR/whisper-stream-ws-client.py"
chmod 755 "$BIN_DIR/dictate-toggle.sh" "$BIN_DIR/whisper-osd.py" "$BIN_DIR/whisper-stream-ws-client.py"

PYTHONPATH="$REPO_ROOT" "$VENV_DIR/bin/python" - <<'PY'
import faster_whisper
import websockets
import whisper_streaming
print("Verified Python deps: faster_whisper, websockets, whisper_streaming")
PY

echo
echo "Staged files:"
echo "  $API_DIR/app.py"
echo "  $BIN_DIR/dictate-toggle.sh"
echo "  $BIN_DIR/whisper-osd.py"
echo "  $BIN_DIR/whisper-stream-ws-client.py"
echo "  $VENDOR_DIR/whisper_online.py"
echo
echo "Next steps (Tim or CL runs):"
echo "  systemctl --user restart whisper-api"
echo "  sleep 2 && curl -fsS http://127.0.0.1:8788/health || echo \"FAIL\""
