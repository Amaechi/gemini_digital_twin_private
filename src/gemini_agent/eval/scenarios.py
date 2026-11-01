"""Evaluation scenarios S1–S11 (spec §12 EV-01): the ``evals/scenarios.yaml`` schema and checks.

Every scenario states the spec's expected behaviour and at least one deterministic check of a
finished (or paused/refused) run. Behaviour that needs a later work package is listed under
``pending`` with the work packages it needs and the reason; a pending item with a ``check`` is still
evaluated so the report shows what the run does today, but it never decides the outcome. Nothing is
stubbed silently: a pending item is reported as pending until it is promoted to ``checks``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, JsonValue, model_validator

from gemini_agent.runner.design_runner import RunResult
from gemini_agent.runner.state import InputMode, RunState
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import REPO_ROOT
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
    "summary_contains",
    "conflict_contains",
    "max_doublets",
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
    "files",
    "token_saving",
    "llm_metrics",
]
_REQUIRED: dict[str, tuple[str, ...]] = {
    "status": ("equals",),
    "phase": ("equals",),
    "verdict": ("equals",),
    "phases_started": ("words",),
    "summary_contains": ("contains",),
    "conflict_contains": ("contains",),
    "max_doublets": ("value",),
    "question_mentions": ("words",),
    "assumption_or_question": ("id",),
    "tool_called": ("tool",),
    "tool_calls_max": ("value",),
    "stub_tool_calls_max": ("value",),
    "gap_reported": ("contains",),
    "design_basis_value": ("path", "value"),
    "dr_mentions": ("id", "words"),
    "files": ("files",),
}


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
    extra: dict[str, Any] = field(default_factory=dict)

    def tool_output(self, tool: str) -> dict[str, Any] | None:
        """Full JSON output of the latest successful call of ``tool`` (None if never called)."""
        for record in reversed(self.state.tool_results):
            if record.tool == tool and record.ok and record.path:
                path = self.run_dir / record.path
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
        return None


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


def _max_doublets(check: Check, obs: Observation) -> tuple[bool, str]:
    counts = [candidate.doublets for candidate in obs.state.candidates]
    if not counts:
        return False, "no candidates generated"
    return max(counts) <= float(check.value or 0), f"max {max(counts)} doublets per candidate"


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


_EVALUATORS = {
    "status": _status,
    "phase": _phase,
    "verdict": _verdict,
    "phases_started": _phases_started,
    "summary_contains": _summary_contains,
    "conflict_contains": _conflict_contains,
    "max_doublets": _max_doublets,
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
    "files": _files,
    "token_saving": _token_saving,
    "llm_metrics": _llm_metrics,
}
