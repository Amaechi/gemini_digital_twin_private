# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/runner/incident_runner.py @ d69507a. Modified by
# GeoAgent team, 2026-11-01: incident loop replaced by the deterministic phase machine P0–P10 with
# per-phase step/tool/token budgets, verifier-gated loops (<= 3), request_missing_input
# pause/resume, Decision Records and run artefacts; synchronous; kept _build_context /
# _compact_payload / _prior_action_signatures ideas.
"""Workflow runner: deterministic phases P0–P10 (spec §4.1, §5.2).

The runner decides WHICH phase runs, enforces budgets (AG-04), executes every tool call,
persists state after each step (AG-10) and copies the verifier's verdict — it never creates an
ACCEPTED verdict itself (AG-11). Engines only choose steps inside a phase and options of
Decision Records. ``--engine policy`` runs the whole pipeline without an LLM (AG-12).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gemini_agent.agents.engine import (
    AgentDeps,
    DecisionEngine,
    EngineBudgetExceeded,
    PolicyEngine,
    check_choice,
)
from gemini_agent.agents.roles import ROLES
from gemini_agent.agents.scope import (
    DEFAULT_BRIEF,
    constraint_conflicts,
    refusal_message,
    scope_check,
)
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner.decision_records import (
    build_question,
    make_record,
    write_records,
)
from gemini_agent.runner.phases import (
    PHASE_SPECS,
    SELECTED,
    PhaseBudget,
    PhaseSpec,
    next_phase,
)
from gemini_agent.runner.state import InputMode, RunState, ToolRecord, Verification
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
    NextStep,
    Phase,
    Role,
    RunStatus,
)
from gemini_agent.schemas.results import KPISet

RUNS_ROOT = REPO_ROOT / "runs"
STORE_FILE = "events.sqlite"
MAX_VERIFICATION_LOOPS = 3
"""Spec §5.2 P7: FAIL ⇒ violations back to P6 (≤ 3 loops), then escalate."""
HISTORY_EVENTS = 12
"""Working memory: last 12–14 compacted events (spec §10.2)."""
APPROVAL_ID = "APPROVAL"
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
    }
)
_REVIEWER_HISTORY_TYPES = frozenset({"TOOL_RESULT", "TOOL_ERROR"})
_YES = frozenset({"y", "yes", "approve", "approved"})
_NO = frozenset({"n", "no", "reject", "rejected"})

Outcome = Literal["continue", "finalise", "paused", "stopped", "done"]
AskFn = Callable[[InputRequest], str | None]


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
    ) -> None:
        """Configure the runner; ``budgets`` override the phase table per phase."""
        self.engine = engine
        self.runs_root = Path(runs_root)
        self.toolbox = toolbox or default_toolbox()
        self.config_dir = config_dir
        self.budgets = budgets or {}
        self.ask_fn = ask_fn
        self.max_verification_loops = max_verification_loops
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
        """Close all open stores."""
        for store in self._stores.values():
            store.close()
        self._stores.clear()

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
        )
        store.create_run(run_id, state.status.value, state.model_dump(mode="json"))
        self._event(state, "RUN_START", {"engine": self.engine.name, "input_mode": input_mode})
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
        if answer is None or not str(answer).strip():
            raise InvalidAnswer(f"an answer is required: {state.pending.question}")
        request = state.pending
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
        while not prog.engine_done:
            if prog.steps_used >= budget.max_steps:
                return self._escalate(
                    state, f"step budget exceeded in {spec.phase.value} ({budget.max_steps} steps)"
                )
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
            if prog.tokens_used > budget.max_tokens:
                return self._escalate(
                    state,
                    f"token budget exceeded in {spec.phase.value} "
                    f"({prog.tokens_used} > {budget.max_tokens})",
                )
            for step in result.value.steps:
                if prog.steps_used >= budget.max_steps:
                    return self._escalate(
                        state,
                        f"step budget exceeded in {spec.phase.value} ({budget.max_steps} steps)",
                    )
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
        return self._call_tool(state, spec, step.tool, dict(step.args), source)

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
        return self._resolve_args(state, dict(spec.default_args.get(tool, {})))

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
    ) -> Outcome:
        prog = state.phase_progress(spec.phase)
        budget = self._budget(spec)
        tr_id = f"TR-{len(state.tool_results) + 1:03d}"
        if tool not in self.toolbox:
            self._event(state, "TOOL_ERROR", {"id": tr_id, "tool": tool, "error": "unknown tool"})
            return "continue"
        if prog.tool_calls_used >= budget.max_tool_calls:
            return self._escalate(
                state,
                f"tool-call budget exceeded in {spec.phase.value} ({budget.max_tool_calls} calls)",
            )
        prog.tool_calls_used += 1
        args = self._resolve_args(state, args)
        tool_spec = self.toolbox.spec(tool)
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
        if tool in {"simulate_annual", "economics"}:
            for kpi in data.kpis:
                self._merge_kpi(state, kpi)
            return [kpi.candidate_id for kpi in data.kpis]
        if tool == "optimise_design":
            state.shortlist = list(data.shortlist)
            state.selected_id = state.shortlist[0] if state.shortlist else None
            return list(state.shortlist)
        if tool == "verify_design":
            verdict = DesignVerdict(data.verdict)
            if verdict == DesignVerdict.ACCEPTED and self.toolbox.spec(tool).stub:
                verdict = DesignVerdict.UNVERIFIED  # AG-11: a stub can never accept a design
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
        text = state.gap_questions.get(gap) or (question if isinstance(question, str) else None)
        return InputRequest(
            request_id=f"REQ-{len(state.answers) + 1:02d}-{gap}",
            phase=phase,
            question=text or entry.title,
            assumption_id=gap,
            default=entry.value,
            unit=entry.unit,
        )

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
            if text.upper() not in {"A", "B"}:
                raise InvalidAnswer(f"{request.assumption_id} answer must be A or B")
            return {**default, "interpretation": text.upper()}
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
            {"message": label, "request_id": request.request_id, "durable": source == "user"},
        )

    # ------------------------------------------------------------------ decision records
    def _decision_record(self, state: RunState, spec: PhaseSpec, dr_id: str) -> Outcome:
        question = build_question(dr_id, state, spec.phase, self.config_dir)
        if question is None:
            self._event(state, "DR_SKIPPED", {"message": f"{dr_id}: no eligible options yet"})
            return "continue"
        prog = state.phase_progress(spec.phase)
        context = self._context(state, spec, role=question.role)
        try:
            result = self.engine.choose(question, context)
        except EngineBudgetExceeded as exc:
            prog.tokens_used += exc.input_tokens + exc.output_tokens
            return self._escalate(state, f"LLM usage limit exceeded for {dr_id}: {exc}")
        prog.tokens_used += result.input_tokens + result.output_tokens
        choice, source = result.value, result.source
        deps = AgentDeps(
            phase=question.phase,
            allowed_tools=(),
            dr_id=question.dr_id,
            option_ids=tuple(o.id for o in question.options),
            eligible_ids=tuple(o.id for o in question.options if o.eligible),
        )
        error = check_choice(deps, choice)
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
        budget = self._budget(spec)
        if prog.tokens_used > budget.max_tokens:
            return self._escalate(
                state,
                f"token budget exceeded in {spec.phase.value} "
                f"({prog.tokens_used} > {budget.max_tokens})",
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

    def _loop_back(self, state: RunState, verification: Verification) -> Phase | None:
        state.loop_count += 1
        self._event(
            state,
            "LOOP_BACK",
            {
                "loop": state.loop_count,
                "violations": verification.violations,
                "message": f"verification REJECTED ({state.loop_count}): back to P6",
            },
        )
        if state.loop_count > self.max_verification_loops:
            self._escalate(
                state,
                f"verification still REJECTED after {self.max_verification_loops} loops: "
                + "; ".join(verification.violations)[:200],
            )
            return None
        state.violations = list(verification.violations)
        for phase in (Phase.P6, Phase.P7, Phase.P8, Phase.P9, Phase.P10):
            state.progress.pop(phase.value, None)
        state.decision_records = [
            r for r in state.decision_records if r.dr_id not in {"DR-06", "DR-10"}
        ]
        return Phase.P6

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
            for conflict in constraint_conflicts(state.user_prompt, limits):
                state.conflicts.append(conflict)
                self._event(state, "CONFLICT", {"message": conflict})

    def _refuse(self, state: RunState, reason: str) -> Outcome:
        state.status = RunStatus.REFUSED
        state.final_summary = refusal_message(state.user_prompt)
        self._event(state, "REFUSE", {"message": state.final_summary, "reason": reason[:300]})
        return "stopped"

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
        self._event(state, "RUN_END", {"message": state.final_summary})

    # ------------------------------------------------------------------ context and storage
    def _context(
        self, state: RunState, spec: PhaseSpec, role: Role | None = None
    ) -> DecisionContext:
        prog = state.phase_progress(spec.phase)
        budget = self._budget(spec)
        role = role or spec.role
        tools = [tool for tool in ROLES[role].tools if tool in self.toolbox]
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
        # AG-08: the Independent Reviewer sees tool evidence only, not earlier decisions.
        types = _REVIEWER_HISTORY_TYPES if role == Role.REVIEWER else _HISTORY_TYPES
        events = [
            event
            for event in self.store(state.run_id).list_events(state.run_id)
            if event.type in types
        ][-HISTORY_EVENTS:]
        return [
            {"type": e.type, "phase": e.phase, **_compact_payload(e.type, e.payload)}
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
