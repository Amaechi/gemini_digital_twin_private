# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file scripts/validate_submission.py @ d69507a. Modified by GeoAgent team, 2026-11-01: required
# file list replaced by the GeoAgent spec §18 acceptance checklist (final run, eval suite, models,
# notices, cost placeholders, VERIFY cost items); typed results, Markdown/JSON report, --pytest.
# 2026-11-02 (pre-L-2): SUB-06 includes DR-11; SUB-11 reads the TL-15 design of a DR-11 variant.
"""Submission checks (spec §18 acceptance checklist; WP-13).

``validate_submission`` checks every §18 item that a program can check and lists the rest as
``manual``. A check ``fail``s the submission; ``warn`` and ``manual`` do not. The final run must be
stub-free and ACCEPTED by the verifier (golden rule 3), so a run on today's stub tools fails here
on purpose until WP-14 binds the real tools.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from gemini_agent.logging import llm_logger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner.design_runner import RUNS_ROOT, STORE_FILE
from gemini_agent.runner.state import RunState
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.decision import DR_TITLES, DesignVerdict
from gemini_agent.verify.design_verifier import VERIFIER_NAME

FINAL_RUNS_DIR = RUNS_ROOT / "final"
EVAL_ROOT = RUNS_ROOT / "eval"
"""Local eval outputs of scripts/run_eval.py (git-ignored)."""
SUBMITTED_EVAL_ROOT = FINAL_RUNS_DIR / "eval"
"""Evals that ship with the submission (``runs/final/`` is the only tracked run folder)."""
RESULTS_FILE = "eval_results.json"
MODEL_COMPARISON_FILE = "model_comparison.json"
SCENARIO_IDS = tuple(f"S{number}" for number in range(1, 12))
"""Spec §12 EV-01: S1–S11."""
COMPARED_CONCEPTS = tuple(f"C{number}" for number in range(8))
"""Spec §18: C0–C7 compared with KPIs."""
COOLING_MODELS = ("absorption_chiller", "electric_chiller", "heat_rejection", "free_cooling")
PLANT_DEBUGGER_MARK = "Adapted from marcosanyo/PlantDebugger"
PLANT_DEBUGGER_COMMIT = "d69507a"
GEMINI_NOTICE = "Modified by"
GEMINI_PATHS = ("src/gemini_model", "src/gemini_simulator", "simulator_examples")
ADAPTED_HEADER_LINES = 10
GEMINI_NOTICE_LINES = 60
"""'near the top' (CLAUDE.md): the GEMINI notice may follow the file's licence block."""
TEST_COMMAND = (
    "-m",
    "pytest",
    "-q",
    "unit_test/gemini_model",
    "unit_test/gemini_simulator",
    "unit_test/gemini_agent",
    "-x",
)


class CheckStatus(StrEnum):
    """Outcome of one submission check (only FAIL blocks the submission)."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    MANUAL = "manual"


class SubmissionCheck(StrictModel):
    """One checklist item."""

    id: str
    item: str
    status: CheckStatus
    detail: str


class SubmissionReport(StrictModel):
    """All checks of one validation."""

    generated_at: str
    repo_root: str
    run_dir: str | None
    eval_root: str
    checks: list[SubmissionCheck]

    @property
    def ok(self) -> bool:
        """True when no check failed."""
        return not any(check.status == CheckStatus.FAIL for check in self.checks)

    def counts(self) -> dict[str, int]:
        """Count the checks per status."""
        return {
            status.value: sum(c.status == status for c in self.checks) for status in CheckStatus
        }

    def failed_ids(self) -> list[str]:
        """Ids of the failed checks."""
        return [check.id for check in self.checks if check.status == CheckStatus.FAIL]


# --------------------------------------------------------------------------- final run
@dataclass
class FinalRun:
    """The submitted run: ``run_summary.json`` plus the persisted ``RunState``."""

    run_dir: Path
    summary: dict[str, Any]
    state: RunState


def find_final_run(path: Path | str) -> Path:
    """Run directory at ``path`` or its only sub-directory with a ``run_summary.json``.

    Raises ``FileNotFoundError`` if there is none and ``ValueError`` if there are several.
    """
    root = Path(path)
    if (root / "run_summary.json").exists():
        return root
    runs = sorted(p.parent for p in root.glob("*/run_summary.json")) if root.is_dir() else []
    if not runs:
        raise FileNotFoundError(f"no run_summary.json in {root} or its sub-directories")
    if len(runs) > 1:
        names = ", ".join(run.name for run in runs)
        raise ValueError(f"several runs in {root} ({names}); pass --run-dir <one of them>")
    return runs[0]


def load_final_run(run_dir: Path) -> FinalRun:
    """Read ``run_summary.json`` and the run's state from ``events.sqlite``."""
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    store_path = run_dir / STORE_FILE
    if not store_path.exists():
        raise FileNotFoundError(f"{store_path} missing")
    store = SQLiteEventStore(store_path)
    try:
        record = store.get_run(str(summary.get("run_id", "")))
    finally:
        store.close()
    if record is None:
        raise KeyError(f"run '{summary.get('run_id')}' not in {store_path}")
    return FinalRun(run_dir=run_dir, summary=summary, state=RunState.model_validate(record.state))


def verify_cost_items(run_dir: Path) -> list[str] | None:
    """Names of ``cost_items[].verify`` items in the run's economics outputs (RC-05 VERIFY tags).

    Returns None when no economics output carries ``cost_items`` (no real economics run; the
    WP-10 stub returns KPIs only).
    """
    names: set[str] = set()
    found = False
    for path in sorted((run_dir / "tool_results").glob("*_economics.json")):
        data = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
        if "cost_items" not in data:
            continue
        found = True
        for item in data["cost_items"] or []:
            if isinstance(item, dict) and item.get("verify"):
                names.add(str(item.get("name")))
    return sorted(names) if found else None


def config_verify_items(config_dir: Path | None) -> list[str]:
    """Cost assumptions whose source or range carries the card's ``VERIFY`` tag."""
    costs = load_config("cost_assumptions", config_dir)
    return sorted(
        name
        for name, item in costs.items.items()
        if "VERIFY" in item.source or "VERIFY" in str(item.range)
    )


# --------------------------------------------------------------------------- context
@dataclass
class SubmissionContext:
    """Inputs of one validation."""

    repo_root: Path
    config_dir: Path | None
    eval_root: Path
    run: FinalRun | None
    run_error: str
    run_tests: bool = False


def _check(cid: str, item: str, status: CheckStatus, detail: str) -> SubmissionCheck:
    return SubmissionCheck(id=cid, item=item, status=status, detail=detail[:400])


def _passes(cid: str, item: str, ok: bool, detail: str) -> SubmissionCheck:
    return _check(cid, item, CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _needs_run(cid: str, item: str, ctx: SubmissionContext) -> SubmissionCheck | None:
    if ctx.run is None:
        return _check(cid, item, CheckStatus.FAIL, f"no final run: {ctx.run_error}")
    return None


def _selected_kpis(run: FinalRun) -> Any:
    selected = run.state.selected_id
    return run.state.kpis.get(selected) if selected else None


# --------------------------------------------------------------------------- run checks
def check_final_run(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-01: the final run's artefacts exist and load."""
    item = "DL-04: final run artefacts (run_summary.json, events.sqlite, events.jsonl)"
    if ctx.run is None:
        return _check("SUB-01", item, CheckStatus.FAIL, ctx.run_error)
    missing = [name for name in ("events.jsonl",) if not (ctx.run.run_dir / name).exists()]
    return _passes("SUB-01", item, not missing, f"{ctx.run.run_dir}; missing {missing or 'none'}")


def check_completed_and_approved(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-02: the run completed P0–P10 and the human approved the design."""
    item = "Run completed (P0–P10) and the recommended design was approved (P10)"
    if (failed := _needs_run("SUB-02", item, ctx)) is not None:
        return failed
    status, approval = ctx.run.summary.get("status"), ctx.run.summary.get("approval")
    if status != "completed" or approval == "rejected":
        return _check("SUB-02", item, CheckStatus.FAIL, f"status {status}, approval {approval}")
    if approval != "approved":
        detail = f"approval {approval}: non-interactive run; approve with an ask-mode run"
        return _check("SUB-02", item, CheckStatus.WARN, detail)
    return _check("SUB-02", item, CheckStatus.PASS, "completed, approved")


def check_verdict(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-03: the final verdict is ACCEPTED (only DesignVerifier can accept; golden rule 3)."""
    item = "§18: ACCEPTED design (final verifier verdict)"
    if (failed := _needs_run("SUB-03", item, ctx)) is not None:
        return failed
    verdict = ctx.run.summary.get("verdict")
    return _passes(
        "SUB-03",
        item,
        verdict == DesignVerdict.ACCEPTED.value,
        f"selected {ctx.run.summary.get('selected_candidate')}, verdict {verdict}",
    )


def check_no_stub_calls(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-04: ``run_summary.json`` reports no stub tool calls."""
    item = "No stub tool calls (run_summary.json stub_tool_calls = 0)"
    if (failed := _needs_run("SUB-04", item, ctx)) is not None:
        return failed
    stubs = ctx.run.summary.get("stub_tool_calls")
    calls = ctx.run.summary.get("tool_calls")
    ok = isinstance(stubs, int) and stubs == 0
    return _passes("SUB-04", item, ok, f"{stubs}/{calls} tool calls were stubs")


def check_no_stub_evidence(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-05: no final Decision Record rests on stub evidence."""
    item = "No final Decision Record with stub_evidence"
    if (failed := _needs_run("SUB-05", item, ctx)) is not None:
        return failed
    flagged = {
        str(record.get("dr_id"))
        for record in ctx.run.summary.get("decision_records", [])
        if record.get("stub_evidence") is not False
    }
    for path in sorted((ctx.run.run_dir / "decision_records").glob("DR-*.json")):
        if json.loads(path.read_text(encoding="utf-8")).get("stub_evidence") is not False:
            flagged.add(path.stem)
    return _passes("SUB-05", item, not flagged, f"stub evidence in {sorted(flagged) or 'none'}")


def check_decision_records(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-06: every Decision Record (DR-01…DR-10, DR-11 since pre-L-2) written as JSON and MD."""
    item = "§18: DR-01…DR-10 (+ DR-11) written"
    if (failed := _needs_run("SUB-06", item, ctx)) is not None:
        return failed
    folder = ctx.run.run_dir / "decision_records"
    missing = [
        f"{dr_id}.{ext}"
        for dr_id in sorted(DR_TITLES)
        for ext in ("json", "md")
        if not (folder / f"{dr_id}.{ext}").exists()
    ]
    return _passes("SUB-06", item, not missing, f"missing {missing or 'none'}")


def check_unmet_days(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-07: 0 unmet heating/cooling days (a non-zero count needs a written justification)."""
    item = "§18: 0 unmet heating/cooling days over 365 days (or justified)"
    if (failed := _needs_run("SUB-07", item, ctx)) is not None:
        return failed
    kpis = _selected_kpis(ctx.run)
    heat = kpis.unmet_heating_days if kpis else None
    cool = kpis.unmet_cooling_days if kpis else None
    detail = f"unmet heating days {heat}, cooling days {cool}"
    if heat is None or cool is None:
        return _check("SUB-07", item, CheckStatus.FAIL, f"{detail} (annual simulation missing)")
    if heat or cool:
        return _check("SUB-07", item, CheckStatus.MANUAL, f"{detail}: justify in the report")
    return _check("SUB-07", item, CheckStatus.PASS, detail)


def check_verifier_report(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-08: a real (non-stub) verifier report without violations for the selected design."""
    item = "§18: DB-10…17 satisfied every day (verifier report)"
    if (failed := _needs_run("SUB-08", item, ctx)) is not None:
        return failed
    state = ctx.run.state
    latest = state.latest_verification(state.selected_id)
    if latest is None:
        return _check("SUB-08", item, CheckStatus.FAIL, "selected design never verified")
    record = next((r for r in state.tool_results if r.id == latest.tool_result_id), None)
    report = ctx.run.run_dir / record.path if record and record.path else None
    problems = []
    if latest.verifier != VERIFIER_NAME:
        problems.append(f"verdict from '{latest.verifier}', not {VERIFIER_NAME} (golden rule 3)")
    if record is None or record.stub or record.tool != "verify_design":
        problems.append("verifier result is a stub, missing or not from verify_design")
    if report is None or not report.exists():
        problems.append("verifier report file missing")
    if latest.violations:
        problems.append(f"violations {latest.violations[:3]}")
    if latest.verdict != DesignVerdict.ACCEPTED:
        problems.append(f"verdict {latest.verdict.value}")
    detail = f"{latest.verifier} on {latest.candidate_id}: {problems or 'no violations'}"
    return _passes("SUB-08", item, not problems, detail)


def check_alternatives(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-09: every concept C0–C7 has computed KPIs (LCOH) in the final run."""
    item = "§18: C0–C7 compared with KPIs"
    if (failed := _needs_run("SUB-09", item, ctx)) is not None:
        return failed
    state = ctx.run.state
    with_kpis = {
        candidate.concept
        for candidate in state.candidates
        if (kpi := state.kpis.get(candidate.id)) is not None and kpi.lcoh_per_mwh is not None
    }
    missing = [concept for concept in COMPARED_CONCEPTS if concept not in with_kpis]
    return _passes("SUB-09", item, not missing, f"concepts without KPIs: {missing or 'none'}")


def check_economics_kpis(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-10: LCOH, LCOC, CO₂ vs C0 and exergy of the selected design."""
    item = "§18: LCOH/LCOC, CO2 vs C0, exergy (selected design)"
    if (failed := _needs_run("SUB-10", item, ctx)) is not None:
        return failed
    kpis = _selected_kpis(ctx.run)
    fields = ("lcoh_per_mwh", "lcoc_per_mwh", "co2_reduction_vs_c0_frac", "exergy_efficiency_frac")
    missing = [name for name in fields if kpis is None or getattr(kpis, name) is None]
    return _passes("SUB-10", item, not missing, f"missing {missing or 'none'}")


def check_uncertainty(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-11: a non-stub sensitivity result and P10/P50/P90 percentiles.

    The percentiles are those of the design TL-15 analysed: the selected design itself, or the
    design whose robust backups DR-11 installed (``RunState.analysed_id``).
    """
    item = "§18: sensitivity and Monte Carlo P10/P50/P90"
    if (failed := _needs_run("SUB-11", item, ctx)) is not None:
        return failed
    sensitivity = [
        r.tool
        for r in ctx.run.state.tool_results
        if "sensitivity" in r.tool and r.ok and not r.stub
    ]
    analysed = ctx.run.state.analysed_id(ctx.run.state.selected_id)
    kpis = ctx.run.state.kpis.get(analysed) if analysed else None
    percentiles = [
        name
        for name, values in (kpis.percentiles if kpis else {}).items()
        if {"p10", "p50", "p90"} <= set(values)
    ]
    problems = [
        text
        for text, ok in (("no sensitivity result", sensitivity), ("no P10/P50/P90", percentiles))
        if not ok
    ]
    detail = (
        f"sensitivity {sorted(set(sensitivity)) or 'none'}; percentiles {percentiles or 'none'}"
        + (f" (TL-15 on {analysed})" if analysed != ctx.run.state.selected_id else "")
    )
    return _passes("SUB-11", item, not problems, detail)


def check_llm_logs(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-12: the final run logged its LLM calls and wrote the token summary."""
    item = "§18: llm_calls.jsonl + token_summary.json (final run)"
    if (failed := _needs_run("SUB-12", item, ctx)) is not None:
        return failed
    run_dir = ctx.run.run_dir
    records = llm_logger.LLMLogger(run_dir, create=False).records()
    problems = []
    if not records:
        problems.append(
            f"no {llm_logger.LLM_CALLS_FILE} records (the final run must use --engine llm)"
        )
    if not (run_dir / llm_logger.TOKEN_SUMMARY_FILE).exists():
        problems.append(f"{llm_logger.TOKEN_SUMMARY_FILE} missing")
    return _passes("SUB-12", item, not problems, f"{len(records)} LLM calls; {problems or 'ok'}")


def check_cost_placeholders(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-13: ``CostAssumptions.unset_placeholders()`` is empty."""
    item = "Cost placeholders set (CostAssumptions.unset_placeholders() empty)"
    try:
        unset = load_config("cost_assumptions", ctx.config_dir).unset_placeholders()
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        return _check("SUB-13", item, CheckStatus.FAIL, f"cost_assumptions.yaml: {exc}")
    return _passes("SUB-13", item, not unset, f"unset placeholders: {unset or 'none'}")


def check_verify_cost_items(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-14: flag VERIFY-tagged cost items (RC-05 chiller cost functions); never blocks."""
    item = "VERIFY-tagged cost items flagged (cost_items[].verify, RC-05)"
    used = verify_cost_items(ctx.run.run_dir) if ctx.run is not None else None
    if used is None:
        try:
            tagged = config_verify_items(ctx.config_dir)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            return _check("SUB-14", item, CheckStatus.FAIL, f"cost_assumptions.yaml: {exc}")
        detail = f"no economics output in the final run; config items tagged VERIFY: {tagged}"
        return _check("SUB-14", item, CheckStatus.WARN if tagged else CheckStatus.PASS, detail)
    if used:
        detail = f"economics used VERIFY-tagged items {used}: state the RC-05 caveat in the report"
        return _check("SUB-14", item, CheckStatus.WARN, detail)
    return _check("SUB-14", item, CheckStatus.PASS, "no VERIFY-tagged item used")


# --------------------------------------------------------------------------- eval checks
def llm_eval_results(eval_root: Path) -> list[dict[str, Any]]:
    """``eval_results.json`` of every ``--engine llm`` eval under ``eval_root``."""
    results = []
    for path in sorted(eval_root.glob(f"*/{RESULTS_FILE}")) if eval_root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("engine") == "llm":
            data["_dir"] = str(path.parent)
            results.append(data)
    return results


def check_token_comparison(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-15: an LLM eval holds the S10 naive-vs-optimised token comparison."""
    item = "§18: naive-vs-optimised token comparison (S10, --engine llm)"
    found = [
        f"{data.get('model')}"
        for data in llm_eval_results(ctx.eval_root)
        for run in data.get("runs", [])
        if run.get("scenario") == "S10" and run.get("token_comparison")
    ]
    return _passes("SUB-15", item, bool(found), f"S10 comparison in models {found or 'none'}")


def check_model_comparison(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-16: ``model_comparison.json`` compares at least two LLM models (S11)."""
    item = "§18: model-size comparison (S11, two --engine llm models)"
    path = ctx.eval_root / MODEL_COMPARISON_FILE
    if not path.exists():
        return _check("SUB-16", item, CheckStatus.FAIL, f"{path} missing (run_eval.py --compare)")
    data = json.loads(path.read_text(encoding="utf-8"))
    models = [m.get("model") for m in data.get("models", []) if m.get("engine") == "llm"]
    return _passes("SUB-16", item, len(models) >= 2, f"LLM models compared: {models or 'none'}")


def check_transcripts(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-17: one LLM eval covers S1–S11 with transcripts (events + LLM call log)."""
    item = "§18: S1–S11 transcripts (prompts, answers, tokens)"
    best: tuple[int, str, list[str]] | None = None
    for data in llm_eval_results(ctx.eval_root):
        runs = {run.get("scenario"): run for run in data.get("runs", [])}
        problems = []
        for sid in SCENARIO_IDS:
            run = runs.get(sid)
            if run is None or run.get("outcome") in {"error", "skipped"}:
                problems.append(f"{sid} not run")
                continue
            for run_dir in run.get("run_dirs", []):
                folder = Path(data["_dir"]) / run_dir
                if not (folder / "events.jsonl").exists():
                    problems.append(f"{sid} events.jsonl")
                if (
                    run.get("status") != "refused"
                    and not (folder / llm_logger.LLM_CALLS_FILE).exists()
                ):
                    problems.append(f"{sid} {llm_logger.LLM_CALLS_FILE}")
        if best is None or len(problems) < best[0]:
            best = (len(problems), str(data.get("model")), problems)
    if best is None:
        return _check("SUB-17", item, CheckStatus.FAIL, f"no --engine llm eval in {ctx.eval_root}")
    return _passes("SUB-17", item, best[0] == 0, f"model {best[1]}: missing {best[2] or 'none'}")


# --------------------------------------------------------------------------- repository checks
def check_cooling_models(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-18: the four cooling models have a card copy, tests, a registry entry and docs."""
    from gemini_simulator.registry import COMPONENT_REGISTRY

    item = "§18: absorption, electric chiller, heat rejection, free cooling models"
    root = ctx.repo_root
    docs = root / "src" / "gemini_documentation" / "source"
    rst = " ".join(p.read_text(encoding="utf-8") for p in sorted(docs.glob("*.rst")))
    problems = []
    for name in COOLING_MODELS:
        if name not in COMPONENT_REGISTRY:
            problems.append(f"{name}: not registered")
        if not (root / "src" / "gemini_model" / name / "REFERENCE.md").exists():
            problems.append(f"{name}: REFERENCE.md")
        if not list((root / "unit_test" / "gemini_model" / name).glob("test_*.py")):
            problems.append(f"{name}: tests")
        if f"``{name}``" not in rst:
            problems.append(f"{name}: .rst page")
    return _passes("SUB-18", item, not problems, f"problems: {problems or 'none'}")


def check_brine_pvt(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-19: brine density/viscosity (DB-08) and specific heat (DB-33) come from config."""
    item = "§18: brine PVT configurable (DB-08, DB-33)"
    try:
        brine = load_config("design_basis", ctx.config_dir).brine
        cp_value = load_config("assumptions", ctx.config_dir).get("DB-33").value
    except (OSError, KeyError, yaml.YAMLError, ValidationError) as exc:
        return _check("SUB-19", item, CheckStatus.FAIL, f"config: {exc}")
    values = {
        "density_kg_m3": brine.density_kg_m3,
        "viscosity_pa_s": brine.viscosity_pa_s,
        "specific_heat_j_kgk (DB-33)": cp_value,
    }
    bad = [k for k, v in values.items() if not isinstance(v, int | float) or v <= 0]
    detail = f"{values}; consistency across components is part of the verifier verdict (SUB-03)"
    return _passes("SUB-19", item, not bad, detail if not bad else f"invalid {bad}")


def _head(path: Path, lines: int) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readline() for _ in range(lines))
    except OSError:
        return ""


def check_plant_debugger_notices(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-20: Apache-2.0 notices for the Plant Debugger reuse."""
    item = "§18: Apache-2.0 notices for Plant Debugger reuse"
    root = ctx.repo_root
    problems = [
        f"{name} missing"
        for name in ("LICENSE", "NOTICE", "third_party/plantdebugger/LICENSE")
        if not (root / name).exists()
    ]
    notice = (root / "NOTICE").read_text(encoding="utf-8") if (root / "NOTICE").exists() else ""
    if notice and "PlantDebugger" not in notice:
        problems.append("NOTICE does not name Plant Debugger")
    adapted = 0
    for folder in ("src", "scripts"):
        for path in sorted((root / folder).rglob("*.py")) if (root / folder).is_dir() else []:
            head = _head(path, ADAPTED_HEADER_LINES)
            if PLANT_DEBUGGER_MARK not in head:
                continue
            adapted += 1
            if PLANT_DEBUGGER_COMMIT not in head or "Modified by" not in head:
                problems.append(f"{path.relative_to(root)}: incomplete header")
    detail = f"{adapted} adapted file(s); problems: {problems or 'none'}"
    return _passes("SUB-20", item, not problems and adapted > 0, detail)


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def gemini_fork_base(root: Path) -> str | None:
    """GEMINI commit the repo was forked from, as stated in NOTICE ('forked from ... @ <sha>').

    None if NOTICE does not state it or git cannot resolve it (e.g. a shallow clone).
    """
    notice = root / "NOTICE"
    text = notice.read_text(encoding="utf-8") if notice.exists() else ""
    match = re.search(r"forked from[^@]*@\s*([0-9a-f]{7,40})", text)
    if match is None:
        return None
    resolved = _git(root, "rev-parse", "--verify", "--quiet", f"{match.group(1)}^{{commit}}")
    return resolved.strip() if resolved else None


def check_gemini_notices(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-21: every GEMINI file changed since the fork carries a 'Modified by' notice."""
    item = "§18: Apache-2.0 notices on modified GEMINI files"
    base = gemini_fork_base(ctx.repo_root)
    changed = None
    if base is not None:
        changed = _git(
            ctx.repo_root, "diff", "--name-only", "--diff-filter=MR", base, "--", *GEMINI_PATHS
        )
    if base is None or changed is None:
        detail = "git history unavailable (shallow clone?): check the notices by hand"
        return _check("SUB-21", item, CheckStatus.MANUAL, detail)
    files = [name for name in changed.splitlines() if name.endswith(".py")]
    others = [name for name in changed.splitlines() if name and not name.endswith(".py")]
    missing = [
        name
        for name in files
        if GEMINI_NOTICE not in _head(ctx.repo_root / name, GEMINI_NOTICE_LINES)
    ]
    detail = (
        f"{len(files)} GEMINI .py file(s) modified since {base[:7]}; no notice: "
        f"{missing or 'none'}; other modified files (check by hand): {others or 'none'}"
    )
    if missing:
        return _check("SUB-21", item, CheckStatus.FAIL, detail)
    return _check("SUB-21", item, CheckStatus.WARN if others else CheckStatus.PASS, detail)


def check_readme(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-22: README present; DL-03 API placeholders documented."""
    item = "§18/DL-03: code pushed with README (API placeholders documented)"
    readme = ctx.repo_root / "README.md"
    if not readme.exists():
        return _check("SUB-22", item, CheckStatus.FAIL, "README.md missing")
    if "placeholder" not in readme.read_text(encoding="utf-8").lower():
        return _check("SUB-22", item, CheckStatus.WARN, "README does not mention API placeholders")
    return _check("SUB-22", item, CheckStatus.PASS, "README.md present, placeholders documented")


def check_deliverables(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-23: deck and video are checked by hand."""
    item = "§18: 10 slides + appendix, ≤ 5-min video"
    return _check("SUB-23", item, CheckStatus.MANUAL, "check the deck and the video by hand")


def check_tests(ctx: SubmissionContext) -> SubmissionCheck:
    """SUB-24: the test suite is green (only with ``run_tests``)."""
    item = "EV-02: unit tests green (GEMINI + gemini_agent)"
    if not ctx.run_tests:
        return _check("SUB-24", item, CheckStatus.MANUAL, "run with --pytest to include the suite")
    result = subprocess.run(
        [sys.executable, *TEST_COMMAND],
        cwd=ctx.repo_root,
        env={**os.environ, "PYTHONPATH": str(ctx.repo_root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    last = (result.stdout.strip().splitlines() or ["no output"])[-1]
    return _passes("SUB-24", item, result.returncode == 0, last)


CHECKS: tuple[Callable[[SubmissionContext], SubmissionCheck], ...] = (
    check_final_run,
    check_completed_and_approved,
    check_verdict,
    check_no_stub_calls,
    check_no_stub_evidence,
    check_decision_records,
    check_unmet_days,
    check_verifier_report,
    check_alternatives,
    check_economics_kpis,
    check_uncertainty,
    check_llm_logs,
    check_cost_placeholders,
    check_verify_cost_items,
    check_token_comparison,
    check_model_comparison,
    check_transcripts,
    check_cooling_models,
    check_brine_pvt,
    check_plant_debugger_notices,
    check_gemini_notices,
    check_readme,
    check_deliverables,
    check_tests,
)


def validate_submission(
    *,
    run_dir: Path | str = FINAL_RUNS_DIR,
    eval_root: Path | str = SUBMITTED_EVAL_ROOT,
    config_dir: Path | str | None = None,
    repo_root: Path | str = REPO_ROOT,
    run_tests: bool = False,
) -> SubmissionReport:
    """Run every submission check; the submission is acceptable iff ``report.ok``."""
    run: FinalRun | None = None
    run_error = ""
    try:
        run = load_final_run(find_final_run(run_dir))
    except (OSError, KeyError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        run_error = f"{type(exc).__name__}: {exc}"
    ctx = SubmissionContext(
        repo_root=Path(repo_root),
        config_dir=Path(config_dir) if config_dir is not None else None,
        eval_root=Path(eval_root),
        run=run,
        run_error=run_error,
        run_tests=run_tests,
    )
    return SubmissionReport(
        generated_at=datetime.now(UTC).isoformat(),
        repo_root=str(ctx.repo_root),
        run_dir=str(run.run_dir) if run else None,
        eval_root=str(ctx.eval_root),
        checks=[check(ctx) for check in CHECKS],
    )


def render_markdown(report: SubmissionReport) -> str:
    """Markdown table of all checks (failures first in the summary line)."""
    counts = report.counts()
    verdict = "READY" if report.ok else f"NOT READY (failed: {', '.join(report.failed_ids())})"
    lines = [
        "# GeoAgent submission check (spec §18)",
        "",
        f"Generated: {report.generated_at}",
        f"Final run: `{report.run_dir or 'not found'}`  ",
        f"Eval root: `{report.eval_root}`",
        "",
        (
            f"**{verdict}** — pass {counts['pass']}, fail {counts['fail']}, "
            f"warn {counts['warn']}, manual {counts['manual']}"
        ),
        "",
        "| ID | Item | Status | Detail |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        detail = check.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.id} | {check.item} | {check.status.value.upper()} | {detail} |")
    lines += [
        "",
        (
            "Only FAIL blocks the submission. WARN items need a note in the report; MANUAL items "
            "are checked by hand."
        ),
        "",
    ]
    return "\n".join(lines)
