#!/usr/bin/env python3
"""Bridge raw PCM stdin to Whisper Toggle's streaming WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys


CONNECT_ERROR = 75
STREAM_ERROR = 76


class StreamAbort(Exception):
    """Raised when the server signals an error or closes before a final."""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--final-file", required=True)
    parser.add_argument("--osd-fifo", default="")
    parser.add_argument("--xdotool-partials", action="store_true")
    parser.add_argument("--chunk-bytes", type=int, default=8192)
    parser.add_argument("--open-timeout", type=float, default=1.0)
    return parser.parse_args()


def write_osd(osd, line: str):
    if osd is None:
        return
    try:
        osd.write(line + "\n")
        osd.flush()
    except BrokenPipeError:
        return


def type_text(text: str):
    if not text:
        return
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "0", text],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def revise_text(previous: str, current: str) -> str:
    if previous:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "--repeat", str(len(previous)), "--delay", "0", "BackSpace"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    type_text(current)
    return current


async def send_audio(websocket, chunk_bytes: int):
    while True:
        data = await asyncio.to_thread(sys.stdin.buffer.read, chunk_bytes)
        if not data:
            await websocket.send(json.dumps({"type": "end"}))
            return
        await websocket.send(data)


async def receive_messages(websocket, final_file: Path, osd, xdotool_partials: bool):
    typed = ""
    async for message in websocket:
        if isinstance(message, bytes):
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue

        line = json.dumps(payload, ensure_ascii=False)
        print(line, flush=True)
        write_osd(osd, line)

        kind = payload.get("type")
        if kind == "error":
            detail = str(payload.get("message", "server error"))
            raise StreamAbort(f"server error: {detail}")
        text = str(payload.get("text", ""))
        if xdotool_partials and kind in ("partial", "confirmed"):
            typed = revise_text(typed, text)
        elif kind == "final":
            final_file.write_text(text, encoding="utf-8")
            if xdotool_partials and typed:
                revise_text(typed, "")
            return

    # The socket closed without ever delivering a final transcript.
    raise StreamAbort("stream closed before final transcript")


async def run(args) -> int:
    try:
        import websockets
    except ModuleNotFoundError:
        print("[dictate] ERROR: python websockets module is not installed", file=sys.stderr)
        return CONNECT_ERROR

    osd = None
    if args.osd_fifo:
        osd = open(args.osd_fifo, "w", encoding="utf-8")

    try:
        async with websockets.connect(
            args.endpoint,
            open_timeout=args.open_timeout,
            max_size=None,
        ) as websocket:
            sender = asyncio.create_task(send_audio(websocket, args.chunk_bytes))
            receiver = asyncio.create_task(
                receive_messages(websocket, Path(args.final_file), osd, args.xdotool_partials)
            )
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            exceptions = [
                task.exception() for task in done if task.exception() is not None
            ]
            # Prefer a StreamAbort so a server error maps to STREAM_ERROR even
            # if the sender also tripped on the closing socket.
            for exc in exceptions:
                if isinstance(exc, StreamAbort):
                    raise exc
            if exceptions:
                raise exceptions[0]
            return 0
    except StreamAbort as exc:
        print(f"[dictate] streaming aborted: {exc}", file=sys.stderr)
        return STREAM_ERROR
    except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as exc:
        print(f"[dictate] streaming connection failed: {exc}", file=sys.stderr)
        return CONNECT_ERROR
    finally:
        if osd is not None:
            osd.close()


def main():
    args = parse_args()
    Path(args.final_file).parent.mkdir(parents=True, exist_ok=True)
    code = asyncio.run(run(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
