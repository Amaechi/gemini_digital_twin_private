"""Tests of the voice dialogue loop (``gemini_agent.ui.dialogue``; FINAL_SPRINT §I) — offline.

STT and TTS are fakes (scripted transcripts, a recording ``say`` stub or a muted speaker), the
LLM is a ``FunctionModel`` that answers the Lead Engineer mapping like the keyword policy and the
normaliser like a sensible ear. The scripted three-turn dialogue runs the real what-if tool on a
copy of the committed fixture run; the adoption tests use the fake what-if on a stub run.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from collections import deque
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from gemini_agent import speech
from gemini_agent.agents import engine as engine_module
from gemini_agent.agents.engine import LLMEngine, PolicyEngine, ResilientEngine
from gemini_agent.agents.objections import map_objection_by_keywords
from gemini_agent.cli import EXIT_OK, EXIT_USAGE, main
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner import review as review_mod
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import REVIEW_DR_ID, ReviewQuestion, RunStatus
from gemini_agent.tools import whatif as whatif_tool
from gemini_agent.ui import dialogue, voice
from unit_test.gemini_agent.llm_fakes import (
    always_invalid,
    fake_model,
    output_name,
    policy_like,
    user_prompt,
)
from unit_test.gemini_agent.ui.test_voice import _fake_say, voice_like
from unit_test.gemini_agent.whatif_fakes import fake_whatif


# ----------------------------------------------------------------------------- helpers
def dialogue_like(messages, info) -> ModelResponse:
    """FunctionModel: keyword mapping for ``LeverChoice``, a normaliser ear, else the policy."""
    if output_name(info) == "LeverChoice":
        data = json.loads(user_prompt(messages).split("DATA:\n", 1)[1])
        choice = map_objection_by_keywords(ReviewQuestion.model_validate(data))
        return ModelResponse(parts=[TextPart(choice.model_dump_json())])
    if output_name(info) == "NormalisedAnswer":
        return voice_like(messages, info)
    return policy_like(messages, info)


def _voice(transcripts, confirm=None, normaliser=None):
    """A muted ``VoiceSession`` hearing the scripted transcripts ('' when they run out)."""
    heard = deque(transcripts)
    out = io.StringIO()
    speaker = speech.Speaker(True, out=out)
    speaker.available = False  # every text lands in speaker.spoken; no subprocess
    speaker._noted = True
    session = voice.VoiceSession(
        speaker=speaker,
        listen=lambda: heard.popleft() if heard else "",
        normaliser=normaliser or voice.PolicyNormaliser(),
        confirm=confirm or (lambda question: True),
        out=out,
    )
    return session, heard, out


def _completed_stub_run(tmp_path: Path, run_id: str, **kwargs) -> DesignRunner:
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path, **kwargs)
    result = runner.start("", run_id=run_id, input_mode="assume")
    assert result.status == RunStatus.COMPLETED and result.approval == "pending"
    return runner


def _hashes(run_dir: Path) -> dict[str, str]:
    """sha256 of every file of the run outside ``whatif/`` (the dialogue's own folder)."""
    return {
        str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and whatif_tool.WHATIF_DIR not in path.relative_to(run_dir).parts
    }


def _events(runner: DesignRunner, run_id: str):
    return runner.store(run_id).list_events(run_id)


# ----------------------------------------------------------------------------- words
@pytest.mark.parametrize(
    ("text", "stop"),
    [
        ("stop", True),
        ("That's all, thanks.", True),
        ("I'd like to stop", True),
        ("nothing", True),
        ("no change", True),
        ("done", True),
        ("stop using the robust backup boiler", False),  # five words: a change request
        ("change the return temperature to 65", False),
        ("try 3 doublets", False),
        ("", False),
    ],
)
def test_is_stop(text, stop):
    """Short utterances with a stop word (or a no) end the dialogue; change requests do not."""
    assert dialogue.is_stop(text) is stop


@pytest.mark.parametrize(
    ("text", "decision"),
    [
        ("adopt", "adopt"),
        ("Adopt it.", "adopt"),
        ("let's accept it", "adopt"),
        ("try another", "another"),
        ("another one please", "another"),
        ("stop", "stop"),
        ("I'm done, stop", "stop"),
        ("yes", None),  # ambiguous after "Adopt it, try another, or stop?"
        ("no", None),
        ("adopt it and then stop", None),  # two decisions
        ("", None),
    ],
)
def test_map_decision_keywords(text, decision):
    """The adopt / another / stop vocabulary; yes/no and double answers are asked again."""
    assert dialogue.map_decision(text) == decision


def test_decision_request_is_a_choice_for_both_normalisers():
    """The decision question is a choice with no default: silence never adopts."""
    request = dialogue.decision_request()
    spec = voice.answer_spec(request)
    assert spec == voice.AnswerSpec(kind="choice", options=("adopt", "another", "stop"))
    assert spec.default_text is None
    policy = voice.PolicyNormaliser()
    assert policy.normalise(request, "Adopt.").value == "adopt"
    assert policy.normalise(request, "the default").interpretation == "unclear"
    assert policy.normalise(request, "try another").interpretation == "unclear"  # keywords then
    loop = dialogue.DialogueLoop.__new__(dialogue.DialogueLoop)
    loop.session = _voice([])[0]
    assert loop.understand_decision(request, "try another") == "another"
    assert loop.understand_decision(request, "adopt it") == "adopt"
    assert loop.understand_decision(request, "yes") is None


# ----------------------------------------------------------------------------- speech
@pytest.mark.usefixtures("stub_tools")
def test_spoken_summary_reads_rounded_numbers_with_units(tmp_path, monkeypatch):
    """Three sentences; every number is a rounded table value with its spoken unit."""
    runner = _completed_stub_run(tmp_path, "s")
    result = fake_whatif([], lcoh_after=40.04)(tmp_path / "s", {"DB-31": 65}, "smoke")
    text = dialogue.spoken_summary(result)
    sentences = [s for s in text.split(". ") if s]
    assert len(sentences) == 3, text
    assert text.startswith(
        "What-if 1: changing the heating return temperature from 55 degrees Celsius to "
        "65 degrees Celsius turns the recommended design C1-3d into C1-3d-w1."
    )
    assert "LCOH from 45.0 to 40.0 dollars per megawatt-hour" in text
    assert "LCOC from 26.0 to 26.0 dollars per megawatt-hour" in text
    assert "CAPEX from not computed to not computed million dollars" in text
    assert "Unmet heating days from 0 to 0, unmet cooling days from 0 to 0;" in text
    assert text.endswith(
        "the DesignGuard approved and the verifier says UNVERIFIED for the what-if design."
    )
    flag = fake_whatif([])(tmp_path / "s", {"OPT-ROBUST-BACKUP": "off"}, "smoke")
    # The stub run carries no robust backup: the flag is read off → off (true, if dull).
    assert "changing the robust backup from off to off turns" in dialogue.spoken_summary(flag)
    assert dialogue.spoken_number(12345.6, "co2_t_per_yr") == "12346"
    assert dialogue.spoken_number(0.8949, "probability_demand_met") == "0.89"
    assert dialogue.spoken_number(None, "lcoh_per_mwh") == "not computed"
    assert dialogue.spoken_value(True) == "on" and dialogue.spoken_value(3, "-") == "3"
    assert dialogue.spoken_value(65.0, "degC") == "65 degrees Celsius"
    runner.close()


# ----------------------------------------------------------------------------- mapping
@pytest.mark.usefixtures("stub_tools")
def test_lever_mapper_uses_the_review_schema_and_falls_back_to_keywords(tmp_path):
    """LLM mapping is logged as the Lead Engineer; invalid output → keyword policy fallback."""
    runner = _completed_stub_run(tmp_path, "m")
    state = runner.load_state("m")
    logger = LLMLogger(tmp_path / "m" / "whatif")
    good = ResilientEngine(
        LLMEngine(load_config("llm"), model=fake_model(dialogue_like), logger=logger, run_id="m")
    )
    choice, source = dialogue.LeverMapper(good).map(state, "the boiler is too big", 1)
    assert (choice.lever, choice.value, source) == ("OPT-ROBUST-BACKUP", "off", "llm")
    bad = ResilientEngine(
        LLMEngine(load_config("llm"), model=fake_model(always_invalid), logger=logger, run_id="m")
    )
    choice, source = dialogue.LeverMapper(bad).map(state, "I'd prefer 3 doublets", 2)
    assert (choice.lever, choice.value, source) == ("doublets", "3", "policy_fallback")
    choice, source = dialogue.LeverMapper(PolicyEngine()).map(state, "make it purple", 3)
    assert choice.lever is None and source == "policy"
    records = [json.loads(line) for line in logger.path.read_text().splitlines()]
    assert [r["agent"] for r in records] == ["lead_engineer", "lead_engineer"]
    assert [r["decision_source"] for r in records] == ["llm", "policy_fallback"]
    assert records[0]["phase"] == "P9"
    context = dialogue.dialogue_context(state)
    assert context.budget_left["tokens"] == 0 and context.allowed_tools == []
    runner.close()


# ----------------------------------------------------------------------------- the loop
@pytest.mark.usefixtures("stub_tools")
def test_adopt_reruns_p6_to_p9_by_the_review_path_and_writes_dr12(tmp_path, monkeypatch):
    """'the boiler is too big' → what-if → adopt → P6–P9 rerun, spoken approval, DR-12 adopted."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    session, heard, out = _voice(["the boiler is too big", "adopt", "yes", "stop"])
    runner = _completed_stub_run(tmp_path, "ad", ask_fn=session.ask)
    before = runner.result("ad")
    loop = dialogue.DialogueLoop(
        session,
        runner,
        "ad",
        budget="smoke",
        log_path=tmp_path / "ad" / "whatif" / "dialogue.jsonl",
    )
    summary = loop.run(before)
    assert not heard, "every transcript was consumed"
    assert calls == [{"OPT-ROBUST-BACKUP": False}]
    assert summary.adopted == 1 and summary.whatif_indices == [1] and summary.stopped_by == "user"
    rerun = summary.last_result
    assert rerun.status == RunStatus.COMPLETED and rerun.approval == "approved"
    assert rerun.selected_id == "C1-3d-w1" and rerun.verdict is not None
    assert session.answers == [("APPROVAL", "yes")]  # the new P10 approval was asked by voice
    state = runner.load_state("ad")
    assert state.loop_count == 1 and state.review_pin == "C1-3d-w1" and state.input_mode == "ask"
    assert state.review_levers == {"OPT-ROBUST-BACKUP": False}
    events = _events(runner, "ad")
    types = [e.type for e in events]
    assert types.count("PHASE_START") == 11 + 5  # P0–P10, then P6–P10 again
    start = next(e for e in events if e.type == "REVIEW_START")
    assert start.payload["message"].startswith("voice dialogue: review of C1-3d after the P10")
    assert next(e for e in events if e.type == "LOOP_BACK").payload["reason"] == "review"
    assert (
        next(e for e in events if e.type == "REVIEW_ADOPTED").payload["candidate_id"] == "C1-3d-w1"
    )
    dr12 = [
        e.payload for e in events if e.type == "DECISION_RECORD" and e.payload["dr_id"] == "DR-12"
    ]
    assert [d["choice"] for d in dr12] == ["C1-3d-w1"]
    record = next(r for r in state.decision_records if r.dr_id == REVIEW_DR_ID)
    assert record.title.startswith("User review: the boiler is too big → OPT-ROBUST-BACKUP=off")
    assert record.choice == "C1-3d-w1" and record.decision_source == "user"
    whatif_records = [r for r in state.tool_results if r.tool == review_mod.WHATIF_TOOL]
    assert len(whatif_records) == 1 and whatif_records[0].id in record.evidence_ids
    p6 = [r for r in state.tool_results if r.tool == "optimise_design" and r.loop == 1]
    assert p6 and all(r.args.get("candidate_id") == "C1-3d-w1" for r in p6)
    assert (tmp_path / "ad" / "decision_records" / "DR-12.md").exists()
    summary_json = json.loads((tmp_path / "ad" / "run_summary.json").read_text())
    assert summary_json["approval"] == "approved" and summary_json["loop_count"] == 1
    lines = (tmp_path / "ad" / "whatif" / "dialogue.jsonl").read_text().splitlines()
    assert len(lines) == 1
    round_ = json.loads(lines[0])
    assert round_["decision"] == "adopt" and round_["rerun_status"] == "completed"
    assert round_["lever"] == "OPT-ROBUST-BACKUP" and round_["value"] is False
    spoken = session.speaker.spoken
    assert spoken[0].startswith("We can now explore changes to the recommended design C1-3d")
    assert dialogue.CHANGE_PROMPT in spoken and dialogue.DECISION_PROMPT in spoken
    assert any(
        s.startswith("What-if 1: changing the robust backup from off to off") for s in spoken
    )
    assert any(s.startswith("Adopted. Phases P6 to P9 were rerun on C1-3d-w1") for s in spoken)
    assert dialogue.NEXT_CHANGE_PROMPT in spoken  # the loop went on until "stop"
    assert spoken[-1] == "Dialogue over: 1 what-ifs computed, 1 adopted."
    text = out.getvalue()
    assert 'I heard: "the boiler is too big"' in text and "| LCOH | USD/MWh |" in text
    assert "I understood: change the robust backup (OPT-ROBUST-BACKUP) to off." in text
    runner.close()


@pytest.mark.usefixtures("stub_tools")
def test_unmatched_refused_and_declined_changes_are_spoken_and_asked_again(tmp_path, monkeypatch):
    """No lever / guard refusal / declined mapping: the reason is spoken, no what-if is written."""
    calls: list = []
    monkeypatch.setattr(review_mod, "whatif", fake_whatif(calls))
    declined = []

    def confirm(question):
        if question == "Compute the what-if?" and not declined:
            declined.append(question)
            return False
        return True

    session, heard, _ = _voice(
        ["make it purple", "return temperature 60", "5 doublets", "stop"], confirm
    )
    runner = _completed_stub_run(tmp_path, "u", ask_fn=session.ask)
    loop = dialogue.DialogueLoop(session, runner, "u", budget="smoke")
    summary = loop.run(runner.result("u"))
    assert not heard and summary.whatif_indices == [] and summary.adopted == 0
    assert calls == [{"doublets": 5}]  # parsed, then refused by the DesignGuard (DB-10)
    assert [r.note.split(":")[0] for r in summary.rounds] == [
        "no lever",
        "mapping declined",
        "refused",
    ]
    assert summary.rounds[2].mapping_source == "policy"
    spoken = session.speaker.spoken
    assert any(s.startswith("No design lever matches that.") for s in spoken)
    assert any(s.startswith("That change is refused: ") and "doublets" in s for s in spoken)
    assert "Not computed. Let's try another change." in spoken
    assert not (tmp_path / "u" / whatif_tool.WHATIF_DIR).exists()
    assert runner.load_state("u").loop_count == 0
    runner.close()


@pytest.mark.usefixtures("stub_tools")
def test_round_limit_and_unconfirmed_answers_end_the_dialogue(tmp_path):
    """Five change requests are the limit; three unusable recordings give up."""
    session, _, _ = _voice(["make it purple"] * 6)
    runner = _completed_stub_run(tmp_path, "r")
    summary = dialogue.DialogueLoop(session, runner, "r", budget="smoke").run(runner.result("r"))
    assert len(summary.rounds) == 5 and summary.stopped_by == "round limit"
    assert "That was round 5 of 5; the dialogue ends." in session.speaker.spoken
    silent, _, _ = _voice([])  # nothing heard three times
    summary = dialogue.DialogueLoop(silent, runner, "r", budget="smoke").run(runner.result("r"))
    assert summary.rounds == [] and summary.stopped_by == "no change confirmed"
    assert silent.speaker.spoken.count(voice.NOTHING_HEARD) == 3
    stop, _, _ = _voice(["stop"])
    summary = dialogue.DialogueLoop(stop, runner, "r", budget="smoke").run(runner.result("r"))
    assert summary.stopped_by == "user" and summary.rounds == []
    runner.close()


@pytest.mark.usefixtures("stub_tools")
def test_not_adoptable_lever_is_shown_but_adopt_is_not_offered(tmp_path, monkeypatch):
    """design_flow_m3h: the what-if is computed and spoken; 'adopt' is refused with the reason."""
    monkeypatch.setattr(review_mod, "whatif", fake_whatif([]))
    session, heard, _ = _voice(["design flow 200", "adopt", "stop"])
    runner = _completed_stub_run(tmp_path, "n", ask_fn=session.ask)
    summary = dialogue.DialogueLoop(session, runner, "n", budget="smoke").run(runner.result("n"))
    assert not heard and summary.whatif_indices == [1] and summary.adopted == 0
    assert summary.rounds[0].lever == "design_flow_m3h" and summary.rounds[0].decision == "stop"
    spoken = session.speaker.spoken
    assert any(
        s.startswith("This change cannot be adopted in the run: the design flow") for s in spoken
    )
    assert any(s.startswith("It cannot be adopted: ") for s in spoken)
    assert dialogue.DECISION_PROMPT not in spoken
    assert runner.load_state("n").loop_count == 0 and runner.result("n").approval == "pending"
    runner.close()


@pytest.mark.usefixtures("stub_tools")
def test_submitted_runs_are_never_adopted_into(tmp_path, monkeypatch):
    """runs/final* of the repository: the what-if is shown, 'adopt' is refused with the reason."""
    from gemini_agent.schemas.config import REPO_ROOT

    assert dialogue.submitted_run_reason(REPO_ROOT / "runs" / "final" / "final_llm").startswith(
        "runs/final/final_llm is a submitted run"
    )
    assert "runs/final_policy is a submitted run" in dialogue.submitted_run_reason(
        REPO_ROOT / "runs" / "final_policy"
    )
    assert dialogue.submitted_run_reason(REPO_ROOT / "runs" / "voice" / "demo") == ""
    assert dialogue.submitted_run_reason(tmp_path / "final_policy") == ""  # not in the repo
    monkeypatch.setattr(review_mod, "whatif", fake_whatif([]))
    session, heard, _ = _voice(["the boiler is too big", "adopt", "stop"])
    runner = _completed_stub_run(tmp_path, "sub", ask_fn=session.ask)
    loop = dialogue.DialogueLoop(
        session, runner, "sub", budget="smoke", adopt_block="runs/x is a submitted run"
    )
    summary = loop.run(runner.result("sub"))
    assert not heard and summary.whatif_indices == [1] and summary.adopted == 0
    assert any(
        s == "This change cannot be adopted in the run: runs/x is a submitted run. Try another, "
        "or stop?"
        for s in session.speaker.spoken
    )
    assert runner.load_state("sub").loop_count == 0
    runner.close()


@pytest.mark.usefixtures("stub_tools")
def test_adopt_whatif_preconditions_write_nothing(tmp_path, monkeypatch):
    """Refused: a paused run, another run's what-if, a non-adoptable lever — nothing written."""
    monkeypatch.setattr(review_mod, "whatif", fake_whatif([]))
    runner = _completed_stub_run(tmp_path, "c")
    before = _hashes(tmp_path / "c")
    flow = fake_whatif([])(tmp_path / "c", {"design_flow_m3h": 200}, "smoke")
    with pytest.raises(ValueError, match="cannot be adopted"):
        runner.adopt_whatif("c", flow, objection="flow 200")
    other = DesignRunner(PolicyEngine(), runs_root=tmp_path)
    other.start("", run_id="o", input_mode="pause")
    with pytest.raises(ValueError, match="paused, not completed"):
        other.adopt_whatif("o", flow, objection="x")
    doublets = fake_whatif([])(tmp_path / "c", {"doublets": 2}, "smoke")
    foreign = doublets.model_copy(update={"run_id": "o"})
    with pytest.raises(ValueError, match="belongs to run 'o'"):
        runner.adopt_whatif("c", foreign, objection="x")
    assert _hashes(tmp_path / "c") == before
    other.close()
    runner.close()


# ----------------------------------------------------------------------------- cli talk
def test_scripted_three_turn_dialogue_on_a_finished_run(
    fixture_runs, tmp_path, monkeypatch, capsys
):
    """`talk --run-id final_policy` (completed): return temperature 65 → 3 doublets → stop.

    Real what-if tool (smoke budget) on a copy of the fixture run; STT, TTS and the LLM are
    fakes. Two what-if records are written under ``whatif/``; every original file of the run is
    byte-identical; the dialogue's LLM calls are logged under ``whatif/``.
    """
    runs = tmp_path / "runs"  # this test's own copy: the shared fixture stays clean
    shutil.copytree(fixture_runs / "final_policy", runs / "final_policy")
    run_dir = runs / "final_policy"
    before = _hashes(run_dir)
    assert before and not (run_dir / whatif_tool.WHATIF_DIR).exists()
    spoken = _fake_say(monkeypatch)
    heard = deque(["change the return temperature to 65", "try another", "try 3 doublets", "stop"])
    monkeypatch.setattr(
        engine_module, "build_ollama_model", lambda *a, **k: fake_model(dialogue_like)
    )
    monkeypatch.setattr(voice, "whisper_listener", lambda model_size: heard.popleft)
    monkeypatch.setattr(voice, "import_voice_module", lambda module: module)
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")  # Enter
    code = main(
        [
            "talk",
            "--engine",
            "llm",
            "--model",
            "fake",
            "--runs-dir",
            str(runs),
            "--run-id",
            "final_policy",
            "--budget",
            "smoke",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert not heard, "every transcript was consumed"
    assert out.startswith("run final_policy: completed (phase P10); selected C2-opt-robust")
    # Two what-if records, nothing else of the run changed.
    one = whatif_tool.WhatIfResult.model_validate_json(
        (run_dir / "whatif" / "1" / "whatif.json").read_text()
    )
    two = whatif_tool.WhatIfResult.model_validate_json(
        (run_dir / "whatif" / "2" / "whatif.json").read_text()
    )
    assert one.levers == {"DB-31": 65.0} and two.levers == {"doublets": 3}
    assert one.before.candidate.id == "C2-opt-robust" == two.before.candidate.id
    # Both what-if designs were evaluated (the 3-doublet one gets a re-placed outage schedule).
    assert one.after.status == "ok" and two.after.status == "ok", (one.after, two.after)
    assert two.after.outage_start_indices is None or len(two.after.outage_start_indices) == 3
    assert "re-placed by TL-12" in two.applied[0].mechanism
    assert (run_dir / "whatif" / "1" / "DR-W1.md").exists() and (
        run_dir / "whatif" / "2" / "DR-W2.md"
    ).exists()
    assert _hashes(run_dir) == before
    assert not (run_dir / "llm_calls.jsonl").exists()
    assert not (run_dir / "decision_records" / "DR-12.json").exists()
    rounds = [
        json.loads(line)
        for line in (run_dir / "whatif" / dialogue.DIALOGUE_FILE).read_text().splitlines()
    ]
    assert [(r["round"], r["lever"], r["whatif_index"], r["decision"]) for r in rounds] == [
        (1, "DB-31", 1, "another"),
        (2, "doublets", 2, "stop"),
    ]
    assert all(r["mapping_source"] == "llm" for r in rounds)
    # The dialogue's LLM calls: two Lead Engineer mappings, two normalised decisions, under whatif/.
    records = [
        json.loads(line)
        for line in (run_dir / "whatif" / "llm_calls.jsonl").read_text().splitlines()
    ]
    assert [r["agent"] for r in records] == [
        "lead_engineer",
        voice.NORMALISER_AGENT,
        "lead_engineer",
        voice.NORMALISER_AGENT,
    ]
    assert all(r["decision_source"] == "llm" for r in records)
    assert [r["response"]["value"] for r in records if r["agent"] == voice.NORMALISER_AGENT] == [
        "another",
        "stop",
    ]
    # Spoken: the prompts, two three-sentence summaries with rounded numbers and units, the close.
    assert (
        spoken.count(dialogue.CHANGE_PROMPT) == 1 and spoken.count(dialogue.NEXT_CHANGE_PROMPT) == 1
    )
    assert spoken.count(dialogue.DECISION_PROMPT) == 2
    summaries = [s for s in spoken if s.startswith("What-if ")]
    assert len(summaries) == 2
    assert (
        summaries[0].startswith("What-if 1: changing the heating return temperature from ")
        and "to 65 degrees Celsius turns the recommended design C2-opt-robust into "
        "C2-opt-robust-w1." in summaries[0]
    )
    assert summaries[1].startswith("What-if 2: changing the number of doublets from 2 to 3 turns")
    for summary, result in zip(summaries, (one, two), strict=True):
        assert len([s for s in summary.split(". ") if s]) == 3
        lcoh = next(r for r in result.table if r.metric == "lcoh_per_mwh")
        assert (
            f"LCOH from {lcoh.before:.1f} to {lcoh.after:.1f} dollars per megawatt-hour" in summary
        )
        verdict = result.after.verdict.value if result.after.verdict else "not run"
        assert summary.endswith(f"the verifier says {verdict} for the what-if design.")
        assert "P(demand met) 0.90 before, not recomputed with the smoke budget" in summary
    assert spoken[-1] == "Dialogue over: 2 what-ifs computed, 0 adopted."
    # Every understood utterance was echoed and confirmed on the keyboard before acting.
    assert (
        prompts.count("Use it? [Y/n] ") == 2 and prompts.count("Compute the what-if? [Y/n] ") == 2
    )
    assert prompts.count("I understood another - apply? [Y/n] ") == 1
    assert prompts.count("I understood stop - apply? [Y/n] ") == 1
    assert 'I heard: "change the return temperature to 65"' in out
    assert (
        "I understood: change the heating return temperature (DB-31) to 65 degrees Celsius." in out
    )
    assert "I understood: change the number of doublets (doublets) to 3." in out
    assert "voice: 2 normaliser LLM calls, 0 policy fallbacks (4 transcripts)" in out


@pytest.mark.usefixtures("stub_tools")
def test_talk_refuses_a_run_that_is_not_completed(tmp_path, capsys):
    """`talk --run-id` on a paused run: exit 2 with the state and the `resume` hint."""
    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path)
    runner.start("", run_id="p", input_mode="pause")
    runner.close()
    code = main(
        ["talk", "--engine", "policy", "--typed", "--runs-dir", str(tmp_path), "--run-id", "p"]
    )
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "run 'p' is paused" in err and "resume" in err


@pytest.mark.usefixtures("stub_tools")
def test_talk_no_dialogue_ends_after_the_approval(tmp_path, monkeypatch, capsys):
    """`talk --no-dialogue --typed`: the run completes and no change is asked."""
    monkeypatch.setattr(speech.shutil, "which", lambda command: None)
    lines = deque(["design the geothermal plant", "defaults", "A", "55", "25", "yes"])
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": lines.popleft() if "[typed]" in prompt else ""
    )
    args = ["talk", "--engine", "policy", "--typed", "--mute", "--runs-dir", str(tmp_path)]
    assert main([*args, "--run-id", "nd", "--no-dialogue"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "run nd: completed" in out and dialogue.CHANGE_PROMPT not in out
    assert not (tmp_path / "nd" / whatif_tool.WHATIF_DIR).exists()


def test_talk_help_lists_the_dialogue_options(capsys):
    """`talk --help` documents --no-dialogue and the completed-run form of --run-id."""
    with pytest.raises(SystemExit) as exc:
        main(["talk", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--no-dialogue" in out and "completed run" in out and "DR-12" in out
