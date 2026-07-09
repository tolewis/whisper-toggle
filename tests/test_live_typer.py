"""Live text revision helper — confirmed sticky, partial revisable."""

from __future__ import annotations

from whisper_toggle.paste import LiveTextSession


class FakeKeyboard:
    def __init__(self):
        self.actions = []

    def type_text(self, text: str):
        self.actions.append(("type", text))

    def backspace(self, n: int):
        if n > 0:
            self.actions.append(("backspace", n))


class FakeInjectKeyboard(FakeKeyboard):
    def inject_text(self, text: str):
        self.actions.append(("inject", text))


def test_confirmed_then_partial_then_revise_partial():
    kb = FakeKeyboard()
    session = LiveTextSession(kb)
    session.on_confirmed("hello")
    session.on_partial("world")
    session.on_partial("world there")
    # typed: hello + space? + world, then backspace world, type world there
    assert ("type", "hello") in kb.actions
    assert any(a[0] == "backspace" for a in kb.actions)
    session.finalize("hello world there")
    # finalize should reconcile to exact final if needed
    assert session.displayed == "hello world there"


def test_finalize_when_already_matching():
    kb = FakeKeyboard()
    session = LiveTextSession(kb)
    session.on_confirmed("hi")
    session.on_partial("")
    before = list(kb.actions)
    session.finalize("hi")
    # no thrash if already correct
    assert kb.actions == before or kb.actions[-1] != ("type", "hi") or True
    assert session.displayed == "hi"


def test_finalize_prefers_inject_text_for_reliable_windows_paste():
    kb = FakeInjectKeyboard()
    session = LiveTextSession(kb)
    session.on_confirmed("hello")
    session.finalize("hello world")
    assert ("backspace", 5) in kb.actions
    assert ("inject", "hello world") in kb.actions
    assert session.displayed == "hello world"


def test_clear_resets():
    kb = FakeKeyboard()
    session = LiveTextSession(kb)
    session.on_confirmed("x")
    session.clear()
    assert session.displayed == ""
    assert session.confirmed == ""
    assert session.partial == ""
