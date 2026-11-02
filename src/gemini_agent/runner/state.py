"""Serialisable state of one workflow run (persisted in SQLite for pause/resume, AG-05/AG-10)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, JsonValue

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.decision import (
    ArgValue,
    DecisionRecord,
    DecisionSource,
    DesignVerdict,
    InputAnswer,
    InputRequest,
    Phase,
    RunStatus,
)
from gemini_agent.schemas.results import KPISet

InputMode = Literal["assume", "ask", "pause"]
"""assume: record labelled register defaults (non-interactive); ask: prompt on the terminal;
pause: save state and stop until ``resume`` brings the answer."""


class ToolRecord(StrictModel):
    """One executed tool call; ``id`` is the evidence id quoted in Decision Records."""

    id: str
    tool: str
    phase: Phase
    args: dict[str, ArgValue]
    ok: bool
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    stub: bool = False
    candidate_ids: list[str] = Field(default_factory=list)
    source: DecisionSource
    path: str | None = Field(default=None, description="Full output, relative to the run dir.")
    signature: str = Field(
        default="", description="Tool name + normalised arguments (duplicate-call guard)."
    )
    loop: int = Field(default=0, ge=0, description="Verification loop the call ran in.")
    data_keys: list[str] = Field(
        default_factory=list, description="Field names of the result data (citable evidence keys)."
    )


class Verification(StrictModel):
    """Verdict copied verbatim from a ``verify_design`` result (AG-11)."""

    candidate_id: str
    verdict: DesignVerdict
    violations: list[str] = Field(default_factory=list)
    verifier: str
    tool_result_id: str
    phase: Phase


class PhaseProgress(StrictModel):
    """Budget use and completion inside one phase pass."""

    steps_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    decisions: int = 0
    fallbacks: int = 0
    duplicate_rejections: int = 0
    completed_tools: list[str] = Field(default_factory=list)
    started: bool = False
    engine_done: bool = False


class RunState(StrictModel):
    """Everything the runner needs to continue a run after a pause or a crash."""

    run_id: str
    user_prompt: str
    engine: str
    input_mode: InputMode
    model_tag: str = Field(default="", description="CLI --model override, reused by resume.")
    status: RunStatus = RunStatus.RUNNING
    phase: Phase = Phase.P0
    loop_count: int = 0
    earlier_pass_fallbacks: int = Field(
        default=0, description="Policy fallbacks of P6–P10 passes discarded by a loop-back."
    )
    in_scope: bool = True
    scope_reason: str = ""
    conflicts: list[str] = Field(default_factory=list)
    progress: dict[str, PhaseProgress] = Field(default_factory=dict)
    tool_results: list[ToolRecord] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    kpis: dict[str, KPISet] = Field(default_factory=dict)
    guard_failed: list[str] = Field(default_factory=list)
    shortlist: list[str] = Field(default_factory=list)
    selected_id: str | None = None
    verifications: list[Verification] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)
    gap_questions: dict[str, str] = Field(default_factory=dict)
    answers: list[InputAnswer] = Field(default_factory=list)
    pending: InputRequest | None = None
    decision_records: list[DecisionRecord] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    approval: Literal["approved", "rejected", "pending"] | None = None
    final_summary: str = ""
    escalation_reason: str = ""

    def phase_progress(self, phase: Phase) -> PhaseProgress:
        """Progress of the current pass of ``phase`` (created on first use)."""
        return self.progress.setdefault(phase.value, PhaseProgress())

    def answer_values(self) -> dict[str, JsonValue]:
        """Assumption id → answered or assumed value."""
        return {a.assumption_id: a.answer for a in self.answers if a.assumption_id}

    def rejected_ids(self) -> set[str]:
        """Candidates the verifier REJECTED in this run."""
        return {v.candidate_id for v in self.verifications if v.verdict == DesignVerdict.REJECTED}

    def excluded_ids(self) -> set[str]:
        """Candidates that failed DesignGuard or were REJECTED (never offered or selected)."""
        return set(self.guard_failed) | self.rejected_ids()

    def fallback_count(self) -> int:
        """LLM decisions and Decision Records that fell back to the policy (AG-03)."""
        steps = sum(progress.fallbacks for progress in self.progress.values())
        records = sum(1 for r in self.decision_records if r.decision_source == "policy_fallback")
        return steps + records + self.earlier_pass_fallbacks

    def latest_verification(self, candidate_id: str | None) -> Verification | None:
        """Most recent verification of a candidate."""
        for item in reversed(self.verifications):
            if item.candidate_id == candidate_id:
                return item
        return None

    def evidence_for(self, candidate_ids: set[str] | None = None, tools: set[str] | None = None):
        """Successful tool records touching the candidates and/or produced by the tools."""
        records: list[ToolRecord] = []
        for record in self.tool_results:
            if not record.ok:
                continue
            hit_candidate = candidate_ids is not None and bool(
                candidate_ids & set(record.candidate_ids)
            )
            hit_tool = tools is not None and record.tool in tools
            if hit_candidate or hit_tool:
                records.append(record)
        return records

    def summary_dict(self) -> dict[str, Any]:
        """Compact run summary written to ``run_summary.json``."""
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "phase": self.phase.value,
            "engine": self.engine,
            "input_mode": self.input_mode,
            "selected_candidate": self.selected_id,
            "verdict": self.verdict_value(),
            "approval": self.approval,
            "loop_count": self.loop_count,
            "excluded_candidates": sorted(self.excluded_ids()),
            "llm_fallbacks": self.fallback_count(),
            "conflicts": self.conflicts,
            "assumptions_and_answers": [a.label for a in self.answers],
            "decision_records": [
                {
                    "dr_id": r.dr_id,
                    "choice": r.choice,
                    "decision_source": r.decision_source,
                    "stub_evidence": r.stub_evidence,
                }
                for r in self.decision_records
            ],
            "tool_calls": len(self.tool_results),
            "stub_tool_calls": sum(1 for r in self.tool_results if r.stub),
            "final_summary": self.final_summary,
            "escalation_reason": self.escalation_reason,
        }

    def verdict_value(self) -> str | None:
        """Verdict of the selected candidate as returned by the verifier (None if never run)."""
        latest = self.latest_verification(self.selected_id)
        return latest.verdict.value if latest else None
