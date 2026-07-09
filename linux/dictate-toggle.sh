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
WHISPER_OSD="${WHISPER_OSD:-0}"
WHISPER_OSD_COMMAND="${WHISPER_OSD_COMMAND:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_OSD_BIN="$SCRIPT_DIR/whisper-osd.py"
DEFAULT_STREAM_CLIENT="$SCRIPT_DIR/whisper-stream-ws-client.py"
[[ -x "$DEFAULT_OSD_BIN" ]] || DEFAULT_OSD_BIN="$SCRIPT_DIR/osd_overlay.py"
[[ -x "$DEFAULT_STREAM_CLIENT" ]] || DEFAULT_STREAM_CLIENT="$SCRIPT_DIR/stream_ws_client.py"
WHISPER_OSD_BIN="${WHISPER_OSD_BIN:-$DEFAULT_OSD_BIN}"
WHISPER_STREAM_CLIENT="${WHISPER_STREAM_CLIENT:-$DEFAULT_STREAM_CLIENT}"
# Recorder for the streaming (raw PCM) path. "auto" prefers pw-record so a
# pipewire-only install works without ALSA; falls back to arecord. Override
# with a specific binary name (e.g. arecord) if desired.
WHISPER_STREAM_RECORDER="${WHISPER_STREAM_RECORDER:-auto}"

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

is_recording() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid=$(<"$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

# True when running under a native Wayland session (no X11 tooling works).
is_wayland() {
    [[ -n "${WAYLAND_DISPLAY:-}" ]]
}

# Copy stdin to the clipboard using the session-appropriate tool. Degrades with
# a notify-send message (never a silent no-op) when the required tool is absent.
copy_to_clipboard() {
    if is_wayland; then
        if command -v wl-copy >/dev/null 2>&1; then
            wl-copy
        else
            cat >/dev/null 2>&1 || true
            notify "Install wl-clipboard (wl-copy) for Wayland clipboard" critical
            return 1
        fi
    else
        if command -v xclip >/dev/null 2>&1; then
            xclip -selection clipboard
        else
            cat >/dev/null 2>&1 || true
            notify "Install xclip for clipboard support" critical
            return 1
        fi
    fi
}

# Echo "<wm_class> <title>" (lowercased) for the active window, or empty. On
# Wayland there is no portable active-window query (GNOME blocks it), so we
# gracefully degrade to an empty target and the default (non-terminal) paste.
detect_window() {
    if is_wayland; then
        return 0
    fi
    local wid wm_class="" title=""
    if command -v xdotool >/dev/null 2>&1 && wid=$(xdotool getactivewindow 2>/dev/null); then
        if command -v xprop >/dev/null 2>&1; then
            wm_class=$(xprop -id "$wid" WM_CLASS 2>/dev/null | sed 's/.*= //' | tr -d '"' | tr '[:upper:]' '[:lower:]' || true)
        fi
        title=$(xdotool getwindowname "$wid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)
    fi
    printf '%s %s' "$wm_class" "$title"
}

is_terminal_target() {
    case "$1" in
        *gnome-terminal*|*kgx*|*kitty*|*alacritty*|*foot*|*xterm*|*tilix*|*terminator*|*konsole*|*wezterm*|*st-256color*|*tmux*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Fire the paste keystroke (Ctrl+V, or Ctrl+Shift+V in terminals) via the
# session-appropriate tool.
paste_text() {
    local target terminal=0
    target=$(detect_window)
    is_terminal_target "$target" && terminal=1

    sleep 0.05

    if is_wayland; then
        if command -v wtype >/dev/null 2>&1; then
            if (( terminal )); then
                wtype -M ctrl -M shift -k v -m shift -m ctrl
            else
                wtype -M ctrl -k v -m ctrl
            fi
        else
            notify "Install wtype for Wayland paste" critical
            return 1
        fi
    else
        if command -v xdotool >/dev/null 2>&1; then
            if (( terminal )); then
                xdotool key --clearmodifiers ctrl+shift+v
            else
                xdotool key --clearmodifiers ctrl+v
            fi
        else
            notify "Install xdotool for paste support" critical
            return 1
        fi
    fi
}

type_text() {
    local text="$1"
    sleep 0.05
    if is_wayland; then
        if command -v wtype >/dev/null 2>&1; then
            wtype -- "$text"
        else
            notify "Install wtype for Wayland typing" critical
            return 1
        fi
    else
        if command -v xdotool >/dev/null 2>&1; then
            xdotool type --clearmodifiers --delay 0 "$text"
        else
            notify "Install xdotool for typing support" critical
            return 1
        fi
    fi
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

    printf '%s' "$text" | copy_to_clipboard
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

# Echo the streaming recorder to use, or return nonzero if none is available.
# "auto" prefers pw-record (pipewire) then arecord (ALSA). A specific override
# must be present on PATH or resolution fails (so we can fall back to batch).
resolve_stream_recorder() {
    if [[ "$WHISPER_STREAM_RECORDER" != "auto" ]]; then
        command -v "$WHISPER_STREAM_RECORDER" >/dev/null 2>&1 || return 1
        printf '%s' "$WHISPER_STREAM_RECORDER"
        return 0
    fi
    if command -v pw-record >/dev/null 2>&1; then
        printf 'pw-record'
        return 0
    fi
    if command -v arecord >/dev/null 2>&1; then
        printf 'arecord'
        return 0
    fi
    return 1
}

# Launch the given recorder writing headerless raw S16LE mono 16k PCM to the
# PCM fifo, backgrounded. pw-record streams to stdout (`-`) with no header;
# arecord uses its raw mode.
launch_raw_recorder() {
    local recorder="$1"
    case "$recorder" in
        pw-record)
            pw-record --rate 16000 --channels 1 --format s16 - 9>&- > "$PCM_FIFO" &
            ;;
        *)
            "$recorder" -t raw -f S16_LE -r 16000 -c 1 - 9>&- > "$PCM_FIFO" &
            ;;
    esac
}

start_streaming() {
    local recorder="${1:-pw-record}"
    rm -f "$WAV_FILE" "$FINAL_FILE" "$STREAM_LOG" "$PCM_FIFO"
    cleanup_stream_files
    mkfifo "$PCM_FIFO"
    start_osd

    local osd_arg=()
    if [[ "$WHISPER_OSD" == "1" ]]; then
        osd_arg=(--osd-fifo "$OSD_FIFO")
    fi

    local partial_arg=()
    # Decoupled from WHISPER_OSD: the iPhone/Windows-style UX is no OSD AND
    # no in-place partial typing. WS client streams partials silently, types
    # the final on commit. Set WHISPER_PARTIALS_INLINE=1 to re-enable jankier
    # in-place partial typing (BackSpace + retype on revisions).
    if [[ "${WHISPER_PARTIALS_INLINE:-0}" == "1" ]]; then
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

    launch_raw_recorder "$recorder"

    local rec_pid=$!
    sleep 0.2
    if ! kill -0 "$rec_pid" 2>/dev/null; then
        kill "$client_pid" 2>/dev/null || true
        wait "$client_pid" 2>/dev/null || true
        cleanup_stream_files
        echo "[dictate] recorder ($recorder) failed to start, falling back to batch" >&2
        start_recording_batch
        return 0
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

    printf '%s' "$text" | copy_to_clipboard
    paste_text
    local chars=${#text}
    local preview="${text:0:60}"
    [[ ${#text} -gt 60 ]] && preview="${preview}..."
    notify "$preview  (${chars} chars)"
}

main() {
    mkdir -p "$WORK_DIR"

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        notify "Busy, wait a moment" low
        exit 0
    fi
    trap cleanup_lock EXIT

    local mode="batch"
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
        local recorder=""
        if [[ "$WHISPER_STREAMING" == "1" ]] && recorder=$(resolve_stream_recorder); then
            start_streaming "$recorder"
        else
            if [[ "$WHISPER_STREAMING" == "1" ]]; then
                echo "[dictate] no streaming recorder available, using batch" >&2
                notify "Streaming recorder missing, using batch" low
            fi
            start_recording_batch
        fi
    fi
}

# Only run the toggle when executed directly; sourcing (e.g. from tests)
# exposes the helper functions without side effects.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
