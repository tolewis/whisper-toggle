"""S3 — LivePreviewOverlay start/stop/update lifecycle robustness.

These drive the *controllable* worker-management logic (idempotent start/stop,
single-worker guarantee, auto-start on update, restart after stop) without ever
opening a real Tk window: the Tk mainloop in ``_run`` is replaced with a tiny
fake worker that mirrors the real queue-drain lifecycle (exits on the ``None``
sentinel). The actual on-screen rendering can only be validated interactively
on Windows and is out of scope here.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whisper_toggle.live_overlay import LivePreviewOverlay


def _install_fake_worker(overlay, runs):
    """Replace the Tk mainloop with a queue-draining worker that records each
    launch and exits on the None sentinel — same lifecycle contract as _run."""

    def fake_run(self):
        runs.append(threading.current_thread())
        q = self._queue
        while True:
            item = q.get()
            if item is None:
                return

    overlay._run = types.MethodType(fake_run, overlay)


def _alive(runs):
    return [t for t in runs if t.is_alive()]


def test_start_is_idempotent():
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    overlay.start()
    overlay.start()
    overlay.start()
    time.sleep(0.05)

    assert len(runs) == 1, "start() spawned more than one worker"
    assert len(_alive(runs)) == 1
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_update_autostarts_worker():
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    overlay.update("hello")
    time.sleep(0.05)

    assert len(runs) == 1
    assert len(_alive(runs)) == 1
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_update_reuses_live_worker_no_second_thread():
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    overlay.start()
    for i in range(20):
        overlay.update(f"text {i}")
    time.sleep(0.05)

    assert len(runs) == 1, "update() spawned a second Tk worker thread"
    assert len(_alive(runs)) == 1
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_update_after_stop_restarts_single_worker():
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    overlay.start()
    time.sleep(0.05)
    overlay.stop()
    overlay._thread.join(timeout=2)
    assert len(_alive(runs)) == 0, "worker did not exit on stop()"

    # A fresh update after stop must spin up exactly one new worker.
    overlay.update("again")
    time.sleep(0.05)
    assert len(runs) == 2
    assert len(_alive(runs)) == 1
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_concurrent_first_updates_spawn_single_worker():
    """Many threads hitting update() at once (the real usage: recv callbacks
    firing while the hotkey handler also drives the overlay) must not each spin
    up their own Tk mainloop. Without a lock guarding worker creation this
    races and spawns several Tk threads."""
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    n = 12
    barrier = threading.Barrier(n)

    def hammer():
        barrier.wait()
        overlay.update("x")

    threads = [threading.Thread(target=hammer) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.05)

    assert len(runs) == 1, f"race spawned {len(runs)} Tk worker threads"
    assert len(_alive(runs)) == 1
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_restart_ignores_stale_stop_sentinel():
    """A leftover None sentinel from a prior stop() (whose worker had already
    exited) must not immediately kill a freshly started worker. The worker
    creation path must hand the new worker a clean queue."""
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    # Stale sentinel sitting in the queue before any worker runs.
    overlay._queue.put_nowait(None)

    overlay.start()
    time.sleep(0.05)

    assert len(_alive(runs)) == 1, "new worker consumed a stale stop sentinel and died"
    overlay.stop()
    overlay._thread.join(timeout=2)


def test_never_more_than_one_live_worker_under_churn():
    overlay = LivePreviewOverlay()
    runs: list[threading.Thread] = []
    _install_fake_worker(overlay, runs)

    for _ in range(30):
        overlay.start()
        overlay.update("x")
        overlay.stop()
        overlay.update("y")
    time.sleep(0.1)

    assert len(_alive(runs)) <= 1, "more than one Tk worker alive at once"
    overlay.stop()
    if overlay._thread is not None:
        overlay._thread.join(timeout=2)
