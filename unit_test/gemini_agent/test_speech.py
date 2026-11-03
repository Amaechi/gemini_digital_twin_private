"""Unit tests for gemini_agent.speech (``--speak``; the ``say`` subprocess is monkeypatched)."""

from __future__ import annotations

import io
import subprocess

from gemini_agent import speech
from gemini_agent.schemas.decision import InputRequest, Phase


def _request(default="A", unit=None) -> InputRequest:
    return InputRequest(
        request_id="REQ-01-DB-30",
        phase=Phase.P1,
        question="Which DB-30 interpretation applies?",
        assumption_id="DB-30",
        default=default,
        unit=unit,
    )


def test_speaker_runs_say_with_the_text(monkeypatch):
    """When ``say`` is on the PATH each utterance is one ``say <text>`` subprocess call."""
    calls = []
    monkeypatch.setattr(speech.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        speech.subprocess,
        "run",
        lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0),
    )
    speaker = speech.Speaker(True)
    assert speaker.available
    assert speaker.say("  Hello\n  world  ") is True
    assert calls == [
        (
            ["say", "--", "Hello world"],
            {"check": False, "timeout": speech.SAY_TIMEOUT_S, "capture_output": True},
        )
    ]
    assert speaker.spoken == ["Hello world"]
    assert speaker.say("") is False and len(calls) == 1  # nothing to say


def test_speaker_is_a_noop_with_one_note_without_say(monkeypatch):
    """Without ``say`` (Linux, CI) nothing is run; one note is printed; texts are still kept."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: None)

    def boom(*args, **kwargs):
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(speech.subprocess, "run", boom)
    out = io.StringIO()
    speaker = speech.Speaker(True, out=out)
    assert not speaker.available
    assert speaker.say("one") is False and speaker.say("two") is False
    assert speaker.spoken == ["one", "two"]
    assert out.getvalue().count("speech is disabled") == 1


def test_disabled_speaker_never_speaks(monkeypatch):
    """Without ``--speak`` the speaker records nothing and runs nothing."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(speech.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError))
    speaker = speech.Speaker(False)
    assert speaker.say("quiet") is False and speaker.spoken == [] and not speaker.available


def test_subprocess_errors_are_reported_not_raised(monkeypatch):
    """A failing or hanging ``say`` never breaks the CLI."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: f"/usr/bin/{command}")

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(speech.subprocess, "run", timeout)
    out = io.StringIO()
    assert speech.Speaker(True, out=out).say("slow") is False
    assert "TimeoutExpired" in out.getvalue()


def test_request_text_states_the_default_and_unit():
    """Each InputRequest is spoken as its question followed by the default (and unit)."""
    assert speech.request_text(_request("A")) == (
        "Which DB-30 interpretation applies? The default is A."
    )
    assert speech.request_text(_request(55.0, "degC")).endswith("The default is 55.0 degC.")
    assert speech.request_text(_request({"interpretation": "A"})).endswith(
        'The default is {"interpretation": "A"}.'
    )


def test_speaking_ask_speaks_then_asks(monkeypatch):
    """``speaking_ask`` speaks the question with its default, then calls the terminal prompt."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: None)
    speaker = speech.Speaker(True, out=io.StringIO())
    asked = []
    ask = speech.speaking_ask(speaker, lambda request: asked.append(request.request_id) or "B")
    assert ask(_request()) == "B"
    assert asked == ["REQ-01-DB-30"]
    assert speaker.spoken == ["Which DB-30 interpretation applies? The default is A."]
