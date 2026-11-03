"""Evaluation scenarios S1–S11 (spec §12 EV-01): the ``evals/scenarios.yaml`` schema and checks.

Every scenario states the spec's expected behaviour and at least one deterministic check of a
finished (or paused/refused) run. Behaviour that needs a later work package is listed under
``pending`` with the work packages it needs and the reason; a pending item with a ``check`` is still
evaluated so the report shows what the run does today, but it never decides the outcome. Nothing is
stubbed silently: a pending item is reported as pending until it is promoted to ``checks``.

WP-13 follow-up (2026-11-03): ``cross_run_sizing`` compares a run's well and ESP sizing with
another scenario's run of the same eval directory (S8 vs S1); S2/S5 checks use the P1 intake
requests (``CFG-INPUT-DEFAULTS``, ``CFG-BASIS-CONFIRM``).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, JsonValue, model_validator

from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner import design_runner
from gemini_agent.runner.design_runner import MAX_VERIFICATION_LOOPS, RunResult
from gemini_agent.runner.state import InputMode, RunState
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.decision import DR_TITLES

SCENARIOS_FILE = REPO_ROOT / "evals" / "scenarios.yaml"
EngineKind = Literal["policy", "testmodel", "llm"]
ENGINE_KINDS: tuple[EngineKind, ...] = ("policy", "testmodel", "llm")
TokenProfile = Literal["naive", "optimised"]

CheckType = Literal[
    "status",
    "phase",
    "verdict",
    "phases_started",
    "phases_in_order",
    "loops_within_limit",
    "summary_contains",
    "conflict_contains",
    "within_limits",
    "selected_candidate",
    "decision_records",
    "pending_question",
    "question_mentions",
    "no_assumptions",
    "assumption_or_question",
    "tool_called",
    "tool_calls_max",
    "stub_tool_calls_max",
    "gap_reported",
    "design_basis_value",
    "dr_mentions",
    "tool_output_mentions",
    "files",
    "token_saving",
    "llm_metrics",
    "cross_run_sizing",
]
_REQUIRED: dict[str, tuple[str, ...]] = {
    "status": ("equals",),
    "phase": ("equals",),
    "verdict": ("equals",),
    "phases_started": ("words",),
    "phases_in_order": ("words",),
    "summary_contains": ("contains",),
    "conflict_contains": ("contains",),
    "question_mentions": ("words",),
    "assumption_or_question": ("id",),
    "tool_called": ("tool",),
    "tool_calls_max": ("value",),
    "stub_tool_calls_max": ("value",),
    "gap_reported": ("contains",),
    "design_basis_value": ("path", "value"),
    "dr_mentions": ("id", "words"),
    "tool_output_mentions": ("tool", "words"),
    "files": ("files",),
    "cross_run_sizing": ("baseline",),
}
ESP_PEAK_TOLERANCE_FRAC = 0.05
"""``cross_run_sizing``: the peak ESP electric power must rise by more than this share to count
as an ESP re-sizing (the wells' doublets, depth, flow or ESP stages/setting depth differing
counts on its own)."""


class Check(StrictModel):
    """One deterministic assertion about a run (fields used depend on ``type``)."""

    type: CheckType
    expect: str = Field(min_length=1, description="Expected behaviour in words (report text).")
    equals: str | None = None
    contains: str | None = None
    value: float | None = None
    id: str | None = Field(default=None, description="Assumption id, e.g. 'DB-31', or a DR id.")
    tool: str | None = None
    phase: str | None = None
    path: str | None = Field(default=None, description="Dotted path, e.g. 'reservoir.x_md'.")
    words: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    baseline: str | None = Field(
        default=None,
        description="Scenario id of the run to compare with (same eval directory), e.g. 'S1'.",
    )

    @model_validator(mode="after")
    def _check_args(self) -> Check:
        missing = [
            name for name in _REQUIRED.get(self.type, ()) if getattr(self, name) in (None, [])
        ]
        if missing:
            raise ValueError(f"check '{self.type}' needs {missing}")
        return self


class Pending(StrictModel):
    """Expected behaviour that cannot be asserted yet (needs a later work package)."""

    expect: str = Field(min_length=1)
    needs: list[str] = Field(min_length=1, description="Work packages, e.g. ['WP-07', 'WP-14'].")
    reason: str = Field(min_length=1)
    check: Check | None = Field(
        default=None, description="Evaluated for information only; promote it once it passes."
    )


class ScenarioSetup(StrictModel):
    """How the eval harness prepares a scenario."""

    config_overrides: dict[str, dict[str, JsonValue]] = Field(
        default_factory=dict,
        description="Config name -> nested values merged into a copy of config/ (e.g. S8).",
    )
    remove_components: list[str] = Field(
        default_factory=list, description="COMPONENT_REGISTRY entries removed during the run (S7)."
    )
    token_profiles: list[TokenProfile] = Field(
        default_factory=list,
        description="Run once per profile and compare tokens (S10); empty = one optimised run.",
    )


class Scenario(StrictModel):
    """One spec §12 scenario."""

    id: str = Field(pattern=r"^S\d+$")
    situation: str = Field(min_length=1, description="Spec §12 'Prompt / situation'.")
    expected: str = Field(min_length=1, description="Spec §12 'Expected behaviour'.")
    prompt: str = Field(default="", description="Request text; empty = the default brief.")
    input_mode: InputMode = "assume"
    engines: list[EngineKind] = Field(default_factory=lambda: list(ENGINE_KINDS))
    skip_reason: str = Field(default="", description="Why other engines skip this scenario.")
    setup: ScenarioSetup = Field(default_factory=ScenarioSetup)
    checks: list[Check] = Field(min_length=1)
    pending: list[Pending] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_engines(self) -> Scenario:
        if not self.engines:
            raise ValueError(f"{self.id}: at least one engine")
        if set(self.engines) != set(ENGINE_KINDS) and not self.skip_reason:
            raise ValueError(f"{self.id}: engines {self.engines} need a skip_reason")
        if self.setup.token_profiles and set(self.setup.token_profiles) != {"naive", "optimised"}:
            raise ValueError(f"{self.id}: token_profiles must be [naive, optimised]")
        return self


class ScenarioSuite(StrictModel):
    """``evals/scenarios.yaml``."""

    schema_version: int = 1
    source: str
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> ScenarioSuite:
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate scenario ids in {ids}")
        return self

    def get(self, scenario_id: str) -> Scenario:
        """Scenario by id (KeyError if unknown)."""
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(f"unknown scenario '{scenario_id}'")


def load_scenarios(path: Path | str | None = None) -> ScenarioSuite:
    """Load and validate ``evals/scenarios.yaml`` (or ``path``)."""
    with Path(path or SCENARIOS_FILE).open(encoding="utf-8") as handle:
        return ScenarioSuite.model_validate(yaml.safe_load(handle))


# --------------------------------------------------------------------------- observations
@dataclass
class Observation:
    """What the checks see of one finished run."""

    result: RunResult
    state: RunState
    run_dir: Path
    phases_started: list[str]
    llm_calls: int = 0
    llm_valid_calls: int = 0
    token_comparison: dict[str, Any] | None = None
    config_dir: Path | None = None

    def tool_output(self, tool: str) -> dict[str, Any] | None:
        """Full JSON output of the latest successful call of ``tool`` (None if never called)."""
        return latest_tool_output(self.state, self.run_dir, tool)


def latest_tool_output(
    state: RunState, run_dir: Path, tool: str, candidate_id: str | None = None
) -> dict[str, Any] | None:
    """Full JSON output of the latest successful call of ``tool`` in a run (None if never).

    With ``candidate_id`` the latest call that lists that candidate is preferred (a call that
    names no candidate, e.g. a P2 resource tool, is the fallback).
    """
    records = [r for r in state.tool_results if r.tool == tool and r.ok and r.path]
    if candidate_id is not None:
        tagged = [r for r in records if candidate_id in r.candidate_ids]
        records = tagged or records
    for record in reversed(records):
        path = run_dir / record.path
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def load_run_state(run_dir: Path) -> RunState | None:
    """Persisted state of a finished run directory (None if it holds no run of that name)."""
    path = Path(run_dir) / design_runner.STORE_FILE
    if not path.exists():
        return None
    store = SQLiteEventStore(path)
    try:
        record = store.get_run(Path(run_dir).name)
    finally:
        store.close()
    return RunState.model_validate(record.state) if record is not None else None


@dataclass(frozen=True)
class WellSizing:
    """What ``cross_run_sizing`` compares between two runs (None = not in the run's evidence)."""

    productivity_index_m3h_per_bar: float | None
    doublets: int | None
    depth_m: float | None
    design_flow_m3h: float | None
    esp_stages: int | None
    esp_setting_depth_m: float | None
    esp_peak_electric_mw_by_mode: dict[str, float]

    @property
    def wells(self) -> tuple[int | None, float | None, float | None]:
        """Doublets, depth and design flow of the selected design."""
        return self.doublets, self.depth_m, self.design_flow_m3h

    @property
    def esp_peak_electric_mw(self) -> float | None:
        """Peak ESP electric power of the full year, else of the fast year (None without a CSV)."""
        for mode in ("full", "fast"):
            if mode in self.esp_peak_electric_mw_by_mode:
                return self.esp_peak_electric_mw_by_mode[mode]
        return None

    def describe(self, mode: str | None = None) -> str:
        """One-line summary for the check detail (peak of ``mode``, default full else fast)."""
        depth = "?" if self.esp_setting_depth_m is None else f"{self.esp_setting_depth_m:g} m"
        peak_mw = self.esp_peak_electric_mw_by_mode.get(mode) if mode else self.esp_peak_electric_mw
        peak = "?" if peak_mw is None else f"{peak_mw:.2f} MW ({mode or 'full/fast'} year)"
        return (
            f"{self.doublets} x {self.design_flow_m3h} m3/h @ {self.depth_m} m, ESP "
            f"{self.esp_stages} stages @ {depth}, peak {peak}"
        )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def esp_peak_electric_mw(run_dir: Path, candidate_id: str | None) -> dict[str, float]:
    """Largest daily ESP electric power of a design's annual CSVs, per year mode (full, fast)."""
    peaks: dict[str, float] = {}
    if not candidate_id:
        return peaks
    for mode in ("full", "fast"):
        path = Path(run_dir) / f"annual_{candidate_id}_{mode}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = [
                name
                for name in reader.fieldnames or []
                if name.startswith("elec_esp_") and name.endswith("_mw")
            ]
            values = [
                float(row[name])
                for row in reader
                for name in columns
                if row.get(name) not in (None, "", "nan")
            ]
        if values:
            peaks[mode] = max(values)
    return peaks


def well_sizing(state: RunState, run_dir: Path) -> WellSizing:
    """Well and ESP sizing of a run's selected design from its persisted evidence.

    PI: the latest ``productivity_index`` output (TL-04); ESP stages and setting depth: the latest
    ``doublet_operating_point`` output (TL-05 ``doublet.esp``); wells: the selected candidate;
    peak ESP power: the selected design's annual CSV (TL-12 ``elec_esp_<n>_mw`` columns).
    """
    selected_id = state.selected_id
    pi_output = latest_tool_output(state, run_dir, "productivity_index", selected_id) or {}
    esp = _dotted(
        latest_tool_output(state, run_dir, "doublet_operating_point", selected_id) or {},
        "data.doublet.esp",
    )
    esp = esp if isinstance(esp, dict) else {}
    selected = next((c for c in state.candidates if c.id == selected_id), None)
    stages = esp.get("stages")
    return WellSizing(
        productivity_index_m3h_per_bar=_number(
            _dotted(pi_output, "data.productivity_index_m3h_per_bar")
        ),
        doublets=selected.doublets if selected is not None else None,
        depth_m=selected.depth_m if selected is not None else None,
        design_flow_m3h=selected.design_flow_m3h if selected is not None else None,
        esp_stages=int(stages) if isinstance(stages, int | float) else None,
        esp_setting_depth_m=_number(esp.get("setting_depth_m")),
        esp_peak_electric_mw_by_mode=esp_peak_electric_mw(run_dir, selected_id),
    )


class CheckResult(StrictModel):
    """Outcome of one check."""

    type: str
    expect: str
    passed: bool
    detail: str


class PendingResult(StrictModel):
    """A pending item and, if it has a check, what the run does today."""

    expect: str
    needs: list[str]
    reason: str
    currently: str | None = None
    now_passes: bool | None = None


def _lower(text: str) -> str:
    return " ".join(text.lower().split())


def _dotted(data: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def evaluate(check: Check, obs: Observation) -> CheckResult:
    """Evaluate one check against an observation."""
    passed, detail = _EVALUATORS[check.type](check, obs)
    return CheckResult(type=check.type, expect=check.expect, passed=passed, detail=detail)


def evaluate_pending(item: Pending, obs: Observation) -> PendingResult:
    """Report a pending item; its check (if any) is informational."""
    if item.check is None:
        return PendingResult(expect=item.expect, needs=item.needs, reason=item.reason)
    outcome = evaluate(item.check, obs)
    return PendingResult(
        expect=item.expect,
        needs=item.needs,
        reason=item.reason,
        currently=outcome.detail,
        now_passes=outcome.passed,
    )


def _status(check: Check, obs: Observation) -> tuple[bool, str]:
    status = obs.result.status.value
    return status.lower() == str(check.equals).lower(), f"status {status}"


def _phase(check: Check, obs: Observation) -> tuple[bool, str]:
    return obs.result.phase.value == check.equals, f"phase {obs.result.phase.value}"


def _verdict(check: Check, obs: Observation) -> tuple[bool, str]:
    verdict = obs.result.verdict
    return verdict == check.equals, f"verdict {verdict or 'none'}"


def _phases_started(check: Check, obs: Observation) -> tuple[bool, str]:
    return obs.phases_started == check.words, f"phases {obs.phases_started}"


def _summary_contains(check: Check, obs: Observation) -> tuple[bool, str]:
    found = _lower(str(check.contains)) in _lower(obs.result.summary)
    return found, f"summary: {obs.result.summary[:120]}"


def _conflict_contains(check: Check, obs: Observation) -> tuple[bool, str]:
    hits = [c for c in obs.state.conflicts if _lower(str(check.contains)) in _lower(c)]
    return bool(hits), f"{len(obs.state.conflicts)} conflict(s); matching: {hits[:1] or 'none'}"


def _phases_in_order(check: Check, obs: Observation) -> tuple[bool, str]:
    """Check each listed phase starts, first starts in the listed order (loop-backs allowed)."""
    firsts = list(dict.fromkeys(phase for phase in obs.phases_started if phase in check.words))
    return firsts == check.words, f"first starts {firsts}; {len(obs.phases_started)} starts"


def _loops_within_limit(check: Check, obs: Observation) -> tuple[bool, str]:
    """Check verifier loop-backs stay within the runner limit (spec §5.2 P7: ≤ 3)."""
    loops = obs.state.loop_count
    ok = loops <= MAX_VERIFICATION_LOOPS
    return ok, f"{loops} loop-back(s) (limit {MAX_VERIFICATION_LOOPS})"


def _within_limits(check: Check, obs: Observation) -> tuple[bool, str]:
    """Check every geothermal candidate respects DB-10 doublets and DB-11 flow (limits.yaml)."""
    limits = load_config("limits", obs.config_dir)
    candidates = [c for c in obs.state.candidates if c.doublets > 0]
    if not candidates:
        return False, "no geothermal candidates generated"
    bad = [
        c.id
        for c in candidates
        if not limits.doublet_count_min <= c.doublets <= limits.doublet_count_max
        or (
            c.design_flow_m3h is not None
            and not limits.flow_min_active_m3h <= c.design_flow_m3h <= limits.flow_max_m3h
        )
    ]
    flows = [c.design_flow_m3h for c in candidates if c.design_flow_m3h is not None]
    detail = (
        f"{len(candidates)} candidates; doublets ≤ {max(c.doublets for c in candidates)}, "
        f"flows {'≤ ' + format(max(flows), 'g') + ' m3/h' if flows else 'not sized yet'}; "
        f"outside limits: {bad or 'none'}"
    )
    return not bad, detail


def _selected_candidate(check: Check, obs: Observation) -> tuple[bool, str]:
    selected = obs.state.selected_id
    ok = selected is not None and selected not in obs.state.excluded_ids()
    return ok, f"selected {selected or 'none'}"


def _decision_records(check: Check, obs: Observation) -> tuple[bool, str]:
    present = {record.dr_id for record in obs.state.decision_records}
    missing = sorted(set(DR_TITLES) - present)
    return not missing, f"{len(present)} DRs; missing {missing or 'none'}"


def _pending_question(check: Check, obs: Observation) -> tuple[bool, str]:
    pending = obs.result.pending
    if pending is None:
        return False, "no pending question"
    ok = check.id is None or pending.assumption_id == check.id
    return ok, f"asks [{pending.assumption_id or pending.request_id}]: {pending.question[:100]}"


def _question_mentions(check: Check, obs: Observation) -> tuple[bool, str]:
    pending = obs.result.pending
    text = _lower(pending.question) if pending else ""
    missing = [word for word in check.words if word.lower() not in text]
    return bool(pending) and not missing, f"question misses {missing or 'nothing'}"


def _no_assumptions(check: Check, obs: Observation) -> tuple[bool, str]:
    labels = [answer.label for answer in obs.state.answers]
    return not labels, f"{len(labels)} assumption(s)/answer(s) recorded"


def _assumption_or_question(check: Check, obs: Observation) -> tuple[bool, str]:
    recorded = [a.label for a in obs.state.answers if a.assumption_id == check.id]
    pending = obs.result.pending
    asked = pending is not None and pending.assumption_id == check.id
    if recorded:
        return True, f"recorded: {recorded[-1]}"
    return asked, f"asked {check.id}" if asked else f"{check.id} neither asked nor recorded"


def _tool_called(check: Check, obs: Observation) -> tuple[bool, str]:
    calls = [
        record
        for record in obs.state.tool_results
        if record.tool == check.tool and (check.phase is None or record.phase.value == check.phase)
    ]
    where = f" in {check.phase}" if check.phase else ""
    return bool(calls), f"{check.tool} called {len(calls)}x{where}"


def _tool_calls_max(check: Check, obs: Observation) -> tuple[bool, str]:
    count = len(obs.state.tool_results)
    return count <= float(check.value or 0), f"{count} tool call(s)"


def _stub_tool_calls_max(check: Check, obs: Observation) -> tuple[bool, str]:
    stubs = sum(1 for record in obs.state.tool_results if record.stub)
    return stubs <= float(check.value or 0), f"{stubs}/{len(obs.state.tool_results)} stub calls"


def _gap_reported(check: Check, obs: Observation) -> tuple[bool, str]:
    output = obs.tool_output("model_gap_analysis")
    if output is None:
        return False, "model_gap_analysis not called"
    missing = _dotted(output, "data.missing_component_types") or []
    return check.contains in missing, f"gaps reported: {missing or 'none'}"


def _design_basis_value(check: Check, obs: Observation) -> tuple[bool, str]:
    output = obs.tool_output("load_design_basis")
    if output is None:
        return False, "load_design_basis not called"
    value = _dotted(output, f"data.design_basis.{check.path}")
    ok = isinstance(value, int | float) and abs(float(value) - float(check.value or 0)) < 1e-9
    return ok, f"{check.path} = {value}"


def _dr_mentions(check: Check, obs: Observation) -> tuple[bool, str]:
    record = next((r for r in obs.state.decision_records if r.dr_id == check.id), None)
    if record is None:
        return False, f"{check.id} not written"
    text = _lower(record.model_dump_json())
    missing = [word for word in check.words if word.lower() not in text]
    return not missing, f"{check.id} misses {missing or 'nothing'}"


def _tool_output_mentions(check: Check, obs: Observation) -> tuple[bool, str]:
    """WP-14: the latest ``tool`` output (at dotted ``path``, e.g. ``data.cases``) names words."""
    output = obs.tool_output(str(check.tool))
    if output is None:
        return False, f"{check.tool} not called"
    part = _dotted(output, check.path) if check.path else output
    text = _lower(json.dumps(part, default=str))
    missing = [word for word in check.words if word.lower() not in text]
    where = f"{check.tool}.{check.path}" if check.path else str(check.tool)
    return not missing, f"{where} misses {missing or 'nothing'}"


def _files(check: Check, obs: Observation) -> tuple[bool, str]:
    missing = [name for name in check.files if not (obs.run_dir / name).exists()]
    return not missing, f"missing {missing or 'none'}"


def _token_saving(check: Check, obs: Observation) -> tuple[bool, str]:
    comparison = obs.token_comparison
    if not comparison:
        return False, "no naive-vs-optimised comparison"
    total = comparison.get("total", {})
    naive, optimised = total.get("naive_tokens", 0), total.get("optimised_tokens", 0)
    ok = naive > 0 and optimised <= naive
    return ok, f"naive {naive:.0f} vs optimised {optimised:.0f} tokens"


def _llm_metrics(check: Check, obs: Observation) -> tuple[bool, str]:
    if not obs.llm_calls:
        return False, "no LLM calls logged"
    return True, f"{obs.llm_valid_calls}/{obs.llm_calls} LLM calls used without fallback"


def _cross_run_sizing(check: Check, obs: Observation) -> tuple[bool, str]:
    """S8: the selected design is re-sized against ``baseline`` (same eval dir) at a lower PI.

    Passes when the baseline's productivity index is higher and the wells are re-sized for it
    (more doublets, a lower design flow per doublet, or another depth) or the ESP is larger
    (more stages, a deeper setting depth, or a peak electric power more than
    ``ESP_PEAK_TOLERANCE_FRAC`` above the baseline's, both peaks from the same year mode).
    Fails, with the reason, when the baseline run is missing from the eval directory or either
    run's evidence has no PI (stub tools).
    """
    baseline_dir = obs.run_dir.parent / str(check.baseline)
    base_state = load_run_state(baseline_dir)
    if base_state is None:
        return False, (
            f"baseline {check.baseline} not found in {obs.run_dir.parent}; run both scenarios "
            "in the same eval"
        )
    this, base = well_sizing(obs.state, obs.run_dir), well_sizing(base_state, baseline_dir)
    pi_this, pi_base = this.productivity_index_m3h_per_bar, base.productivity_index_m3h_per_bar
    if pi_this is None or pi_base is None:
        return False, "productivity_index output has no PI in this run or the baseline (stubs?)"
    modes = [
        m
        for m in ("full", "fast")
        if m in this.esp_peak_electric_mw_by_mode and m in base.esp_peak_electric_mw_by_mode
    ]
    mode = modes[0] if modes else None
    detail = (
        f"PI {pi_this:.2f} vs {check.baseline} {pi_base:.2f} m3/h/bar; design "
        f"{this.describe(mode)} vs {base.describe(mode)}"
    )
    if not pi_this < pi_base:
        return False, f"no lower PI than {check.baseline}: {detail}"
    wells_changed = (
        (this.doublets is not None and base.doublets is not None and this.doublets > base.doublets)
        or (
            this.design_flow_m3h is not None
            and base.design_flow_m3h is not None
            and this.design_flow_m3h < base.design_flow_m3h
        )
        or (this.depth_m is not None and base.depth_m is not None and this.depth_m != base.depth_m)
    )
    peak_this = this.esp_peak_electric_mw_by_mode.get(mode) if mode else None
    peak_base = base.esp_peak_electric_mw_by_mode.get(mode) if mode else None
    esp_larger = (
        (
            this.esp_stages is not None
            and base.esp_stages is not None
            and this.esp_stages > base.esp_stages
        )
        or (
            this.esp_setting_depth_m is not None
            and base.esp_setting_depth_m is not None
            and this.esp_setting_depth_m > base.esp_setting_depth_m
        )
        or (
            peak_this is not None
            and peak_base is not None
            and peak_this > (1.0 + ESP_PEAK_TOLERANCE_FRAC) * peak_base
        )
    )
    how = (
        "wells re-sized"
        if wells_changed
        else "ESP re-sized" if esp_larger else "neither the wells nor the ESP grew"
    )
    return wells_changed or esp_larger, f"{how}: {detail}"


_EVALUATORS = {
    "status": _status,
    "phase": _phase,
    "verdict": _verdict,
    "phases_started": _phases_started,
    "summary_contains": _summary_contains,
    "conflict_contains": _conflict_contains,
    "phases_in_order": _phases_in_order,
    "loops_within_limit": _loops_within_limit,
    "within_limits": _within_limits,
    "selected_candidate": _selected_candidate,
    "decision_records": _decision_records,
    "pending_question": _pending_question,
    "question_mentions": _question_mentions,
    "no_assumptions": _no_assumptions,
    "assumption_or_question": _assumption_or_question,
    "tool_called": _tool_called,
    "tool_calls_max": _tool_calls_max,
    "stub_tool_calls_max": _stub_tool_calls_max,
    "gap_reported": _gap_reported,
    "design_basis_value": _design_basis_value,
    "dr_mentions": _dr_mentions,
    "tool_output_mentions": _tool_output_mentions,
    "files": _files,
    "token_saving": _token_saving,
    "llm_metrics": _llm_metrics,
    "cross_run_sizing": _cross_run_sizing,
}
