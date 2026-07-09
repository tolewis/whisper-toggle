"""L3 — stream_ws_client handles server error frames and early aborts cleanly.

A server-side stream error (an ``{"type":"error"}`` frame, or the socket
closing before a ``final`` frame) must not produce a lost transcript or an
uncaught traceback. The client must exit with a distinct nonzero code the
shell can detect, and never crash.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT = REPO_ROOT / "linux" / "stream_ws_client.py"


def _load_client_module():
    spec = importlib.util.spec_from_file_location("stream_ws_client", CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLIENT_MOD = _load_client_module()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


MOCK_SERVER = r'''
import asyncio
import json
import os
import sys

import websockets

MODE = sys.argv[1]
PORT = int(sys.argv[2])


async def handler(websocket):
    # Wait for the client to send at least one frame so the connection is live.
    try:
        await websocket.recv()
    except Exception:
        pass
    if MODE == "error":
        await websocket.send(json.dumps({"type": "error", "message": "boom"}))
        await websocket.close()
    else:  # earlyclose: shut the socket without ever sending a final
        await websocket.close()


async def main():
    async with websockets.serve(handler, "127.0.0.1", PORT):
        await asyncio.Future()


asyncio.run(main())
'''


def _run_scenario(tmp_path: Path, mode: str):
    port = _free_port()
    server_script = tmp_path / "mock_server.py"
    server_script.write_text(MOCK_SERVER, encoding="utf-8")

    server = subprocess.Popen(
        [sys.executable, str(server_script), mode, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Wait for the port to accept connections.
    deadline = time.time() + 5
    while time.time() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    else:
        server.kill()
        raise RuntimeError("mock server did not start")

    final_file = tmp_path / "final.txt"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(CLIENT),
                "--endpoint",
                f"ws://127.0.0.1:{port}",
                "--final-file",
                str(final_file),
                "--open-timeout",
                "2.0",
            ],
            input=b"\x00\x00" * 1600,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    return result, final_file


def test_server_error_frame_exits_with_stream_error_no_traceback(tmp_path):
    result, final_file = _run_scenario(tmp_path, "error")
    stderr = result.stderr.decode("utf-8", "replace")

    assert "Traceback" not in stderr, stderr
    assert result.returncode == CLIENT_MOD.STREAM_ERROR, (
        result.returncode,
        stderr,
    )
    # No transcript should have been written on a server error.
    assert not final_file.exists() or final_file.read_text() == ""


def test_early_close_without_final_exits_nonzero_no_traceback(tmp_path):
    result, final_file = _run_scenario(tmp_path, "earlyclose")
    stderr = result.stderr.decode("utf-8", "replace")

    assert "Traceback" not in stderr, stderr
    assert result.returncode != 0, stderr
    assert not final_file.exists() or final_file.read_text() == ""
