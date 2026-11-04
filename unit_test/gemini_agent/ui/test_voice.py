"""Tests of the push-to-talk voice front-end (``gemini_agent.ui.voice``, ``cli talk``).

Offline: the microphone (``sounddevice``) and faster-whisper are replaced by fakes or the
``--typed`` keyboard path, ``say`` by a recording subprocess stub, and the LLM normaliser by a
``FunctionModel``. The talk loop drives a complete policy run end to end.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import types
import typing
from collections import deque
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from gemini_agent import speech
from gemini_agent.agents import engine as engine_module
from gemini_agent.cli import EXIT_OK, EXIT_USAGE, main
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner.design_runner import APPROVAL_ID
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import InputRequest, Phase
from gemini_agent.ui import dialogue, voice
from unit_test.gemini_agent.llm_fakes import (
    Counting,
    always_invalid,
    fake_model,
    output_name,
    policy_like,
    user_prompt,
)

# ----------------------------------------------------------------------------- helpers


def _request(assumption_id="DB-31", default=55.0, unit="degC", request_id=None) -> InputRequest:
    return InputRequest(
        request_id=request_id or f"REQ-01-{assumption_id}",
        phase=Phase.P1,
        question=f"Value of {assumption_id}?",
        assumption_id=assumption_id,
        default=default,
        unit=unit,
    )


APPROVAL = InputRequest(
    request_id=APPROVAL_ID,
    phase=Phase.P10,
    question="Approve the recommended design C2 (verifier verdict ACCEPTED)? yes/no",
    default="pending",
)
DB30 = _request("DB-30", {"interpretation": "A", "days": 330}, None)
BASIS = _request("CFG-BASIS-CONFIRM", "keep", None)
INPUTS = _request("CFG-INPUT-DEFAULTS", "repository defaults", None)

_WORDS = {"fifty": 50, "sixty": 60, "five": 5, "six": 6, "twenty": 20}


def _fake_normaliser_reply(transcript: str, expected: dict) -> dict:
    """Answer like a well-behaved LLM (words → digits, options, yes/no, default)."""
    text = transcript.lower().replace("-", " ")
    if "default" in text:
        return {"heard": transcript, "interpretation": "default", "value": None}
    kind = expected["kind"]
    if kind == "number":
        total = sum(_WORDS.get(word, 0) for word in text.split())
        digits = [tok for tok in text.replace(",", ".").split() if tok.replace(".", "").isdigit()]
        value = digits[0] if digits else (str(total) if total else None)
    elif kind == "yes_no":
        value = "yes" if any(w in text for w in ("yes", "approve", "fine")) else "no"
    elif kind == "choice":
        value = next((o for o in expected["options"] if o.lower() in text.split()), None)
    else:
        value = transcript
    if value is None:
        return {"heard": transcript, "interpretation": "unclear", "value": None}
    return {"heard": transcript, "interpretation": "value", "value": value}


def voice_like(messages, info) -> ModelResponse:
    """FunctionModel: the policy for the runner's agents, a sensible ear for the normaliser."""
    if output_name(info) != "NormalisedAnswer":
        return policy_like(messages, info)
    prompt = user_prompt(messages)
    expected = json.loads(prompt.split("EXPECTED: ", 1)[1].split("\n", 1)[0])
    transcript = prompt.split("TRANSCRIPT: ", 1)[1]
    return ModelResponse(parts=[TextPart(json.dumps(_fake_normaliser_reply(transcript, expected)))])


def _fake_say(monkeypatch) -> list[str]:
    """Record ``say`` calls; any other subprocess (matplotlib's font lookup in P10) runs."""
    spoken: list[str] = []
    real_run = subprocess.run
    real_which = speech.shutil.which
    monkeypatch.setattr(
        speech.shutil,
        "which",
        lambda cmd, *a, **k: (
            "/usr/bin/say" if cmd == speech.SAY_COMMAND else real_which(cmd, *a, **k)
        ),
    )

    def run(argv, **kwargs):
        if argv[:2] != [speech.SAY_COMMAND, "--"]:
            return real_run(argv, **kwargs)
        spoken.append(argv[2])
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(speech.subprocess, "run", run)
    return spoken


class ScriptedKeyboard:
    """``input`` replacement: transcripts for the ``[typed]`` prompt, y/n for ``[Y/n]``."""

    def __init__(self, transcripts, confirmations=None) -> None:
        """Queue the lines the user would type."""
        self.transcripts = deque(transcripts)
        self.confirmations = deque(confirmations or [])
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        """Answer one prompt; EOF when the transcripts are exhausted."""
        self.prompts.append(prompt)
        if "[Y/n]" in prompt:
            return self.confirmations.popleft() if self.confirmations else ""
        if not self.transcripts:
            raise EOFError
        return self.transcripts.popleft()


def _session(listen, confirm=lambda q: True, normaliser=None, **kw) -> voice.VoiceSession:
    out = io.StringIO()
    speaker = speech.Speaker(True, out=out)
    speaker.available = False  # keep every text in speaker.spoken, run no subprocess
    speaker._noted = True
    return voice.VoiceSession(
        speaker=speaker,
        listen=listen,
        normaliser=normaliser or voice.PolicyNormaliser(),
        confirm=confirm,
        out=out,
        **kw,
    )


# ----------------------------------------------------------------------------- answer spec


def test_answer_spec_mirrors_the_runners_coercion():
    """One spec per ``_coerce_answer`` branch; the approval has no usable default."""
    assert voice.answer_spec(APPROVAL) == voice.AnswerSpec(kind="yes_no", options=("yes", "no"))
    assert voice.answer_spec(BASIS).options == ("keep", "change")
    assert voice.answer_spec(BASIS).default_text == "keep"
    assert voice.answer_spec(INPUTS) == voice.AnswerSpec(
        kind="choice", options=("defaults", "yes"), default_text="defaults"
    )
    number = voice.answer_spec(_request())
    assert number == voice.AnswerSpec(kind="number", unit="degC", default_text="55")
    assert number.describe() == "a number in degC"
    assert voice.answer_spec(DB30) == voice.AnswerSpec(
        kind="choice", options=("A", "B"), default_text="A"
    )
    text = voice.answer_spec(_request("X", "free", None))
    assert text.kind == "text" and text.default_text == "free"


def test_canonical_value_and_check_normalised():
    """Values are returned in the runner's form; anything else is a validation error."""
    yes_no = voice.answer_spec(APPROVAL)
    assert voice.canonical_value(yes_no, "Yes, approve it.") == "yes"
    assert voice.canonical_value(yes_no, "No.") == "no"
    with pytest.raises(ValueError):
        voice.canonical_value(yes_no, "yes and no")
    number = voice.answer_spec(_request())
    assert voice.canonical_value(number, "about 65 degrees") == "65"
    assert voice.canonical_value(number, "62,5") == "62.5"
    assert voice.canonical_value(number, "DB-31 is 65") == "65"  # no sign glued to a word
    assert voice.canonical_value(number, "-5") == "-5"
    for negated in ("do not approve", "don't approve", "not approved", "never approve it"):
        with pytest.raises(ValueError, match="negated"):
            voice.canonical_value(yes_no, negated)
    with pytest.raises(ValueError):
        voice.canonical_value(number, "warm")
    choice = voice.answer_spec(DB30)
    assert voice.canonical_value(choice, "b.") == "B"
    with pytest.raises(ValueError):
        voice.canonical_value(choice, "C")
    assert voice.canonical_value(voice.answer_spec(_request("X", "free", None)), " hi ") == "hi"

    ok = voice.NormalisedAnswer(heard="65", interpretation="value", value="65")
    assert voice.check_normalised(number, ok) is None
    bad = voice.NormalisedAnswer(heard="x", interpretation="value", value="sixty-five")
    assert "expected answer is a number in degC" in voice.check_normalised(number, bad)
    assert "needs a value" in voice.check_normalised(number, ok.model_copy(update={"value": None}))
    default = voice.NormalisedAnswer(heard="default", interpretation="default")
    assert voice.check_normalised(number, default) is None
    assert "no default" in voice.check_normalised(yes_no, default)
    assert (
        voice.check_normalised(yes_no, ok.model_copy(update={"interpretation": "unclear"})) is None
    )


@pytest.mark.parametrize(
    ("request_", "transcript", "interpretation", "value"),
    [
        (_request(), "65 degrees", "value", "65"),
        (_request(), "Let's take 60.", "value", "60"),
        (_request(), "sixty-five", "unclear", None),
        (_request(), "the default", "default", None),
        (_request(), "keep the default please", "unclear", None),  # not a bare default phrase
        (DB30, "A.", "value", "A"),
        (DB30, "option B", "value", "B"),
        (DB30, "I'd say interpretation A", "value", "A"),
        (DB30, "a bit of both", "unclear", None),
        (BASIS, "keep", "value", "keep"),
        (BASIS, "change", "value", "change"),
        (INPUTS, "use the defaults", "default", None),
        (APPROVAL, "Yes please", "value", "yes"),
        (APPROVAL, "No, reject it", "value", "no"),
        (APPROVAL, "default", "unclear", None),  # the approval has no default
        (APPROVAL, "do not approve", "unclear", None),  # negated yes-word is never yes
        (APPROVAL, "I don't approve this", "unclear", None),
        (APPROVAL, "no, do not approve", "unclear", None),  # mixed: asked again
        (_request(), "not the default", "unclear", None),
        (_request(), "DB-31 is 65", "value", "65"),
        (INPUTS, "yes", "value", "yes"),
        (APPROVAL, "", "unclear", None),
    ],
)
def test_policy_normaliser_keywords(request_, transcript, interpretation, value):
    """The deterministic normaliser (``--engine policy`` and the LLM fallback)."""
    result = voice.PolicyNormaliser().normalise(request_, transcript)
    assert (result.interpretation, result.value) == (interpretation, value)
    assert result.heard == transcript.strip()


# ----------------------------------------------------------------------------- LLM normaliser


def _llm_normaliser(tmp_path, func) -> voice.LLMNormaliser:
    return voice.LLMNormaliser(
        load_config("llm"), fake_model(func), LLMLogger(tmp_path / "v"), run_id="v"
    )


def _records(tmp_path) -> list[dict]:
    return [
        json.loads(line) for line in (tmp_path / "v" / "llm_calls.jsonl").read_text().splitlines()
    ]


def test_llm_normaliser_makes_one_logged_native_output_call(tmp_path):
    """Spoken words become the expected form through one logged call (MEM-04)."""
    counting = Counting(voice_like)
    normaliser = _llm_normaliser(tmp_path, counting)
    result = normaliser.normalise(_request(), "make it sixty five degrees")
    assert (result.interpretation, result.value) == ("value", "65")
    assert normaliser.normalise(_request(), "keep the default").interpretation == "default"
    assert normaliser.normalise(APPROVAL, "yes, approve").value == "yes"
    assert normaliser.normalise(DB30, "option b").value == "B"
    assert counting.calls == 4 and normaliser.calls == 4 and normaliser.fallbacks == 0
    records = _records(tmp_path)
    assert len(records) == 4
    assert {r["agent"] for r in records} == {voice.NORMALISER_AGENT}
    assert {r["decision_source"] for r in records} == {"llm"}
    assert records[0]["phase"] == "P1" and records[2]["phase"] == "P10"
    assert records[0]["response"]["value"] == "65" and records[0]["retries"] == 0
    assert records[0]["model"] == "fake-llm" and records[0]["requests"] >= 1


def test_llm_normaliser_retries_invalid_values_and_falls_back_to_the_policy(tmp_path):
    """An invalid value is sent back once (retry); a hopeless model yields the policy answer."""
    replies = deque(
        [
            {"heard": "x", "interpretation": "value", "value": "sixty-five"},  # not digits
            {"heard": "x", "interpretation": "value", "value": "65"},
        ]
    )

    def flaky(messages, info):
        if output_name(info) != "NormalisedAnswer":
            return policy_like(messages, info)
        return ModelResponse(parts=[TextPart(json.dumps(replies.popleft()))])

    normaliser = _llm_normaliser(tmp_path, flaky)
    assert normaliser.normalise(_request(), "sixty-five").value == "65"
    record = _records(tmp_path)[-1]
    assert record["retries"] == 1 and record["decision_source"] == "llm"

    hopeless = _llm_normaliser(tmp_path, always_invalid)
    result = hopeless.normalise(_request(), "70 degrees")
    assert (result.interpretation, result.value) == ("value", "70")  # the policy's answer
    assert hopeless.fallbacks == 1 and hopeless.name == "llm"
    record = _records(tmp_path)[-1]
    assert record["decision_source"] == "policy_fallback" and record["error"]
    assert record["response"] is None


# ----------------------------------------------------------------------------- audio fakes


def test_missing_optional_package_gives_the_install_hint(monkeypatch, tmp_path, capsys):
    """Without faster-whisper/sounddevice `talk` stops before any question with the fix."""
    real_import = voice.importlib.import_module

    def missing(name, *args, **kwargs):
        if name in {"faster_whisper", "sounddevice"}:
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(voice.importlib, "import_module", missing)
    with pytest.raises(voice.VoiceDependencyError) as exc:
        voice.WhisperTranscriber().load()
    message = str(exc.value)
    assert "faster_whisper" in message and voice.VOICE_REQUIREMENTS in message
    assert "0.5 GB" in message and "--typed" in message
    assert voice.import_voice_module("json") is json  # other imports are untouched
    code = main(["talk", "--engine", "policy", "--runs-dir", str(tmp_path), "--mute"])
    assert code == EXIT_USAGE
    assert voice.VOICE_REQUIREMENTS in capsys.readouterr().err
    assert not list(tmp_path.iterdir())  # nothing was started


def _install_fake_audio(monkeypatch, samples, transcript="sixty five"):
    """Fake ``sounddevice`` (one chunk per stream) and ``faster_whisper`` modules."""
    import numpy as np

    opened = []

    class InputStream:
        def __init__(self, *, samplerate, channels, dtype, callback):
            opened.append((samplerate, channels, dtype))
            self.callback = callback

        def __enter__(self):
            self.callback(np.asarray(samples, dtype=np.float32).reshape(-1, 1), len(samples), 0, 0)
            return self

        def __exit__(self, *exc):
            return False

    class WhisperModel:
        built: typing.ClassVar[list] = []

        def __init__(self, size, device, compute_type):
            WhisperModel.built.append((size, device, compute_type))

        def transcribe(self, audio, **kwargs):
            assert kwargs == {"language": "en", "beam_size": 5, "vad_filter": True}
            words = transcript.split()
            segments = [types.SimpleNamespace(text=f" {word}") for word in words]
            return iter(segments), {"audio": audio}

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=InputStream))
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel)
    )
    return opened, WhisperModel


def test_push_to_talk_records_between_two_enters_and_transcribes(monkeypatch):
    """Enter starts the stream, Enter closes it; the clip goes to faster-whisper once loaded."""
    import numpy as np

    opened, WhisperModel = _install_fake_audio(monkeypatch, [0.1, 0.2, 0.3])
    keyboard = ScriptedKeyboard(["", ""])
    out = io.StringIO()
    microphone = voice.Microphone(input_fn=keyboard, out=out)
    clip = microphone.record()
    assert opened == [(voice.SAMPLE_RATE_HZ, 1, "float32")]
    assert clip.dtype == np.float32 and clip.tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert "press Enter to start" in out.getvalue() and "press Enter to stop" in out.getvalue()

    transcriber = voice.WhisperTranscriber()
    assert transcriber.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert WhisperModel.built == []  # nothing loaded for an empty clip
    assert transcriber.transcribe(clip) == "sixty five"
    assert transcriber.transcribe(clip) == "sixty five"
    assert WhisperModel.built == [("small", "cpu", "int8")]  # loaded once

    listen = voice.whisper_listener(input_fn=ScriptedKeyboard(["", ""]), out=io.StringIO())
    assert listen() == "sixty five"
    # EOF before the first Enter: an empty clip, no stream opened
    assert voice.Microphone(input_fn=ScriptedKeyboard([]), out=io.StringIO()).record().size == 0
    assert len(opened) == 2


# ----------------------------------------------------------------------------- the session


def test_request_prompt_is_confirmed_or_recorded_again():
    """(a) The transcript is echoed and confirmed; a refusal records again; give up → None."""
    heard = deque(["", "Heat the campus", "Heat and cool the campus"])
    confirmations = deque([False, True])
    session = _session(lambda: heard.popleft(), lambda q: confirmations.popleft())
    assert session.request_prompt() == "Heat and cool the campus"
    assert session.transcripts == ["Heat the campus", "Heat and cool the campus"]
    assert session.speaker.spoken[0] == voice.REQUEST_PROMPT
    assert voice.NOTHING_HEARD in session.speaker.spoken
    assert 'I heard: "Heat the campus"' in session.out.getvalue()

    giving_up = _session(lambda: "x", lambda q: False)
    assert giving_up.request_prompt() is None
    assert len(giving_up.transcripts) == voice.MAX_ATTEMPTS


def test_ask_speaks_listens_normalises_echoes_and_confirms():
    """(b) unclear → repeat; refused echo → again; confirmed → the value; nothing → None."""
    heard = deque(["warm", "65 degrees", "60"])
    confirmations = deque([False, True])
    session = _session(lambda: heard.popleft(), lambda q: confirmations.popleft())
    assert session.ask(_request()) == "60"
    assert session.answers == [("REQ-01-DB-31", "60")]
    spoken = session.speaker.spoken
    assert spoken[0] == "Value of DB-31? The default is 55.0 degC."
    assert spoken[1].startswith('I heard "warm" but expected a number in degC')
    assert spoken[2] == "Let's try again."
    out = session.out.getvalue()
    assert "[P1] Value of DB-31?" in out and "default: 55.0 degC" in out
    assert "expected: a number in degC" in out

    echoes = []

    def confirm(question):
        echoes.append(question)
        return True

    session = _session(lambda: "the default", confirm)
    assert session.ask(_request()) == "55"
    assert echoes == ["I understood 55 degC - apply?"]
    session = _session(lambda: "yes", confirm)
    assert session.ask(APPROVAL) == "yes"
    assert echoes[-1] == "I understood yes - apply?"

    class VerboseEar:
        """A normaliser whose values pass the validator but are not yet in the runner's form."""

        name = "verbose"
        replies: typing.ClassVar[deque] = deque(["65 degrees", "Yes.", "warm"])

        def normalise(self, request, transcript):
            return voice.NormalisedAnswer(
                heard=transcript, interpretation="value", value=self.replies.popleft()
            )

    session = _session(lambda: "spoken", confirm, normaliser=VerboseEar())
    assert session.ask(_request()) == "65"  # not "65 degrees": the runner would reject it
    assert echoes[-1] == "I understood 65 degC - apply?"
    assert session.ask(APPROVAL) == "yes"
    assert echoes[-1] == "I understood yes - apply?"
    session.max_attempts = 1
    assert session.ask(_request()) is None  # "warm" cannot be canonicalised → re-ask → default
    assert any(s.startswith("I could not use that answer") for s in session.speaker.spoken)

    silent = _session(lambda: "", lambda q: True)
    assert silent.ask(_request()) is None
    assert silent.answers == [("REQ-01-DB-31", None)]
    assert silent.speaker.spoken[-1] == "No answer was confirmed; the default applies."
    assert silent.speaker.spoken.count(voice.NOTHING_HEARD) == voice.MAX_ATTEMPTS


def test_approval_text_reads_the_stored_verifier_summary(tmp_path):
    """(c) P10: the verifier's own summary precedes the yes/no question (nothing recomputed)."""
    session = _session(lambda: "yes", run_dir=None)
    assert session.question_text(APPROVAL) == f"{APPROVAL.question} {voice.APPROVAL_SUFFIX}"
    run_dir = tmp_path / "r"
    (run_dir / "tool_results").mkdir(parents=True)
    (run_dir / "tool_results" / "TR-050_verify_design.json").write_text(
        json.dumps(
            {"data": {"candidate_id": "C2", "verdict": "ACCEPTED", "summary": "ACCEPTED: 46/46."}}
        )
    )
    session.run_dir = run_dir
    text = session.question_text(APPROVAL)
    assert text.startswith("Verifier summary: ACCEPTED: 46/46. Approve the recommended design")
    assert text.endswith(voice.APPROVAL_SUFFIX)
    (run_dir / "tool_results" / "TR-050_verify_design.json").write_text("{not json")
    assert session.question_text(APPROVAL) == f"{APPROVAL.question} {voice.APPROVAL_SUFFIX}"


def test_dialogue_hook_is_the_only_post_run_step():
    """The §I hook (``ui.dialogue``) receives the RunResult once; without a hook nothing happens."""
    seen = []
    session = _session(lambda: "", dialogue_hook=seen.append)
    session.after_run("result")  # type: ignore[arg-type]
    assert seen == ["result"]
    _session(lambda: "").after_run("result")  # type: ignore[arg-type]


def test_confirm_terminal_defaults_to_yes(monkeypatch):
    """Enter, 'y' and anything but n/no confirm; EOF does not."""
    answers = iter(["", "y", "No", "n", "sure"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert [voice.confirm_terminal("q") for _ in range(5)] == [True, True, False, False, True]

    def eof(prompt=""):
        raise EOFError

    assert voice.confirm_terminal("q", eof) is False


# ----------------------------------------------------------------------------- cli talk


def test_talk_drives_a_smoke_budget_policy_run_end_to_end(tmp_path, monkeypatch, capsys):
    """`talk --typed --engine policy --budget smoke`: request, 4 questions, approval, outputs.

    Real tools with the smoke budget; the keyboard is scripted (``--typed`` listener and the
    ``[Y/n]`` confirmations both read ``input``), ``say`` is a recording stub.
    """
    spoken = _fake_say(monkeypatch)
    keyboard = ScriptedKeyboard(
        transcripts=[
            "Design a deep geothermal district heating and cooling system for the campus",
            "use the defaults",  # CFG-INPUT-DEFAULTS → default
            "option A",  # DB-30
            "sixty-five",  # DB-31: the policy normaliser needs digits → unclear, re-asked
            "65 degrees",  # DB-31
            "26",  # DB-32
            "yes, approve",  # P10 approval
            "stop",  # the post-run dialogue (§I): nothing to change
        ],
        confirmations=["", "", "", "", "", "", ""],
    )
    monkeypatch.setattr("builtins.input", keyboard)
    code = main(
        [
            "talk",
            "--engine",
            "policy",
            "--typed",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "talk1",
            "--budget",
            "smoke",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "run talk1: completed (phase P10)" in out
    assert 'I heard "sixty-five" but expected a number in degC' in out
    assert "I understood 65 degC - apply? [Y/n] " in keyboard.prompts
    assert "I understood yes - apply? [Y/n] " in keyboard.prompts
    assert not keyboard.transcripts and not keyboard.confirmations  # every line was consumed
    summary = json.loads((tmp_path / "talk1" / "run_summary.json").read_text())
    assert summary["status"] == "completed" and summary["approval"] == "approved"
    events = [
        json.loads(line) for line in (tmp_path / "talk1" / "events.jsonl").read_text().splitlines()
    ]
    answers = [e["payload"] for e in events if e["type"] == "USER_ANSWER"]
    assert [a["request_id"] for a in answers] == [
        "REQ-01-CFG-INPUT-DEFAULTS",
        "REQ-02-DB-30",
        "REQ-03-DB-31",
        "REQ-04-DB-32",
        "APPROVAL",
    ]
    assert all("(user" in a["message"] for a in answers)  # confirmed by the user, none assumed
    by_id = {a["assumption_id"]: a["answer"] for a in answers if a["assumption_id"]}
    assert by_id["DB-31"] == 65.0 and by_id["DB-32"] == 26.0
    assert answers[-1]["answer"] == "approved"
    assert not any(e["type"] == "INVALID_ANSWER" for e in events)  # confirmed values validate
    assert spoken[0] == voice.REQUEST_PROMPT
    approval = next(s for s in spoken if "Approve the recommended design" in s)
    assert approval.startswith("Verifier summary:") and approval.endswith(voice.APPROVAL_SUFFIX)
    assert any(s.startswith("Run talk1 completed.") for s in spoken)
    assert not (tmp_path / "talk1" / "llm_calls.jsonl").exists()  # policy: no LLM call at all
    # The dialogue loop (§I) followed the approval and was stopped by the user at once.
    assert dialogue.CHANGE_PROMPT in spoken and "Stop the dialogue? [Y/n] " in keyboard.prompts
    assert spoken[-1] == "Dialogue over: 0 what-ifs computed, 0 adopted."
    assert not (tmp_path / "talk1" / "whatif").exists()


@pytest.mark.usefixtures("stub_tools")
def test_talk_with_the_llm_engine_logs_the_normaliser_calls(tmp_path, monkeypatch, capsys):
    """`talk --engine llm`: STT monkeypatched, FunctionModel ear; every call in llm_calls.jsonl."""
    spoken = _fake_say(monkeypatch)
    heard = deque(
        [
            "Design a deep geothermal district heating and cooling system for the campus",
            "use the defaults",
            "interpretation A",
            "sixty five",
            "twenty six",
            "yes",
        ]
    )
    built = []

    def fake_build(llm_config, kind="orchestrator", tag=""):
        built.append((kind, tag))
        return fake_model(voice_like)

    order = []
    monkeypatch.setattr(engine_module, "build_ollama_model", fake_build)
    monkeypatch.setattr(
        engine_module, "close_model_clients", lambda *models: order.append("closed")
    )
    monkeypatch.setattr(voice.VoiceSession, "after_run", lambda self, result: order.append("hook"))
    monkeypatch.setattr(voice, "whisper_listener", lambda model_size: heard.popleft)
    monkeypatch.setattr(voice, "import_voice_module", lambda module: module)
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")  # → yes
    code = main(
        [
            "talk",
            "--engine",
            "llm",
            "--model",
            "ear-test",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "t2",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert built == [("orchestrator", "ear-test")]  # one model, shared by engine and normaliser
    assert order == ["hook", "closed"]  # the §I hook runs while the shared model is still open
    assert "voice: 5 normaliser LLM calls, 0 policy fallbacks (6 transcripts)" in out
    records = [
        json.loads(line) for line in (tmp_path / "t2" / "llm_calls.jsonl").read_text().splitlines()
    ]
    ears = [r for r in records if r["agent"] == voice.NORMALISER_AGENT]
    assert len(ears) == 5 and all(r["decision_source"] == "llm" for r in ears)
    assert [r["response"]["value"] for r in ears] == [None, "A", "65", "26", "yes"]
    assert ears[0]["response"]["interpretation"] == "default"
    assert any(r["agent"] != voice.NORMALISER_AGENT for r in records)  # the runner's own calls
    assert "I understood 65 degC - apply? [Y/n] " in prompts
    assert prompts[-1] == "I understood yes - apply? [Y/n] "
    assert spoken[0] == voice.REQUEST_PROMPT and not heard
    events = (tmp_path / "t2" / "events.jsonl").read_text()
    assert "DB-31 = 65" in events and "approved" in events


def test_talk_without_a_confirmed_request_can_use_the_brief(tmp_path, monkeypatch, capsys):
    """Three refused transcripts → offer the default brief; declining starts nothing."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: None)
    keyboard = ScriptedKeyboard(["a", "b", "c"], ["n", "n", "n", "n"])
    monkeypatch.setattr("builtins.input", keyboard)
    code = main(["talk", "--engine", "policy", "--typed", "--runs-dir", str(tmp_path), "--mute"])
    assert code == EXIT_USAGE
    assert "no request; nothing started" in capsys.readouterr().err
    assert "No request was confirmed. Use the default challenge brief? [Y/n] " in keyboard.prompts
    assert not any(path.is_dir() for path in tmp_path.iterdir())
    # --engine llm: the normaliser's logger (which creates runs/<id>/) is built only after the
    # request is confirmed, so declining still writes nothing and builds no model.
    monkeypatch.setattr(
        engine_module, "build_ollama_model", lambda *a, **k: pytest.fail("no model")
    )
    monkeypatch.setattr("builtins.input", ScriptedKeyboard(["a", "b", "c"], ["n", "n", "n", "n"]))
    args = ["talk", "--engine", "llm", "--typed", "--runs-dir", str(tmp_path), "--mute"]
    assert main(args) == EXIT_USAGE
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_talk_help_lists_the_voice_options(capsys):
    """`talk --help` documents the optional packages, --typed and --mute."""
    with pytest.raises(SystemExit) as exc:
        main(["talk", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "requirements-voice.txt" in out and "--typed" in out and "--mute" in out
    assert "--stt-model" in out and "--speak" not in out


def test_requirements_voice_is_not_installed_by_the_main_ci():
    """The pin file exists with both packages and no workflow installs it."""
    root = Path(__file__).resolve().parents[3]
    pins = (root / voice.VOICE_REQUIREMENTS).read_text()
    assert "faster-whisper==" in pins and "sounddevice==" in pins
    for workflow in (root / ".github" / "workflows").glob("*.y*ml"):
        assert voice.VOICE_REQUIREMENTS not in workflow.read_text(), workflow
    assert voice.VOICE_REQUIREMENTS not in (root / "requirements-hackathon.txt").read_text()
