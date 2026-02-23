#!/usr/bin/env bash
#
# dictate-toggle.sh — Push-to-talk voice dictation via warm Whisper API
#
# Press hotkey once to start recording, again to stop + transcribe + paste.
# Uses pw-record (PipeWire), local whisper-api on :8788, xdotool for paste.
#
# Designed for GNOME + PipeWire. Bound to Super+H and Ctrl+`.
#

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
WHISPER_API="http://127.0.0.1:8788/v1/audio/transcriptions"
WORK_DIR="/tmp/dictate-toggle"
PID_FILE="$WORK_DIR/rec.pid"
WAV_FILE="$WORK_DIR/current.wav"
LOCK_FILE="$WORK_DIR/lock"
NOTIFY_ID=991337
KILL_TIMEOUT=1

# ── Helpers ─────────────────────────────────────────────────────────────────
mkdir -p "$WORK_DIR"

notify() {
    local urgency="${2:-normal}"
    notify-send -a "Dictation" \
        -h "int:transient:1" \
        -r "$NOTIFY_ID" \
        -u "$urgency" \
        -- "$1"
}

die() {
    notify "$1" critical
    echo "[dictate] ERROR: $1" >&2
    exit 1
}

cleanup_lock() {
    exec 9>&-  # close fd to release flock
}

# ── Locking (prevent rapid double-press race) ──────────────────────────────
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    notify "Busy — wait a moment" low
    exit 0
fi
trap cleanup_lock EXIT

# ── Detect state ────────────────────────────────────────────────────────────
is_recording() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid=$(<"$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

# ── Stop + transcribe + paste ───────────────────────────────────────────────
stop_and_transcribe() {
    local pid
    pid=$(<"$PID_FILE")

    notify "Processing..."

    # Graceful stop: SIGINT lets pw-record flush the WAV header
    kill -INT "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < KILL_TIMEOUT * 10 )); do
        sleep 0.1
        (( waited++ )) || true
    done
    # Force kill if still alive
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true

    rm -f "$PID_FILE"

    # Release the lock before the slow transcription step so a new
    # recording can start while we finish this one.
    cleanup_lock

    # Validate WAV file
    if [[ ! -f "$WAV_FILE" ]]; then
        die "No audio file found"
    fi
    local size
    size=$(stat -c%s "$WAV_FILE" 2>/dev/null || echo 0)
    if (( size < 1000 )); then
        rm -f "$WAV_FILE"
        notify "Recording too short — ignored" low
        exit 0
    fi

    # Move to timestamped file so a new recording can start concurrently
    local ts_wav="$WORK_DIR/run_$(date +%s%N).wav"
    mv "$WAV_FILE" "$ts_wav"

    # Transcribe via warm API
    local response http_code body
    response=$(curl -s -w "\n%{http_code}" \
        -X POST "$WHISPER_API" \
        -F "file=@${ts_wav}" \
        -F "model=small.en" \
        -F "language=en" \
        --max-time 30 2>&1) || {
        rm -f "$ts_wav"
        die "API request failed — is whisper-api running?"
    }

    http_code=$(tail -1 <<<"$response")
    body=$(sed '$ d' <<<"$response")

    rm -f "$ts_wav"

    if [[ "$http_code" != "200" ]]; then
        die "API returned HTTP $http_code"
    fi

    # Extract text from JSON {"text": "..."}
    local text
    text=$(printf '%s' "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null) || {
        die "Failed to parse API response"
    }

    if [[ -z "$text" || "$text" == " " ]]; then
        notify "Nothing detected" low
        exit 0
    fi

    # Copy to clipboard
    printf '%s' "$text" | xclip -selection clipboard

    # Auto-paste into focused window
    paste_text

    local chars=${#text}
    local preview="${text:0:60}"
    [[ ${#text} -gt 60 ]] && preview="${preview}..."
    notify "$preview  (${chars} chars)"
}

# ── Paste into focused window ───────────────────────────────────────────────
paste_text() {
    # Detect if focused window is a terminal (needs Ctrl+Shift+V)
    local wm_class=""
    local wid
    wid=$(xdotool getactivewindow 2>/dev/null) && \
        wm_class=$(xprop -id "$wid" WM_CLASS 2>/dev/null | sed 's/.*= //' | tr -d '"')

    # Small delay to let any hotkey modifier keys release
    sleep 0.05

    case "$wm_class" in
        *gnome-terminal*|*kitty*|*Alacritty*|*foot*|*xterm*|*Tilix*|*terminator*|*konsole*|*st-256color*|*tmux*)
            xdotool key --clearmodifiers ctrl+shift+v
            ;;
        *)
            xdotool key --clearmodifiers ctrl+v
            ;;
    esac
}

# ── Start recording ────────────────────────────────────────────────────────
start_recording() {
    # Clean up stale WAV if any
    rm -f "$WAV_FILE"

    notify "Recording..."

    pw-record \
        --rate 16000 \
        --channels 1 \
        --format s16 \
        "$WAV_FILE" 9>&- &

    local rec_pid=$!

    # Verify it actually started
    sleep 0.2
    if ! kill -0 "$rec_pid" 2>/dev/null; then
        die "pw-record failed to start"
    fi

    echo "$rec_pid" > "$PID_FILE"
}

# ── Main ────────────────────────────────────────────────────────────────────
if is_recording; then
    stop_and_transcribe
else
    start_recording
fi
