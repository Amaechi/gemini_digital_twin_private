"""Unit tests for gemini_agent.agents.model_builder (spec §5.1, §7.1, §12 S7) — offline.

The coder is a Pydantic AI ``FunctionModel`` that returns a scripted FreeCooling model built from
RC-07 (``s7_scripted_model.py``); the real-LLM demo runs locally. All files go to ``tmp_path``
under a NEW folder name; registrations go to registry copies (or a monkeypatched live registry).
"""

import asyncio
import json
import time

import pytest
import yaml

from gemini_agent.agents import engine as engine_module
from gemini_agent.agents import model_builder as mb
from gemini_agent.agents.model_builder import (
    CoderError,
    LLMCoder,
    ModelBuilder,
    ModelBuildRequest,
    ModelDraft,
    check_draft,
    class_name_for,
    run_s7_demo,
)
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.runner.phases import PHASE_SPECS
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.decision import Phase
from gemini_agent.tools.model_builder_tools import ModelSandbox, model_gap_analysis
from gemini_model.free_cooling.free_cooling import FreeCooling
from gemini_simulator.registry import COMPONENT_REGISTRY, create_component
from unit_test.gemini_agent.agents.s7_scripted_model import (
    ScriptedCoder,
    build_request,
    scripted_draft,
    scripted_model,
)
from unit_test.gemini_agent.llm_fakes import always_invalid, fake_model

pytestmark = pytest.mark.usefixtures("stub_tools")

NAME = "free_cooling_rc07"


def _coder(tmp_path, scripted=None, **kwargs) -> LLMCoder:
    model = scripted_model(scripted) if isinstance(scripted, ScriptedCoder) else scripted
    return LLMCoder(
        llm_config=kwargs.pop("llm_config", None) or load_config("llm"),
        logger=LLMLogger(tmp_path / "logs"),
        model=model,
        run_id="s7",
        **kwargs,
    )


def _gap(registry):
    (gap,) = model_gap_analysis(registry=registry).data.missing
    return gap


def _request(**overrides) -> ModelBuildRequest:
    values = {
        "component_type": "free_cooling",
        "model_name": NAME,
        "class_name": "FreeCoolingRc07",
        "reference_card": "RC-07",
        "card_text": "## Free cooling (water-side economiser)\n- T_wb + 6.5 < 26",
        "attempt": 1,
        "max_attempts": 3,
        "files": {},
        "allowed_model_imports": [],
        "allowed_test_imports": [],
    }
    return ModelBuildRequest(**{**values, **overrides})


# --------------------------------------------------------------------------- S7
def test_s7_cooling_model_removed_is_rebuilt_and_registered(tmp_path):
    """S7: gap detected → coder writes the RC-07 model → reference tests pass → registered."""
    scripted = ScriptedCoder()
    demo = run_s7_demo(tmp_path / "sandbox", _coder(tmp_path, scripted))
    report = demo.report
    assert report.gap_detected and report.gap_closed
    assert report.registered_class == "FreeCoolingRc07"
    (result,) = report.run.results
    assert result.status == "registered" and len(result.attempts) == 1
    assert result.model_name == NAME and result.registration.component_type == "free_cooling"
    assert result.attempts[0].pytest.passed and result.attempts[0].pytest.tests == 7
    assert report.run.after.missing_component_types == [] and report.run.closed == ["free_cooling"]
    assert len(result.summary) <= 300
    # the coder saw the card and wrote REFERENCE.md from it (REF-01)
    assert "Free cooling (water-side economiser)" in scripted.requests[0]["card_text"]
    sandbox = tmp_path / "sandbox"
    reference = (sandbox / "src" / "gemini_model" / NAME / "REFERENCE.md").read_text()
    assert reference.startswith("# RC-07") and "13.5" in reference
    # registered in the COPY only; the live registry and the repository are untouched
    assert demo.registry["free_cooling"].__name__ == "FreeCoolingRc07"
    assert COMPONENT_REGISTRY["free_cooling"] is FreeCooling
    assert not (REPO_ROOT / "src" / "gemini_model" / NAME).exists()


def test_s7_rebuilt_model_matches_the_wp02_reference_model(tmp_path):
    """The model rebuilt from RC-07 reproduces the WP-02 FreeCooling outputs."""
    demo = run_s7_demo(tmp_path / "sandbox", _coder(tmp_path, ScriptedCoder()))
    rebuilt_cls = demo.registry["free_cooling"]
    for t_wb in (-2.0, 5.0, 13.5, 13.6, 15.4, 18.0, 19.4, 19.5, 25.0):
        for load in (0.0, 1.0e6, 4.0e6):
            inputs = {"ambient_wet_bulb_degc": t_wb, "cooling_load_w": load}
            rebuilt, reference = rebuilt_cls(), FreeCooling()
            rebuilt.calculate_output(inputs)
            reference.calculate_output(inputs)
            new, old = rebuilt.get_output(), reference.get_output()
            assert new["mode"] == old["mode"]
            for key in ("free_cooling_w", "residual_cooling_w", "power_el", "power_th"):
                assert new[key] == pytest.approx(old[key], abs=1e-6), (t_wb, load, key)


def test_s7_llm_calls_are_logged(tmp_path):
    """MEM-04: every coder call is logged with tokens and the decision source."""
    run_s7_demo(tmp_path / "sandbox", _coder(tmp_path, ScriptedCoder()))
    records = LLMLogger(tmp_path / "logs", create=False).records()
    assert len(records) == 1
    record = records[0]
    assert record.agent == "model_builder" and record.phase == "P4"
    assert record.decision_source == "llm" and record.model == "scripted-coder"
    assert record.input_tokens > 0 and record.output_tokens > 0


def test_failed_tests_are_fed_back_and_the_fix_is_registered(tmp_path):
    """Draft 1 fails its RC-07 threshold tests; the failure goes back; draft 2 is registered."""
    scripted = ScriptedCoder(broken_drafts=1)
    demo = run_s7_demo(tmp_path / "sandbox", _coder(tmp_path, scripted))
    (result,) = demo.report.run.results
    assert result.status == "registered" and len(result.attempts) == 2
    first, second = result.attempts
    assert not first.pytest.passed and first.pytest.failures == 3
    assert second.pytest.passed
    assert scripted.requests[0]["feedback"] == ""
    assert "13.6-partial" in scripted.requests[1]["feedback"]
    assert scripted.requests[1]["attempt"] == 2


def test_never_passing_model_is_not_registered(tmp_path):
    """Tests never pass → no registration; the gap stays open."""
    scripted = ScriptedCoder(broken_drafts=99)
    demo = run_s7_demo(tmp_path / "sandbox", _coder(tmp_path, scripted), max_attempts=2)
    (result,) = demo.report.run.results
    assert result.status == "tests_failed" and result.registration is None
    assert len(result.attempts) == 2 and scripted.calls == 2
    assert not demo.report.gap_closed and demo.report.run.still_missing == ["free_cooling"]
    assert "free_cooling" not in demo.registry and demo.report.registered_class is None


def test_invalid_coder_output_ends_the_build(tmp_path):
    """Output that never validates: ≤ 2 retries, then coder_failed (logged as failed)."""
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}
    coder = _coder(tmp_path, fake_model(always_invalid))
    builder = ModelBuilder(ModelSandbox(tmp_path / "sandbox"), coder, registry=registry)
    result = builder.build(_gap(registry))
    assert result.status == "coder_failed" and result.attempts[0].decision_source == "failed"
    assert "free_cooling" not in registry
    (record,) = LLMLogger(tmp_path / "logs", create=False).records()
    assert record.decision_source == "failed" and record.error


def _builder_config(**limits):
    """llm.yaml with overridden ``calls.model_builder`` limits."""
    config = load_config("llm")
    builder = config.calls.model_builder.model_copy(update=limits)
    return config.model_copy(
        update={"calls": config.calls.model_copy(update={"model_builder": builder})}
    )


class HangingCoder(ScriptedCoder):
    """Scripted coder whose first ``hangs`` calls never answer (the local S7 timeout)."""

    def __init__(self, hangs: int = 99) -> None:
        """Hang for the first ``hangs`` requests, then answer the scripted draft."""
        super().__init__()
        self.hangs = hangs
        self.settings: list[dict] = []

    async def __call__(self, messages, info):  # type: ignore[override]
        """Record the model settings; sleep past the deadline or answer."""
        self.settings.append(dict(info.model_settings or {}))
        if len(self.settings) <= self.hangs:
            self.requests.append(build_request(messages))
            await asyncio.sleep(5)
        return super().__call__(messages, info)


def test_coder_settings_come_from_llm_yaml_calls_model_builder(tmp_path):
    """WP-12 follow-up: the coder uses the engine settings path with calls.model_builder."""
    scripted = HangingCoder(hangs=0)
    config = _builder_config(request_timeout_s=777.0, max_output_tokens=4096, thinking=True)
    coder = _coder(tmp_path, scripted_model(scripted), llm_config=config)
    coder(_request())
    (seen,) = scripted.settings
    assert seen["request_timeout"] == seen["timeout"] == 777.0
    assert seen["max_tokens"] == 4096 and "openai_reasoning_effort" not in seen  # thinking on
    assert seen["temperature"] == config.temperatures.extraction and seen["seed"] == config.seed
    assert seen["extra_body"]["options"]["num_ctx"] == config.ollama.num_ctx
    assert seen == engine_module.model_settings(
        config, config.calls.model_builder, temperature=config.temperatures.extraction
    )
    # the yaml defaults (1200 s, 8192 tokens, thinking off) differ from the Decision limits
    default = _coder(tmp_path, scripted_model(scripted)).model_settings()
    assert default["request_timeout"] == 1200.0 and default["max_tokens"] == 8192
    assert default["openai_reasoning_effort"] == "none"
    assert (
        default["request_timeout"]
        != engine_module.model_settings(load_config("llm"))["request_timeout"]
    )


def test_timed_out_drafts_are_retried_then_coder_failed(tmp_path):
    """A draft past request_timeout_s is one failed attempt; the loop retries up to max."""
    scripted = HangingCoder()
    coder = _coder(
        tmp_path, scripted_model(scripted), llm_config=_builder_config(request_timeout_s=0.05)
    )
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}
    builder = ModelBuilder(
        ModelSandbox(tmp_path / "sandbox"), coder, registry=registry, max_attempts=3
    )
    start = time.perf_counter()
    result = builder.build(_gap(registry))
    assert time.perf_counter() - start < 4.0
    assert result.status == "coder_failed" and len(result.attempts) == 3
    assert len(scripted.settings) == 3
    for number, attempt in enumerate(result.attempts, start=1):
        assert attempt.attempt == number and attempt.decision_source == "failed"
        assert attempt.timed_out and "ModelRequestTimeout" in (attempt.error or "")
        assert "request_timeout_s = 0.05" in attempt.error and attempt.elapsed_s > 0
    assert "not registered after 3 draft(s)" in result.summary
    assert result.input_tokens == sum(a.input_tokens for a in result.attempts)
    assert "free_cooling" not in registry and not (tmp_path / "sandbox" / "src").exists()
    records = LLMLogger(tmp_path / "logs", create=False).records()
    assert len(records) == 3 and all(r.decision_source == "failed" for r in records)
    # the retry prompt tells the coder why the previous draft failed
    assert "timed out" in scripted.requests[1]["feedback"]
    assert scripted.requests[0]["feedback"] == ""


def test_timed_out_draft_then_valid_draft_is_registered(tmp_path):
    """First draft times out, the second passes: registered with two recorded attempts."""
    scripted = HangingCoder(hangs=1)
    coder = _coder(
        tmp_path, scripted_model(scripted), llm_config=_builder_config(request_timeout_s=0.05)
    )
    demo = run_s7_demo(tmp_path / "sandbox", coder, max_attempts=2)
    (result,) = demo.report.run.results
    assert result.status == "registered" and demo.report.gap_closed
    assert [a.timed_out for a in result.attempts] == [True, False]
    assert [a.decision_source for a in result.attempts] == ["failed", "llm"]
    assert result.attempts[1].pytest is not None and result.attempts[1].pytest.passed
    assert result.output_tokens == sum(a.output_tokens for a in result.attempts) > 0


def test_timed_out_draft_then_failing_draft_is_tests_failed(tmp_path):
    """A timeout followed by a draft whose tests fail is reported as tests_failed."""
    scripted = HangingCoder(hangs=1)
    scripted.broken_drafts = 99
    coder = _coder(
        tmp_path, scripted_model(scripted), llm_config=_builder_config(request_timeout_s=0.05)
    )
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}
    builder = ModelBuilder(
        ModelSandbox(tmp_path / "sandbox"), coder, registry=registry, max_attempts=2
    )
    result = builder.build(_gap(registry))
    assert result.status == "tests_failed" and len(result.attempts) == 2
    assert [a.timed_out for a in result.attempts] == [True, False]
    assert result.attempts[1].pytest is not None and not result.attempts[1].pytest.passed


def test_non_timeout_coder_error_still_ends_the_build(tmp_path):
    """Only timeouts are retried: a budget error stops after one recorded attempt."""
    coder = _coder(tmp_path, ScriptedCoder(), token_budget=10, tokens_used=10)
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}
    builder = ModelBuilder(ModelSandbox(tmp_path / "sandbox"), coder, registry=registry)
    result = builder.build(_gap(registry))
    assert result.status == "coder_failed" and len(result.attempts) == 1
    assert not result.attempts[0].timed_out and "budget" in result.attempts[0].error


def test_main_reports_elapsed_time_and_timeout_on_failure(tmp_path, monkeypatch, capsys):
    """The demo CLI prints the elapsed time, the coder limits and each attempt on failure."""
    scripted = HangingCoder()
    monkeypatch.setattr(
        engine_module, "build_ollama_model", lambda cfg, kind, tag="": scripted_model(scripted)
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "llm.yaml").write_text(
        yaml.safe_dump(_builder_config(request_timeout_s=0.05).model_dump(mode="json")),
        encoding="utf-8",
    )
    sandbox = tmp_path / "s7"
    code = mb.main(
        ["--sandbox", str(sandbox), "--config-dir", str(config_dir), "--max-attempts", "2"]
    )
    out = capsys.readouterr().out
    assert code == 1 and len(scripted.settings) == 2
    assert "FAILED after" in out and "request_timeout_s = 0.05" in out
    assert "max_output_tokens = 8192" in out and "scripted-coder" in out
    assert "attempt 1: timed out after" in out and "attempt 2: timed out after" in out
    assert "Raise calls.model_builder.request_timeout_s" in out
    report = json.loads((sandbox / "s7_report.json").read_text())
    (result,) = report["run"]["results"]
    assert result["status"] == "coder_failed" and len(result["attempts"]) == 2
    assert all(a["timed_out"] for a in result["attempts"])


def test_token_budget_is_enforced(tmp_path):
    """No tokens left → CoderError before any call."""
    coder = _coder(tmp_path, ScriptedCoder(), token_budget=10, tokens_used=10)
    with pytest.raises(CoderError, match="budget"):
        coder(_request())


def test_policy_engine_has_no_coder_and_writes_nothing(tmp_path):
    """--engine policy: the gap is reported, nothing is written or registered."""
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}
    builder = ModelBuilder(ModelSandbox(tmp_path / "sandbox"), None, registry=registry)
    result = builder.build(_gap(registry))
    assert result.status == "no_coder" and result.attempts == []
    assert not any(p.is_file() for p in (tmp_path / "sandbox").rglob("*"))


def test_gap_without_card_is_not_built(tmp_path):
    """No reference card → not_buildable (the coder is never called)."""
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "mixer"}
    scripted = ScriptedCoder()
    builder = ModelBuilder(ModelSandbox(tmp_path / "sb"), _coder(tmp_path, scripted), registry)
    result = builder.build(_gap(registry))
    assert result.status == "not_buildable" and scripted.calls == 0


def test_runtime_registration_makes_the_type_usable_by_gemini(tmp_path, monkeypatch):
    """Registered at runtime in the live registry → create_component builds it (undone after)."""
    monkeypatch.delitem(COMPONENT_REGISTRY, "free_cooling")
    builder = ModelBuilder(ModelSandbox(tmp_path / "sandbox"), _coder(tmp_path, ScriptedCoder()))
    result = builder.build(_gap(None))
    assert result.status == "registered"
    unit = create_component("free_cooling")
    assert type(unit).__name__ == "FreeCoolingRc07"
    unit.calculate_output({"ambient_wet_bulb_degc": 10.0, "cooling_load_w": 2.0e6})
    assert unit.get_output()["mode"] == "full"


def test_live_registry_restored_after_runtime_registration():
    """The previous test's monkeypatch restored the WP-02 model."""
    assert COMPONENT_REGISTRY["free_cooling"] is FreeCooling
    assert all(not name.endswith("_rc07") for name in COMPONENT_REGISTRY)


# --------------------------------------------------------------------------- draft checks
def test_check_draft_accepts_the_scripted_draft():
    """The scripted RC-07 draft satisfies every check."""
    request = _request()
    draft = ModelDraft(**scripted_draft(request.model_dump()))
    assert check_draft(request, draft) is None


@pytest.mark.parametrize(
    "change, message",
    [
        ({"class_name": "Other"}, "class_name must be"),
        ({"model_code": "import os\n"}, "code refused"),
        ({"model_code": "x = 1\n"}, "must define class"),
        ({"test_code": "import pytest\n"}, "no test_ function"),
        ({"test_code": "def test_x():\n    assert True\n"}, "must import the model"),
        ({"reference_md": "# copied card\n"}, "RC-07"),
    ],
)
def test_check_draft_refuses_bad_drafts(change, message):
    """Each check gives a message the coder can act on."""
    request = _request()
    draft = ModelDraft(**{**scripted_draft(request.model_dump()), **change})
    assert message in check_draft(request, draft)


def test_check_draft_requires_power_and_emission_outputs():
    """power_th, power_el and emission are mandatory outputs (spec §7.2)."""
    request = _request()
    values = scripted_draft(request.model_dump())
    values["model_code"] = values["model_code"].replace('"emission": 0.0,', "")
    assert "emission" in check_draft(request, ModelDraft(**values))


def test_class_name_for():
    """Folder name → CamelCase class name."""
    assert class_name_for(NAME) == "FreeCoolingRc07"


# --------------------------------------------------------------------------- runner and CLI
def test_p4_policy_run_records_the_real_gap_analysis(tmp_path):
    """P4 requires model_gap_analysis; the real (non-stub) result is recorded as evidence."""
    assert PHASE_SPECS[Phase.P4].required_tools == ("model_gap_analysis",)
    runner = DesignRunner(engine_module.PolicyEngine(), runs_root=tmp_path)
    runner.start("", run_id="r1")
    state = runner.load_state("r1")
    (record,) = [r for r in state.tool_results if r.tool == "model_gap_analysis"]
    assert record.phase == Phase.P4 and record.ok and not record.stub
    assert "missing: none" in record.summary
    data = json.loads((tmp_path / "r1" / record.path).read_text())["data"]
    assert len(data["limitations"]) == 15 and data["missing_component_types"] == []
    assert not (tmp_path / "r1" / "model_builder").exists()  # policy builds nothing


def test_main_runs_the_s7_demo_with_the_coder_model(tmp_path, monkeypatch):
    """The local demo entry point (Ollama replaced by the scripted FunctionModel)."""
    scripted = ScriptedCoder()
    monkeypatch.setattr(
        engine_module, "build_ollama_model", lambda cfg, kind, tag="": scripted_model(scripted)
    )
    sandbox = tmp_path / "s7"
    assert mb.main(["--sandbox", str(sandbox)]) == 0
    report = json.loads((sandbox / "s7_report.json").read_text())
    assert report["gap_closed"] and report["registered_class"] == "FreeCoolingRc07"
    assert (sandbox / "llm_calls.jsonl").is_file() and scripted.calls == 1
    assert COMPONENT_REGISTRY["free_cooling"] is FreeCooling


def test_main_refuses_a_sandbox_in_the_repository_before_creating_it():
    """--sandbox inside src/ is refused before any folder or log is created."""
    target = REPO_ROOT / "src" / "zz_model_builder_probe"
    with pytest.raises(mb.ModelBuilderError, match="outside the repository"):
        mb.main(["--sandbox", str(target)])
    assert not target.exists()
