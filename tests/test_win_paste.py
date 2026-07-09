"""W4 — clipboard paste sequencing without the restore race (portable).

The old inject_text copied text, fired an async SendInput paste, then a
detached thread slept a fixed 0.4s and restored the previous clipboard. A slow
target that had not yet read the clipboard then pasted the restored (stale)
content, and the text was lost.

Fix: sequence copy -> paste -> (paste observed/bounded poll) -> restore behind
injected ports so restore can never run before paste and a slow paste keeps the
text on the clipboard until it is consumed. Tested headlessly with fakes.
"""

from __future__ import annotations

from whisper_toggle.win_paste import ClipboardInjector


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.t += dt


class FakeClipboard:
    def __init__(self, initial):
        self.value = initial
        self.log: list[tuple] = []

    def get(self):
        return self.value

    def set(self, text):
        self.value = text
        self.log.append(("set", text))


class FakePaster:
    """Records the paste and observes the clipboard when it 'consumes' it.

    ``consume_after`` = number of consumed() polls before the paste is treated
    as having read the clipboard (simulates a slow target).
    """

    def __init__(self, clipboard: FakeClipboard, consume_after: int = 1, never: bool = False):
        self.clipboard = clipboard
        self.consume_after = consume_after
        self.never = never
        self.polls = 0
        self.pasted = False
        self.clipboard_at_consume = None

    def paste(self) -> None:
        self.pasted = True
        self.clipboard.log.append(("paste", self.clipboard.value))

    def consumed(self) -> bool:
        assert self.pasted, "consumed() must never be polled before paste()"
        if self.never:
            return False
        self.polls += 1
        if self.polls >= self.consume_after:
            self.clipboard_at_consume = self.clipboard.value
            return True
        return False


def _order(log, kind, payload):
    for i, entry in enumerate(log):
        if entry == (kind, payload):
            return i
    raise AssertionError(f"{(kind, payload)} not in {log}")


def test_slow_paste_keeps_text_and_restores_after():
    clip = FakeClipboard("PREVIOUS")
    paster = FakePaster(clip, consume_after=4)  # slow target
    clock = FakeClock()
    inj = ClipboardInjector(clip, paster, clock, max_wait=2.0, poll_interval=0.02)

    result = inj.inject("HELLO", restore=True)

    # copy happened, then paste, then restore — strictly in that order.
    copy_i = _order(clip.log, "set", "HELLO")
    paste_i = next(i for i, e in enumerate(clip.log) if e[0] == "paste")
    restore_i = _order(clip.log, "set", "PREVIOUS")
    assert copy_i < paste_i < restore_i
    # The slow paste read OUR text, not the restored previous value.
    assert paster.clipboard_at_consume == "HELLO"
    # Final clipboard is the restored previous content.
    assert clip.value == "PREVIOUS"
    assert result.consumed is True
    assert result.restored is True


def test_restore_never_before_paste():
    clip = FakeClipboard("OLD")
    paster = FakePaster(clip, consume_after=1)
    clock = FakeClock()
    inj = ClipboardInjector(clip, paster, clock)

    inj.inject("NEW", restore=True)

    kinds = [e[0] for e in clip.log]
    # First mutation is the copy, a paste occurs, and the LAST set is restore.
    assert kinds[0] == "set"
    assert "paste" in kinds
    assert clip.log[-1] == ("set", "OLD")
    assert kinds.index("paste") < len(kinds) - 1  # paste precedes the restore set


def test_no_restore_when_previous_is_none():
    clip = FakeClipboard(None)  # nothing to preserve
    paster = FakePaster(clip, consume_after=1)
    inj = ClipboardInjector(clip, paster, FakeClock())

    result = inj.inject("HELLO", restore=True)

    assert clip.value == "HELLO"  # our text stays put
    assert result.restored is False
    # Only the copy set happened; no restore set.
    assert [e for e in clip.log if e[0] == "set"] == [("set", "HELLO")]


def test_no_restore_when_disabled():
    clip = FakeClipboard("PREVIOUS")
    paster = FakePaster(clip, consume_after=1)
    inj = ClipboardInjector(clip, paster, FakeClock())

    result = inj.inject("HELLO", restore=False)

    assert clip.value == "HELLO"
    assert result.restored is False
    assert ("set", "PREVIOUS") not in clip.log[1:]  # never restored


def test_bounded_timeout_restores_even_if_never_consumed():
    clip = FakeClipboard("PREVIOUS")
    paster = FakePaster(clip, never=True)  # paste is never observed to consume
    clock = FakeClock()
    inj = ClipboardInjector(clip, paster, clock, max_wait=0.2, poll_interval=0.02)

    result = inj.inject("HELLO", restore=True)

    # We still restore (never leave our text on the clipboard forever)...
    assert clip.value == "PREVIOUS"
    assert result.restored is True
    assert result.consumed is False
    # ...but the poll was bounded, not unbounded.
    assert len(clock.sleeps) <= 11  # ~max_wait/poll_interval + slack


def test_empty_text_is_noop():
    clip = FakeClipboard("PREVIOUS")
    paster = FakePaster(clip)
    inj = ClipboardInjector(clip, paster, FakeClock())

    result = inj.inject("", restore=True)

    assert clip.value == "PREVIOUS"
    assert clip.log == []
    assert paster.pasted is False
    assert result.restored is False
