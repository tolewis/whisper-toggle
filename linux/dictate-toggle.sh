#!/usr/bin/env bash
#
# dictate-toggle.sh - Push-to-talk dictation via local Whisper API.
#
# Press once to start recording, again to stop and type the transcript.
#

set -euo pipefail

WHISPER_API="${WHISPER_API:-http://127.0.0.1:8788/v1/audio/transcriptions}"
WHISPER_STREAMING="${WHISPER_STREAMING:-1}"
WHISPER_STREAMING_ENDPOINT="${WHISPER_STREAMING_ENDPOINT:-ws://127.0.0.1:8788/v1/audio/stream}"
WHISPER_OSD="${WHISPER_OSD:-1}"
WHISPER_OSD_COMMAND="${WHISPER_OSD_COMMAND:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_OSD_BIN="$SCRIPT_DIR/whisper-osd.py"
DEFAULT_STREAM_CLIENT="$SCRIPT_DIR/whisper-stream-ws-client.py"
[[ -x "$DEFAULT_OSD_BIN" ]] || DEFAULT_OSD_BIN="$SCRIPT_DIR/osd_overlay.py"
[[ -x "$DEFAULT_STREAM_CLIENT" ]] || DEFAULT_STREAM_CLIENT="$SCRIPT_DIR/stream_ws_client.py"
WHISPER_OSD_BIN="${WHISPER_OSD_BIN:-$DEFAULT_OSD_BIN}"
WHISPER_STREAM_CLIENT="${WHISPER_STREAM_CLIENT:-$DEFAULT_STREAM_CLIENT}"

WORK_DIR="${WHISPER_WORK_DIR:-/tmp/dictate-toggle}"
PID_FILE="$WORK_DIR/rec.pid"
MODE_FILE="$WORK_DIR/mode"
CLIENT_PID_FILE="$WORK_DIR/client.pid"
OSD_PID_FILE="$WORK_DIR/osd.pid"
WAV_FILE="$WORK_DIR/current.wav"
PCM_FIFO="$WORK_DIR/audio.pcm"
OSD_FIFO="$WORK_DIR/osd.jsonl"
STREAM_LOG="$WORK_DIR/stream.jsonl"
FINAL_FILE="$WORK_DIR/final.txt"
LOCK_FILE="$WORK_DIR/lock"
NOTIFY_ID=991337
KILL_TIMEOUT=1

mkdir -p "$WORK_DIR"

notify() {
    local message="$1"
    local urgency="${2:-normal}"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a "Dictation" \
            -h "int:transient:1" \
            -r "$NOTIFY_ID" \
            -u "$urgency" \
            -- "$message" || true
    fi
}

die() {
    notify "$1" critical
    echo "[dictate] ERROR: $1" >&2
    exit 1
}

cleanup_lock() {
    exec 9>&-
}

cleanup_stream_files() {
    rm -f "$PCM_FIFO" "$OSD_FIFO" "$CLIENT_PID_FILE" "$OSD_PID_FILE" "$MODE_FILE"
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    notify "Busy, wait a moment" low
    exit 0
fi
trap cleanup_lock EXIT

is_recording() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid=$(<"$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

paste_text() {
    local wm_class=""
    local wid
    wid=$(xdotool getactivewindow 2>/dev/null) && \
        wm_class=$(xprop -id "$wid" WM_CLASS 2>/dev/null | sed 's/.*= //' | tr -d '"')

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

type_text() {
    local text="$1"
    sleep 0.05
    xdotool type --clearmodifiers --delay 0 "$text"
}

start_recording_batch() {
    rm -f "$WAV_FILE"
    notify "Recording..."

    pw-record \
        --rate 16000 \
        --channels 1 \
        --format s16 \
        "$WAV_FILE" 9>&- &

    local rec_pid=$!
    sleep 0.2
    if ! kill -0 "$rec_pid" 2>/dev/null; then
        die "pw-record failed to start"
    fi

    echo "$rec_pid" > "$PID_FILE"
    echo "batch" > "$MODE_FILE"
}

stop_batch() {
    local pid
    pid=$(<"$PID_FILE")

    notify "Processing..."
    kill -INT "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < KILL_TIMEOUT * 10 )); do
        sleep 0.1
        (( waited++ )) || true
    done
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$PID_FILE" "$MODE_FILE"

    cleanup_lock

    [[ -f "$WAV_FILE" ]] || die "No audio file found"
    local size
    size=$(stat -c%s "$WAV_FILE" 2>/dev/null || echo 0)
    if (( size < 1000 )); then
        rm -f "$WAV_FILE"
        notify "Recording too short, ignored" low
        exit 0
    fi

    local ts_wav="$WORK_DIR/run_$(date +%s%N).wav"
    mv "$WAV_FILE" "$ts_wav"

    local response http_code body
    response=$(curl -s -w "\n%{http_code}" \
        -X POST "$WHISPER_API" \
        -F "file=@${ts_wav}" \
        -F "model=small.en" \
        -F "language=en" \
        --max-time 30 2>&1) || {
        rm -f "$ts_wav"
        die "API request failed, is whisper-api running?"
    }

    http_code=$(tail -1 <<<"$response")
    body=$(sed '$ d' <<<"$response")
    rm -f "$ts_wav"

    [[ "$http_code" == "200" ]] || die "API returned HTTP $http_code"

    local text
    text=$(printf '%s' "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null) || {
        die "Failed to parse API response"
    }

    if [[ -z "$text" || "$text" == " " ]]; then
        notify "Nothing detected" low
        exit 0
    fi

    printf '%s' "$text" | xclip -selection clipboard
    paste_text

    local chars=${#text}
    local preview="${text:0:60}"
    [[ ${#text} -gt 60 ]] && preview="${preview}..."
    notify "$preview  (${chars} chars)"
}

start_osd() {
    [[ "$WHISPER_OSD" == "1" ]] || return 0
    rm -f "$OSD_FIFO"
    mkfifo "$OSD_FIFO"

    if [[ -n "$WHISPER_OSD_COMMAND" ]]; then
        bash -c "$WHISPER_OSD_COMMAND" 9>&- < "$OSD_FIFO" &
    else
        "$WHISPER_OSD_BIN" 9>&- < "$OSD_FIFO" &
    fi
    echo "$!" > "$OSD_PID_FILE"
}

stop_osd_if_needed() {
    [[ -f "$OSD_PID_FILE" ]] || return 0
    local osd_pid
    osd_pid=$(<"$OSD_PID_FILE")
    wait "$osd_pid" 2>/dev/null || true
}

start_streaming() {
    rm -f "$WAV_FILE" "$FINAL_FILE" "$STREAM_LOG" "$PCM_FIFO"
    cleanup_stream_files
    mkfifo "$PCM_FIFO"
    start_osd

    local osd_arg=()
    if [[ "$WHISPER_OSD" == "1" ]]; then
        osd_arg=(--osd-fifo "$OSD_FIFO")
    fi

    local partial_arg=()
    if [[ "$WHISPER_OSD" == "0" ]]; then
        partial_arg=(--xdotool-partials)
    fi

    "$WHISPER_STREAM_CLIENT" \
        --endpoint "$WHISPER_STREAMING_ENDPOINT" \
        --final-file "$FINAL_FILE" \
        "${osd_arg[@]}" \
        "${partial_arg[@]}" \
        9>&- < "$PCM_FIFO" > "$STREAM_LOG" &
    local client_pid=$!
    echo "$client_pid" > "$CLIENT_PID_FILE"

    arecord \
        -t raw \
        -f S16_LE \
        -r 16000 \
        -c 1 \
        - 9>&- > "$PCM_FIFO" &

    local rec_pid=$!
    sleep 0.2
    if ! kill -0 "$rec_pid" 2>/dev/null; then
        if ! kill -0 "$client_pid" 2>/dev/null; then
            wait "$client_pid" 2>/dev/null || true
            cleanup_stream_files
            echo "[dictate] streaming unavailable within 1s, falling back to v1 batch" >&2
            start_recording_batch
            return 0
        fi
        kill "$client_pid" 2>/dev/null || true
        cleanup_stream_files
        die "pw-record failed to start"
    fi

    sleep 0.8
    if ! kill -0 "$client_pid" 2>/dev/null; then
        wait "$client_pid" 2>/dev/null || true
        kill -INT "$rec_pid" 2>/dev/null || true
        wait "$rec_pid" 2>/dev/null || true
        if [[ -f "$OSD_PID_FILE" ]]; then
            kill "$(<"$OSD_PID_FILE")" 2>/dev/null || true
        fi
        cleanup_stream_files
        echo "[dictate] streaming unavailable within 1s, falling back to v1 batch" >&2
        start_recording_batch
        return 0
    fi

    notify "Recording..."
    echo "$rec_pid" > "$PID_FILE"
    echo "stream" > "$MODE_FILE"
}

stop_streaming() {
    local pid client_pid
    pid=$(<"$PID_FILE")
    client_pid=$(<"$CLIENT_PID_FILE")

    notify "Finalizing..."
    kill -INT "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < KILL_TIMEOUT * 10 )); do
        sleep 0.1
        (( waited++ )) || true
    done
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"

    cleanup_lock

    local client_wait=0
    while kill -0 "$client_pid" 2>/dev/null && (( client_wait < 100 )); do
        sleep 0.1
        (( client_wait++ )) || true
    done
    if kill -0 "$client_pid" 2>/dev/null; then
        kill "$client_pid" 2>/dev/null || true
        die "Streaming final timed out"
    fi
    wait "$client_pid" 2>/dev/null || true
    stop_osd_if_needed

    local text=""
    if [[ -f "$FINAL_FILE" ]]; then
        text=$(<"$FINAL_FILE")
    fi

    cleanup_stream_files

    if [[ -z "$text" || "$text" == " " ]]; then
        notify "Nothing detected" low
        exit 0
    fi

    type_text "$text"
    local chars=${#text}
    local preview="${text:0:60}"
    [[ ${#text} -gt 60 ]] && preview="${preview}..."
    notify "$preview  (${chars} chars)"
}

mode="batch"
if [[ -f "$MODE_FILE" ]]; then
    mode=$(<"$MODE_FILE")
fi

if is_recording; then
    if [[ "$mode" == "stream" ]]; then
        stop_streaming
    else
        stop_batch
    fi
else
    if [[ "$WHISPER_STREAMING" == "1" ]]; then
        start_streaming
    else
        start_recording_batch
    fi
fi
