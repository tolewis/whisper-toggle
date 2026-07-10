"""Controller state machine tests — injected fakes, no mic/GPU."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from whisper_toggle.controller import Controller, ControllerConfig, State


@dataclass
class FakeAudio:
    chunks: list = field(default_factory=list)
    recording: bool = False

    def start(self):
        self.recording = True
        self.chunks = [b"\x00\x00" * 1600]

    def stop(self) -> bytes:
        self.recording = False
        data = b"".join(self.chunks)
        self.chunks = []
        return data


@dataclass
class FakeApi:
    healthy: bool = True
    partials: list[str] = field(default_factory=lambda: ["hello", "hello world"])
    final_text: str = "hello world"
    stream_calls: int = 0
    batch_calls: int = 0

    def is_healthy(self) -> bool:
        return self.healthy

    def stream(self, pcm: bytes, on_partial, on_confirmed, on_final):
        self.stream_calls += 1
        for p in self.partials:
            on_partial(p)
        on_confirmed("hello")
        on_final(self.final_text)

    def batch(self, pcm: bytes) -> str:
        self.batch_calls += 1
        return self.final_text


@dataclass
class FakeTyper:
    events: list = field(default_factory=list)
    current: str = ""

    def set_live_text(self, confirmed: str, partial: str):
        text = (confirmed + (" " if confirmed and partial else "") + partial).strip()
        self.events.append(("live", text))
        self.current = text

    def commit_final(self, text: str):
        self.events.append(("final", text))
        self.current = text

    def clear_session(self):
        self.events.append(("clear",))
        self.current = ""


def make_controller(api=None, audio=None, typer=None, streaming=True, min_bytes=100):
    api = api or FakeApi()
    audio = audio or FakeAudio()
    typer = typer or FakeTyper()
    cfg = ControllerConfig(
        streaming=streaming,
        min_audio_bytes=min_bytes,
        partial_debounce_ms=0,
        hardware_catchup_ms=0,
    )
    return Controller(api=api, audio=audio, typer=typer, config=cfg), api, audio, typer


def test_idle_to_recording_on_toggle():
    ctl, *_ = make_controller()
    assert ctl.state == State.IDLE
    ctl.toggle()
    assert ctl.state == State.RECORDING


def test_recording_to_processing_on_toggle():
    ctl, api, audio, typer = make_controller()
    ctl.toggle()  # start
    ctl.toggle()  # stop → process
    assert ctl.state == State.IDLE
    assert api.stream_calls == 1
    assert any(e[0] == "final" for e in typer.events)


def test_ignore_toggle_while_processing():
    """Second toggle during processing must not re-enter recording mid-flight."""

    class SlowApi(FakeApi):
        def stream(self, pcm, on_partial, on_confirmed, on_final):
            self.stream_calls += 1
            # Simulate work; controller should be PROCESSING
            time.sleep(0.05)
            on_final(self.final_text)

    ctl, api, audio, typer = make_controller(api=SlowApi())
    ctl.toggle()
    # Force processing path on a thread-less sync controller: stop records then process
    ctl.toggle()
    assert ctl.state == State.IDLE
    assert api.stream_calls == 1


def test_short_audio_rejected():
    audio = FakeAudio()
    audio.chunks = [b"\x00\x00"]  # tiny
    ctl, api, _, typer = make_controller(audio=audio, min_bytes=1000)
    # Patch stop to return tiny payload
    audio.start = lambda: setattr(audio, "recording", True)
    audio.stop = lambda: b"\x00\x00"
    ctl.toggle()
    ctl.toggle()
    assert api.stream_calls == 0
    assert api.batch_calls == 0
    assert ctl.state == State.IDLE


def test_api_down_moves_to_error_and_recovers():
    api = FakeApi(healthy=False)
    ctl, _, _, _ = make_controller(api=api)
    ctl.toggle()
    assert ctl.state == State.ERROR
    api.healthy = True
    ctl.toggle()
    assert ctl.state == State.RECORDING


def test_live_partials_emitted_before_final():
    ctl, api, _, typer = make_controller(streaming=True)
    ctl.toggle()
    ctl.toggle()
    kinds = [e[0] for e in typer.events]
    assert "live" in kinds
    assert kinds[-1] == "final"
    assert typer.current == "hello world"


def test_controller_default_is_reliable_batch_mode():
    assert ControllerConfig().streaming is False


def test_batch_path_when_streaming_disabled():
    ctl, api, _, typer = make_controller(streaming=False)
    ctl.toggle()
    ctl.toggle()
    assert api.batch_calls == 1
    assert api.stream_calls == 0
    assert typer.events[-1] == ("final", "hello world")


def test_paste_uses_final_text():
    api = FakeApi(final_text="ship it")
    ctl, _, _, typer = make_controller(api=api)
    ctl.toggle()
    ctl.toggle()
    assert typer.events[-1] == ("final", "ship it")


# ── A5 / H1: the toggle lock must be held for the whole call ────────────────
# Old code released the lock during transcription and re-acquired it after,
# leaving a window (state IDLE, lock free) where a re-entrant/concurrent toggle
# could start a SECOND recording. Reproduced deterministically by toggling again
# from the on_state IDLE callback that fires at exactly that window.

def test_no_reentrant_double_start_at_finalize_window():
    starts = {"n": 0}

    class CountingAudio(FakeAudio):
        def start(self):
            starts["n"] += 1
            super().start()

    audio = CountingAudio()
    api = FakeApi()
    typer = FakeTyper()
    cfg = ControllerConfig(streaming=False, min_audio_bytes=100,
                           partial_debounce_ms=0, hardware_catchup_ms=0)

    fired = {"done": False}

    def on_state(state, msg):
        # Fire ONE re-entrant toggle the instant we finish processing.
        if state == State.IDLE and msg == "Ready" and not fired["done"]:
            fired["done"] = True
            ctl.toggle()

    ctl = Controller(api=api, audio=audio, typer=typer, config=cfg, on_state=on_state)
    ctl.toggle()  # IDLE -> RECORDING (start #1)
    ctl.toggle()  # RECORDING -> PROCESSING -> IDLE (fires the re-entrant toggle)

    # The re-entrant toggle must be a no-op: exactly one recording ever started.
    assert starts["n"] == 1


def test_stream_commits_confirmed_when_no_final_frame():
    """If the server closes without a `final`, the confirmed text must still be
    typed instead of silently dropped."""

    class NoFinalApi(FakeApi):
        def stream(self, pcm, on_partial, on_confirmed, on_final):
            self.stream_calls += 1
            on_partial("hello")
            on_confirmed("hello world")
            # server ends the stream WITHOUT calling on_final

    ctl, api, _, typer = make_controller(api=NoFinalApi(), streaming=True)
    ctl.toggle()
    ctl.toggle()
    finals = [e for e in typer.events if e[0] == "final"]
    assert finals, "no final commit happened despite confirmed text"
    assert finals[-1] == ("final", "hello world")
    assert ctl.state == State.IDLE
