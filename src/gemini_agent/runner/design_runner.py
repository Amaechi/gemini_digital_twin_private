# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/runner/incident_runner.py @ d69507a. Modified by
# GeoAgent team, 2026-11-01: incident loop replaced by the deterministic phase machine P0–P10 with
# per-phase step/tool/token budgets, verifier-gated loops (<= 3), request_missing_input
# pause/resume, Decision Records and run artefacts; synchronous; kept _build_context /
# _compact_payload / _prior_action_signatures ideas. 2026-11-02 (L-1 follow-up): duplicate
# tool calls rejected by signature, step-kind coercions logged, DR choices checked on criteria.
# 2026-11-02 (pre-L-2): phase workflow tools (P8 robust_backup) and DR-11, whose choice selects
# the installed design for P9. 2026-11-02 (L-1 P0 follow-up): P0 accepts only finalise/refuse and
# closes from the deterministic intake when its step budget runs out (never escalates in scope).
# 2026-11-03 (WP-13 follow-up, S2/S5): P1 intake requests — confirm a supply-temperature conflict
# with the basis (CFG-BASIS-CONFIRM) and list the inputs the request does not provide
# (CFG-INPUT-DEFAULTS) — asked once, before any tool runs or default is assumed. 2026-11-03
# (runner J): a Decision Record call past the phase token budget records TOKEN_BUDGET_OVERRUN
# instead of escalating mid-DR (the engine floors its usage limit; past the call's own ceiling
# the policy chooses, a counted fallback); decisions keep AG-04. 2026-11-03 (review loop): P9b
# user review — objection → one bounded what-if lever (engine) → what-if (tools) → adopt (P6–P9
# rerun on the loop-back path, TL-13 sized from the adopted design) or keep; DR-12. 2026-11-04
# (voice dialogue): ``adopt_whatif`` applies that adoption path to a completed run.
"""Workflow runner: deterministic phases P0–P10 (spec §4.1, §5.2).

The runner decides WHICH phase runs, enforces budgets (AG-04), executes every tool call,
persists state after each step (AG-10) and copies the verifier's verdict — it never creates an
ACCEPTED verdict itself (AG-11). Engines only choose steps inside a phase and options of
Decision Records. ``--engine policy`` runs the whole pipeline without an LLM (AG-12).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gemini_agent.agents.engine import (
    DecisionEngine,
    EngineBudgetExceeded,
    EngineResult,
    PolicyEngine,
    check_choice,
    choice_deps,
)
from gemini_agent.agents.roles import ROLES
from gemini_agent.agents.scope import (
    DEFAULT_BRIEF,
    constraint_conflicts,
    missing_request_inputs,
    refusal_message,
    scope_check,
)
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner import review as review_mod
from gemini_agent.runner.decision_records import (
    build_question,
    installed_design,
    make_record,
    result_keys,
    write_records,
)
from gemini_agent.runner.phases import (
    PHASE_SPECS,
    SELECTED,
    PhaseBudget,
    PhaseSpec,
    next_phase,
)
from gemini_agent.runner.state import (
    InputMode,
    ReviewState,
    RobustBackup,
    RunState,
    ToolRecord,
    Verification,
)
from gemini_agent.runner.toolbox import Toolbox, ToolContext, default_toolbox
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.decision import (
    PHASE_TITLES,
    ArgValue,
    DecisionContext,
    DecisionSource,
    DesignVerdict,
    InputAnswer,
    InputRequest,
    LeverChoice,
    NextStep,
    Phase,
    Role,
    RunStatus,
    check_lever_choice,
    states_numbers,
)
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import whatif as whatif_tool

RUNS_ROOT = REPO_ROOT / "runs"
STORE_FILE = "events.sqlite"
TOKEN_FIGURE_TOOL = "plot_figures"
"""P10 tool that draws ``figures/fig_tokens.png``; an interim ``token_summary.json`` precedes it."""
TOKENS_FIGURE = "fig_tokens.png"
MAX_VERIFICATION_LOOPS = 3
"""Spec §5.2 P7: FAIL ⇒ violations back to P6 (≤ 3 loops), then escalate."""
HISTORY_EVENTS = 12
"""Working memory: last 12–14 compacted events (spec §10.2)."""
MAX_DUPLICATE_REJECTIONS = 2
"""Duplicate tool calls rejected in one phase pass before the runner ends the engine's turn
(rule 4; the phase then closes deterministically, running any required tool still open)."""
_CONSTANT_CONTEXT = frozenset({"run_id", "run_dir", "config_dir"})
"""``ToolContext`` fields that never change within a run (left out of the fingerprint)."""
APPROVAL_ID = "APPROVAL"
BASIS_CONFIRM_ID = "CFG-BASIS-CONFIRM"
"""Register entry behind the S5 question: a requested supply temperature that is not DB-16/DB-17
is asked to be confirmed in P1; the default ``keep`` continues on the design basis, ``change``
stops the run (the basis is changed in ``config/`` and a new run started)."""
INPUT_DEFAULTS_ID = "CFG-INPUT-DEFAULTS"
"""Register entry behind the S2 question: the challenge inputs a request does not provide are
listed in ONE P1 question; the default uses the repository inputs (config/, data/demand/)."""
_BASIS_CONFLICT_MARKS = ("(DB-16)", "(DB-17)")
"""Conflict texts (``agents/scope.constraint_conflicts``) that need the confirm-basis question."""
_KEEP = frozenset({"keep", "default"})
_CHANGE = frozenset({"change"})
"""Only the two words the question offers: 'yes'/'no' would be ambiguous for "confirm the change
of basis?" (yes = change it?) and are rejected with the question repeated (reviewer finding)."""
_DEFAULTS = frozenset(
    {"defaults", "default", "repository", "repository defaults", "y", "yes", "ok"}
)
P0_STEP_KINDS = frozenset({"finalise", "refuse"})
"""The only step kinds P0 accepts (L-1 P0 follow-up): the runner's intake already decided scope,
so an agent can only confirm it (``finalise``) or refuse an off-topic request. Any tool or other
kind is rejected with STEP_REJECTED feedback, and when the step budget runs out the phase closes
from the intake result (``INTAKE_CLOSED``) instead of escalating."""
LOOP_BACK_PHASES = (Phase.P6, Phase.P7, Phase.P8, Phase.P9, Phase.P10)
"""Phases re-run after a REJECTED verification."""
_HISTORY_TYPES = frozenset(
    {
        "DECISION",
        "TOOL_RESULT",
        "TOOL_ERROR",
        "STEP_REJECTED",
        "USER_ANSWER",
        "ASSUMPTION",
        "CONFLICT",
        "LOOP_BACK",
        "DECISION_RECORD",
        "DESIGN_SELECTED",
    }
)
_REVIEWER_HISTORY_TYPES = frozenset({"TOOL_RESULT", "TOOL_ERROR"})
_YES = frozenset({"y", "yes", "approve", "approved"})
_NO = frozenset({"n", "no", "reject", "rejected"})

Outcome = Literal["continue", "finalise", "paused", "stopped", "done"]
AskFn = Callable[[InputRequest], str | None]
ReviewOutcome = Literal["continue", "paused", "adopted"]
"""Review loop (P9b): keep the design and go on, wait for the user, or rerun P6–P9."""


def _db30_interpretation_b(default: dict[str, Any]) -> dict[str, Any]:
    """DB-30 interpretation B (spec §2.4): simulate only the available days, with no outage.

    Derived from the register's interpretation-A entry so no number is invented: the simulated
    year is ``doublet_available_days_per_yr`` days long and no doublet has a planned outage.
    """
    return {
        **default,
        "interpretation": "B",
        "planned_outage_days_per_doublet": 0,
        "outage_schedule": "none (interpretation B: no planned outage)",
        "demand_met_days_per_yr": default["doublet_available_days_per_yr"],
    }


class InvalidAnswer(ValueError):
    """An answer to a pending question failed validation (the run stays paused)."""


@dataclass
class RunResult:
    """What the CLI reports after ``start`` or ``resume``."""

    run_id: str
    status: RunStatus
    phase: Phase
    verdict: str | None
    selected_id: str | None
    summary: str
    run_dir: Path
    pending: InputRequest | None = None
    decision_records: list[str] = field(default_factory=list)
    approval: str | None = None


def _compact_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only what an engine needs from an event (Plant Debugger ``_compact_payload``)."""
    if event_type == "DECISION":
        return {
            "source": payload.get("source"),
            "steps": [
                {"kind": step.get("kind"), "tool": step.get("tool"), "args": step.get("args")}
                for step in payload.get("steps", [])
            ],
        }
    if event_type in {"TOOL_RESULT", "TOOL_ERROR"}:
        return {
            key: payload.get(key) for key in ("id", "tool", "summary", "error") if payload.get(key)
        }
    if event_type == "DECISION_RECORD":
        return {"dr_id": payload.get("dr_id"), "choice": payload.get("choice")}
    return {"message": payload.get("message")}


class DesignRunner:
    """Bounded, persisted, verifier-gated workflow over phases P0–P10."""

    def __init__(
        self,
        engine: DecisionEngine,
        *,
        runs_root: Path = RUNS_ROOT,
        toolbox: Toolbox | None = None,
        config_dir: Path | None = None,
        budgets: dict[Phase, PhaseBudget] | None = None,
        ask_fn: AskFn | None = None,
        max_verification_loops: int = MAX_VERIFICATION_LOOPS,
        history_events: int | None = HISTORY_EVENTS,
        compact_history: bool = True,
        review: bool = False,
        review_budget: str = whatif_tool.DEFAULT_BUDGET,
    ) -> None:
        """Configure the runner; ``budgets`` override the phase table per phase.

        ``history_events`` (None = all) and ``compact_history`` are the MEM-06 context switches;
        the defaults are the optimised settings (WP-13 S10 runs the naive ones for comparison).
        ``review`` (``cli run --review``) adds the P9b user review; ``review_budget`` is the
        what-if budget of that review (``tools.whatif.BUDGETS``; default quick = full year).
        """
        if review_budget not in whatif_tool.BUDGETS:
            raise ValueError(f"review_budget must be one of {whatif_tool.BUDGETS}")
        self.review = review
        self.review_budget = review_budget
        self.engine = engine
        self.runs_root = Path(runs_root)
        self.toolbox = toolbox or default_toolbox()
        self.config_dir = config_dir
        self.budgets = budgets or {}
        self.ask_fn = ask_fn
        self.max_verification_loops = max_verification_loops
        self.history_events = history_events
        self.compact_history = compact_history
        self._policy = PolicyEngine()
        self._stores: dict[str, SQLiteEventStore] = {}

    # ------------------------------------------------------------------ public API
    def run_dir(self, run_id: str) -> Path:
        """Output directory of a run (``runs/<run_id>/``)."""
        return self.runs_root / run_id

    def store(self, run_id: str) -> SQLiteEventStore:
        """Return the SQLite event store of a run."""
        if run_id not in self._stores:
            self._stores[run_id] = SQLiteEventStore(self.run_dir(run_id) / STORE_FILE)
        return self._stores[run_id]

    def close(self) -> None:
        """Close all open stores and the engine (its HTTP clients and event loop)."""
        for store in self._stores.values():
            store.close()
        self._stores.clear()
        close = getattr(self.engine, "close", None)
        if callable(close):
            close()

    def start(
        self,
        prompt: str,
        *,
        run_id: str,
        input_mode: InputMode = "assume",
        model_tag: str = "",
    ) -> RunResult:
        """Start a new run from the user's request (empty → the default challenge brief).

        ``model_tag`` (the CLI ``--model`` override) is stored so ``resume`` reuses it.
        """
        store = self.store(run_id)
        if store.get_run(run_id) is not None:
            raise ValueError(f"run '{run_id}' already exists; use resume")
        state = RunState(
            run_id=run_id,
            user_prompt=prompt.strip() or DEFAULT_BRIEF,
            engine=self.engine.name,
            input_mode=input_mode,
            model_tag=model_tag,
            review_enabled=self.review,
            review_budget=self.review_budget if self.review else "",
        )
        store.create_run(run_id, state.status.value, state.model_dump(mode="json"))
        payload: dict[str, Any] = {"engine": self.engine.name, "input_mode": input_mode}
        if self.review:  # events of a run without --review are unchanged
            payload["review"] = True
        self._event(state, "RUN_START", payload)
        return self._loop(state)

    def load_state(self, run_id: str) -> RunState:
        """Load the persisted state of a run (KeyError if unknown)."""
        record = self.store(run_id).get_run(run_id)
        if record is None:
            raise KeyError(f"unknown run '{run_id}'")
        return RunState.model_validate(record.state)

    def resume(self, run_id: str, answer: str | None) -> RunResult:
        """Answer the pending question of a paused run and continue (AG-05)."""
        state = self.load_state(run_id)
        if state.status != RunStatus.PAUSED or state.pending is None:
            raise ValueError(f"run '{run_id}' is {state.status.value}, not paused")
        request = state.pending
        if request.request_id.startswith(review_mod.REVIEW_PREFIX):
            # Review loop: the objection text (Enter = none) or the adopt yes/no.
            self._apply_review_answer(state, request, "" if answer is None else str(answer))
        else:
            if answer is None or not str(answer).strip():
                raise InvalidAnswer(f"an answer is required: {state.pending.question}")
            self._apply_answer(state, request, str(answer))
        state.pending = None
        state.status = RunStatus.RUNNING
        self._event(state, "RESUME", {"request_id": request.request_id, "engine": self.engine.name})
        return self._loop(state)

    # ------------------------------------------------------------------ main loop
    def _loop(self, state: RunState) -> RunResult:
        while state.status == RunStatus.RUNNING:
            spec = PHASE_SPECS[state.phase]
            if self._run_phase(state, spec) != "done":
                break
            following = self._gate(state, spec)
            if state.status != RunStatus.RUNNING:
                break
            if following is None:
                self._complete(state)
                break
            state.phase = following
            self._save(state)
        self._save(state)
        self._write_outputs(state)
        return self._result(state)

    def _result(self, state: RunState) -> RunResult:
        return RunResult(
            run_id=state.run_id,
            status=state.status,
            phase=state.phase,
            verdict=state.verdict_value(),
            selected_id=state.selected_id,
            summary=state.final_summary,
            run_dir=self.run_dir(state.run_id),
            pending=state.pending,
            decision_records=[record.dr_id for record in state.decision_records],
            approval=state.approval,
        )

    def result(self, run_id: str) -> RunResult:
        """The ``RunResult`` of a stored run as ``start``/``resume`` reported it (read-only)."""
        return self._result(self.load_state(run_id))

    def _budget(self, spec: PhaseSpec) -> PhaseBudget:
        return self.budgets.get(spec.phase, spec.budget)

    def _run_phase(self, state: RunState, spec: PhaseSpec) -> Outcome:
        prog = state.phase_progress(spec.phase)
        budget = self._budget(spec)
        if not prog.started:
            prog.started = True
            self._event(
                state,
                "PHASE_START",
                {
                    "title": PHASE_TITLES[spec.phase],
                    "role": spec.role.value,
                    "loop": state.loop_count,
                },
            )
            if spec.phase == Phase.P0:
                self._intake(state)
        if spec.phase == Phase.P1 and not prog.engine_done:
            # WP-13 follow-up (S5, S2): the intake's questions come before any P1 tool or default.
            outcome = self._intake_requests(state, spec)
            if outcome in ("paused", "stopped"):
                self._save(state)
                return outcome
        while not prog.engine_done:
            if prog.steps_used >= budget.max_steps:
                outcome = self._out_of_steps(state, spec, budget)
                if outcome is not None:
                    return outcome
                break
            context = self._context(state, spec)
            try:
                result = self.engine.decide(context)
            except EngineBudgetExceeded as exc:
                prog.tokens_used += exc.input_tokens + exc.output_tokens
                return self._escalate(
                    state, f"LLM usage limit exceeded in {spec.phase.value}: {exc}"
                )
            prog.decisions += 1
            prog.tokens_used += result.input_tokens + result.output_tokens
            prog.fallbacks += int(result.source == "policy_fallback")
            self._event(
                state,
                "DECISION",
                {
                    "source": result.source,
                    "steps": [step.model_dump(mode="json") for step in result.value.steps],
                    "open_questions": result.value.open_questions,
                    "retries": result.retries,
                    "error": result.error,
                },
            )
            for coercion in result.kind_coercions:
                # Counted by the eval harness (kind_coercions) like STEP_REJECTED.
                self._event(
                    state,
                    "KIND_COERCED",
                    {
                        **coercion,
                        "message": f"{coercion['tool']}: step kind {coercion['from_kind']} "
                        f"coerced to {coercion['to_kind']}",
                    },
                )
            if prog.tokens_used > budget.max_tokens:
                return self._escalate(
                    state,
                    f"token budget exceeded in {spec.phase.value} "
                    f"({prog.tokens_used} > {budget.max_tokens})",
                )
            for step in result.value.steps:
                if prog.steps_used >= budget.max_steps:
                    outcome = self._out_of_steps(state, spec, budget)
                    if outcome is not None:
                        return outcome
                    break
                prog.steps_used += 1
                outcome = self._execute_step(state, spec, step, result.source)
                if outcome in ("paused", "stopped"):
                    self._save(state)
                    return outcome
                if outcome == "finalise":
                    prog.engine_done = True
                    break
            self._save(state)
        return self._close_phase(state, spec)

    def _out_of_steps(
        self, state: RunState, spec: PhaseSpec, budget: PhaseBudget
    ) -> Outcome | None:
        """Step budget used up: escalate (AG-04), except in P0, which closes from the intake.

        L-1 P0 follow-up: three of five models spent P0's steps on ``delegate`` or a tool call and
        were escalated although the deterministic scope check had passed. The intake result, not
        the agent's wording, decides P0: the runner records ``INTAKE_CLOSED``, ends the engine's
        turn and ``_close_phase`` continues to P1 (in scope) or refuses (off-topic). Returns None
        when the phase was closed this way.
        """
        reason = f"step budget exceeded in {spec.phase.value} ({budget.max_steps} steps)"
        if spec.phase != Phase.P0:
            return self._escalate(state, reason)
        prog = state.phase_progress(spec.phase)
        prog.engine_done = True
        verdict = "in scope, the workflow continues" if state.in_scope else "off-topic, refused"
        self._event(
            state,
            "INTAKE_CLOSED",
            {
                "in_scope": state.in_scope,
                "steps_used": prog.steps_used,
                "message": f"{reason} without finalise or refuse; the intake scope check "
                f"decides: {verdict} ({state.scope_reason})",
            },
        )
        return None

    def _close_phase(self, state: RunState, spec: PhaseSpec) -> Outcome:
        """Deterministic completion (spec §5.4.6) and Decision Records of the phase."""
        prog = state.phase_progress(spec.phase)
        if spec.phase == Phase.P0 and not state.in_scope:
            return self._refuse(state, state.scope_reason)
        for tool in spec.required_tools:
            if tool in prog.completed_tools:
                continue
            args = self._default_args(state, spec, tool)
            if self._call_tool(state, spec, tool, args, "workflow") == "stopped":
                return "stopped"
            if tool not in prog.completed_tools:
                return self._escalate(state, f"required tool {tool} failed in {spec.phase.value}")
        if spec.phase == Phase.P1:
            # Gaps the agent did not ask about still go through the input mode (AG-05).
            for gap in list(state.open_gaps):
                request = self._gap_request(state, spec.phase, gap)
                outcome = self._handle_request(state, request)
                if outcome == "paused":
                    return "paused"
        for tool in spec.workflow_tools:
            # Pre-L-2: the runner's own deterministic steps (outside the agent's AG-04 budget).
            if tool in prog.completed_tools:
                continue
            args = self._default_args(state, spec, tool)
            if self._call_tool(state, spec, tool, args, "workflow", budgeted=False) == "stopped":
                return "stopped"
            if tool not in prog.completed_tools:
                return self._escalate(state, f"workflow step {tool} failed in {spec.phase.value}")
        for dr_id in spec.decision_records:
            if any(record.dr_id == dr_id for record in state.decision_records):
                continue
            if self._decision_record(state, spec, dr_id) == "stopped":
                return "stopped"
        self._save(state)
        return "done"

    # ------------------------------------------------------------------ steps
    def _execute_step(
        self, state: RunState, spec: PhaseSpec, step: NextStep, source: DecisionSource
    ) -> Outcome:
        if spec.phase == Phase.P0 and step.kind not in P0_STEP_KINDS:
            expected = "finalise" if state.in_scope else "refuse"
            return self._reject_step(
                state,
                step,
                "in P0 the only valid steps are finalise (in scope) or refuse (off-topic); the "
                f"intake scope check expects {expected} here; no tool runs in intake",
            )
        role_tools = ROLES[spec.role].tools
        if step.tool is not None and step.tool not in role_tools:
            return self._reject_step(state, step, f"tool not in the {spec.role.value} tool list")
        if step.kind == "refuse":
            if spec.phase != Phase.P0:
                return self._reject_step(state, step, "refuse is only allowed in P0")
            if state.in_scope:
                return self._reject_step(state, step, "the scope check found the request in scope")
            return self._refuse(state, step.rationale)
        if step.kind == "escalate":
            return self._escalate(state, f"agent escalated in {spec.phase.value}: {step.rationale}")
        if step.kind == "finalise":
            return "finalise"
        if step.kind == "delegate":
            self._event(state, "DELEGATE", {"args": step.args, "message": step.rationale})
            return "continue"
        if step.kind == "request_input":
            gap = step.args.get("assumption_id")
            if not isinstance(gap, str) or gap not in state.open_gaps:
                return self._reject_step(state, step, f"'{gap}' is not an open gap")
            question = step.args.get("question")
            request = self._gap_request(state, spec.phase, gap, question)
            return self._handle_request(state, request)
        assert step.tool is not None  # guaranteed by NextStep validation
        duplicate = self._duplicate_of(state, spec, step.tool, dict(step.args))
        if duplicate is not None:
            prog = state.phase_progress(spec.phase)
            prog.duplicate_rejections += 1
            reason = (
                f"duplicate call: {step.tool} already ran with these arguments on the same run "
                f"state as {duplicate.id} in {duplicate.phase.value}; use that result from the "
                "history or choose another step (rule 4)"
            )
            if prog.duplicate_rejections >= MAX_DUPLICATE_REJECTIONS:
                self._reject_step(
                    state, step, f"{reason}; repeated, so the runner closes the phase"
                )
                return "finalise"
            return self._reject_step(state, step, reason)
        return self._call_tool(state, spec, step.tool, dict(step.args), source)

    def _context_fingerprint(self, state: RunState) -> str:
        """Hash of the run state a tool can read (``ToolContext`` minus its constant fields)."""
        context = self._tool_context(state)
        payload = {
            item.name: getattr(context, item.name)
            for item in dataclasses.fields(context)
            if item.name not in _CONSTANT_CONTEXT
        }

        def encode(value: Any) -> Any:
            return value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value)

        text = json.dumps(payload, sort_keys=True, default=encode)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _signature(self, state: RunState, tool: str, args: dict[str, ArgValue]) -> str:
        """Tool + arguments normalised by its own schema + fingerprint of the state it reads.

        Tools are deterministic in (arguments, ``ToolContext``): the same signature gives the
        same result, while a call after the state changed (new candidates, KPIs, answers, …) is
        a new call.
        """
        resolved = self._resolve_args(state, args)
        try:
            normalised = self.toolbox.validate_args(tool, resolved).model_dump(mode="json")
        except (KeyError, ValueError):
            normalised = resolved
        arguments = json.dumps(normalised, sort_keys=True, default=str)
        return f"{tool}:{arguments}@{self._context_fingerprint(state)}"

    def _duplicate_of(
        self, state: RunState, spec: PhaseSpec, tool: str, args: dict[str, ArgValue]
    ) -> ToolRecord | None:
        """Earlier call with the same signature, if the step repeats it (rule 4).

        Only engine-proposed steps are checked; a failed call counts too (same inputs, same
        failure). Calls from an earlier verification loop do not count. A required tool of the
        phase that has not completed in this pass may repeat an earlier phase's call once (P9
        re-runs verify_design after P7 on an unchanged state).
        """
        signature = self._signature(state, tool, args)
        earlier = [
            record
            for record in state.tool_results
            if record.signature == signature and record.loop == state.loop_count
        ]
        if not earlier:
            return None
        this_pass = [record for record in earlier if record.phase == spec.phase]
        required_open = (
            tool in spec.required_tools
            and tool not in state.phase_progress(spec.phase).completed_tools
        )
        if required_open and not this_pass:
            return None
        return (this_pass or earlier)[-1]

    def _reject_step(self, state: RunState, step: NextStep, reason: str) -> Outcome:
        self._event(
            state,
            "STEP_REJECTED",
            {"step": step.model_dump(mode="json"), "message": f"rejected: {reason}"},
        )
        return "continue"

    def _resolve_args(self, state: RunState, args: dict[str, ArgValue]) -> dict[str, ArgValue]:
        return {
            key: (state.selected_id if value == SELECTED else value) for key, value in args.items()
        }

    def _default_args(self, state: RunState, spec: PhaseSpec, tool: str) -> dict[str, ArgValue]:
        return self._pin_args(
            state, tool, self._resolve_args(state, dict(spec.default_args.get(tool, {})))
        )

    @staticmethod
    def _pin_args(state: RunState, tool: str, args: dict[str, ArgValue]) -> dict[str, ArgValue]:
        """Runner-only arguments: TL-13's ``candidate_id`` is the adopted what-if design or None.

        Review loop: after an adoption TL-13 sizes from the adopted design; without one the
        argument is forced to None, so an agent can never choose what TL-13 sizes (the brief
        does not show the argument; ``toolbox.RUNNER_ONLY``).
        """
        if tool == "optimise_design":
            return {**args, "candidate_id": state.review_pin}
        return args

    def _tool_context(self, state: RunState) -> ToolContext:
        return ToolContext(
            run_id=state.run_id,
            run_dir=self.run_dir(state.run_id),
            config_dir=self.config_dir,
            candidates=tuple(state.candidates),
            kpis=dict(state.kpis),
            shortlist=tuple(state.shortlist),
            selected_id=state.selected_id,
            decision_records=tuple(r.model_dump(mode="json") for r in state.decision_records),
            answers=state.answer_values(),
            guard_failed=tuple(state.guard_failed),
            rejected_ids=tuple(sorted(state.rejected_ids())),
            violations=tuple(state.violations),
        )

    def _call_tool(
        self,
        state: RunState,
        spec: PhaseSpec,
        tool: str,
        args: dict[str, ArgValue],
        source: DecisionSource,
        *,
        budgeted: bool = True,
    ) -> Outcome:
        """Execute one tool call; ``budgeted=False`` for workflow steps (not the agent's AG-04)."""
        prog = state.phase_progress(spec.phase)
        budget = self._budget(spec)
        tr_id = f"TR-{len(state.tool_results) + 1:03d}"
        if tool not in self.toolbox:
            self._event(state, "TOOL_ERROR", {"id": tr_id, "tool": tool, "error": "unknown tool"})
            return "continue"
        if budgeted and prog.tool_calls_used >= budget.max_tool_calls:
            return self._escalate(
                state,
                f"tool-call budget exceeded in {spec.phase.value} ({budget.max_tool_calls} calls)",
            )
        prog.tool_calls_used += int(budgeted)
        args = self._pin_args(state, tool, args)
        signature = self._signature(state, tool, args)
        args = self._resolve_args(state, args)
        tool_spec = self.toolbox.spec(tool)
        if tool == TOKEN_FIGURE_TOOL:
            # Post-submission fix: the P10 token chart reads token_summary.json, which used to
            # be written only at run end (placeholder in every LLM run). Interim summary here;
            # _write_outputs redraws the chart from the final summary.
            LLMLogger(self.run_dir(state.run_id)).write_token_summary()
        try:
            result = self.toolbox.execute(tool, args, self._tool_context(state))
        except Exception as exc:  # noqa: BLE001 - a failing tool is evidence, not a crash
            error = f"{type(exc).__name__}: {exc}"[:300]
            state.tool_results.append(
                ToolRecord(
                    id=tr_id,
                    tool=tool,
                    phase=spec.phase,
                    args=args,
                    ok=False,
                    error=error,
                    stub=tool_spec.stub,
                    source=source,
                    signature=signature,
                    loop=state.loop_count,
                )
            )
            self._event(state, "TOOL_ERROR", {"id": tr_id, "tool": tool, "error": error})
            return "continue"
        path = self._write_tool_output(state, tr_id, tool, result)
        candidate_ids = self._apply_result(state, spec, tool, tr_id, result, args)
        state.tool_results.append(
            ToolRecord(
                id=tr_id,
                tool=tool,
                phase=spec.phase,
                args=args,
                ok=True,
                summary=result.summary,
                warnings=list(result.warnings),
                stub=tool_spec.stub,
                candidate_ids=candidate_ids,
                source=source,
                path=path,
                signature=signature,
                loop=state.loop_count,
                data_keys=result_keys(result.data),
            )
        )
        if tool not in prog.completed_tools:
            prog.completed_tools.append(tool)
        self._event(
            state,
            "TOOL_RESULT",
            {
                "id": tr_id,
                "tool": tool,
                "summary": result.summary,
                "stub": tool_spec.stub,
                "source": source,
            },
        )
        return "continue"

    def _write_tool_output(self, state: RunState, tr_id: str, tool: str, result: Any) -> str:
        out_dir = self.run_dir(state.run_id) / "tool_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{tr_id}_{tool}.json"
        path.write_text(json.dumps(result.model_dump(mode="json"), indent=1), "utf-8")
        return str(path.relative_to(self.run_dir(state.run_id)))

    def _apply_result(
        self,
        state: RunState,
        spec: PhaseSpec,
        tool: str,
        tr_id: str,
        result: Any,
        args: dict[str, ArgValue],
    ) -> list[str]:
        """Update typed state from a tool result (reducer contract in ``runner/toolbox.py``)."""
        data = result.data
        if tool == "load_design_basis" and isinstance(getattr(data, "open_gaps", None), list):
            answered = {a.assumption_id for a in state.answers}
            state.open_gaps = [g for g in data.open_gaps if g not in answered]
            state.gap_questions.update(getattr(data, "gap_questions", {}) or {})
            return []
        if tool == "generate_candidates":
            state.candidates = list(data.candidates)
            return [c.id for c in state.candidates]
        if tool == "design_guard":
            failed = set(state.guard_failed)  # merge: a single-candidate check keeps the rest
            for check in data.checks:
                (failed.discard if check.passed else failed.add)(check.candidate_id)
            state.guard_failed = sorted(failed)
            return [c.candidate_id for c in data.checks]
        if tool in {"simulate_annual", "economics", "uncertainty"}:
            for kpi in getattr(data, "kpis", None) or []:  # WP-14: + uncertainty percentiles
                self._merge_kpi(state, kpi)
            return [kpi.candidate_id for kpi in getattr(data, "kpis", None) or []]
        if tool == "optimise_design":
            # WP-14: the real TL-13 returns its sized designs; they join the run as candidates.
            sized = {c.id: c for c in getattr(data, "candidates", None) or []}
            if sized:
                state.candidates = [c for c in state.candidates if c.id not in sized]
                state.candidates += list(sized.values())
            for kpi in getattr(data, "kpis", None) or []:
                self._merge_kpi(state, kpi)
            state.shortlist = list(data.shortlist)
            state.selected_id = state.shortlist[0] if state.shortlist else None
            return list(state.shortlist)
        if tool == "robust_backup":
            # Pre-L-2: the TL-15 robust-backup variant joins the run unselected (DR-11 decides).
            variants = list(getattr(data, "candidates", None) or [])
            ids = {c.id for c in variants}
            state.candidates = [c for c in state.candidates if c.id not in ids] + variants
            for kpi in getattr(data, "kpis", None) or []:
                state.kpis.pop(kpi.candidate_id, None)  # a re-run replaces, never merges
                self._merge_kpi(state, kpi)
            state.robust_backup = RobustBackup.model_validate(
                {
                    key: value
                    for key, value in data.model_dump(mode="json").items()
                    if key in RobustBackup.model_fields
                }
                | {"tool_result_id": tr_id}
            )
            return [data.candidate_id] + sorted(ids)
        if tool == "verify_design":
            verdict = DesignVerdict(data.verdict)
            stub_evidence = any(record.stub for record in state.tool_results)
            if verdict == DesignVerdict.ACCEPTED and (
                self.toolbox.spec(tool).stub or stub_evidence
            ):
                # AG-11: neither a stub verifier nor evidence from stub tools accepts a design
                verdict = DesignVerdict.UNVERIFIED
            state.verifications.append(
                Verification(
                    candidate_id=data.candidate_id,
                    verdict=verdict,
                    violations=list(getattr(data, "violations", [])),
                    verifier=str(getattr(data, "verifier", tool)),
                    tool_result_id=tr_id,
                    phase=spec.phase,
                )
            )
            return [data.candidate_id]
        candidate = args.get("candidate_id")
        if isinstance(candidate, str) and candidate != "all":
            return [candidate]
        return []

    @staticmethod
    def _merge_kpi(state: RunState, kpi: KPISet) -> None:
        existing = state.kpis.get(kpi.candidate_id)
        if existing is None:
            state.kpis[kpi.candidate_id] = kpi
            return
        update = {
            key: value
            for key, value in kpi.model_dump().items()
            if value is not None and value != {} and value != []
        }
        state.kpis[kpi.candidate_id] = KPISet.model_validate({**existing.model_dump(), **update})

    # ------------------------------------------------------------------ missing input (AG-05)
    def _gap_request(
        self, state: RunState, phase: Phase, gap: str, question: Any = None
    ) -> InputRequest:
        entry = load_config("assumptions", self.config_dir).get(gap)
        # An LLM question is only a fallback, and never one that states numbers (AG-02).
        llm_question = question if isinstance(question, str) else None
        if llm_question is not None and states_numbers(llm_question):
            llm_question = None
        text = state.gap_questions.get(gap) or llm_question
        return InputRequest(
            request_id=f"REQ-{len(state.answers) + 1:02d}-{gap}",
            phase=phase,
            question=text or entry.title,
            assumption_id=gap,
            default=entry.value,
            unit=entry.unit,
        )

    def _register_request(
        self, state: RunState, phase: Phase, assumption_id: str, question: str
    ) -> InputRequest:
        """Runner-worded question whose default is the register value of ``assumption_id``."""
        try:
            entry = load_config("assumptions", self.config_dir).get(assumption_id)
        except KeyError as exc:
            raise KeyError(
                f"register entry {assumption_id} missing in {self.config_dir or 'config/'}"
                "/assumptions.yaml (the P1 intake questions need it)"
            ) from exc
        return InputRequest(
            request_id=f"REQ-{len(state.answers) + 1:02d}-{assumption_id}",
            phase=phase,
            question=question,
            assumption_id=assumption_id,
            default=entry.value,
            unit=entry.unit,
        )

    def _intake_requests(self, state: RunState, spec: PhaseSpec) -> Outcome:
        """P1 questions raised by the P0 intake (WP-13 follow-up): S5 then S2.

        A supply temperature that conflicts with DB-16/DB-17 is asked to be confirmed first
        (``CFG-BASIS-CONFIRM``: ``keep`` = design basis, ``change`` = stop the run); then the
        inputs the request does not provide are listed in one question (``CFG-INPUT-DEFAULTS``:
        the repository inputs). Each is asked once; an answered id is skipped after a resume.
        Nothing is loaded or assumed before the answers (S2: "does not invent").
        """
        answered = {answer.assumption_id for answer in state.answers}
        conflicts = [c for c in state.conflicts if any(m in c for m in _BASIS_CONFLICT_MARKS)]
        if conflicts and BASIS_CONFIRM_ID not in answered:
            request = self._register_request(
                state,
                spec.phase,
                BASIS_CONFIRM_ID,
                " ".join(conflicts) + " Confirm the change of basis? Answer 'keep' to continue "
                "on the design basis (default) or 'change' to stop this run so the basis can be "
                "changed in config/limits.yaml and config/design_basis.yaml before a new run.",
            )
            if self._handle_request(state, request) == "paused":
                return "paused"
        if conflicts and state.answer_values().get(BASIS_CONFIRM_ID) == "change":
            # Answered now, or by ``resume`` just before this pass (AG-05).
            return self._escalate(
                state,
                "change of basis requested by the user: "
                + " ".join(conflicts)[:300]
                + " Change config/limits.yaml and config/design_basis.yaml, then start a "
                "new run",
            )
        if state.missing_inputs and INPUT_DEFAULTS_ID not in answered:
            request = self._register_request(
                state,
                spec.phase,
                INPUT_DEFAULTS_ID,
                "The request does not provide: " + "; ".join(state.missing_inputs) + ". Use the "
                "repository defaults (design basis in config/ and the demand CSVs in "
                "data/demand/)? Answer 'defaults' (default) to continue with them; other inputs "
                "must be placed there before a new run.",
            )
            if self._handle_request(state, request) == "paused":
                return "paused"
        return "continue"

    def _handle_request(self, state: RunState, request: InputRequest) -> Outcome:
        if state.input_mode == "assume":
            self._record_answer(state, request, request.default, "assumption")
            return "continue"
        if state.input_mode == "ask" and self.ask_fn is not None:
            for _ in range(3):
                raw = self.ask_fn(request)
                if raw is None or not str(raw).strip():
                    break
                try:
                    self._apply_answer(state, request, str(raw))
                    return "continue"
                except InvalidAnswer as exc:
                    self._event(state, "INVALID_ANSWER", {"message": str(exc)})
            self._record_answer(state, request, request.default, "assumption")
            return "continue"
        state.pending = request
        state.status = RunStatus.PAUSED
        state.final_summary = f"Paused in {request.phase.value}: {request.question}"
        self._event(state, "PAUSE", {"request": request.model_dump(mode="json")})
        return "paused"

    def _coerce_answer(self, request: InputRequest, raw: str) -> Any:
        text = raw.strip()
        default = request.default
        if request.assumption_id == BASIS_CONFIRM_ID:
            if text.lower() in _KEEP:
                return "keep"
            if text.lower() in _CHANGE:
                return "change"
            raise InvalidAnswer(
                f"{BASIS_CONFIRM_ID} answer must be 'keep' (continue on the design basis) or "
                "'change' (stop the run; change the basis in config/ before a new run)"
            )
        if request.assumption_id == INPUT_DEFAULTS_ID:
            if text.lower() in _DEFAULTS:
                return default
            raise InvalidAnswer(
                f"{INPUT_DEFAULTS_ID}: answer 'defaults' to continue with the repository inputs "
                "(config/ and data/demand/); other inputs must be placed there before a new run"
            )
        if isinstance(default, int | float) and not isinstance(default, bool):
            unit = f" ({request.unit})" if request.unit else ""
            try:
                value = float(text)
            except ValueError as exc:
                raise InvalidAnswer(f"{request.assumption_id} needs a number{unit}") from exc
            if not math.isfinite(value):
                raise InvalidAnswer(f"{request.assumption_id} needs a finite number{unit}")
            limits = load_config("limits", self.config_dir)
            if request.assumption_id == "DB-31" and value >= limits.heating_supply_degc:
                raise InvalidAnswer(
                    f"DB-31 return {value:g} degC must be below the "
                    f"{limits.heating_supply_degc:g} degC heating supply"
                )
            if request.assumption_id == "DB-32" and value <= limits.cooling_supply_degc:
                raise InvalidAnswer(
                    f"DB-32 return {value:g} degC must be above the "
                    f"{limits.cooling_supply_degc:g} degC cooling supply"
                )
            return value
        if isinstance(default, dict) and "interpretation" in default:
            choice = text.upper()
            if choice not in {"A", "B"}:
                raise InvalidAnswer(f"{request.assumption_id} answer must be A or B")
            if choice == default["interpretation"]:
                return dict(default)
            if request.assumption_id == "DB-30" and choice == "B":
                return _db30_interpretation_b(default)
            raise InvalidAnswer(
                f"{request.assumption_id}: the register has no values for interpretation {choice}"
            )
        return text

    def _apply_answer(self, state: RunState, request: InputRequest, raw: str) -> None:
        if request.request_id == APPROVAL_ID:
            norm = raw.strip().lower()
            if norm not in _YES | _NO:
                raise InvalidAnswer("approval answer must be yes or no")
            self._record_answer(state, request, "approved" if norm in _YES else "rejected", "user")
            return
        self._record_answer(state, request, self._coerce_answer(request, raw), "user")

    def _record_answer(
        self,
        state: RunState,
        request: InputRequest,
        value: Any,
        source: Literal["user", "assumption"],
    ) -> None:
        if request.request_id == APPROVAL_ID:
            state.approval = value
            label = (
                f"Human approval: {value} (user)"
                if source == "user"
                else "Human approval pending (non-interactive run)"
            )
        else:
            unit = f" {request.unit}" if request.unit else ""
            shown = json.dumps(value, separators=(",", ":")) if isinstance(value, dict) else value
            how = "user answer" if source == "user" else "assumed: register default"
            label = f"{request.assumption_id} = {shown}{unit} ({how})"
        answer = InputAnswer(
            request_id=request.request_id,
            assumption_id=request.assumption_id,
            answer=value,
            source=source,
            label=label,
        )
        if request.assumption_id in state.open_gaps:
            state.open_gaps.remove(request.assumption_id)
        state.answers.append(answer)
        event = "USER_ANSWER" if source == "user" else "ASSUMPTION"
        # MEM-01: only explicit user answers are candidates for durable project knowledge (WP-09).
        self._event(
            state,
            event,
            {
                "message": label,
                "request_id": request.request_id,
                "assumption_id": request.assumption_id,
                "answer": value,
                "durable": source == "user",
            },
        )

    # ------------------------------------------------------------------ decision records
    def _decision_record(self, state: RunState, spec: PhaseSpec, dr_id: str) -> Outcome:
        question = build_question(dr_id, state, spec.phase, self.config_dir)
        if question is None:
            self._event(state, "DR_SKIPPED", {"message": f"{dr_id}: no eligible options yet"})
            return "continue"
        prog = state.phase_progress(spec.phase)
        context = self._context(state, spec, role=question.role)
        budget = self._budget(spec)
        limit_error: str | None = None
        try:
            result = self.engine.choose(question, context)
        except EngineBudgetExceeded as exc:
            # Runner J: the call went past its own token ceiling (the engine floors its usage
            # limit there), i.e. beyond the configured call limits. The DR is still required,
            # so the policy chooses (a counted fallback) and the overrun is recorded below.
            limit_error = f"LLM usage limit exceeded for {dr_id}: {exc}"[:500]
            result = EngineResult(
                self._policy.choose(question, context).value,
                source="policy_fallback",
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                error=limit_error,
            )
        prog.tokens_used += result.input_tokens + result.output_tokens
        choice, source = result.value, result.source
        error = check_choice(choice_deps(question), choice)
        if error:
            self._event(state, "STEP_REJECTED", {"message": f"{dr_id} choice rejected: {error}"})
            choice, source = self._policy.choose(question, context).value, "policy_fallback"
        record = make_record(question, choice, source, state)
        state.decision_records = [r for r in state.decision_records if r.dr_id != dr_id]
        state.decision_records.append(record)
        self._event(
            state,
            "DECISION_RECORD",
            {
                "dr_id": dr_id,
                "choice": record.choice,
                "source": source,
                "stub_evidence": record.stub_evidence,
            },
        )
        target = installed_design(record, state)
        if target is not None and target != state.selected_id:
            # DR-11 installs the chosen backups: that design is selected and verified in P9.
            previous = state.selected_id
            state.selected_id = target
            state.shortlist = [target] + [c for c in state.shortlist if c != target]
            self._event(
                state,
                "DESIGN_SELECTED",
                {
                    "candidate_id": target,
                    "replaces": previous,
                    "dr_id": dr_id,
                    "message": f"{dr_id} {record.choice}: selected {target} instead of "
                    f"{previous}; the P9 DesignVerifier verifies it",
                },
            )
        if limit_error is not None or prog.tokens_used > budget.max_tokens:
            # Runner J: the DR is a phase-close step the workflow requires, so the engine floors
            # its usage limit and the overrun is recorded (counted by the eval harness like the
            # fallbacks) rather than escalated mid-DR. Ordinary decisions still escalate at the
            # budget (AG-04).
            overrun = max(prog.tokens_used - budget.max_tokens, 0)
            message = (
                f"{dr_id} written {overrun} tokens past the {spec.phase.value} token budget "
                f"({prog.tokens_used} > {budget.max_tokens}); a required Decision Record is not "
                "starved, ordinary decisions still escalate at the budget (AG-04)"
            )
            if limit_error is not None:
                message = (
                    f"{dr_id}: the LLM call exceeded its own usage ceiling, the policy chose "
                    f"the option (policy_fallback); {limit_error}"
                )
            self._event(
                state,
                "TOKEN_BUDGET_OVERRUN",
                {
                    "dr_id": dr_id,
                    "tokens_used": prog.tokens_used,
                    "max_tokens": budget.max_tokens,
                    "overrun": overrun,
                    "error": limit_error,
                    "message": message,
                },
            )
        return "continue"

    # ------------------------------------------------------------------ gates
    def _gate(self, state: RunState, spec: PhaseSpec) -> Phase | None:
        phase = spec.phase
        if phase in (Phase.P7, Phase.P9):
            verification = state.latest_verification(state.selected_id)
            if verification is None or verification.phase != phase:
                self._escalate(state, f"no verification of the selected design in {phase.value}")
                return None
            if state.review is None:  # a resumed review passed this gate before the pause
                self._event(
                    state,
                    "VERIFY_GATE",
                    {
                        "candidate_id": verification.candidate_id,
                        "verdict": verification.verdict.value,
                        "verifier": verification.verifier,
                        "message": f"{verification.candidate_id}: {verification.verdict.value}",
                    },
                )
            if verification.verdict == DesignVerdict.REJECTED:
                return self._loop_back(state, verification)
            if phase == Phase.P9 and self._unverified_without_stubs(state, verification):
                self._escalate(
                    state,
                    f"verifier could not verify {verification.candidate_id}: UNVERIFIED although "
                    f"no tool in the run is a stub (missing evidence, see "
                    f"{verification.tool_result_id})",
                )
                return None
            if phase == Phase.P9 and state.review_enabled:
                outcome = self._review(state)
                if outcome == "paused":
                    return None
                if outcome == "adopted":
                    return self._restart_from_p6(
                        state,
                        violations=list(state.violations),
                        message=f"user review adopted ({state.loop_count + 1}): back to P6",
                        reason="review",
                    )
        if phase == Phase.P10 and state.approval is None:
            verdict = state.verdict_value() or "not verified"
            request = InputRequest(
                request_id=APPROVAL_ID,
                phase=Phase.P10,
                question=(
                    f"Approve the recommended design {state.selected_id} "
                    f"(verifier verdict {verdict})? yes/no"
                ),
                default="pending",
            )
            self._handle_request(state, request)
            return None
        return next_phase(phase)

    @staticmethod
    def _unverified_without_stubs(state: RunState, verification: Verification) -> bool:
        """WP-09 decision: a real verifier's UNVERIFIED escalates at P9 in a stub-free run.

        UNVERIFIED means missing evidence, not a violation, so it never loops back. While any
        tool is still a stub the run completes UNVERIFIED (WP-13 blocks submission instead).
        """
        return verification.verdict == DesignVerdict.UNVERIFIED and not any(
            record.stub for record in state.tool_results
        )

    def _loop_back(self, state: RunState, verification: Verification) -> Phase | None:
        """REJECTED verification: back to P6 (≤ 3 loops), else escalate (spec §5.2 P7)."""
        if state.review_pin is not None:
            # The adopted what-if design (review loop) failed verification: TL-13 is free again.
            state.review_pin = None
        return self._restart_from_p6(
            state,
            violations=list(verification.violations),
            message=f"verification REJECTED ({state.loop_count + 1}): back to P6",
            reason="verifier",
        )

    def _restart_from_p6(
        self,
        state: RunState,
        *,
        violations: list[str],
        message: str,
        reason: Literal["verifier", "review"],
    ) -> Phase | None:
        """Rerun P6–P10 (verifier loop-back and adopted user review share this path)."""
        state.loop_count += 1
        self._event(
            state,
            "LOOP_BACK",
            (
                {
                    "loop": state.loop_count,
                    "violations": violations,
                    "reason": reason,
                    "message": message,
                }
                if reason == "review"
                else {"loop": state.loop_count, "violations": violations, "message": message}
            ),
        )
        if state.loop_count > self.max_verification_loops:
            self._escalate(
                state,
                f"verification still REJECTED after {self.max_verification_loops} loops: "
                + "; ".join(violations)[:200],
            )
            return None
        state.violations = list(violations)
        state.review = None  # a new pass gets a new review
        state.robust_backup = None  # P8 re-prices the robust backups of the new selection
        redo = {"DR-06", "DR-10", "DR-11"}
        dropped = [state.progress.pop(phase.value, None) for phase in LOOP_BACK_PHASES]
        state.earlier_pass_fallbacks += sum(p.fallbacks for p in dropped if p is not None) + sum(
            1
            for r in state.decision_records
            if r.dr_id in redo and r.decision_source == "policy_fallback"
        )
        state.decision_records = [r for r in state.decision_records if r.dr_id not in redo]
        return Phase.P6

    # ------------------------------------------------------------------ review loop (P9b)
    def _review(self, state: RunState) -> ReviewOutcome:
        """P9b user review (``--review``): objection → lever → what-if → adopt or keep.

        A small state machine persisted in ``state.review`` so the pause mode can resume it:
        ``objection`` (ask, map with the engine, compute the what-if), ``adopt`` (show the
        before → after table, ask yes/no), ``done``. At most :data:`review_mod.MAX_REVIEW_ROUNDS`
        objections are mapped per pass; "no lever matches" and refused levers ask again with the
        reason. The engine only chooses the lever; every number is the what-if tool's.
        """
        review = state.review
        if review is None:
            review = state.review = ReviewState()
            self._event(
                state,
                "REVIEW_START",
                {
                    "candidate_id": state.selected_id,
                    "verdict": state.verdict_value(),
                    "budget": state.review_budget or self.review_budget,
                    "message": f"user review of {state.selected_id} "
                    f"(verdict {state.verdict_value() or 'none'})",
                },
            )
        invalid = 0
        while True:
            if review.stage == "objection":
                if review.round >= review_mod.MAX_REVIEW_ROUNDS:
                    return self._finish_review(
                        state,
                        None,
                        adopted=False,
                        note=f"no lever matched in {review_mod.MAX_REVIEW_ROUNDS} rounds",
                    )
                answer = self._ask_review(state, review_mod.objection_request(state, review))
                if answer is None:
                    return "paused"
                text = " ".join(answer.split())
                if text.lower() in review_mod.NO_OBJECTION:
                    self._event(state, "REVIEW_NONE", {"message": "no objection"})
                    return self._finish_review(state, None, adopted=False, note="no objection")
                review.round += 1
                review.objection, review.feedback = text[:600], ""
                self._event(
                    state,
                    "REVIEW_OBJECTION",
                    {"round": review.round, "message": review.objection},
                )
                choice = self._map_objection(state, review)
                if choice.lever is None:
                    review.feedback = f"No lever matches '{review.objection}': {choice.rationale}"
                    self._event(state, "REVIEW_UNMATCHED", {"message": review.feedback})
                    self._save(state)
                    continue
                self._save(state)  # the what-if reads the persisted run state
                try:
                    result = review_mod.run_whatif(
                        self.run_dir(state.run_id),
                        choice.lever,
                        review_mod.resolve_value(state, choice.lever, choice.value),
                        state.review_budget or self.review_budget,
                        self.config_dir,
                    )
                except whatif_tool.WhatIfRefused as exc:
                    review.feedback = f"{choice.lever}={choice.value} refused: {exc}"[:500]
                    self._event(state, "REVIEW_REFUSED", {"message": review.feedback})
                    continue
                except (whatif_tool.UnknownLever, ValueError, OSError) as exc:
                    review.feedback = (
                        f"what-if {choice.lever}={choice.value} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )[:500]
                    self._event(state, "REVIEW_REFUSED", {"message": review.feedback})
                    continue
                tr_id = f"TR-{len(state.tool_results) + 1:03d}"
                stub = self.toolbox.spec("verify_design").stub
                state.tool_results.append(review_mod.whatif_record(state, result, tr_id, stub=stub))
                review.lever = next(iter(result.levers))
                review.value = result.levers[review.lever]
                review.whatif_index = result.index
                review.whatif_candidate_id = result.after.candidate.id
                review.whatif_tool_result_id = tr_id
                after = result.after
                self._event(
                    state,
                    "REVIEW_WHATIF",
                    {
                        "id": tr_id,
                        "index": result.index,
                        "lever": review.lever,
                        "value": review.value,
                        "candidate_id": after.candidate.id,
                        "guard_approved": after.guard_approved,
                        "verdict": after.verdict.value if after.verdict is not None else None,
                        "record": result.record.dr_id,
                        "path": result.outputs.get("json"),
                        "message": result.summary,
                    },
                )
                review.stage = "adopt"
                self._save(state)
            elif review.stage == "adopt":
                assert review.whatif_index is not None
                result = review_mod.load_whatif(self.run_dir(state.run_id), review.whatif_index)
                answer = self._ask_review(state, review_mod.adopt_request(state, review, result))
                if answer is None:
                    return "paused"
                norm = answer.strip().lower()
                if norm in _YES:
                    return self._adopt(state, review, result)
                if norm in _NO or not norm:
                    return self._finish_review(
                        state, result, adopted=False, note="declined by the user"
                    )
                invalid += 1
                self._event(
                    state, "INVALID_ANSWER", {"message": f"adopt answer must be yes or no: {norm}"}
                )
                if invalid >= 3:
                    return self._finish_review(
                        state, result, adopted=False, note="no yes/no answer; kept by default"
                    )
            else:
                return "continue"

    def _ask_review(self, state: RunState, request: InputRequest) -> str | None:
        """Ask a review question through the input mode; None = paused (``resume`` answers)."""
        review = state.review
        assert review is not None
        if review.answer is not None:  # brought by resume
            text, review.answer = review.answer, None
            return text
        if state.input_mode == "assume":
            return str(request.default)
        if state.input_mode == "ask" and self.ask_fn is not None:
            raw = self.ask_fn(request)
            return "" if raw is None else str(raw)
        state.pending = request
        state.status = RunStatus.PAUSED
        state.final_summary = f"Paused in {request.phase.value} (user review): {request.question}"
        self._event(state, "PAUSE", {"request": request.model_dump(mode="json")})
        self._save(state)
        return None

    def _apply_review_answer(self, state: RunState, request: InputRequest, raw: str) -> None:
        """Validate and store the answer ``resume`` brought for a pending review question."""
        if state.review is None:
            raise ValueError("no user review in progress")
        text = raw.strip()
        adopt = request.request_id.startswith(f"{review_mod.REVIEW_PREFIX}-ADOPT")
        if adopt and text and text.lower() not in _YES | _NO:
            raise InvalidAnswer("adopt answer must be yes or no")
        state.review.answer = text

    def _map_objection(self, state: RunState, review: ReviewState) -> LeverChoice:
        """Engine call: the objection → one offered lever (``LeverChoice``), checked and logged.

        Like a Decision Record call the engine floors its usage limit; past the phase budget
        the overrun is recorded (``TOKEN_BUDGET_OVERRUN``); an invalid or failed mapping falls
        back to the keyword policy (counted fallback).
        """
        spec = PHASE_SPECS[Phase.P9]
        prog, budget = state.phase_progress(spec.phase), self._budget(spec)
        question = review_mod.build_review_question(state, review)
        context = self._context(state, spec, role=question.role)
        limit_error: str | None = None
        try:
            result = self.engine.map_objection(question, context)
        except EngineBudgetExceeded as exc:
            limit_error = f"LLM usage limit exceeded for the review mapping: {exc}"[:500]
            result = EngineResult(
                self._policy.map_objection(question, context).value,
                source="policy_fallback",
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                error=limit_error,
            )
        prog.tokens_used += result.input_tokens + result.output_tokens
        choice, source = result.value, result.source
        error = check_lever_choice(question, choice)
        if error:
            self._event(state, "STEP_REJECTED", {"message": f"review mapping rejected: {error}"})
            choice, source = self._policy.map_objection(question, context).value, "policy_fallback"
        prog.fallbacks += int(source == "policy_fallback")
        review.mapping_source = source
        self._event(
            state,
            "REVIEW_MAPPED",
            {
                "round": review.round,
                "lever": choice.lever,
                "value": choice.value,
                "source": source,
                "retries": result.retries,
                "error": result.error,
                "message": choice.rationale,
            },
        )
        if limit_error is not None or prog.tokens_used > budget.max_tokens:
            overrun = max(prog.tokens_used - budget.max_tokens, 0)
            self._event(
                state,
                "TOKEN_BUDGET_OVERRUN",
                {
                    "dr_id": review_mod.REVIEW_DR_ID,
                    "tokens_used": prog.tokens_used,
                    "max_tokens": budget.max_tokens,
                    "overrun": overrun,
                    "error": limit_error,
                    "message": f"review mapping {overrun} tokens past the P9 token budget "
                    f"({prog.tokens_used} > {budget.max_tokens})"
                    + (f"; {limit_error}" if limit_error else ""),
                },
            )
        return choice

    def _adopt(
        self, state: RunState, review: ReviewState, result: whatif_tool.WhatIfResult
    ) -> ReviewOutcome:
        """Adopt the what-if design: it becomes the selection TL-13 sizes from in P6–P9."""
        lever = review.lever or ""
        value = result.levers.get(lever)
        after = result.after
        if lever not in review_mod.ADOPTABLE:
            reason = review_mod.NOT_ADOPTABLE_REASON.get(lever, "not a design lever")
            return self._finish_review(
                state, result, adopted=False, note=f"lever {lever} cannot be adopted: {reason}"
            )
        if after.status != "ok" or not after.guard_approved:
            return self._finish_review(
                state,
                result,
                adopted=False,
                note=f"what-if design not usable: {after.reason or after.status}",
            )
        if state.loop_count >= self.max_verification_loops:
            return self._finish_review(
                state,
                result,
                adopted=False,
                note=f"the {self.max_verification_loops} verification loops are used; the "
                "design cannot be changed again in this run",
            )
        candidate = after.candidate
        state.candidates = [c for c in state.candidates if c.id != candidate.id] + [candidate]
        if after.kpis is not None:
            state.kpis.pop(candidate.id, None)
            self._merge_kpi(state, after.kpis)
        self._register_adopted_design(state, result)
        if lever in review_mod.ANSWER_LEVERS:
            request = self._register_request(
                state, Phase.P9, lever, f"user review: {lever} = {value}"
            )
            self._record_answer(state, request, self._coerce_answer(request, str(value)), "user")
        state.review_levers[lever] = value
        state.review_pin = candidate.id
        previous = state.selected_id
        state.selected_id = candidate.id
        state.shortlist = [candidate.id] + [c for c in state.shortlist if c != candidate.id]
        state.violations = [
            (
                f"user review (DR-12): '{review.objection}' → "
                f"{whatif_tool.format_levers({lever: value})}; TL-13 sizes from the adopted "
                f"what-if design {candidate.id}"
            )
        ]
        self._event(
            state,
            "DESIGN_SELECTED",
            {
                "candidate_id": candidate.id,
                "replaces": previous,
                "dr_id": review_mod.REVIEW_DR_ID,
                "message": f"DR-12 adopted what-if {result.index}: selected {candidate.id} "
                f"instead of {previous}; P6–P9 rerun on it",
            },
        )
        return self._finish_review(
            state,
            result,
            adopted=True,
            note=f"P6–P9 rerun on {candidate.id} (TL-13 sizes from it)",
            recommended_id=previous,
        )

    def adopt_whatif(
        self,
        run_id: str,
        result: whatif_tool.WhatIfResult,
        *,
        objection: str,
        mapping_source: str = "user",
    ) -> RunResult:
        """Adopt a what-if of a finished run: P6–P9 rerun on its design, DR-12 (FINAL_SPRINT §I).

        The voice dialogue (``ui.dialogue``) computes what-ifs on a completed run with
        ``tools.whatif`` and, when the user says "adopt", hands the result here. This is the
        review loop's own adoption path (:meth:`_adopt` → :meth:`_restart_from_p6` →
        :meth:`_loop`) applied after P10: the what-if joins the run as ``whatif`` evidence
        (``TR-…``), the ``REVIEW_*`` events record the objection, the lever and the what-if, DR-12
        "User review" is written adopted, and the run continues from P6 on the adopted design
        (TL-13 pinned to it) up to a new P10 approval asked through ``ask_fn``. Counted against
        the ≤ 3 verification loops like any loop-back; the run's artefacts are rewritten as after
        one. ``ValueError`` when the run is not completed, the what-if is not this run's, the
        lever is not adoptable, the what-if design is unusable or no loop is left — nothing is
        written then.
        """
        state = self.load_state(run_id)
        if state.status != RunStatus.COMPLETED:
            raise ValueError(f"run '{run_id}' is {state.status.value}, not completed")
        if result.run_id != run_id:
            raise ValueError(f"what-if {result.index} belongs to run '{result.run_id}'")
        lever = next(iter(result.levers))
        value = result.levers[lever]
        if lever not in review_mod.ADOPTABLE:
            reason = review_mod.NOT_ADOPTABLE_REASON.get(lever, "not a design lever")
            raise ValueError(f"lever {lever} cannot be adopted: {reason}")
        after = result.after
        if after.status != "ok" or not after.guard_approved:
            raise ValueError(f"what-if design not usable: {after.reason or after.status}")
        if state.loop_count >= self.max_verification_loops:
            raise ValueError(
                f"the {self.max_verification_loops} verification loops are used; the design "
                "cannot be changed again in this run"
            )
        # Reopen the run at the P9 review with the what-if as evidence (as _review records it).
        previous_approval = state.approval
        state.status = RunStatus.RUNNING
        state.phase = Phase.P9
        state.pending = None
        state.approval = None
        if self.ask_fn is not None:  # the dialogue is interactive: the new P10 approval is asked
            state.input_mode = "ask"
        review = state.review = ReviewState(
            stage="adopt",
            round=1,
            objection=objection[:600],
            lever=lever,
            value=value,
            mapping_source=mapping_source,
            whatif_index=result.index,
            whatif_candidate_id=after.candidate.id,
        )
        self._event(
            state,
            "REVIEW_START",
            {
                "candidate_id": state.selected_id,
                "verdict": state.verdict_value(),
                "budget": result.budget,
                "message": f"voice dialogue: review of {state.selected_id} after the P10 "
                f"approval ({previous_approval})",
            },
        )
        self._event(state, "REVIEW_OBJECTION", {"round": 1, "message": review.objection})
        self._event(
            state,
            "REVIEW_MAPPED",
            {
                "round": 1,
                "lever": lever,
                "value": str(result.record.levers.get(lever, value)),
                "source": mapping_source,
                "retries": 0,
                "error": None,
                "message": f"voice dialogue: {whatif_tool.format_levers(result.levers)}",
            },
        )
        tr_id = f"TR-{len(state.tool_results) + 1:03d}"
        stub = self.toolbox.spec("verify_design").stub
        state.tool_results.append(review_mod.whatif_record(state, result, tr_id, stub=stub))
        review.whatif_tool_result_id = tr_id
        self._event(
            state,
            "REVIEW_WHATIF",
            {
                "id": tr_id,
                "index": result.index,
                "lever": lever,
                "value": value,
                "candidate_id": after.candidate.id,
                "guard_approved": after.guard_approved,
                "verdict": after.verdict.value if after.verdict is not None else None,
                "record": result.record.dr_id,
                "path": result.outputs.get("json"),
                "message": result.summary,
            },
        )
        if self._adopt(state, review, result) != "adopted":  # defensive: checked above
            state.status = RunStatus.COMPLETED
            state.phase = Phase.P10
            state.approval = previous_approval
            self._save(state)
            self._write_outputs(state)
            return self._result(state)
        following = self._restart_from_p6(
            state,
            violations=list(state.violations),
            message=f"user review adopted ({state.loop_count + 1}): back to P6",
            reason="review",
        )
        if following is None:  # escalated at the loop limit
            self._save(state)
            self._write_outputs(state)
            return self._result(state)
        state.phase = following
        self._save(state)
        return self._loop(state)

    def _register_adopted_design(self, state: RunState, result: whatif_tool.WhatIfResult) -> None:
        """Hand the adopted design (and its outage schedule) to the real tool session."""
        session = getattr(self.toolbox, "session", None)
        if session is None or not hasattr(session, "register_design"):
            return  # stub toolbox: the shortlist stub needs no sized design
        from gemini_agent.runner.real_tools import SESSION_DIR, SizedDesign

        after = result.after
        path = (
            self.run_dir(state.run_id)
            / whatif_tool.WHATIF_DIR
            / str(result.index)
            / SESSION_DIR
            / "sized_designs.json"
        )
        entry = None
        if path.is_file():
            entry = json.loads(path.read_text(encoding="utf-8")).get(after.candidate.id)
        sized = (
            SizedDesign.model_validate(entry)
            if entry
            else SizedDesign(
                candidate=after.candidate,
                concept_candidate_id=after.candidate.id,
                outage_start_indices=after.outage_start_indices,
            )
        )
        session.register_design(self._tool_context(state), sized)

    def _finish_review(
        self,
        state: RunState,
        result: whatif_tool.WhatIfResult | None,
        *,
        adopted: bool,
        note: str,
        recommended_id: str | None = None,
    ) -> ReviewOutcome:
        """Write DR-12 (adopted or kept) and close the review of this pass.

        ``recommended_id`` is the design the review was about (after an adoption the selection
        already points at the what-if design).
        """
        review = state.review
        assert review is not None
        recommended = recommended_id or state.selected_id
        evidence_ids: list[str] = []
        if review.whatif_tool_result_id:
            evidence_ids.append(review.whatif_tool_result_id)
        verification = state.latest_verification(recommended)
        if verification is not None:
            evidence_ids.append(verification.tool_result_id)
        levers = {review.lever: review.value} if review.lever else {}
        keys = review_mod.evidence_keys_for(state, evidence_ids, levers)
        record = review_mod.review_record(
            state,
            review,
            result,
            adopted=adopted,
            note=note,
            evidence_ids=evidence_ids,
            evidence_keys=keys,
            recommended_id=recommended,
        )
        state.decision_records = [
            r for r in state.decision_records if r.dr_id != review_mod.REVIEW_DR_ID
        ] + [record]
        self._event(
            state,
            "DECISION_RECORD",
            {
                "dr_id": record.dr_id,
                "choice": record.choice,
                "source": record.decision_source,
                "stub_evidence": record.stub_evidence,
            },
        )
        self._event(
            state,
            "REVIEW_ADOPTED" if adopted else "REVIEW_KEPT",
            {
                "lever": review.lever,
                "value": review.value,
                "whatif_index": review.whatif_index,
                "candidate_id": review.whatif_candidate_id,
                "message": f"{record.title}: {'adopted' if adopted else 'kept'} — {note}",
            },
        )
        review.stage = "done"
        if not adopted:
            state.review = None
        self._save(state)
        return "adopted" if adopted else "continue"

    # ------------------------------------------------------------------ terminal states
    def _intake(self, state: RunState) -> None:
        scope = scope_check(state.user_prompt)
        state.in_scope = scope.in_scope
        state.scope_reason = scope.reason
        self._event(
            state,
            "SCOPE",
            {"in_scope": scope.in_scope, "message": scope.reason, "terms": scope.matched_terms},
        )
        if scope.in_scope:
            limits = load_config("limits", self.config_dir)
            basis = load_config("design_basis", self.config_dir)
            for conflict in constraint_conflicts(state.user_prompt, limits, basis):
                state.conflicts.append(conflict)
                self._event(state, "CONFLICT", {"message": conflict})
            if state.user_prompt != DEFAULT_BRIEF:  # the brief comes with every file (S1)
                state.missing_inputs = missing_request_inputs(state.user_prompt)
            if state.missing_inputs:
                self._event(
                    state,
                    "MISSING_INPUTS",
                    {
                        "inputs": list(state.missing_inputs),
                        "message": "the request does not provide: "
                        + "; ".join(state.missing_inputs),
                    },
                )

    def _refuse(self, state: RunState, reason: str) -> Outcome:
        state.status = RunStatus.REFUSED
        state.final_summary = refusal_message(state.user_prompt)
        self._event(state, "REFUSE", {"message": state.final_summary, "reason": reason[:300]})
        return "stopped"

    def token_budget_overruns(self, run_id: str) -> int:
        """Decision Record calls made past their phase token budget (``TOKEN_BUDGET_OVERRUN``)."""
        events = self.store(run_id).list_events(run_id)
        return sum(1 for event in events if event.type == "TOKEN_BUDGET_OVERRUN")

    def _escalate(self, state: RunState, reason: str) -> Outcome:
        prog = state.phase_progress(state.phase)
        state.status = RunStatus.ESCALATED
        state.escalation_reason = reason
        state.final_summary = (
            f"Escalated to a human in {state.phase.value} {PHASE_TITLES[state.phase]}: {reason}. "
            f"Used {prog.steps_used} steps, {prog.tool_calls_used} tool calls, "
            f"{prog.tokens_used} tokens in this phase."
        )
        self._event(state, "ESCALATE", {"message": state.final_summary, "reason": reason})
        return "stopped"

    def _complete(self, state: RunState) -> None:
        state.status = RunStatus.COMPLETED
        stubs = sum(1 for record in state.tool_results if record.stub)
        state.final_summary = (
            f"Completed P0–P10: selected {state.selected_id or 'none'} "
            f"(verifier verdict {state.verdict_value() or 'none'}); "
            f"{len(state.decision_records)} Decision Records; approval {state.approval}; "
            f"{stubs}/{len(state.tool_results)} tool calls were stubs."
        )
        if state.engine != "policy":
            state.final_summary += f" LLM fallbacks to policy: {state.fallback_count()}."
            overruns = self.token_budget_overruns(state.run_id)
            if overruns:
                state.final_summary += f" Decision Records past a phase token budget: {overruns}."
        self._event(state, "RUN_END", {"message": state.final_summary})

    # ------------------------------------------------------------------ context and storage
    def _context(
        self, state: RunState, spec: PhaseSpec, role: Role | None = None
    ) -> DecisionContext:
        prog = state.phase_progress(spec.phase)
        budget = self._budget(spec)
        role = role or spec.role
        # P0 is a pure intake check (finalise or refuse): no tool is offered there.
        tools = [tool for tool in ROLES[role].tools if tool in self.toolbox]
        if spec.phase == Phase.P0:
            tools = []
        remaining = [tool for tool in spec.required_tools if tool not in prog.completed_tools]
        return DecisionContext(
            run_id=state.run_id,
            phase=spec.phase,
            phase_title=PHASE_TITLES[spec.phase],
            role=role,
            objective=spec.objective,
            user_prompt=state.user_prompt[:500],
            in_scope=state.in_scope,
            scope_reason=state.scope_reason,
            allowed_tools=[self.toolbox.brief(tool) for tool in tools],
            required_tools_remaining=remaining,
            completed_tools=list(prog.completed_tools),
            default_args={tool: self._default_args(state, spec, tool) for tool in remaining},
            open_gaps=list(state.open_gaps) if spec.phase == Phase.P1 else [],
            gap_questions=dict(state.gap_questions) if spec.phase == Phase.P1 else {},
            history=self._history(state, role),
            violations=list(state.violations),
            loop_count=state.loop_count,
            budget_left={
                "steps": max(budget.max_steps - prog.steps_used, 0),
                "tool_calls": max(budget.max_tool_calls - prog.tool_calls_used, 0),
                "tokens": max(budget.max_tokens - prog.tokens_used, 0),
            },
        )

    def _history(self, state: RunState, role: Role | None = None) -> list[dict[str, Any]]:
        # AG-08: the Independent Reviewer sees tool evidence only, not earlier decisions — plus
        # the rejections of its own steps in this phase, so it gets the runner's feedback.
        reviewer = role == Role.REVIEWER
        types = _REVIEWER_HISTORY_TYPES if reviewer else _HISTORY_TYPES
        events = [
            event
            for event in self.store(state.run_id).list_events(state.run_id)
            if event.type in types
            or (reviewer and event.type == "STEP_REJECTED" and event.phase == state.phase.value)
        ]
        if self.history_events is not None:
            events = events[-self.history_events :]
        return [
            {
                "type": e.type,
                "phase": e.phase,
                **(_compact_payload(e.type, e.payload) if self.compact_history else e.payload),
            }
            for e in events
        ]

    def _event(self, state: RunState, event_type: str, payload: dict[str, Any]) -> None:
        self.store(state.run_id).append_event(
            state.run_id, event_type, payload, phase=state.phase.value
        )

    def _save(self, state: RunState) -> None:
        self.store(state.run_id).update_run(
            state.run_id, state.status.value, state.model_dump(mode="json")
        )

    def _write_outputs(self, state: RunState) -> None:
        run_dir = self.run_dir(state.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        write_records(state.decision_records, run_dir / "decision_records")
        (run_dir / "run_summary.json").write_text(
            json.dumps(state.summary_dict(), indent=2), "utf-8"
        )
        events = self.store(state.run_id).list_events(state.run_id)
        (run_dir / "events.jsonl").write_text(
            "".join(event.model_dump_json() + "\n" for event in events), "utf-8"
        )
        LLMLogger(run_dir).write_token_summary()
        if (run_dir / "figures" / TOKENS_FIGURE).is_file():
            # P10 plotted before the run's last LLM calls: redraw from the final summary. A
            # drawing failure must not lose the run outputs written above (state is saved).
            from gemini_agent.tools.plots import render_tokens_figure

            try:
                render_tokens_figure(run_dir)
            except (OSError, ValueError) as exc:  # corrupt summary, unwritable figure
                self._event(state, "WARNING", {"message": f"fig_tokens not redrawn: {exc}"})
