# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file scripts/run_eval.py @ d69507a. Modified by GeoAgent team, 2026-11-01: HTTP incident polling
# replaced by in-process DesignRunner runs of the spec §12 scenarios (evals/scenarios.yaml) with
# policy / TestModel / Ollama engines; EvalRun metrics (tool calls, retries, fallbacks, tokens,
# runtime), declarative checks + pending items, naive-vs-optimised tokens, model comparison;
# 2026-11-02 (WP-10 L-1 follow-up): kind_coercions metric.
"""Evaluation harness for scenarios S1–S11 (spec §12 EV-01, EV-04; WP-13).

Engines:

* ``policy`` — deterministic PolicyEngine (no LLM); used in CI.
* ``testmodel`` — Pydantic AI ``TestModel`` through the real LLM path (agents, validators,
  bounded retries, logger, policy fallback); used in CI. TestModel output never validates
  against our structured outputs, so every call ends in a logged policy fallback after its
  retries: it exercises the AG-03 path and the token accounting, not decision quality.
* ``llm`` — a local Ollama model (``--model TAG``); used only on a laptop (L-1/L-2). Results go
  to ``runs/eval/<model>/`` with each scenario's transcript (``llm_calls.jsonl``, ``events.jsonl``).

Outputs per eval: ``<eval_dir>/<scenario run id>/`` (a normal run directory),
``eval_results.json`` and ``EVAL_REPORT.md``; ``compare_evals`` writes the S11 model comparison.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from gemini_agent.agents.engine import (
    DecisionEngine,
    LLMEngine,
    PolicyEngine,
    ResilientEngine,
    create_decision_engine,
)
from gemini_agent.eval import submission
from gemini_agent.eval.scenarios import (
    CheckResult,
    EngineKind,
    Observation,
    PendingResult,
    Scenario,
    ScenarioSetup,
    ScenarioSuite,
    evaluate,
    evaluate_pending,
)
from gemini_agent.logging.llm_logger import LLMLogger, compare_token_summaries
from gemini_agent.runner import design_runner
from gemini_agent.runner.design_runner import RUNS_ROOT, STORE_FILE, DesignRunner
from gemini_agent.runner.toolbox import Toolbox, default_toolbox
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import LLMConfig, default_config_dir, load_config

EVAL_ROOT = RUNS_ROOT / "eval"
REPORT_FILE = "EVAL_REPORT.md"
MODEL_COMPARISON_MD = "model_comparison.md"
TOKEN_COMPARISON_SUFFIX = "-token_comparison.json"


class Outcome(StrEnum):
    """Scenario outcome: ``pending`` = every active check passed, pending items remain."""

    PASS = "pass"
    PENDING = "pending"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"


class RunMetrics(StrictModel):
    """EV-04 metrics of one scenario (summed over its runs, e.g. S10 naive + optimised)."""

    runtime_s: float = 0.0
    tool_calls: int = 0
    stub_tool_calls: int = 0
    llm_calls: int = 0
    llm_valid_calls: int = Field(default=0, description="LLM calls used without fallback.")
    retries: int = 0
    fallbacks: int = 0
    step_rejections: int = 0
    kind_coercions: int = Field(
        default=0, description="LLM step kinds repaired from the tool name (KIND_COERCED)."
    )
    token_budget_overruns: int = Field(
        default=0,
        description="Decision Record calls made past their phase token budget "
        "(TOKEN_BUDGET_OVERRUN; runner J).",
    )
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Input plus output tokens."""
        return self.input_tokens + self.output_tokens

    @property
    def tool_call_validity_frac(self) -> float | None:
        """Share of LLM calls whose structured output was used (None without LLM calls)."""
        return round(self.llm_valid_calls / self.llm_calls, 4) if self.llm_calls else None

    def add(self, other: RunMetrics) -> RunMetrics:
        """Field-wise sum."""
        return RunMetrics(
            **{name: getattr(self, name) + getattr(other, name) for name in RunMetrics.model_fields}
        )


class EvalRun(StrictModel):
    """Result of one scenario on one engine/model."""

    scenario: str
    situation: str
    expected: str
    engine: EngineKind
    model: str
    outcome: Outcome
    status: str | None = None
    phase: str | None = None
    verdict: str | None = None
    selected_candidate: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    checks: list[CheckResult] = Field(default_factory=list)
    pending: list[PendingResult] = Field(default_factory=list)
    run_dirs: list[str] = Field(default_factory=list, description="Relative to the eval dir.")
    verify_cost_items: list[str] = Field(default_factory=list)
    token_comparison: dict[str, Any] | None = None
    note: str = ""


class EvalResults(StrictModel):
    """``eval_results.json``."""

    generated_at: str
    engine: EngineKind
    model: str
    eval_dir: str
    scenarios_source: str
    runs: list[EvalRun]

    def counts(self) -> dict[str, int]:
        """Count the scenarios per outcome."""
        return {o.value: sum(run.outcome == o for run in self.runs) for o in Outcome}

    @property
    def ok(self) -> bool:
        """True when no scenario failed or errored."""
        return not any(run.outcome in {Outcome.FAIL, Outcome.ERROR} for run in self.runs)


# --------------------------------------------------------------------------- engines
def model_label(
    engine: EngineKind, model_tag: str = "", llm_config: LLMConfig | None = None
) -> str:
    """Model name recorded in results: the Ollama tag for ``llm``, else the engine name."""
    if engine != "llm":
        return engine
    if model_tag:
        return model_tag
    config = llm_config or load_config("llm")
    return config.profiles[config.active_profile].orchestrator


def eval_dir_name(model: str) -> str:
    """File-system safe directory name of a model tag (``qwen3.5:9b`` -> ``qwen3.5_9b``)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("._") or "model"


def offline_test_model(seed: int) -> Any:
    """Pydantic AI ``TestModel`` that accepts native structured output (offline, no network)."""
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.profiles import ModelProfile

    return TestModel(
        profile=ModelProfile(supports_json_schema_output=True), seed=seed, model_name="testmodel"
    )


def build_engine(
    engine: EngineKind,
    *,
    run_dir: Path,
    run_id: str,
    llm_config: LLMConfig,
    toolbox: Toolbox,
    model_tag: str = "",
) -> DecisionEngine:
    """Decision engine for one scenario run (every LLM call logged to ``run_dir``, MEM-04)."""
    if engine == "policy":
        return PolicyEngine()
    logger = LLMLogger(run_dir)
    if engine == "testmodel":
        primary = LLMEngine(
            llm_config,
            model=offline_test_model(llm_config.seed),
            logger=logger,
            run_id=run_id,
            validate_args=toolbox.validate_args,
        )
        return ResilientEngine(primary, PolicyEngine())
    return create_decision_engine(
        "llm",
        llm_config,
        logger=logger,
        run_id=run_id,
        model_tag=model_tag,
        validate_args=toolbox.validate_args,
    )


NAIVE_PROFILE = {"history_events": None, "compact_history": False}
"""MEM-06 'naive' token profile for S10: the full, uncompacted event history in every context.
The optimised default keeps the last ``HISTORY_EVENTS`` compacted events (spec §10.2)."""
NAIVE_PROFILE_NOTE = (
    "naive = full event history with full payloads in every context; optimised = the last "
    f"{design_runner.HISTORY_EVENTS} compacted events (MEM-06 compact context); no other "
    "setting differs."
)


# --------------------------------------------------------------------------- setup
def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def prepare_config(
    overrides: dict[str, dict[str, Any]], target: Path, config_dir: Path | None = None
) -> Path:
    """Copy ``config_dir`` to ``target`` and merge ``overrides`` (validated by ``load_config``)."""
    source = Path(config_dir) if config_dir is not None else default_config_dir()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    for name, values in overrides.items():
        path = target / f"{name}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        path.write_text(yaml.safe_dump(_merge(raw, values), sort_keys=False), encoding="utf-8")
        load_config(name, target)
    return target


@contextmanager
def removed_components(names: Sequence[str]) -> Iterator[None]:
    """Temporarily remove entries from GEMINI's ``COMPONENT_REGISTRY`` (S7)."""
    from gemini_simulator.registry import COMPONENT_REGISTRY

    unknown = [name for name in names if name not in COMPONENT_REGISTRY]
    if unknown:
        raise KeyError(f"not in COMPONENT_REGISTRY: {unknown}")
    saved = {name: COMPONENT_REGISTRY.pop(name) for name in names}
    try:
        yield
    finally:
        COMPONENT_REGISTRY.update(saved)


def _clear_run_dir(path: Path) -> None:
    """Remove an earlier output of the same scenario (only directories that look like runs)."""
    if not path.exists():
        return
    if not path.is_dir() or (any(path.iterdir()) and not (path / STORE_FILE).exists()):
        raise FileExistsError(f"{path} exists and is not a run directory; refusing to delete it")
    shutil.rmtree(path)


# --------------------------------------------------------------------------- running
def _metrics(runner: DesignRunner, run_id: str, runtime_s: float) -> RunMetrics:
    state = runner.load_state(run_id)
    events = runner.store(run_id).list_events(run_id)
    records = LLMLogger(runner.run_dir(run_id), create=False).records()
    return RunMetrics(
        runtime_s=round(runtime_s, 3),
        tool_calls=len(state.tool_results),
        stub_tool_calls=sum(1 for record in state.tool_results if record.stub),
        llm_calls=len(records),
        llm_valid_calls=sum(1 for r in records if r.decision_source == "llm" and r.error is None),
        retries=sum(record.retries for record in records),
        fallbacks=state.fallback_count(),
        step_rejections=sum(1 for event in events if event.type == "STEP_REJECTED"),
        kind_coercions=sum(1 for event in events if event.type == "KIND_COERCED"),
        token_budget_overruns=sum(1 for e in events if e.type == "TOKEN_BUDGET_OVERRUN"),
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
    )


TOOL_PROFILE = "quick"
"""WP-14: default search effort of the real tools in evals (``runner/real_tools.make_profile``);
the scenarios check behaviour, not optimisation depth (``run_eval.py --tool-profile``)."""


def _run_once(
    scenario: Scenario,
    run_id: str,
    *,
    engine: EngineKind,
    eval_dir: Path,
    config_dir: Path | None,
    llm_config: LLMConfig,
    model_tag: str,
    naive: bool,
    tool_profile: str = TOOL_PROFILE,
) -> tuple[Observation, RunMetrics]:
    _clear_run_dir(eval_dir / run_id)
    toolbox = default_toolbox(tool_profile)
    decision_engine = build_engine(
        engine,
        run_dir=eval_dir / run_id,
        run_id=run_id,
        llm_config=llm_config,
        toolbox=toolbox,
        model_tag=model_tag,
    )
    runner = DesignRunner(
        decision_engine,
        runs_root=eval_dir,
        toolbox=toolbox,
        config_dir=config_dir,
        **(NAIVE_PROFILE if naive else {}),
    )
    try:
        started = time.monotonic()
        result = runner.start(
            scenario.prompt,
            run_id=run_id,
            input_mode=scenario.input_mode,
            model_tag=model_tag,
        )
        runtime = time.monotonic() - started
        metrics = _metrics(runner, run_id, runtime)
        events = runner.store(run_id).list_events(run_id)
        observation = Observation(
            result=result,
            state=runner.load_state(run_id),
            run_dir=runner.run_dir(run_id),
            phases_started=[event.phase for event in events if event.type == "PHASE_START"],
            llm_calls=metrics.llm_calls,
            llm_valid_calls=metrics.llm_valid_calls,
            config_dir=config_dir,
        )
    finally:
        runner.close()
    return observation, metrics


def _outcome(checks: list[CheckResult], pending: list[PendingResult]) -> Outcome:
    if not all(check.passed for check in checks):
        return Outcome.FAIL
    return Outcome.PENDING if pending else Outcome.PASS


def run_scenario(
    scenario: Scenario,
    *,
    engine: EngineKind,
    eval_dir: Path,
    config_dir: Path | None = None,
    llm_config: LLMConfig | None = None,
    model_tag: str = "",
    tool_profile: str = TOOL_PROFILE,
) -> EvalRun:
    """Run one scenario (several runs for token profiles) and evaluate its checks."""
    llm_config = llm_config or load_config("llm", config_dir)
    model = model_label(engine, model_tag, llm_config)
    base = EvalRun(
        scenario=scenario.id,
        situation=scenario.situation,
        expected=scenario.expected,
        engine=engine,
        model=model,
        outcome=Outcome.SKIPPED,
    )
    if engine not in scenario.engines:
        return base.model_copy(update={"note": scenario.skip_reason})
    try:
        return _run_scenario(
            scenario, base, eval_dir, config_dir, llm_config, model_tag, tool_profile
        )
    except Exception as exc:  # noqa: BLE001 - one broken scenario must not stop the suite
        return base.model_copy(
            update={"outcome": Outcome.ERROR, "note": f"{type(exc).__name__}: {exc}"[:500]}
        )


def _run_scenario(
    scenario: Scenario,
    base: EvalRun,
    eval_dir: Path,
    config_dir: Path | None,
    llm_config: LLMConfig,
    model_tag: str,
    tool_profile: str = TOOL_PROFILE,
) -> EvalRun:
    setup: ScenarioSetup = scenario.setup
    eval_dir.mkdir(parents=True, exist_ok=True)
    if setup.config_overrides:
        target = eval_dir / f"{scenario.id}.config"
        config_dir = prepare_config(setup.config_overrides, target, config_dir)
    profiles = setup.token_profiles or ["optimised"]
    metrics = RunMetrics()
    observations: dict[str, Observation] = {}
    with removed_components(setup.remove_components):
        for profile in profiles:
            run_id = scenario.id if len(profiles) == 1 else f"{scenario.id}-{profile}"
            observation, run_metrics = _run_once(
                scenario,
                run_id,
                engine=base.engine,
                eval_dir=eval_dir,
                config_dir=config_dir,
                llm_config=llm_config,
                model_tag=model_tag,
                naive=profile == "naive",
                tool_profile=tool_profile,
            )
            observations[profile] = observation
            metrics = metrics.add(run_metrics)
    primary = observations["optimised"]
    if len(observations) > 1:
        naive, optimised = (
            LLMLogger(observations[p].run_dir, create=False).token_summary()
            for p in ("naive", "optimised")
        )
        comparison = compare_token_summaries(naive, optimised)
        path = eval_dir / f"{scenario.id}{TOKEN_COMPARISON_SUFFIX}"
        path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
        primary.token_comparison = comparison
    checks = [evaluate(check, primary) for check in scenario.checks]
    pending = [evaluate_pending(item, primary) for item in scenario.pending]
    result = primary.result
    return base.model_copy(
        update={
            "outcome": _outcome(checks, pending),
            "status": result.status.value,
            "phase": result.phase.value,
            "verdict": result.verdict,
            "selected_candidate": result.selected_id,
            "conflicts": list(primary.state.conflicts),
            "metrics": metrics,
            "checks": checks,
            "pending": pending,
            "run_dirs": [obs.run_dir.name for obs in observations.values()],
            "verify_cost_items": submission.verify_cost_items(primary.run_dir) or [],
            "token_comparison": primary.token_comparison,
        }
    )


def run_suite(
    suite: ScenarioSuite,
    *,
    engine: EngineKind,
    eval_root: Path = EVAL_ROOT,
    scenario_ids: Sequence[str] | None = None,
    config_dir: Path | None = None,
    model_tag: str = "",
    scenarios_source: str = "evals/scenarios.yaml",
    tool_profile: str = TOOL_PROFILE,
) -> EvalResults:
    """Run the suite (or ``scenario_ids``) and write ``eval_results.json`` + ``EVAL_REPORT.md``.

    Results go to ``<eval_root>/<model>/`` (``policy``, ``testmodel`` or the Ollama tag).
    """
    llm_config = load_config("llm", config_dir)
    model = model_label(engine, model_tag, llm_config)
    eval_dir = Path(eval_root) / eval_dir_name(model)
    selected = [suite.get(sid) for sid in scenario_ids] if scenario_ids else suite.scenarios
    runs = [
        run_scenario(
            scenario,
            engine=engine,
            eval_dir=eval_dir,
            config_dir=config_dir,
            llm_config=llm_config,
            model_tag=model_tag if engine == "llm" else "",
            tool_profile=tool_profile,
        )
        for scenario in selected
    ]
    results = EvalResults(
        generated_at=datetime.now(UTC).isoformat(),
        engine=engine,
        model=model,
        eval_dir=str(eval_dir),
        scenarios_source=scenarios_source,
        runs=runs,
    )
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / submission.RESULTS_FILE).write_text(
        results.model_dump_json(indent=2), encoding="utf-8"
    )
    (eval_dir / REPORT_FILE).write_text(render_markdown(results), encoding="utf-8")
    return results


# --------------------------------------------------------------------------- reports
def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f} %"


def render_markdown(results: EvalResults) -> str:
    """EV-04 report: one table row per scenario, then checks, pending items and S10 tokens."""
    counts = results.counts()
    lines = [
        "# GeoAgent evaluation report (spec §12, S1–S11)",
        "",
        f"Generated: {results.generated_at}  ",
        (
            f"Engine: `{results.engine}` · model: `{results.model}` · scenarios: "
            f"`{results.scenarios_source}`"
        ),
        "",
        "Outcomes: " + ", ".join(f"{name} {count}" for name, count in counts.items()),
        "",
        (
            "| Scenario | Expected | Success | Status | Verdict | Tool calls (stub) | LLM calls | "
            "Valid | Retries | Fallbacks | Overruns | Tokens | Runtime s |"
        ),
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in results.runs:
        m = run.metrics
        lines.append(
            f"| {run.scenario} | {_cell(run.expected)} | {run.outcome.value} | "
            f"{run.status or '-'} | {run.verdict or '-'} | {m.tool_calls} ({m.stub_tool_calls}) | "
            f"{m.llm_calls} | {_pct(m.tool_call_validity_frac)} | {m.retries} | {m.fallbacks} | "
            f"{m.token_budget_overruns} | {m.total_tokens} | {m.runtime_s:.2f} |"
        )
    lines += ["", "## Checks and pending items", ""]
    for run in results.runs:
        lines.append(f"### {run.scenario} — {_cell(run.situation)} ({run.outcome.value})")
        if run.note:
            lines.append(f"- note: {run.note}")
        for check in run.checks:
            mark = "x" if check.passed else " "
            lines.append(f"- [{mark}] {check.expect} — {check.detail}")
        for item in run.pending:
            now = f"; currently: {item.currently}" if item.currently is not None else ""
            promote = " — now passes: promote to checks" if item.now_passes else ""
            lines.append(
                f"- PENDING ({', '.join(item.needs)}): {item.expect} — "
                f"{item.reason}{now}{promote}"
            )
        for conflict in run.conflicts:
            lines.append(f"- conflict noted: {conflict}")
        if run.verify_cost_items:
            lines.append(f"- VERIFY-tagged cost items (RC-05): {', '.join(run.verify_cost_items)}")
        lines.append("")
    for run in results.runs:
        if run.token_comparison:
            lines += _token_table(run)
    lines += [
        (
            "> Stub tools are labelled in each run (`stub_tool_calls`); a run on stubs can never "
            "be ACCEPTED. TestModel output never validates, so its runs show the retry/fallback "
            "path."
        ),
        "",
    ]
    return "\n".join(lines)


def _token_table(run: EvalRun) -> list[str]:
    comparison = run.token_comparison or {}
    lines = [
        f"## {run.scenario} naive vs optimised tokens (MEM-06)",
        "",
        NAIVE_PROFILE_NOTE,
        "",
        "| Phase | Naive tokens | Optimised tokens | Saved |",
        "|---|---:|---:|---:|",
    ]
    by_phase = comparison.get("by_phase", {})
    order = sorted(by_phase, key=lambda name: (len(name), name))  # P0…P9 before P10
    rows = [("total", comparison.get("total", {}))] + [(name, by_phase[name]) for name in order]
    for name, row in rows:
        lines.append(
            f"| {name} | {row.get('naive_tokens', 0):.0f} | {row.get('optimised_tokens', 0):.0f} "
            f"| {_pct(row.get('saved_frac'))} |"
        )
    return lines + [""]


def compare_evals(eval_dirs: Sequence[Path | str], out_dir: Path | str = EVAL_ROOT) -> dict:
    """S11 model comparison of several evals; writes ``model_comparison.json`` and ``.md``."""
    models = []
    for folder in eval_dirs:
        results = EvalResults.model_validate_json(
            (Path(folder) / submission.RESULTS_FILE).read_text(encoding="utf-8")
        )
        ran = [run for run in results.runs if run.outcome != Outcome.SKIPPED]
        total = RunMetrics()
        for run in ran:
            total = total.add(run.metrics)
        models.append(
            {
                "model": results.model,
                "engine": results.engine,
                "eval_dir": str(folder),
                "outcomes": results.counts(),
                "scenarios": {run.scenario: run.outcome.value for run in results.runs},
                "checks_passed": sum(c.passed for run in ran for c in run.checks),
                "checks_total": sum(len(run.checks) for run in ran),
                "tool_call_validity_frac": total.tool_call_validity_frac,
                "llm_calls": total.llm_calls,
                "retries": total.retries,
                "fallbacks": total.fallbacks,
                "step_rejections": total.step_rejections,
                "kind_coercions": total.kind_coercions,
                "token_budget_overruns": total.token_budget_overruns,
                "total_tokens": total.total_tokens,
                "runtime_s": round(total.runtime_s, 3),
            }
        )
    comparison = {"generated_at": datetime.now(UTC).isoformat(), "models": models}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / submission.MODEL_COMPARISON_FILE).write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    (out / MODEL_COMPARISON_MD).write_text(render_comparison(comparison), encoding="utf-8")
    return comparison


def render_comparison(comparison: dict) -> str:
    """Markdown table of the S11 model comparison."""
    lines = [
        "# Model comparison (spec §12 S11)",
        "",
        (
            "| Model | Engine | Pass | Pending | Fail | Checks | Tool-call validity | Retries | "
            "Fallbacks | Overruns | Tokens | Runtime s |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in comparison["models"]:
        o = m["outcomes"]
        lines.append(
            f"| {m['model']} | {m['engine']} | {o.get('pass', 0)} | {o.get('pending', 0)} | "
            f"{o.get('fail', 0) + o.get('error', 0)} | {m['checks_passed']}/{m['checks_total']} | "
            f"{_pct(m['tool_call_validity_frac'])} | {m['retries']} | {m['fallbacks']} | "
            f"{m.get('token_budget_overruns', 0)} | {m['total_tokens']} | {m['runtime_s']:.1f} |"
        )
    return "\n".join(lines) + "\n"
