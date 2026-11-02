"""Unit tests for gemini_agent.eval.submission (spec §18; WP-13 follow-ups) — offline only.

A run on today's stub tools must fail (it is not ACCEPTED and rests on stubs); a fixture that
upgrades such a run to real-looking evidence must pass. Each WP-13 follow-up fails on its own.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.eval.submission import (
    COMPARED_CONCEPTS,
    COOLING_MODELS,
    SCENARIO_IDS,
    CheckStatus,
    SubmissionContext,
    check_brine_pvt,
    check_cooling_models,
    check_gemini_notices,
    check_plant_debugger_notices,
    find_final_run,
    render_markdown,
    validate_submission,
)
from gemini_agent.logging.llm_logger import LLMCallRecord, LLMLogger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner.decision_records import write_records
from gemini_agent.runner.design_runner import STORE_FILE, DesignRunner
from gemini_agent.runner.state import RunState, ToolRecord, Verification
from gemini_agent.schemas.config import REPO_ROOT
from gemini_agent.schemas.decision import DesignVerdict, Phase
from gemini_agent.schemas.results import KPISet
from gemini_agent.verify.design_verifier import VERIFIER_NAME

pytestmark = pytest.mark.usefixtures("stub_tools")

FOLLOW_UPS = ("SUB-03", "SUB-04", "SUB-05", "SUB-13")
"""PROGRESS 'Open follow-ups → WP-13': verdict, stub calls, stub evidence, cost placeholders."""


# --------------------------------------------------------------------------- fixtures
def _stub_run(root: Path, run_id: str = "final") -> Path:
    """Run the policy engine on today's stub tools."""
    runner = DesignRunner(PolicyEngine(), runs_root=root)
    try:
        runner.start("", run_id=run_id)
    finally:
        runner.close()
    return root / run_id


def _load(run_dir: Path) -> tuple[SQLiteEventStore, RunState]:
    store = SQLiteEventStore(run_dir / STORE_FILE)
    summary = json.loads((run_dir / "run_summary.json").read_text())
    return store, RunState.model_validate(store.get_run(summary["run_id"]).state)


def _save(run_dir: Path, store: SQLiteEventStore, state: RunState) -> None:
    store.update_run(state.run_id, state.status.value, state.model_dump(mode="json"))
    store.close()
    (run_dir / "run_summary.json").write_text(json.dumps(state.summary_dict(), indent=2))
    write_records(state.decision_records, run_dir / "decision_records")


def _tool_output(run_dir: Path, tr_id: str, tool: str, data: dict) -> str:
    path = run_dir / "tool_results" / f"{tr_id}_{tool}.json"
    path.write_text(json.dumps({"data": data}))
    return str(path.relative_to(run_dir))


def _upgrade_to_passing(run_dir: Path) -> None:
    """Replace stub evidence by real-looking evidence (test data, not engineering values)."""
    store, state = _load(run_dir)
    selected = state.selected_id
    state.tool_results = [r.model_copy(update={"stub": False}) for r in state.tool_results]
    extra = {
        "TR-901": ("verify_design", {"verdict": "ACCEPTED", "violations": []}),
        "TR-902": ("cost_sensitivity", {"tornado": []}),
        "TR-903": (
            "economics",
            {"cost_items": [{"name": "a", "verify": False}]},
        ),
    }
    for tr_id, (tool, data) in extra.items():
        state.tool_results.append(
            ToolRecord(
                id=tr_id,
                tool=tool,
                phase=Phase.P7,
                args={},
                ok=True,
                source="policy",
                candidate_ids=[selected],
                path=_tool_output(run_dir, tr_id, tool, data),
            )
        )
    state.verifications.append(
        Verification(
            candidate_id=selected,
            verdict=DesignVerdict.ACCEPTED,
            verifier=VERIFIER_NAME,
            tool_result_id="TR-901",
            phase=Phase.P7,
        )
    )
    for candidate in state.candidates:
        state.kpis[candidate.id] = KPISet(
            candidate_id=candidate.id,
            unmet_heating_days=0,
            unmet_cooling_days=0,
            lcoh_per_mwh=1.0,
            lcoc_per_mwh=1.0,
            co2_reduction_vs_c0_frac=0.5,
            exergy_efficiency_frac=0.5,
            percentiles={"lcoh_per_mwh": {"p10": 0.9, "p50": 1.0, "p90": 1.1}},
        )
    state.decision_records = [
        r.model_copy(update={"stub_evidence": False}) for r in state.decision_records
    ]
    state.approval = "approved"
    _save(run_dir, store, state)
    logger = LLMLogger(run_dir)
    logger.log(
        LLMCallRecord(
            run_id=state.run_id, agent="lead_engineer", phase="P0", model="m", decision_source="llm"
        )
    )
    logger.write_token_summary()


def _eval_root(root: Path) -> Path:
    """Write an engine-llm eval with S1–S11 transcripts, S10 tokens and two compared models."""
    runs = []
    for sid in SCENARIO_IDS:
        folder = root / "qwen" / sid
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text("{}\n")
        (folder / "llm_calls.jsonl").write_text("{}\n")
        runs.append(
            {
                "scenario": sid,
                "outcome": "pass",
                "status": "completed",
                "run_dirs": [sid],
                "token_comparison": {"total": {"naive_tokens": 2}} if sid == "S10" else None,
            }
        )
    results = {"engine": "llm", "model": "qwen", "runs": runs}
    (root / "qwen" / "eval_results.json").write_text(json.dumps(results))
    models = [{"model": m, "engine": "llm"} for m in ("qwen", "gemma")]
    (root / "model_comparison.json").write_text(json.dumps({"models": models}))
    return root


def _repo_root(root: Path) -> Path:
    """Build a minimal repository with the files the repository checks need (no git)."""
    (root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# GeoAgent\nAPI keys: placeholders only.\n")
    (root / "LICENSE").write_text("Apache-2.0\n")
    (root / "NOTICE").write_text("Plant Debugger (marcosanyo/PlantDebugger)\n")
    (root / "third_party" / "plantdebugger").mkdir(parents=True)
    (root / "third_party" / "plantdebugger" / "LICENSE").write_text("Apache-2.0\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "run_eval.py").write_text(
        "# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger\n"
        "# contributors, file scripts/run_eval.py @ d69507a. Modified by team, 2026-11-01: x.\n"
    )
    docs = root / "src" / "gemini_documentation" / "source"
    docs.mkdir(parents=True)
    (docs / "model_cooling.rst").write_text(" ".join(f"``{m}``" for m in COOLING_MODELS))
    for name in COOLING_MODELS:
        (root / "src" / "gemini_model" / name).mkdir(parents=True)
        (root / "src" / "gemini_model" / name / "REFERENCE.md").write_text("card\n")
        (root / "unit_test" / "gemini_model" / name).mkdir(parents=True)
        (root / "unit_test" / "gemini_model" / name / f"test_{name}.py").write_text("")
    return root


@pytest.fixture(scope="module")
def stub_run(tmp_path_factory, stub_tools) -> Path:
    """Return today's real run: policy engine on stub tools."""
    return _stub_run(tmp_path_factory.mktemp("runs"))


@pytest.fixture
def passing(tmp_path, stub_run) -> dict:
    """Return the arguments of a submission that passes every automatic check."""
    run_dir = tmp_path / "final"
    shutil.copytree(stub_run, run_dir)
    _upgrade_to_passing(run_dir)
    return {
        "run_dir": tmp_path,  # discovered: the only sub-directory with a run_summary.json
        "eval_root": _eval_root(tmp_path / "eval"),
        "repo_root": _repo_root(tmp_path / "repo"),
    }


def _statuses(report) -> dict[str, CheckStatus]:
    return {check.id: check.status for check in report.checks}


# --------------------------------------------------------------------------- tests
def test_todays_stub_run_fails_the_submission(stub_run, tmp_path):
    """Until WP-14 binds real tools, the real check fails: not ACCEPTED and built on stubs."""
    report = validate_submission(run_dir=stub_run, eval_root=tmp_path, repo_root=REPO_ROOT)
    assert not report.ok
    statuses = _statuses(report)
    for cid in ("SUB-03", "SUB-04", "SUB-05", "SUB-08", "SUB-12", "SUB-17"):
        assert statuses[cid] == CheckStatus.FAIL, cid
    assert statuses["SUB-13"] == CheckStatus.PASS  # no unset cost placeholder today
    assert statuses["SUB-14"] == CheckStatus.WARN  # RC-05 VERIFY items listed from config
    detail = {c.id: c.detail for c in report.checks}
    assert "verdict UNVERIFIED" in detail["SUB-03"]
    assert "chiller" in detail["SUB-14"]
    markdown = render_markdown(report)
    assert "NOT READY" in markdown and "| SUB-03 |" in markdown


def test_passing_fixture_passes_every_automatic_check(passing):
    """An ACCEPTED, stub-free run with complete evidence is READY."""
    report = validate_submission(**passing)
    statuses = _statuses(report)
    assert report.ok, [c for c in report.checks if c.status == CheckStatus.FAIL]
    assert all(statuses[cid] == CheckStatus.PASS for cid in FOLLOW_UPS)
    assert statuses["SUB-14"] == CheckStatus.PASS  # economics used no VERIFY item
    assert statuses["SUB-21"] == CheckStatus.MANUAL  # no git history in the fixture repo
    assert {statuses["SUB-23"], statuses["SUB-24"]} == {CheckStatus.MANUAL}
    assert "**READY**" in render_markdown(report)


def test_verify_cost_items_are_flagged_not_failed(passing):
    """RC-05: VERIFY-tagged cost items used by the economics output are a warning."""
    run_dir = Path(passing["run_dir"]) / "final"
    items = [{"name": "a", "verify": False}, {"name": "b", "verify": True}]
    _tool_output(run_dir, "TR-903", "economics", {"cost_items": items})
    report = validate_submission(**passing)
    check = next(c for c in report.checks if c.id == "SUB-14")
    assert report.ok and check.status == CheckStatus.WARN and "['b']" in check.detail


def test_only_design_verifier_can_accept(passing):
    """Golden rule 3: an ACCEPTED verdict from any other tool or verifier fails SUB-08."""
    run_dir = Path(passing["run_dir"]) / "final"
    store, state = _load(run_dir)
    state.verifications[-1] = state.verifications[-1].model_copy(update={"verifier": "fake"})
    _save(run_dir, store, state)
    report = validate_submission(**passing)
    assert report.failed_ids() == ["SUB-08"]
    assert "not DesignVerifier" in next(c.detail for c in report.checks if c.id == "SUB-08")


@pytest.mark.parametrize("case", ["verdict", "stub_call", "stub_evidence", "placeholder"])
def test_each_follow_up_fails_on_its_own(passing, tmp_path, case):
    """Verdict ≠ ACCEPTED, a stub call, a stub-evidence DR or an unset placeholder each fail."""
    run_dir = Path(passing["run_dir"]) / "final"
    kwargs = dict(passing)
    if case == "verdict":
        store, state = _load(run_dir)
        state.verifications[-1] = state.verifications[-1].model_copy(
            update={"verdict": DesignVerdict.UNVERIFIED}
        )
        _save(run_dir, store, state)
        expected = ["SUB-03", "SUB-08"]  # the verifier report check sees the verdict too
    elif case == "stub_call":
        store, state = _load(run_dir)
        state.tool_results[0] = state.tool_results[0].model_copy(update={"stub": True})
        _save(run_dir, store, state)
        expected = ["SUB-04"]
    elif case == "stub_evidence":
        record = json.loads((run_dir / "decision_records" / "DR-04.json").read_text())
        record["stub_evidence"] = True
        (run_dir / "decision_records" / "DR-04.json").write_text(json.dumps(record))
        expected = ["SUB-05"]
    else:
        config = tmp_path / "config"
        shutil.copytree(REPO_ROOT / "config", config)
        costs = yaml.safe_load((config / "cost_assumptions.yaml").read_text())
        name = next(iter(costs["items"]))
        costs["items"][name].update({"value": None, "placeholder": True, "range": None})
        (config / "cost_assumptions.yaml").write_text(yaml.safe_dump(costs))
        kwargs["config_dir"] = config
        expected = ["SUB-13"]
    report = validate_submission(**kwargs)
    assert not report.ok and report.failed_ids() == expected


def test_missing_concepts_and_unmet_days(passing):
    """C0–C7 must all carry KPIs; unmet days need a justification (manual)."""
    run_dir = Path(passing["run_dir"]) / "final"
    store, state = _load(run_dir)
    for candidate in state.candidates:
        if candidate.concept == COMPARED_CONCEPTS[-1]:
            state.kpis.pop(candidate.id, None)
    selected = state.kpis[state.selected_id]
    state.kpis[state.selected_id] = selected.model_copy(update={"unmet_heating_days": 2})
    _save(run_dir, store, state)
    report = validate_submission(**passing)
    statuses = _statuses(report)
    assert statuses["SUB-07"] == CheckStatus.MANUAL
    assert report.failed_ids() == ["SUB-09"]
    assert "['C7']" in next(c.detail for c in report.checks if c.id == "SUB-09")


def test_no_or_ambiguous_final_run(tmp_path, stub_run):
    """No run fails every run check; several runs need an explicit --run-dir."""
    report = validate_submission(run_dir=tmp_path / "none", eval_root=tmp_path)
    assert _statuses(report)["SUB-01"] == CheckStatus.FAIL
    assert "no final run" in next(c.detail for c in report.checks if c.id == "SUB-03")
    for name in ("a", "b"):
        shutil.copytree(stub_run, tmp_path / "runs" / name)
    with pytest.raises(ValueError, match="several runs"):
        find_final_run(tmp_path / "runs")
    assert find_final_run(tmp_path / "runs" / "a") == tmp_path / "runs" / "a"


def test_repository_checks_pass_on_this_repository():
    """Cooling models, brine PVT and both Apache-2.0 notice checks hold on the real repo."""
    ctx = SubmissionContext(
        repo_root=REPO_ROOT, config_dir=None, eval_root=REPO_ROOT, run=None, run_error=""
    )
    for check in (check_cooling_models, check_brine_pvt, check_plant_debugger_notices):
        result = check(ctx)
        assert result.status == CheckStatus.PASS, result
    assert "scripts/run_eval.py" not in check_plant_debugger_notices(ctx).detail
    assert check_gemini_notices(ctx).status != CheckStatus.FAIL  # MANUAL in a shallow clone


def test_incomplete_adapted_header_fails(passing):
    """A Plant Debugger adaptation without commit and 'Modified by' fails SUB-20."""
    repo = Path(passing["repo_root"])
    (repo / "scripts" / "bad.py").write_text("# Adapted from marcosanyo/PlantDebugger\n")
    report = validate_submission(**passing)
    assert report.failed_ids() == ["SUB-20"]


def test_dr11_variant_takes_its_percentiles_from_the_tl15_design(passing):
    """SUB-11 (pre-L-2): a DR-11 robust-backup variant is covered by its source's TL-15 run."""
    from gemini_agent.runner.state import RobustBackup

    run_dir = Path(passing["run_dir"]) / "final"
    store, state = _load(run_dir)
    source = state.selected_id
    variant = next(c for c in state.candidates if c.id == source).model_copy(
        update={"id": f"{source}-robust"}
    )
    state.candidates.append(variant)
    state.kpis[variant.id] = state.kpis[source].model_copy(
        update={"candidate_id": variant.id, "percentiles": {}}
    )
    state.verifications.append(
        state.verifications[-1].model_copy(update={"candidate_id": variant.id})
    )
    state.selected_id = variant.id
    _save(run_dir, store, state)
    statuses = _statuses(validate_submission(**passing))
    assert statuses["SUB-11"] == CheckStatus.FAIL  # no provenance: the variant has no MC
    store, state = _load(run_dir)
    state.robust_backup = RobustBackup(
        candidate_id=source, robust_candidate_id=variant.id, tool_result_id="TR-902"
    )
    _save(run_dir, store, state)
    report = validate_submission(**passing)
    check = next(c for c in report.checks if c.id == "SUB-11")
    assert check.status == CheckStatus.PASS and f"(TL-15 on {source})" in check.detail
    assert report.ok
