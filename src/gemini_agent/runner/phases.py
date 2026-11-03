"""Phase table of the deterministic workflow skeleton P0–P10 (spec §5.2).

Each phase has a budget (AG-04), the agent role that decides in it, the tools that must have
run before the phase may close (spec §5.4.6: the runner calls them itself if the agent skips
them) and the Decision Records made at the end of the phase (AG-07). DR-02 (depth) and DR-07
(gas handling) are taken in P5 rather than P2 because their options are candidates screened
in P5. WP-14 added ``sensitivity`` to P8 (SUB-11 needs a sensitivity result) and ``plot_figures``
to P10 (TL-21 figures from tool outputs). Pre-L-2 added workflow tools — run by the runner itself
after the required tools and before the Decision Records, never offered to the agent — and the
P8 ``robust_backup`` step with DR-11 (OPT-ROBUST-BACKUP). The L-1 P0 follow-up made P0 a pure
intake check: its only valid steps are ``finalise`` (in scope) and ``refuse`` (off-topic), and the
runner closes it from the deterministic scope check when the step budget runs out. Runner J
(2026-11-03) gave P5 an explicit 90 k token budget: it makes the most LLM calls (two required
tools plus seven Decision Records, each DR choice 8–9 k tokens with retries on verbose models),
and both qwen3.6:27b and gemma4:26b-a4b had used ≈ 60 k of the default 60 k when DR-08 came.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.decision import ArgValue, Phase, Role

SELECTED = "$selected"
"""Placeholder in default arguments, replaced by the candidate selected in P6."""

PHASE_ORDER: tuple[Phase, ...] = tuple(Phase)


class PhaseBudget(StrictModel):
    """Per-phase step, tool-call and token budget (AG-04). Exceeding any ⇒ escalate."""

    max_steps: int = 12
    max_tool_calls: int = 10
    max_tokens: int = 60_000


@dataclass(frozen=True)
class PhaseSpec:
    """Static description of one phase."""

    phase: Phase
    role: Role
    objective: str
    required_tools: tuple[str, ...] = ()
    default_args: dict[str, dict[str, ArgValue]] = field(default_factory=dict)
    budget: PhaseBudget = field(default_factory=PhaseBudget)
    decision_records: tuple[str, ...] = ()
    workflow_tools: tuple[str, ...] = ()
    """Deterministic steps the runner runs at phase close, after the required tools and before
    the Decision Records (default arguments; not in the agent's tool list, context or AG-04
    tool-call budget)."""


PHASE_SPECS: dict[Phase, PhaseSpec] = {
    spec.phase: spec
    for spec in (
        PhaseSpec(
            Phase.P0,
            Role.LEAD_ENGINEER,
            "Check the request is about designing the geothermal district heating (100 degC) and "
            "cooling (20 degC) system; refuse and redirect off-topic requests. The runner's "
            "intake scope check already ran (CONTEXT in_scope, scope_reason): reply with exactly "
            "one step, finalise if in_scope is true or refuse if it is false. No tool and no "
            "other step kind is valid in P0.",
            budget=PhaseBudget(max_steps=3, max_tool_calls=2, max_tokens=20_000),
        ),
        PhaseSpec(
            Phase.P1,
            Role.REQUIREMENTS,
            "Load the design basis, demand profile and climate; resolve open design-basis gaps by "
            "asking the user or recording the labelled register default.",
            required_tools=("load_design_basis", "profile_demand", "climate"),
        ),
        PhaseSpec(
            Phase.P2,
            Role.SUBSURFACE,
            "Assess the resource: reservoir conditions, productivity, doublet operating point, "
            "thermal breakthrough and pressure interference.",
            required_tools=(
                "reservoir_conditions",
                "productivity_index",
                "doublet_operating_point",
                "thermal_breakthrough",
                "pressure_interference",
            ),
            decision_records=("DR-09",),
        ),
        PhaseSpec(
            Phase.P3,
            Role.SYSTEM_ARCHITECT,
            "Generate candidates C0–C8 and check them against the hard limits (DesignGuard).",
            required_tools=("generate_candidates", "design_guard"),
            default_args={"design_guard": {"candidate_id": "all"}},
            budget=PhaseBudget(max_steps=10, max_tool_calls=8),
        ),
        PhaseSpec(
            Phase.P4,
            Role.MODEL_BUILDER,
            "Find component types the candidates need that GEMINI lacks; build them if needed.",
            required_tools=("model_gap_analysis",),
            budget=PhaseBudget(max_steps=8, max_tool_calls=6, max_tokens=40_000),
        ),
        PhaseSpec(
            Phase.P5,
            Role.SYSTEM_ARCHITECT,
            "Screen all candidates with the fast 365-day simulation and economics.",
            required_tools=("simulate_annual", "economics"),
            default_args={
                "simulate_annual": {"candidate_id": "all", "mode": "fast"},
                "economics": {"candidate_id": "all"},
            },
            # Runner J: 2 tools + 7 DR choices (8–9 k tokens each with retries) ≈ 80 k on
            # verbose models; the 60 k default starved DR-08 (qwen3.6:27b, gemma4:26b-a4b).
            budget=PhaseBudget(max_tokens=90_000),
            decision_records=("DR-01", "DR-02", "DR-03", "DR-04", "DR-05", "DR-07", "DR-08"),
        ),
        PhaseSpec(
            Phase.P6,
            Role.SYSTEM_ARCHITECT,
            "Size the top 2–3 candidates and schedule non-overlapping maintenance windows; address "
            "any verifier violations listed in the context.",
            required_tools=("optimise_design",),
            default_args={"optimise_design": {"top_n": 3}},
            budget=PhaseBudget(max_steps=8, max_tool_calls=6),
            decision_records=("DR-06",),
        ),
        PhaseSpec(
            Phase.P7,
            Role.LEAD_ENGINEER,
            "Run the full daily simulation of the selected design and the 4-layer verifier.",
            required_tools=("simulate_annual", "verify_design"),
            default_args={
                "simulate_annual": {"candidate_id": SELECTED, "mode": "full"},
                "verify_design": {"candidate_id": SELECTED},
            },
            budget=PhaseBudget(max_steps=8, max_tool_calls=6),
        ),
        PhaseSpec(
            Phase.P8,
            Role.ECONOMICS,
            "Run the Monte Carlo uncertainty analysis and the cost sensitivity on the selected "
            "design; afterwards the runner (not you) prices its robust backups for DR-11.",
            required_tools=("uncertainty", "sensitivity"),
            default_args={
                "uncertainty": {"candidate_id": SELECTED},
                "sensitivity": {"candidate_id": SELECTED},
                "robust_backup": {"candidate_id": SELECTED},
            },
            budget=PhaseBudget(max_steps=8, max_tool_calls=6),
            decision_records=("DR-11",),
            workflow_tools=("robust_backup",),
        ),
        PhaseSpec(
            Phase.P9,
            Role.REVIEWER,
            "Independently review the selected configuration, KPIs and verification report.",
            required_tools=("read_results", "verify_design"),
            default_args={
                "read_results": {"candidate_id": SELECTED},
                "verify_design": {"candidate_id": SELECTED},
            },
            budget=PhaseBudget(max_steps=8, max_tool_calls=6),
            decision_records=("DR-10",),
        ),
        PhaseSpec(
            Phase.P10,
            Role.REPORTER,
            "Plot the figures, build the report draft, Decision Records and token summary; then "
            "ask for approval.",
            required_tools=("plot_figures", "build_report"),
            budget=PhaseBudget(max_steps=6, max_tool_calls=4),
        ),
    )
}


def next_phase(phase: Phase) -> Phase | None:
    """Phase after ``phase`` in the fixed order (None after P10)."""
    index = PHASE_ORDER.index(phase)
    return PHASE_ORDER[index + 1] if index + 1 < len(PHASE_ORDER) else None
