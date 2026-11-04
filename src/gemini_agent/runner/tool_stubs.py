"""Labelled tool stubs that let the workflow run P0–P10 without the engineering tools.

Since WP-14 ``default_toolbox`` binds the real tools (``runner/real_tools.py``); this catalogue
(``stub_toolbox``) is kept for tests of the workflow mechanics (fast, no simulation).

WP-12's TL-17/TL-18 tools (``model_gap_analysis`` … ``register_model``) are bound for real
(``tools/model_builder_tools.model_builder_specs``, ``stub=False``).

Rules for every stub:
- returns the shared schemas (``ToolResult``, ``Candidate``, ``KPISet``) so the runner and the
  engines are exercised exactly as with the real tools;
- invents no engineering number: values come from ``config/*.yaml`` or spec §3.4, computed
  quantities stay ``None``;
- its summary starts with ``STUB`` and it carries a warning naming the WP that replaces it;
- verification never returns ACCEPTED (only WP-09's ``DesignVerifier`` may, golden rule 3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, JsonValue

from gemini_agent.runner import ranking
from gemini_agent.runner.toolbox import (
    CandidateArgs,
    DepthArgs,
    FlowArgs,
    KnowledgeArgs,
    NoArgs,
    OptimiseArgs,
    RequestInputArgs,
    SimulateArgs,
    Toolbox,
    ToolContext,
    ToolSpec,
)
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling, StrictModel, ToolResult
from gemini_agent.schemas.config import DesignBasis, load_config
from gemini_agent.schemas.decision import DesignVerdict
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools.knowledge import knowledge_search
from gemini_agent.tools.model_builder_tools import model_builder_specs

GAP_IDS: tuple[str, ...] = ("DB-30", "DB-31", "DB-32")
"""Design-basis ambiguities the Requirements agent asks about or assumes (spec §2.4)."""

GAP_QUESTIONS: dict[str, str] = {
    "DB-30": (
        "How should '330 daily steps' be read: A = each doublet available 330 of 365 days "
        "(35-day outage, demand met all year) or B = simulate only 330 days?"
    ),
    "DB-31": "What district-heating return temperature should the design use (degC)?",
    "DB-32": "What cooling-network return temperature should the design use (degC)?",
}


def stub_warning(owner: str) -> str:
    """Return the standard warning carried by every stub result."""
    return f"stub tool: no engineering result; replaced by {owner}"


# --------------------------------------------------------------------------- output models
class StubOutput(StrictModel):
    """Placeholder output of a tool that is not built yet."""

    tool: str
    owner: str
    status: Literal["stub"] = "stub"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    note: str = ""


class RobustBackupOutput(StrictModel):
    """``robust_backup`` stub output: the real field names, no variant (no TL-15 evidence)."""

    candidate_id: str
    robust_candidate_id: str | None = None
    robust_backup_percentile: float | None = None
    installed_backup_mw: dict[str, float] = Field(default_factory=dict)
    robust_backup_mw: dict[str, float] = Field(default_factory=dict)
    probability_demand_met: float | None = None
    probability_demand_met_robust: float | None = None
    robust_meets_demand: bool = False
    reason: str = ""


class DesignBasisOutput(StrictModel):
    """``load_design_basis`` stub: the fixed inputs plus the open design-basis gaps."""

    design_basis: DesignBasis
    open_gaps: list[str]
    gap_questions: dict[str, str]


class CandidateSet(StrictModel):
    """Generated candidates (spec §3.4)."""

    candidates: list[Candidate]


class GuardCheck(StrictModel):
    """Hard-limit check of one candidate."""

    candidate_id: str
    passed: bool
    violations: list[str] = Field(default_factory=list)


class GuardOutput(StrictModel):
    """``design_guard`` output."""

    checks: list[GuardCheck]


class KPIBatch(StrictModel):
    """KPIs of several candidates (``simulate_annual`` / ``economics``)."""

    mode: str
    kpis: list[KPISet]


class ShortlistOutput(StrictModel):
    """``optimise_design`` output: ranked shortlist."""

    shortlist: list[str]
    kpi_evidence: bool


class VerificationOutput(StrictModel):
    """``verify_design`` output; the runner copies ``verdict`` verbatim."""

    candidate_id: str
    verdict: DesignVerdict
    violations: list[str] = Field(default_factory=list)
    verifier: str


class ResultsView(StrictModel):
    """Read-only KPI view for the reviewer."""

    candidate_id: str | None
    kpis: KPISet | None
    shortlist: list[str]


class ReportOutput(StrictModel):
    """``build_report`` output."""

    path: str


# --------------------------------------------------------------------------- helpers
def _generic(name: str, owner: str, description: str, args_model: type = NoArgs) -> ToolSpec:
    def func(args: Any, context: ToolContext) -> ToolResult[StubOutput]:
        inputs = args.model_dump(mode="json")
        return ToolResult[StubOutput](
            data=StubOutput(tool=name, owner=owner, inputs=inputs, note=description),
            summary=f"STUB {name}: not built yet ({owner}); no numbers produced.",
            warnings=[stub_warning(owner)],
        )

    return ToolSpec(name, description, args_model, func, owner=owner, stub=True)


def _select(context: ToolContext, candidate_id: str) -> list[Candidate]:
    if candidate_id == "all":
        return list(context.candidates)
    chosen = [c for c in context.candidates if c.id == candidate_id]
    if not chosen:
        known = ", ".join(c.id for c in context.candidates) or "none generated yet"
        raise ValueError(f"unknown candidate_id '{candidate_id}' (known: {known})")
    return chosen


# --------------------------------------------------------------------------- tools
def load_design_basis(args: NoArgs, context: ToolContext) -> ToolResult[DesignBasisOutput]:
    """TL-01 stub: loads DB-01…19 from config; derived DB-20…29 arrive with WP-04."""
    basis = load_config("design_basis", context.config_dir)
    register = load_config("assumptions", context.config_dir)
    gaps = [gid for gid in GAP_IDS if register.get(gid).status == "default"]
    return ToolResult[DesignBasisOutput](
        data=DesignBasisOutput(
            design_basis=basis,
            open_gaps=gaps,
            gap_questions={gid: GAP_QUESTIONS[gid] for gid in gaps},
        ),
        summary=(
            f"STUB load_design_basis: DB-01…19 loaded from config; derived DB-20…29 not computed "
            f"(WP-04). Open gaps: {', '.join(gaps) or 'none'}."
        ),
        warnings=[stub_warning("WP-04")],
        sources=["config/design_basis.yaml", "config/assumptions.yaml"],
    )


_CONCEPTS: tuple[tuple[str, int, float | None, str | None, str], ...] = (
    ("C0", 0, None, None, "No geothermal: grid-gas boilers, electric chillers (counterfactual)."),
    ("C1", 3, None, None, "3 doublets, plate HEX + gas boiler/CHP top-up, electric chillers."),
    ("C2", 2, None, None, "2 doublets, HEX + HTHP + CHP + boiler; free cooling + electric."),
    ("C3", 2, None, None, "As C2; free cooling + absorption + small electric peak chiller."),
    ("C4", 2, 3200.0, None, "2 doublets at about 3.2 km, direct HEX; free cooling + absorption."),
    ("C5", 3, 1500.0, None, "Doublets at 1.5 km with large heat pumps; free cooling + electric."),
    ("C6", 2, None, "C2", "Parent + seasonal HT-ATES storage of summer surplus."),
    ("C7", 2, None, "C2", "Parent with pressurised loop, no degassing, gas reinjected."),
    ("C8", 3, None, "C2", "Parent + 1 doublet as N-1 redundancy."),
)
"""Spec §3.4 table (depths 3.2 km / 1.5 km from that table). Parents of C6–C8 are C2 in the stub;
WP-06 picks the best of C2/C3 after screening."""


def generate_candidates(args: NoArgs, context: ToolContext) -> ToolResult[CandidateSet]:
    """TL-09 stub: C0–C8 structure from spec §3.4; sizing and GEMINI JSON arrive with WP-06."""
    basis = load_config("design_basis", context.config_dir)
    register = load_config("assumptions", context.config_dir)
    heating_return = float(context.answers.get("DB-31", register.get("DB-31").value))
    cooling_return = float(context.answers.get("DB-32", register.get("DB-32").value))
    candidates = []
    for concept, doublets, depth, parent, text in _CONCEPTS:
        wells = doublets > 0
        gas = GasHandling.PRESSURISED_REINJECT if concept == "C7" else GasHandling.DEGAS_USE
        candidates.append(
            Candidate(
                id=f"{concept}-{doublets}d",
                concept=concept,  # type: ignore[arg-type]
                parent_id=f"{parent}-2d" if parent else None,
                description=text.strip(),
                doublets=doublets,
                depth_m=(depth or basis.reservoir.mid_depth_m) if wells else None,
                design_flow_m3h=basis.doublets.flow_max_m3h if wells else None,
                gas_handling=gas if wells else None,
                heating_return_degc=heating_return,
                cooling_return_degc=cooling_return,
            )
        )
    return ToolResult[CandidateSet](
        data=CandidateSet(candidates=candidates),
        summary=(
            f"STUB generate_candidates: {len(candidates)} candidates C0–C8 (spec §3.4 structure, "
            "return temperatures DB-31/DB-32); no equipment sizing or GEMINI JSON yet (WP-06)."
        ),
        warnings=[
            stub_warning("WP-06"),
            (
                "placeholder: design_flow_m3h = DB-11 maximum per doublet for every candidate; "
                "not a sizing result (WP-06/WP-11 size flows)"
            ),
        ],
        assumption_ids=["DB-31", "DB-32"],
        sources=["spec §3.4"],
    )


def design_guard(args: CandidateArgs, context: ToolContext) -> ToolResult[GuardOutput]:
    """TL-10 stub: doublet-count and flow hard limits from ``config/limits.yaml`` only."""
    limits = load_config("limits", context.config_dir)
    checks = []
    for candidate in _select(context, args.candidate_id):
        violations = []
        if candidate.concept != "C0":
            if not limits.doublet_count_min <= candidate.doublets <= limits.doublet_count_max:
                violations.append(
                    f"{candidate.doublets} doublets outside "
                    f"{limits.doublet_count_min}–{limits.doublet_count_max}"
                )
            flow = candidate.design_flow_m3h or 0.0
            if not limits.flow_min_active_m3h <= flow <= limits.flow_max_m3h:
                violations.append(
                    f"design flow {flow} m3/h outside "
                    f"{limits.flow_min_active_m3h}–{limits.flow_max_m3h} m3/h"
                )
        checks.append(
            GuardCheck(candidate_id=candidate.id, passed=not violations, violations=violations)
        )
    failed = [check.candidate_id for check in checks if not check.passed]
    return ToolResult[GuardOutput](
        data=GuardOutput(checks=checks),
        summary=(
            f"STUB design_guard: {len(checks) - len(failed)}/{len(checks)} pass count/flow limits"
            f"{'; failed: ' + ', '.join(failed) if failed else ''} (full guard: WP-06)."
        )[:300],
        warnings=[stub_warning("WP-06")],
        sources=["config/limits.yaml"],
    )


def simulate_annual(args: SimulateArgs, context: ToolContext) -> ToolResult[KPIBatch]:
    """TL-12 stub: returns empty ``KPISet`` per candidate (WP-07)."""
    kpis = [KPISet(candidate_id=c.id) for c in _select(context, args.candidate_id)]
    return ToolResult[KPIBatch](
        data=KPIBatch(mode=args.mode, kpis=kpis),
        summary=f"STUB simulate_annual({args.mode}): {len(kpis)} candidate(s), KPIs not computed "
        "(WP-07).",
        warnings=[stub_warning("WP-07")],
    )


def economics(args: CandidateArgs, context: ToolContext) -> ToolResult[KPIBatch]:
    """TL-14 stub: returns empty ``KPISet`` per candidate (WP-08)."""
    kpis = [KPISet(candidate_id=c.id) for c in _select(context, args.candidate_id)]
    return ToolResult[KPIBatch](
        data=KPIBatch(mode="economics", kpis=kpis),
        summary=f"STUB economics: {len(kpis)} candidate(s), CAPEX/OPEX/LCOH not computed (WP-08).",
        warnings=[stub_warning("WP-08")],
    )


def optimise_design(args: OptimiseArgs, context: ToolContext) -> ToolResult[ShortlistOutput]:
    """TL-13 stub: shortlist by the deterministic ranking rule (WP-11 sizes the designs).

    Guard-failed and verifier-REJECTED candidates are excluded, so a loop back from P7/P9 moves
    to the next candidate; WP-11 resizes against ``context.violations`` instead.
    """
    excluded = context.excluded_ids
    ranked = ranking.rank_candidates(context.candidates, context.kpis, exclude_ids=excluded)
    shortlist = ranked[: args.top_n]
    if args.candidate_id is not None and args.candidate_id not in excluded:
        # Review loop (DR-12): the adopted design is the shortlist (the real TL-13 sizes from it).
        shortlist = [args.candidate_id] + [c for c in shortlist if c != args.candidate_id]
        shortlist = shortlist[: args.top_n]
    evidence = ranking.has_kpi_evidence(context.kpis)
    skipped = f"; excluded {', '.join(sorted(excluded))}" if excluded else ""
    return ToolResult[ShortlistOutput](
        data=ShortlistOutput(shortlist=shortlist, kpi_evidence=evidence),
        summary=(
            f"STUB optimise_design: shortlist {', '.join(shortlist) or 'empty'} "
            f"({'LCOH ranking' if evidence else 'generation order, no KPIs'}{skipped}); "
            "sizing in WP-11."
        )[:300],
        warnings=[stub_warning("WP-11")],
    )


def verify_design(args: CandidateArgs, context: ToolContext) -> ToolResult[VerificationOutput]:
    """TL-16 stub: never ACCEPTED; the design stays UNVERIFIED until WP-09's DesignVerifier."""
    candidate_id = args.candidate_id
    if candidate_id == "all":
        candidate_id = context.selected_id or (context.shortlist[0] if context.shortlist else "")
    if not candidate_id:
        raise ValueError("no candidate selected for verification")
    _select(context, candidate_id)
    return ToolResult[VerificationOutput](
        data=VerificationOutput(
            candidate_id=candidate_id, verdict=DesignVerdict.UNVERIFIED, verifier="stub"
        ),
        summary=(
            f"STUB verify_design({candidate_id}): DesignVerifier not wired (WP-09); "
            "design stays UNVERIFIED."
        ),
        warnings=[stub_warning("WP-09")],
    )


def read_results(args: CandidateArgs, context: ToolContext) -> ToolResult[ResultsView]:
    """Read-only view of the selected candidate's KPIs (reviewer, P9)."""
    candidate_id = None if args.candidate_id == "all" else args.candidate_id
    candidate_id = candidate_id or context.selected_id
    if candidate_id is not None:
        _select(context, candidate_id)
    kpis = context.kpis.get(candidate_id) if candidate_id else None
    known = kpis is not None and kpis.lcoh_per_mwh is not None
    return ToolResult[ResultsView](
        data=ResultsView(candidate_id=candidate_id, kpis=kpis, shortlist=list(context.shortlist)),
        summary=(
            f"read_results: candidate {candidate_id or 'none'}; KPIs "
            f"{'available' if known else 'not computed (stub tools)'}."
        ),
    )


def robust_backup(args: CandidateArgs, context: ToolContext) -> ToolResult[RobustBackupOutput]:
    """P8 workflow step without TL-15 evidence: no robust variant (DR-11 keeps the backups)."""
    candidate_id = args.candidate_id if args.candidate_id != "all" else context.selected_id
    if not candidate_id:
        raise ValueError("no candidate selected for the robust-backup step")
    _select(context, candidate_id)
    return ToolResult[RobustBackupOutput](
        data=RobustBackupOutput(
            candidate_id=candidate_id, reason="stub uncertainty: no TL-15 robust nameplates"
        ),
        summary=f"STUB robust_backup({candidate_id}): no TL-15 evidence, no robust variant.",
        warnings=[stub_warning("WP-11")],
    )


def build_report(args: NoArgs, context: ToolContext) -> ToolResult[ReportOutput]:
    """Write a numbers-free report draft listing the Decision Records (WP-14)."""
    path = context.run_dir / "report_draft.md"
    lines = [
        f"# GeoAgent report draft — run {context.run_id}",
        "",
        "STUB report: the full report with figures and KPIs is built in WP-14 from kpis.json.",
        "",
        f"Selected candidate: {context.selected_id or 'none'}",
        "",
        "## Decision Records",
    ]
    for record in context.decision_records:
        lines.append(f"- {record['dr_id']} {record['title']}: {record['choice_label']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ToolResult[ReportOutput](
        data=ReportOutput(path=str(path.name)),
        summary=f"STUB build_report: draft with {len(context.decision_records)} DRs written "
        "(full report WP-14).",
        warnings=[stub_warning("WP-14")],
    )


def _knowledge(args: KnowledgeArgs, context: ToolContext) -> ToolResult[Any]:
    return knowledge_search(args.query, args.limit)


def stub_toolbox() -> Toolbox:
    """Catalogue bound to the labelled stubs (tests of the workflow mechanics only)."""
    return Toolbox(stub_specs())


def stub_specs() -> list[ToolSpec]:
    """Return the stub catalogue (``stub_toolbox``); ``default_toolbox`` binds the real tools."""
    return [
        ToolSpec(
            "knowledge_search",
            "Search reference cards and GEMINI docs; returns cited snippets.",
            KnowledgeArgs,
            _knowledge,
            owner="WP-10",
            stub=False,
        ),
        ToolSpec(
            "request_missing_input",
            "Ask the user for a missing design input, or record the register default.",
            RequestInputArgs,
            None,
            owner="WP-10",
            stub=False,
            runner_handled=True,
        ),
        ToolSpec(
            "load_design_basis",
            "Load design basis DB-01…19 and list open design-basis gaps.",
            NoArgs,
            load_design_basis,
            owner="WP-04",
        ),
        _generic("profile_demand", "WP-04", "Demand peaks, monthly totals, load duration."),
        _generic("climate", "WP-04", "Daily dry-bulb, humidity, wet-bulb and design days."),
        _generic(
            "reservoir_conditions",
            "WP-05",
            "Reservoir temperature and pressure at a depth.",
            DepthArgs,
        ),
        _generic("productivity_index", "WP-05", "Productivity and injectivity index with skin."),
        _generic(
            "doublet_operating_point",
            "WP-05",
            "ESP and injection pump operating point for a flow.",
            FlowArgs,
        ),
        _generic("thermal_breakthrough", "WP-05", "Thermal breakthrough time and decline."),
        _generic("pressure_interference", "WP-05", "Pressure interference between doublets."),
        ToolSpec(
            "generate_candidates",
            "Generate candidate configurations C0–C8.",
            NoArgs,
            generate_candidates,
            owner="WP-06",
        ),
        ToolSpec(
            "design_guard",
            "Check candidates against hard limits before simulation.",
            CandidateArgs,
            design_guard,
            owner="WP-06",
        ),
        ToolSpec(
            "simulate_annual",
            "Simulate 365 days for candidates (fast or full mode).",
            SimulateArgs,
            simulate_annual,
            owner="WP-07",
        ),
        ToolSpec(
            "economics",
            "CAPEX, OPEX, LCOH and LCOC for candidates.",
            CandidateArgs,
            economics,
            owner="WP-08",
        ),
        _generic("co2", "WP-08", "Annual CO2 emissions and reduction versus C0.", CandidateArgs),
        _generic("exergy", "WP-08", "Exergy efficiency KPI.", CandidateArgs),
        _generic("sensitivity", "WP-08", "Cost and KPI sensitivity ranges.", CandidateArgs),
        ToolSpec(
            "optimise_design",
            "Size the top candidates and schedule maintenance windows.",
            OptimiseArgs,
            optimise_design,
            owner="WP-11",
        ),
        _generic("uncertainty", "WP-11", "Monte Carlo P10/P50/P90 on a design.", CandidateArgs),
        ToolSpec(
            "robust_backup",
            "Re-simulate and re-price a design with its TL-15 robust backups (DR-11 evidence).",
            CandidateArgs,
            robust_backup,
            owner="WP-11",
        ),
        ToolSpec(
            "verify_design",
            "Run the 4-layer DesignVerifier on a candidate.",
            CandidateArgs,
            verify_design,
            owner="WP-09",
        ),
        *model_builder_specs(),
        ToolSpec(
            "read_results",
            "Read the selected candidate's KPIs (read-only).",
            CandidateArgs,
            read_results,
            owner="WP-10",
            stub=False,
        ),
        ToolSpec(
            "build_report",
            "Build the report draft from tool outputs and Decision Records.",
            NoArgs,
            build_report,
            owner="WP-14",
        ),
        _generic("plot_figures", "WP-14", "Plot supply vs demand, comparison and token charts."),
    ]
