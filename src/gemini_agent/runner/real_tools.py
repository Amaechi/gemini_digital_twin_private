"""Real tool bindings of the workflow catalogue (WP-14 integration; spec §6, §5.4.4).

``real_toolbox(profile)`` binds every catalogue name to the deterministic tools of WP-04…WP-12
(``stub=False``) through thin adapters that keep the reducer contract of ``runner/toolbox.py``
(``load_design_basis.open_gaps``, ``generate_candidates.candidates``, ``design_guard.checks``,
``simulate_annual`` / ``economics`` / ``uncertainty`` ``.kpis``, ``optimise_design.shortlist``
(+ ``candidates``, ``kpis``), ``verify_design.verdict``). Adapters compute nothing themselves:
every number comes from a tool (TL-01…TL-23); they only route inputs and outputs.

A :class:`ToolSession` keeps the large tool outputs of a run in memory (fast and full years,
economics, TL-13 optima, TL-15 reports) so that P7's verifier evidence and P10's figures use the
same objects; the run's tool-result files stay compact (summaries + KPIs). The sized designs of
P6 (candidate + outage schedule) are also written to ``runs/<id>/session/`` so a resumed run
(new process) recovers them. Everything is deterministic: a cache miss recomputes.

Pipeline (policy order): P1 TL-01/02/02b (+ remembered user answers from
``JsonProjectKnowledgeStore``) → P2 TL-03…08 → P3 C0–C8 (spec §3.4) with TL-09 projects and
TL-10 guard → P4 TL-17 → P5 TL-12 fast year + TL-14 economics (C0 CO₂ first, exergy KPI) per
candidate → P6 TL-13 on the top-ranked concepts (``ranking.rank_candidates``) → P7 TL-12 full
year (+ TL-14 on it) and the DesignVerifier on the collected evidence → P8 TL-15 + cost
sensitivity, then the workflow's ``robust_backup`` step (the TL-15 robust backup nameplates
re-simulated and re-priced as ``<id>-robust``) and DR-11 (OPT-ROBUST-BACKUP) → P9 review →
P10 TL-21 figures and the report draft.

TL-13 depth options are limited to depths the DesignVerifier can accept (``depth_check``, TL-05
at the DB-11 maximum flow): the ESP-intake NPSH must be evaluable (SUB-NPSH bounds the vapour
pressure only up to 100 °C, PH-19) and, when degassing, the wellhead brine must stay at or below
that boiling bound at the SUB-PSUC letdown (PH-30; hotter brine flashes in the atmospheric
degasser, WP-11 physics check). With the cards' data this caps the register depths at 2.5 km;
saturation data above 100 °C would re-open the deeper options. Profiles (``full`` / ``quick``
/ ``smoke``) only trade the TL-13/TL-15 search effort (DE budget, Monte Carlo samples) against
runtime; ``full`` uses the register values.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import Field, JsonValue

from gemini_agent.memory import project_knowledge
from gemini_agent.memory.design_cache import DesignCache
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner import ranking
from gemini_agent.runner.decision_records import ROBUST_BACKUP_ID, robust_backup_rule
from gemini_agent.runner.tool_stubs import GAP_IDS, GAP_QUESTIONS
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
from gemini_agent.schemas.common import ConceptId, GasHandling, StrictModel, ToolResult
from gemini_agent.schemas.config import DesignBasis, load_config
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import config_generator as tl09
from gemini_agent.tools import scaling as tl22
from gemini_agent.tools.climate import ClimateProfile, profile_climate
from gemini_agent.tools.demand import profile_demand
from gemini_agent.tools.design_basis import DesignBasisReport, load_design_basis
from gemini_agent.tools.doublet import (
    DoubletDesign,
    OperatingPoint,
    OperatingPointRequest,
    doublet_operating_point,
)
from gemini_agent.tools.economics import (
    Co2Request,
    EconomicsReport,
    EconomicsRequest,
    SensitivityReport,
    co2_emissions,
    cost_sensitivity,
    economics,
)
from gemini_agent.tools.exergy import exergy_kpi
from gemini_agent.tools.knowledge import knowledge_search
from gemini_agent.tools.model_builder_tools import model_builder_specs
from gemini_agent.tools.optimise import (
    SIGNIFICANT_UNMET_MWH,
    DesignEvaluation,
    EvaluationResult,
    Evaluator,
    OptimisationResult,
    OptimiseRequest,
    capex_request,
    load_optimiser_settings,
)
from gemini_agent.tools.optimise import optimise_design as tl13_optimise_design
from gemini_agent.tools.resource import (
    ProductivityIndexRequest,
    ReservoirConditionsRequest,
    load_subsurface_basis,
    productivity_index,
    reservoir_conditions,
)
from gemini_agent.tools.simulate_annual import (
    AnnualSimulation,
    SimulateAnnualRequest,
    cache_config,
    cache_namespace,
    run_annual,
    verification_evidence,
    write_outputs,
)
from gemini_agent.tools.thermal import (
    InterferenceReport,
    InterferenceRequest,
    ThermalBreakthroughReport,
    ThermalBreakthroughRequest,
    pressure_interference,
    thermal_breakthrough,
)
from gemini_agent.tools.uncertainty import UncertaintyReport, UncertaintyRequest
from gemini_agent.tools.uncertainty import uncertainty as tl15_uncertainty
from gemini_agent.verify.design_guard import DesignGuard, GuardResult
from gemini_agent.verify.design_verifier import (
    NUMERIC_TOL,
    P_ATM_BAR,
    DoubletPointEvidence,
    EspPoint,
    SubsurfaceEvidence,
    VerificationEvidence,
    verify_design_spec,
)

TOOL_OWNER = "WP-14"
DERIVED_CONCEPTS = ("C6", "C7", "C8")
"""Spec §3.4: built on the best of C2/C3 (the generator's parent concept)."""
DEFAULT_PARENT_CONCEPT: ConceptId = "C2"
"""Parent of C6–C8 at generation (P3, before screening); spec §3.4 'best of C2/C3' — the P5
screening confirms or the Decision Records state otherwise."""
ROBUST_SUFFIX = "-robust"
"""Id suffix of a design with the TL-15 robust backup nameplates (DR-11, OPT-ROBUST-BACKUP)."""
SIZED_SUFFIXES = ("-opt", ROBUST_SUFFIX)
"""Designs that joined the run after screening (TL-13 optima, robust-backup variants)."""
SESSION_DIR = "session"
"""Sub-folder of ``runs/<id>/`` holding the sized designs (resume after a pause)."""

_CONCEPTS: tuple[tuple[str, int, float | None, str], ...] = (
    ("C0", 0, None, "No geothermal: grid-gas boilers, electric chillers (counterfactual)."),
    ("C1", 3, None, "3 doublets, plate HEX + gas boiler/CHP top-up, electric chillers."),
    ("C2", 2, None, "2 doublets, HEX + HTHP + CHP + boiler; free cooling + electric."),
    ("C3", 2, None, "As C2; free cooling + absorption + small electric peak chiller."),
    ("C4", 2, 3200.0, "2 doublets at about 3.2 km, direct HEX; free cooling + absorption."),
    ("C5", 3, 1500.0, "Doublets at 1.5 km with large heat pumps; free cooling + electric."),
    ("C6", 2, None, "Parent + seasonal HT-ATES storage of summer surplus."),
    ("C7", 2, None, "Parent with pressurised loop, no degassing, gas reinjected."),
    ("C8", 3, None, "Parent + 1 doublet as N-1 redundancy."),
)
"""Spec §3.4 table (doublets, depth: None = DB-01 mid-depth; 3.2 km / 1.5 km from the table)."""


# =========================================================================== profiles
BUDGET_ID = "RUN-BUDGET"
"""Register entry holding the ``quick`` / ``smoke`` search budgets (``full`` = OPT-DE / UQ-MC)."""
BUDGET_ENV = "GEOAGENT_BUDGET"
"""Environment variable naming the default budget (CI, tests); unset → ``full``."""


@dataclass(frozen=True)
class ToolProfile:
    """Search effort of TL-13/TL-15 (runtime only; no engineering value changes)."""

    name: str
    workers: int = 1
    day_indices: tuple[int, ...] | None = None
    """Quick checks only: simulated-day subset (economics cover those days); None: full year."""
    own_point_only: bool = False
    """TL-13 sizes the candidate's own doublet count and (verifiable) depth, not OPT-SPACE."""
    max_concepts: int | None = None
    """At most this many concepts sized in P6 (None: the phase's ``top_n``)."""
    top_k: int | None = None
    popsize: int | None = None
    maxiter: int | None = None
    maxiter_outage: int | None = None
    dispatch_search: bool = True
    storage_scan: bool = True
    mc_samples: int | None = None
    one_at_a_time: bool = True
    uq_cases: bool = True


_BUDGET_KEYS = frozenset(
    {
        "own_point_only", "max_concepts", "top_k", "popsize", "maxiter", "maxiter_outage",
        "dispatch_search", "storage_scan", "mc_samples", "one_at_a_time", "uq_cases",
        "day_step", "workers",
    }
)  # fmt: skip


def default_budget() -> str:
    """Budget named by ``GEOAGENT_BUDGET`` (CI, tests), else ``full``."""
    return os.environ.get(BUDGET_ENV, "").strip() or "full"


def make_profile(
    name: str | None = None,
    workers: int | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolProfile:
    """Return the named budget from the register (RUN-BUDGET); ``None`` → ``default_budget()``.

    ``full`` keeps the OPT-DE / UQ-MC register values; ``workers`` (else the budget's, else all
    CPUs) sets the TL-13/TL-15 process count (1 = no worker pool).
    """
    name = name or default_budget()
    register = load_config("assumptions", config_dir)
    budgets: dict[str, Any] = register.get(BUDGET_ID).value  # type: ignore[assignment]
    if name not in budgets:
        raise ValueError(f"unknown tool profile '{name}' ({', '.join(budgets)}; {BUDGET_ID})")
    values = dict(budgets[name] or {})
    unknown = set(values) - _BUDGET_KEYS
    if unknown:
        raise ValueError(f"{BUDGET_ID}.{name}: unknown keys {sorted(unknown)}")
    step = values.pop("day_step", None)
    cpus = workers or values.pop("workers", None) or os.cpu_count() or 1
    values.pop("workers", None)
    return ToolProfile(
        name,
        workers=int(cpus),
        day_indices=tuple(range(0, 365, int(step))) if step else None,
        **values,
    )


# =========================================================================== outputs
class DesignBasisToolOutput(StrictModel):
    """TL-01 report + the open design-basis gaps (DB-30…32) the runner asks about."""

    report: DesignBasisReport
    design_basis: DesignBasis = Field(description="DB-01…19 (= report.fixed; eval checks).")
    open_gaps: list[str]
    gap_questions: dict[str, str]
    remembered_answers: dict[str, JsonValue] = Field(
        default_factory=dict, description="Durable user answers of earlier runs (MEM-01)."
    )


class ConfigSummary(StrictModel):
    """Compact TL-09 result of one candidate (the project JSON stays in the session)."""

    candidate_id: str
    config_hash: str = ""
    components: int = 0
    connections: int = 0
    equipment_mw: dict[str, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    error: str = ""


class CandidateSet(StrictModel):
    """Generated candidates (spec §3.4) with their TL-09 project summaries."""

    candidates: list[Candidate]
    configs: list[ConfigSummary]


class GuardCheck(StrictModel):
    """Runner view of one TL-10 result."""

    candidate_id: str
    passed: bool
    violations: list[str] = Field(default_factory=list)


class GuardToolOutput(StrictModel):
    """TL-10 results (``checks`` is the reducer field)."""

    checks: list[GuardCheck]
    results: list[GuardResult]


class AnnualSummary(StrictModel):
    """Compact TL-12 result of one candidate (daily results stay in the session / CSV)."""

    candidate_id: str
    mode: str
    status: str = "ok"
    reason: str = ""
    config_hash: str = ""
    simulated_days: int = 0
    outage_start_indices: list[int] = Field(default_factory=list)
    max_fast_full_discrepancy_frac: float | None = None
    invalid_full_days: int = 0
    right_sized_mw: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


class KPIBatch(StrictModel):
    """KPIs of several candidates (``simulate_annual`` / ``economics`` reducer field ``kpis``)."""

    mode: str
    kpis: list[KPISet]
    simulations: list[AnnualSummary] = Field(default_factory=list)
    economics: list[dict[str, JsonValue]] = Field(default_factory=list)
    cost_items: list[dict[str, JsonValue]] = Field(
        default_factory=list, description="TL-14 cost items used (SUB-14 reads ``verify``)."
    )


class SizedDesign(StrictModel):
    """One TL-13 optimum, as it joins the run."""

    candidate: Candidate
    concept_candidate_id: str
    parent_concept: ConceptId | None = None
    outage_start_indices: list[int] | None = None
    evaluation: DesignEvaluation | None = None
    depth_options_m: list[float] = Field(default_factory=list)
    excluded_depths_m: list[float] = Field(default_factory=list)
    robust_backup_of: str | None = Field(
        default=None, description="TL-15 design whose robust backups this variant installs."
    )


class OptimiseToolOutput(StrictModel):
    """TL-13 output for the runner: ranked shortlist + the sized candidates and their KPIs."""

    shortlist: list[str]
    kpi_evidence: bool
    candidates: list[Candidate]
    kpis: list[KPISet]
    designs: list[dict[str, JsonValue]]
    skipped: dict[str, str] = Field(default_factory=dict)


class UncertaintyToolOutput(StrictModel):
    """TL-15 compact output (full report in ``runs/<id>/uncertainty_<candidate>.json``)."""

    candidate_id: str
    kpis: list[KPISet]
    percentiles: dict[str, dict[str, float]]
    probability_demand_met: float | None
    probability_demand_met_robust: float | None
    installed_backup_mw: dict[str, float]
    robust_backup_mw: dict[str, float]
    cases: list[dict[str, JsonValue]]
    samples: int
    failed_samples: int
    file: str = ""


class RobustBackupToolOutput(StrictModel):
    """OPT-ROBUST-BACKUP step (P8 workflow): the TL-15 design with its robust backup nameplates.

    The variant is re-simulated and re-priced on the same year basis as the analysed design
    (``year_mode``); ``delta_*`` = variant − analysed design. Probabilities are TL-15's (share of
    the Monte Carlo samples meeting demand at the installed / robust nameplates). DR-11 evidence.
    """

    candidate_id: str
    robust_candidate_id: str | None = None
    robust_backup_percentile: float
    installed_backup_mw: dict[str, float]
    robust_backup_mw: dict[str, float]
    added_backup_mw: dict[str, float] = Field(default_factory=dict)
    probability_demand_met: float | None
    probability_demand_met_robust: float | None
    year_mode: str = ""
    delta_lcoh_per_mwh: float | None = None
    delta_lcoc_per_mwh: float | None = None
    delta_lcoh_plus_lcoc_per_mwh: float | None = None
    delta_capex_million: float | None = None
    delta_co2_t_per_yr: float | None = None
    robust_meets_demand: bool = False
    reason: str = Field(default="", description="Why no robust variant is offered (if none).")
    candidates: list[Candidate] = Field(
        default_factory=list, description="The robust variant (joins the run, not selected)."
    )
    kpis: list[KPISet] = Field(default_factory=list, description="The variant's KPIs.")


class SensitivityToolOutput(StrictModel):
    """TL-14 cost sensitivity (tornado) of LCOH and LCOC."""

    candidate_id: str
    reports: dict[str, SensitivityReport]


class Co2ToolOutput(StrictModel):
    """TL-14 ``co2`` per candidate."""

    rows: list[dict[str, JsonValue]]


class ExergyToolOutput(StrictModel):
    """TL-14 ``exergy`` per candidate."""

    rows: list[dict[str, JsonValue]]


class ResultsView(StrictModel):
    """Read-only KPI view for the reviewer."""

    candidate_id: str | None
    kpis: KPISet | None
    shortlist: list[str]


class FiguresOutput(StrictModel):
    """TL-21 figures written to ``runs/<id>/figures/``."""

    files: list[str]
    tables: list[str]


class ReportOutput(StrictModel):
    """``build_report`` output."""

    path: str
    sections: list[str]


# =========================================================================== session
@dataclass
class _RunMemo:
    """In-memory outputs of one run (see module doc)."""

    run_id: str
    config_dir: Path | None
    run_dir: Path
    evaluators: dict[str, Evaluator] = field(default_factory=dict)
    configs: dict[str, tl09.GeneratedConfig] = field(default_factory=dict)
    screen: dict[str, EvaluationResult] = field(default_factory=dict)
    full: dict[str, AnnualSimulation] = field(default_factory=dict)
    economics: dict[str, EconomicsReport] = field(default_factory=dict)
    exergy: dict[str, dict[str, Any]] = field(default_factory=dict)
    sized: dict[str, SizedDesign] = field(default_factory=dict)
    optimisations: dict[str, OptimisationResult] = field(default_factory=dict)
    uncertainty: dict[str, UncertaintyReport] = field(default_factory=dict)
    sensitivity: dict[str, dict[str, SensitivityReport]] = field(default_factory=dict)
    points: dict[tuple[float, float], OperatingPoint] = field(default_factory=dict)
    subsurface: dict[str, dict[str, Any]] = field(default_factory=dict)
    scaling: dict[str, tl22.ScalingReport] = field(default_factory=dict)
    unverifiable: dict[str, str] = field(default_factory=dict)
    """Candidates excluded at P3 only by the verifier pre-check (screened for comparison)."""
    climate: ClimateProfile | None = None
    c0_co2_t_per_yr: float | None = None


class ToolSession:
    """Shared state of the real tools for the runs of one runner (see module doc)."""

    def __init__(
        self,
        profile: ToolProfile | str | None = None,
        *,
        map_cache_dir: Path | str | None = None,
        cache: DesignCache | None = None,
    ) -> None:
        """``profile``: search budget (RUN-BUDGET); ``cache``: SIM-07 KPI cache (``runs/cache``)."""
        self.profile = profile if isinstance(profile, ToolProfile) else make_profile(profile)
        self.map_cache_dir = Path(map_cache_dir) if map_cache_dir is not None else None
        self._cache = cache
        self._memos: dict[str, _RunMemo] = {}

    # ------------------------------------------------------------------ bookkeeping
    def memo(self, context: ToolContext) -> _RunMemo:
        """Memo of the context's run (a new one when the config folder changed)."""
        memo = self._memos.get(context.run_id)
        if memo is None or memo.config_dir != context.config_dir:
            memo = _RunMemo(context.run_id, context.config_dir, context.run_dir)
            self._memos[context.run_id] = memo
            self._load_sized(memo)
        return memo

    def cache(self, context: ToolContext) -> DesignCache:
        """Return the SIM-07 design cache next to the runs; namespaces are versioned."""
        if self._cache is None:
            self._cache = DesignCache(context.run_dir.parent / "cache" / "design_cache.sqlite")
        return self._cache

    def knowledge(self, context: ToolContext) -> project_knowledge.JsonProjectKnowledgeStore:
        """Durable user answers shared by the runs of the project (MEM-01)."""
        return project_knowledge.JsonProjectKnowledgeStore(
            context.run_dir.parent / project_knowledge.KNOWLEDGE_FILE
        )

    @staticmethod
    def db30(context: ToolContext) -> JsonValue | None:
        """DB-30 answer of the run (None: register default)."""
        return context.answers.get("DB-30")

    def evaluator(self, context: ToolContext, parent_concept: ConceptId | None) -> Evaluator:
        """TL-13 evaluation context (guard → TL-12 fast → TL-14) for the run and parent."""
        memo = self.memo(context)
        key = json.dumps([self.db30(context), parent_concept], sort_keys=True, default=str)
        if key not in memo.evaluators:
            base = next(iter(memo.evaluators.values()), None)
            if base is None or base.scenario.db30_answer != self.db30(context):
                from gemini_agent.tools.optimise import Scenario

                base = Evaluator.build(
                    scenario=Scenario(db30_answer=self.db30(context)),
                    config_dir=context.config_dir,
                    map_cache_dir=self.map_cache_dir,
                    day_indices=self.profile.day_indices,
                    right_size_backups=True,
                )
            memo.evaluators[key] = replace(base, parent_concept=parent_concept, _plants={})
        return memo.evaluators[key]

    def climate(self, context: ToolContext) -> ClimateProfile:
        """TL-02b profile (T₀ of the exergy KPI)."""
        memo = self.memo(context)
        if memo.climate is None:
            memo.climate = profile_climate(config_dir=context.config_dir).data
        return memo.climate

    def candidate(self, context: ToolContext, candidate_id: str) -> Candidate:
        """Candidate by id (context first, then the sized designs of the session)."""
        for candidate in context.candidates:
            if candidate.id == candidate_id:
                return candidate
        sized = self.memo(context).sized.get(candidate_id)
        if sized is not None:
            return sized.candidate
        known = ", ".join(c.id for c in context.candidates) or "none generated yet"
        raise ValueError(f"unknown candidate_id '{candidate_id}' (known: {known})")

    def select(self, context: ToolContext, candidate_id: str) -> list[Candidate]:
        """``all`` → every candidate not excluded by DesignGuard; else the one asked for.

        Candidates excluded only by the verifier pre-check (``depth_check``) are still screened,
        so the comparison table shows them; ranking and Decision Records exclude them.
        """
        if candidate_id == "all":
            keep = set(self.memo(context).unverifiable)
            return [
                c for c in context.candidates if c.id not in context.guard_failed or c.id in keep
            ]
        return [self.candidate(context, candidate_id)]

    def parent_concept(self, context: ToolContext, candidate: Candidate) -> ConceptId | None:
        """Return the generator parent of C6–C8 (sized design, else the parent's concept)."""
        if candidate.concept not in DERIVED_CONCEPTS:
            return None
        sized = self.memo(context).sized.get(candidate.id)
        if sized is not None:
            return sized.parent_concept
        by_id = {c.id: c for c in context.candidates}
        parent = by_id.get(candidate.parent_id or "")
        if parent is not None and parent.concept not in DERIVED_CONCEPTS + ("C0",):
            return parent.concept
        return DEFAULT_PARENT_CONCEPT

    def outages(self, context: ToolContext, candidate_id: str) -> list[int] | None:
        """Outage schedule of a sized design (None: SIM-OUTAGE default placement)."""
        sized = self.memo(context).sized.get(candidate_id)
        return list(sized.outage_start_indices) if sized and sized.outage_start_indices else None

    # ------------------------------------------------------------------ persistence (resume)
    @staticmethod
    def _sized_path(memo: _RunMemo) -> Path:
        return memo.run_dir / SESSION_DIR / "sized_designs.json"

    def _save_sized(self, memo: _RunMemo) -> None:
        path = self._sized_path(memo)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.model_dump(mode="json", exclude={"evaluation"}) for k, v in memo.sized.items()}
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _load_sized(self, memo: _RunMemo) -> None:
        path = self._sized_path(memo)
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            memo.sized = {k: SizedDesign.model_validate(v) for k, v in raw.items()}

    def register_design(self, context: ToolContext, sized: SizedDesign) -> None:
        """Add a design evaluated elsewhere (an adopted what-if, DR-12) to the run's sized designs.

        The runner calls it when the user adopts a what-if design: TL-13 then sizes from that
        point (``optimise_design`` ``candidate_id``) and the other tools find its outage schedule
        and parent concept. Persisted with the session's sized designs (resume).
        """
        memo = self.memo(context)
        memo.sized[sized.candidate.id] = sized
        for cache in (memo.screen, memo.full, memo.subsurface, memo.economics):
            cache.pop(sized.candidate.id, None)
        self._save_sized(memo)

    # ------------------------------------------------------------------ TL-09 / TL-12 / TL-14
    def config(self, context: ToolContext, candidate: Candidate) -> tl09.GeneratedConfig:
        """TL-09 project of a candidate (cached per run)."""
        memo = self.memo(context)
        key = candidate.model_dump_json()
        if key not in memo.configs:
            memo.configs[key] = tl09.generate_plant(
                candidate,
                parent_concept=self.parent_concept(context, candidate),
                config_dir=context.config_dir,
            )
        return memo.configs[key]

    def screen(self, context: ToolContext, candidate: Candidate) -> EvaluationResult:
        """TL-12 fast year + TL-14 (right-sized backups) of one candidate, kept in memory."""
        memo = self.memo(context)
        result = memo.screen.get(candidate.id)
        if result is None or result.evaluation.candidate.model_dump() != candidate.model_dump():
            evaluator = self.evaluator(context, self.parent_concept(context, candidate))
            result = evaluator.run(candidate, self.outages(context, candidate.id), keep=True)
            memo.screen[candidate.id] = result
            if result.simulation is not None:
                self._cache_kpis(context, candidate, result.simulation)
        return result

    def full_year(self, context: ToolContext, candidate: Candidate) -> AnnualSimulation:
        """TL-12 full mode (GEMINI every day, fast plans), written to ``runs/<id>/``."""
        memo = self.memo(context)
        sim = memo.full.get(candidate.id)
        if sim is None:
            request = SimulateAnnualRequest(
                candidate=candidate,
                mode="full",
                db30_answer=self.db30(context),
                outage_start_indices=self.outages(context, candidate.id),
                parent_concept=self.parent_concept(context, candidate),
                day_indices=list(self.profile.day_indices) if self.profile.day_indices else None,
                write_outputs=False,
            )
            sim = run_annual(
                request, config_dir=context.config_dir, map_cache_dir=self.map_cache_dir
            )
            sim = sim.model_copy(update={"outputs": write_outputs(sim, context.run_dir)})
            memo.full[candidate.id] = sim
            self._cache_kpis(context, candidate, sim, request)
        return sim

    def _cache_kpis(
        self,
        context: ToolContext,
        candidate: Candidate,
        sim: AnnualSimulation,
        request: SimulateAnnualRequest | None = None,
    ) -> None:
        """SIM-07: put the year's KPIs under its versioned config hash (put only)."""
        request = request or SimulateAnnualRequest(
            candidate=candidate,
            mode=sim.mode,
            db30_answer=self.db30(context),
            outage_start_indices=self.outages(context, candidate.id),
            parent_concept=self.parent_concept(context, candidate),
            write_outputs=False,
        )
        self.cache(context).put(
            cache_config(request, sim.calendar, context.config_dir),
            namespace=cache_namespace(sim.mode),
            kpis=sim.annual.kpis,
            summary=f"{sim.candidate_id} {sim.mode}",
            candidate_id=sim.candidate_id,
            run_id=context.run_id,
        )

    def year(
        self, context: ToolContext, candidate: Candidate
    ) -> tuple[AnnualSimulation, EvaluationResult]:
        """Best available year of a candidate: full mode if run, else the fast screening."""
        screened = self.screen(context, candidate)
        full = self.memo(context).full.get(candidate.id)
        if full is not None:
            return full, screened
        if screened.simulation is None:
            raise ValueError(f"{candidate.id}: {screened.evaluation.reason}")
        return screened.simulation, screened

    def c0_co2(self, context: ToolContext) -> float | None:
        """ECO-05: annual CO₂ of the C0 counterfactual (computed first, then reused)."""
        memo = self.memo(context)
        if memo.c0_co2_t_per_yr is None:
            c0 = next((c for c in context.candidates if c.concept == "C0"), None)
            if c0 is None:
                return None
            sim, _ = self.year(context, c0)
            report = co2_emissions(
                Co2Request(service_by_key=sim.service_by_key),
                sim.annual,
                config_dir=context.config_dir,
            ).data
            memo.c0_co2_t_per_yr = report.total_t if report.total_t > 0 else None
        return memo.c0_co2_t_per_yr

    def economics_request(self, context: ToolContext, candidate: Candidate) -> EconomicsRequest:
        """TL-14 input of a candidate's best year (WP-11 ``capex_request`` + right-sized MW)."""
        sim, screened = self.year(context, candidate)
        if screened.plant is None:
            raise ValueError(f"{candidate.id}: no plant ({screened.evaluation.reason})")
        c0 = None if candidate.concept == "C0" else self.c0_co2(context)
        return EconomicsRequest(
            capex=capex_request(screened.plant, sim.annual, screened.evaluation.right_sized_mw),
            gas_handling=candidate.gas_handling,
            service_by_key=sim.service_by_key,
            c0_co2_t_per_yr=c0,
        )

    def economics_kpis(self, context: ToolContext, candidate: Candidate) -> KPISet:
        """TL-14 economics + CO₂ vs C0 + exergy KPI on the candidate's best year."""
        memo = self.memo(context)
        sim, _ = self.year(context, candidate)
        evaluator = self.evaluator(context, self.parent_concept(context, candidate))
        report = economics(
            self.economics_request(context, candidate),
            sim.annual,
            config_dir=context.config_dir,
            book=evaluator.book,
        ).data
        memo.economics[candidate.id] = report
        kpis = report.kpis
        if candidate.doublets > 0 or candidate.concept == "C0":
            ex = exergy_kpi(
                sim.annual,
                climate=self.climate(context),
                config_dir=context.config_dir,
                base_kpis=kpis,
            )
            memo.exergy[candidate.id] = ex.data.model_dump(mode="json")
            kpis = _merge(kpis, _exergy_kpis(ex.data, candidate.id))
        return kpis

    def has_absorption(self, context: ToolContext, candidate: Candidate) -> bool:
        """Return True if the candidate's TL-09 project holds an absorption chiller."""
        try:
            roles = self.config(context, candidate).roles
        except (tl09.ConfigError, ValueError, ArithmeticError, RuntimeError):
            return False
        return any("absorption" in str(role) for role in roles.values())

    # ------------------------------------------------------------------ TL-05/07/08/22
    def operating_point(
        self, context: ToolContext, depth_m: float | None, flow_m3h: float | None
    ) -> OperatingPoint:
        """TL-05 at a depth and flow (cached)."""
        memo = self.memo(context)
        basis = load_subsurface_basis(context.config_dir)
        key = (depth_m or basis.design_depth_m, flow_m3h or basis.flow_max_m3h)
        if key not in memo.points:
            memo.points[key] = doublet_operating_point(
                OperatingPointRequest(flow_m3h=key[1], design=DoubletDesign(depth_m=key[0])),
                config_dir=context.config_dir,
            ).data.point
        return memo.points[key]

    def letdown_safe(self, context: ToolContext, depth_m: float) -> bool:
        """PH-30 pre-filter: TL-05 wellhead T at the DB-11 maximum ≤ the SUB-NPSH boiling bound."""
        basis = load_subsurface_basis(context.config_dir)
        if basis.injection_pump_suction_barg + P_ATM_BAR > basis.vapour_pressure_bound_bara + 1e-9:
            return True  # pressurised letdown: no card saturation data, PH-30 not evaluated
        point = self.operating_point(context, depth_m, basis.flow_max_m3h)
        t_wh = point.wellhead_temperature_degc
        bound = basis.vapour_pressure_bound_valid_to_degc
        # Same relative tolerance as the verifier's bound checks (PH-30), so both agree.
        return t_wh is not None and t_wh <= bound + NUMERIC_TOL * max(1.0, bound, t_wh)

    def depth_check(self, context: ToolContext, depth_m: float, degas: bool) -> str:
        """Return why the verifier could not accept a design at this depth ('' if it can).

        TL-05 at the DB-11 maximum flow: NPSH_A evaluable at the ESP intake (SUB-NPSH ≤ 100 °C,
        PH-19) and, when degassing, no flash at the letdown (PH-30). WP-11 physics follow-up.
        """
        basis = load_subsurface_basis(context.config_dir)
        point = self.operating_point(context, depth_m, basis.flow_max_m3h)
        if point.npsh_available_m is None:
            return "ESP-intake NPSH not evaluable above 100 °C (SUB-NPSH, PH-19)"
        if degas and not self.letdown_safe(context, depth_m):
            return "degassed brine above its boiling point at the letdown (PH-30)"
        return ""

    def subsurface(self, context: ToolContext, candidate: Candidate) -> dict[str, Any]:
        """TL-05/07/08/22 at the candidate's design point (verifier evidence, sizing table)."""
        memo = self.memo(context)
        if candidate.id in memo.subsurface:
            return memo.subsurface[candidate.id]
        cfg_dir = context.config_dir
        point = self.operating_point(context, candidate.depth_m, candidate.design_flow_m3h)
        tb = thermal_breakthrough(
            ThermalBreakthroughRequest(
                flow_m3h=candidate.design_flow_m3h, depth_m=candidate.depth_m
            ),
            config_dir=cfg_dir,
        ).data
        tl08 = (
            pressure_interference(
                InterferenceRequest(
                    doublets=candidate.doublets, flow_m3h=candidate.design_flow_m3h
                ),
                config_dir=cfg_dir,
            ).data
            if candidate.doublets > 1
            else None
        )
        if candidate.gas_handling is None:
            raise ValueError(f"{candidate.id}: a geothermal candidate needs its gas handling")
        scaling = tl22.scaling_check(
            tl22.ScalingRequest(
                gas_handling=candidate.gas_handling,
                design_mitigations=sorted(tl22.required_mitigations(candidate.gas_handling)),
                co2_release_pressure_barg=point.release_pressure_barg,
            )
        ).data
        memo.scaling[candidate.id] = scaling
        memo.subsurface[candidate.id] = {
            "point": point,
            "breakthrough": tb,
            "interference": tl08,
            "spacing_m": load_subsurface_basis(cfg_dir).well_spacing_m,
        }
        _write_json(  # TL-05/07/08/22 design-point summary for the sizing table (TL-21)
            memo.run_dir / SESSION_DIR / f"subsurface_{candidate.id}.json",
            {
                "operating_point": point.model_dump(
                    mode="json", exclude={"esp_evidence", "notes", "reasons"}
                ),
                "well_spacing_m": memo.subsurface[candidate.id]["spacing_m"],
                "breakthrough_time_yr": tb.breakthrough_time_yr,
                "breakthrough_horizon_yr": tb.horizon_yr,
                "interference_max_bar": tl08.max_abs_pressure_change_bar if tl08 else None,
                "doublet_separation_m": tl08.separation_m if tl08 else None,
                "scaling_status": scaling.status,
                "scaling_mitigations": [m.value for m in scaling.evidence.mitigations],
            },
        )
        return memo.subsurface[candidate.id]

    # ------------------------------------------------------------------ verifier evidence
    def evidence(self, candidate: Candidate, context: ToolContext) -> VerificationEvidence:
        """Collector of ``verify_design`` (WP-09 ``EvidenceProvider``): real tool outputs only.

        TL-12 full year (project, daily results of both modes, HEX/HP/pump/ESP points, model
        warnings) + TL-05 design points (feasibility, flow error, intake margin, ESP with the
        computed p_b) + TL-07/TL-08 (t_b, interference and their flags) + TL-22 scaling.
        Absorption operating points are not collected (no GM-04 bound evidence): a design with
        an absorption chiller stays UNVERIFIED (PH-15/16 not evaluated).
        """
        candidate = self.candidate(context, candidate.id)
        sim = self.full_year(context, candidate)
        extra: dict[str, Any] = {}
        if candidate.doublets > 0:
            sub = self.subsurface(context, candidate)
            point: OperatingPoint = sub["point"]
            tb: ThermalBreakthroughReport = sub["breakthrough"]
            tl08: InterferenceReport | None = sub["interference"]
            extra["subsurface"] = SubsurfaceEvidence(
                well_spacing_m=sub["spacing_m"],
                interference_bar=tl08.max_abs_pressure_change_bar if tl08 else None,
                interference_within_limit=tl08.within_limit if tl08 else None,
                thermal_breakthrough_yr=tb.breakthrough_time_yr,
                breakthrough_after_horizon=tb.breakthrough_after_horizon,
                horizon_yr=tb.horizon_yr,
            )
            extra["doublet_points"] = [
                DoubletPointEvidence(
                    name=f"doublet_{i + 1}",
                    flow_m3h=point.flow_m3h,
                    feasible=point.feasible,
                    reasons=list(point.reasons),
                    flow_error_frac=point.flow_error_frac,
                    intake_margin_bar=point.intake_margin_bar,
                )
                for i in range(candidate.doublets)
            ]
            esps = list(sim.evidence.esps) if sim.evidence else []
            if point.esp_evidence is not None:
                esps.append(EspPoint(**point.esp_evidence.model_dump()))
            extra["esps"] = esps
            extra["scaling"] = self.memo(context).scaling[candidate.id].evidence
            extra["letdown_pressure_barg"] = load_subsurface_basis(
                context.config_dir
            ).injection_pump_suction_barg
        return verification_evidence(sim, candidate, **extra)


# =========================================================================== helpers
def _merge(base: KPISet, extra: KPISet) -> KPISet:
    update = {k: v for k, v in extra.model_dump().items() if v is not None and v != {} and v != []}
    update.pop("candidate_id", None)
    return KPISet.model_validate({**base.model_dump(), **update})


def _exergy_kpis(report: Any, candidate_id: str) -> KPISet:
    data = {"candidate_id": candidate_id}
    kpis = getattr(report, "kpis", None)
    if isinstance(kpis, KPISet):
        return kpis.model_copy(update=data)
    return KPISet(**data)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _answer_float(context: ToolContext, assumption_id: str) -> float:
    register = load_config("assumptions", context.config_dir)
    return float(context.answers.get(assumption_id, register.get(assumption_id).value))


def _write_json(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return path.name


# =========================================================================== adapters
@dataclass
class RealTools:
    """Adapter callables bound to one :class:`ToolSession` (toolbox signature)."""

    session: ToolSession

    # ---------------------------------------------------------------- P1
    def load_design_basis(
        self, args: NoArgs, context: ToolContext
    ) -> ToolResult[DesignBasisToolOutput]:
        """TL-01 + DB-30…32 gaps; remembered user answers are named in the questions."""
        result = load_design_basis(config_dir=context.config_dir)
        register = load_config("assumptions", context.config_dir)
        gaps = [gid for gid in GAP_IDS if register.get(gid).status == "default"]
        try:
            remembered = {
                k: v for k, v in self.session.knowledge(context).answers().items() if k in gaps
            }
        except (OSError, ValueError):
            remembered = {}
        questions = {
            gid: GAP_QUESTIONS[gid]
            + (f" (earlier user answer: {remembered[gid]})" if gid in remembered else "")
            for gid in gaps
        }
        return ToolResult[DesignBasisToolOutput](
            data=DesignBasisToolOutput(
                report=result.data,
                design_basis=result.data.fixed,
                open_gaps=gaps,
                gap_questions=questions,
                remembered_answers=remembered,
            ),
            summary=_short(f"{result.summary}; open gaps: {', '.join(gaps) or 'none'}"),
            warnings=result.warnings,
            assumption_ids=result.assumption_ids,
            sources=result.sources + ["config/assumptions.yaml"],
        )

    @staticmethod
    def profile_demand(args: NoArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-02 demand profile."""
        return profile_demand(config_dir=context.config_dir)

    def climate(self, args: NoArgs, context: ToolContext) -> ToolResult[Any]:
        """Run TL-02b, the daily climate; kept for the exergy dead state."""
        result = profile_climate(config_dir=context.config_dir)
        self.session.memo(context).climate = result.data
        return result

    # ---------------------------------------------------------------- P2
    @staticmethod
    def reservoir_conditions(args: DepthArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-03."""
        return reservoir_conditions(
            ReservoirConditionsRequest(depth_m=args.depth_m), config_dir=context.config_dir
        )

    @staticmethod
    def productivity_index(args: NoArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-04."""
        return productivity_index(ProductivityIndexRequest(), config_dir=context.config_dir)

    @staticmethod
    def doublet_operating_point(args: FlowArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-05 at the design-basis depth."""
        return doublet_operating_point(
            OperatingPointRequest(flow_m3h=args.flow_m3h), config_dir=context.config_dir
        )

    @staticmethod
    def thermal_breakthrough(args: ThermalArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-07."""
        return thermal_breakthrough(
            ThermalBreakthroughRequest(
                spacing_m=args.spacing_m, flow_m3h=args.flow_m3h, depth_m=args.depth_m
            ),
            config_dir=context.config_dir,
        )

    @staticmethod
    def pressure_interference(args: InterferenceArgs, context: ToolContext) -> ToolResult[Any]:
        """TL-08 (default: the DB-10 maximum doublet count, the worst case)."""
        limits = load_config("limits", context.config_dir)
        doublets = args.doublets or limits.doublet_count_max
        return pressure_interference(
            InterferenceRequest(
                doublets=doublets, flow_m3h=args.flow_m3h, spacing_m=args.spacing_m
            ),
            config_dir=context.config_dir,
        )

    # ---------------------------------------------------------------- P3
    def generate_candidates(self, args: NoArgs, context: ToolContext) -> ToolResult[CandidateSet]:
        """C0–C8 of spec §3.4 (return temperatures DB-31/DB-32) with their TL-09 projects."""
        basis = load_config("design_basis", context.config_dir)
        heating_return = _answer_float(context, "DB-31")
        cooling_return = _answer_float(context, "DB-32")
        candidates: list[Candidate] = []
        for concept, doublets, depth, text in _CONCEPTS:
            wells = doublets > 0
            derived = concept in DERIVED_CONCEPTS
            gas = GasHandling.PRESSURISED_REINJECT if concept == "C7" else GasHandling.DEGAS_USE
            candidates.append(
                Candidate(
                    id=f"{concept}-{doublets}d",
                    concept=concept,  # type: ignore[arg-type]
                    parent_id=f"{DEFAULT_PARENT_CONCEPT}-2d" if derived else None,
                    description=text,
                    doublets=doublets,
                    depth_m=(depth or basis.reservoir.mid_depth_m) if wells else None,
                    design_flow_m3h=basis.doublets.flow_max_m3h if wells else None,
                    gas_handling=gas if wells else None,
                    heating_return_degc=heating_return,
                    cooling_return_degc=cooling_return,
                )
            )
        configs = []
        for candidate in candidates:
            try:
                cfg = self.session.config(context, candidate)
            except (tl09.ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
                configs.append(
                    ConfigSummary(candidate_id=candidate.id, error=_short(f"{exc}", 200))
                )
                continue
            configs.append(
                ConfigSummary(
                    candidate_id=candidate.id,
                    config_hash=cfg.config_hash,
                    components=len(cfg.project["components"]),  # type: ignore[arg-type]
                    connections=len(cfg.project["connections"]),  # type: ignore[arg-type]
                    equipment_mw=dict(cfg.equipment_mw),
                    flags=cfg.flags[:3],
                )
            )
        failed = [c.candidate_id for c in configs if c.error]
        return ToolResult[CandidateSet](
            data=CandidateSet(candidates=candidates, configs=configs),
            summary=_short(
                f"TL-09: {len(candidates)} candidates C0–C8 (spec §3.4, DB-31 "
                f"{heating_return:g} °C, DB-32 {cooling_return:g} °C); GEMINI projects built for "
                f"{len(candidates) - len(failed)}"
                + (f"; failed: {', '.join(failed)}" if failed else "")
            ),
            warnings=[f"{c.candidate_id}: {f}" for c in configs for f in c.flags][:10],
            assumption_ids=["DB-31", "DB-32", "CFG-LAYOUT", "CFG-SIZING"],
            sources=["spec §3.4", "spec §3.5", "TL-09 generate_gemini_config"],
        )

    def design_guard(
        self, args: CandidateArgs, context: ToolContext
    ) -> ToolResult[GuardToolOutput]:
        """TL-10 on candidates with their TL-09 projects (duplicates by signature)."""
        guard = DesignGuard.from_config(context.config_dir)
        results: list[GuardResult] = []
        targets = (
            list(context.candidates)
            if args.candidate_id == "all"
            else self.session.select(context, args.candidate_id)
        )
        for candidate in targets:
            try:
                cfg = self.session.config(context, candidate)
            except (tl09.ConfigError, ValueError, ArithmeticError, RuntimeError):
                cfg = None
            result = guard.validate(candidate, cfg)
            guard.register(result)
            results.append(result)
        checks = [
            GuardCheck(
                candidate_id=r.candidate_id,
                passed=r.approved,
                violations=[v.reason for v in r.violations] or ([] if r.approved else [r.reason]),
            )
            for r in results
        ]
        # Verifier pre-check (WP-14): a design at a depth the DesignVerifier can never accept
        # (PH-19 NPSH not evaluable, PH-30 flash at the letdown) is not proposed; it is still
        # screened for the comparison (``select``), but ranking and the DRs exclude it.
        memo = self.session.memo(context)
        by_id = {c.id: c for c in targets}
        for check in checks:
            candidate = by_id[check.candidate_id]
            if not check.passed or candidate.doublets == 0 or candidate.depth_m is None:
                continue
            degas = candidate.gas_handling == GasHandling.DEGAS_USE
            why = self.session.depth_check(context, candidate.depth_m, degas)
            if why:
                check.passed = False
                check.violations.append(
                    f"verifier pre-check: depth {candidate.depth_m:g} m — {why}"
                )
                memo.unverifiable[candidate.id] = why
        failed = [c.candidate_id for c in checks if not c.passed]
        summary = f"TL-10: {len(checks) - len(failed)}/{len(checks)} approved"
        if failed:
            first = next(c for c in checks if not c.passed)
            summary += (
                f"; rejected {', '.join(failed)} — {first.candidate_id}: {first.violations[0]}"
            )
        return ToolResult[GuardToolOutput](
            data=GuardToolOutput(checks=checks, results=results),
            summary=_short(summary),
            warnings=[w for r in results for w in r.warnings][:10],
            assumption_ids=["DB-40"],
            sources=["config/limits.yaml", "DB-10…DB-17", "DB-36", "SUB-NPSH", "SUB-PSUC"],
        )

    # ---------------------------------------------------------------- P5 / P7
    def simulate_annual(self, args: SimulateArgs, context: ToolContext) -> ToolResult[KPIBatch]:
        """TL-12: fast = screening year (+ right-sized backups); full = GEMINI every day.

        Full mode also runs TL-14 economics and the exergy KPI on the full-mode year, so the
        selected design's KPIs are all full-mode.
        """
        kpis: list[KPISet] = []
        sims: list[AnnualSummary] = []
        for candidate in self.session.select(context, args.candidate_id):
            if args.mode == "fast":
                screened = self.session.screen(context, candidate)
                ev = screened.evaluation
                if screened.simulation is None:
                    sims.append(
                        AnnualSummary(
                            candidate_id=candidate.id, mode="fast", status=ev.status,
                            reason=ev.reason,
                        )
                    )  # fmt: skip
                    continue
                sim = screened.simulation
                kpis.append(sim.annual.kpis or KPISet(candidate_id=candidate.id))
                sims.append(_annual_summary(sim, ev))
                continue
            sim = self.session.full_year(context, candidate)
            year_kpis = sim.annual.kpis or KPISet(candidate_id=candidate.id)
            kpis.append(_merge(year_kpis, self.session.economics_kpis(context, candidate)))
            sims.append(_annual_summary(sim, self.session.screen(context, candidate).evaluation))
        failed = [s.candidate_id for s in sims if s.status != "ok"]
        text = ", ".join(
            f"{k.candidate_id} unmet {k.unmet_heating_days}/{k.unmet_cooling_days} d"
            for k in kpis[:4]
        )
        return ToolResult[KPIBatch](
            data=KPIBatch(mode=args.mode, kpis=kpis, simulations=sims),
            summary=_short(
                f"TL-12 {args.mode}: {len(kpis)} year(s) simulated ({text}"
                + (", …" if len(kpis) > 4 else "")
                + ")"
                + (f"; not simulated: {', '.join(failed)}" if failed else "")
            ),
            warnings=[w for s in sims for w in s.warnings[:2]][:10],
            assumption_ids=["DB-30", "SIM-FAST", "SIM-OUTAGE", "DSP-FLOW", "CFG-SIZING"],
            sources=["spec §8 SIM-01…07", "TL-12 simulate_annual"],
        )

    def economics(self, args: CandidateArgs, context: ToolContext) -> ToolResult[KPIBatch]:
        """TL-14 economics + CO₂ (C0 first) + exergy KPI of each candidate's best year."""
        kpis: list[KPISet] = []
        rows: list[dict[str, JsonValue]] = []
        items: dict[str, dict[str, JsonValue]] = {}
        warnings: list[str] = []
        selected = self.session.select(context, args.candidate_id)
        selected.sort(key=lambda c: c.concept != "C0")  # ECO-05: C0 CO₂ first
        for candidate in selected:
            try:
                k = self.session.economics_kpis(context, candidate)
            except (ValueError, ArithmeticError, RuntimeError) as exc:
                warnings.append(_short(f"{candidate.id}: {exc}", 200))
                continue
            kpis.append(k)
            report = self.session.memo(context).economics[candidate.id]
            rows.append(
                {
                    "candidate_id": candidate.id,
                    "capex_musd": report.capex.total_usd / 1e6,
                    "opex_musd_per_yr": report.opex.total_usd / 1e6,
                    "lcoh_usd_per_mwh": k.lcoh_per_mwh,
                    "lcoc_usd_per_mwh": k.lcoc_per_mwh,
                    "co2_t_per_yr": k.co2_t_per_yr,
                    "co2_reduction_vs_c0_frac": k.co2_reduction_vs_c0_frac,
                    "exergy_efficiency_frac": k.exergy_efficiency_frac,
                }
            )
            warnings += report.capex.warnings[:1]
            for item in report.cost_items:
                dumped = item.model_dump(mode="json")
                items.setdefault(str(dumped.get("name")), dumped)
        best = ranking.rank_candidates(
            [c for c in selected],
            {k.candidate_id: k for k in kpis},
            exclude_ids=context.excluded_ids,
        )
        text = "; ".join(
            f"{k.candidate_id} LCOH {k.lcoh_per_mwh:.1f} LCOC {k.lcoc_per_mwh or 0:.1f}"
            for k in kpis
            if k.lcoh_per_mwh is not None
        )
        return ToolResult[KPIBatch](
            data=KPIBatch(
                mode="economics", kpis=kpis, economics=rows, cost_items=list(items.values())
            ),
            summary=_short(f"TL-14 USD/MWh: {text}; best {best[0] if best else 'none'}"),
            warnings=warnings[:10],
            assumption_ids=["ECO-CUR", "ECO-REAL", "ECO-OM", "ECO-ALLOC", "ECO-CO2-GAS", "EXG-KPI"],
            sources=["spec §9.2", "RC-10", "RC-11", "TL-14 economics/co2/exergy"],
        )

    def co2(self, args: CandidateArgs, context: ToolContext) -> ToolResult[Co2ToolOutput]:
        """TL-14 ``co2`` (ECO-05) per candidate."""
        rows: list[dict[str, JsonValue]] = []
        c0 = self.session.c0_co2(context)
        for candidate in self.session.select(context, args.candidate_id):
            sim, _ = self.session.year(context, candidate)
            report = co2_emissions(
                Co2Request(
                    gas_handling=candidate.gas_handling,
                    service_by_key=sim.service_by_key,
                    c0_co2_t_per_yr=None if candidate.concept == "C0" else c0,
                ),
                sim.annual,
                config_dir=context.config_dir,
            ).data
            rows.append(
                {
                    "candidate_id": candidate.id,
                    "total_t": report.total_t,
                    "reduction_vs_c0_frac": report.reduction_vs_c0_frac,
                }
            )
        text = "; ".join(
            f"{r['candidate_id']} {float(r['total_t']):.0f} t"  # type: ignore[arg-type]
            for r in rows[:6]
        )
        return ToolResult[Co2ToolOutput](
            data=Co2ToolOutput(rows=rows),
            summary=_short(f"TL-14 CO2 t/yr: {text}"),
            assumption_ids=["ECO-CO2-GAS", "ECO-CO2-AG", "DB-37"],
            sources=["RC-08", "spec §9.2 ECO-05"],
        )

    def exergy(self, args: CandidateArgs, context: ToolContext) -> ToolResult[ExergyToolOutput]:
        """TL-14 ``exergy`` KPI per candidate (T₀ = daily dry bulb, DB-34d)."""
        rows: list[dict[str, JsonValue]] = []
        for candidate in self.session.select(context, args.candidate_id):
            k = self.session.economics_kpis(context, candidate)
            rows.append(
                {
                    "candidate_id": candidate.id,
                    "exergy_efficiency_frac": k.exergy_efficiency_frac,
                    "exergy_utilisation_frac": k.exergy_utilisation_frac,
                }
            )
        text = "; ".join(f"{r['candidate_id']} {r['exergy_efficiency_frac']}" for r in rows[:6])
        return ToolResult[ExergyToolOutput](
            data=ExergyToolOutput(rows=rows),
            summary=_short(f"TL-14 exergy efficiency: {text}"),
            assumption_ids=["EXG-KPI", "EXG-FUEL", "DB-34d"],
            sources=["RC-11", "spec §10.1"],
        )

    def sensitivity(
        self, args: CandidateArgs, context: ToolContext
    ) -> ToolResult[SensitivityToolOutput]:
        """Run the TL-14 cost tornado of LCOH and LCOC on the selected design."""
        candidate_id = args.candidate_id
        if candidate_id == "all":
            candidate_id = context.selected_id or ""
        if not candidate_id:
            raise ValueError("no candidate selected for the sensitivity")
        candidate = self.session.candidate(context, candidate_id)
        sim, _ = self.session.year(context, candidate)
        request = self.session.economics_request(context, candidate)
        evaluator = self.session.evaluator(context, self.session.parent_concept(context, candidate))
        reports = {
            kpi: cost_sensitivity(
                request, sim.annual, kpi=kpi, config_dir=context.config_dir, book=evaluator.book
            ).data
            for kpi in ("lcoh", "lcoc")
        }
        self.session.memo(context).sensitivity[candidate_id] = reports
        _write_json(
            context.run_dir / f"sensitivity_{candidate_id}.json",
            {k: v.model_dump(mode="json") for k, v in reports.items()},
        )
        top = reports["lcoh"].bars[0] if reports["lcoh"].bars else None
        text = f"top LCOH driver {top.input_name} swing {top.swing:.1f}" if top else "no bars"
        return ToolResult[SensitivityToolOutput](
            data=SensitivityToolOutput(candidate_id=candidate_id, reports=reports),
            summary=_short(
                f"TL-14 cost sensitivity {candidate_id}: LCOH base "
                f"{reports['lcoh'].kpi_base:.1f}, LCOC base {reports['lcoc'].kpi_base:.1f} "
                f"{reports['lcoh'].unit}; {text}"
            ),
            assumption_ids=["ECO-SENS-MULT"],
            sources=["spec §9.3", "RC-10"],
        )

    # ---------------------------------------------------------------- P6
    def optimise_design(
        self, args: OptimiseArgs, context: ToolContext
    ) -> ToolResult[OptimiseToolOutput]:
        """TL-13 on the top-ranked concepts (screening KPIs, ``ranking.rank_candidates``).

        Each concept is sized over OPT-SPACE (doublets × verifiable depths, ``depth_check``;
        profile ``quick``: the candidate's own point, else the deepest verifiable register depth
        above it) by the seeded DE on sizes and outage windows; its optimum
        joins the run as ``<concept>-opt``. A sized design REJECTED by the verifier is not
        re-proposed: the loop moves to the next concept.
        """
        session, memo = self.session, self.session.memo(context)
        profile = session.profile
        excluded = context.excluded_ids
        screened = [c for c in context.candidates if not c.id.endswith(SIZED_SUFFIXES)]
        ranked = ranking.rank_candidates(screened, context.kpis, exclude_ids=excluded)
        settings = load_optimiser_settings(context.config_dir)
        sized_out: list[SizedDesign] = []
        skipped: dict[str, str] = {}
        limit = min(args.top_n, profile.max_concepts or args.top_n)
        pinned = args.candidate_id
        if pinned is not None:
            # Review loop (DR-12): size the adopted design only, from its own point. Its
            # doublets, depth and return temperatures are fixed; the DE sizes the equipment
            # (and the flow, a size variable of OPT-SPACE) as for every other concept.
            if pinned in excluded:
                raise ValueError(f"{pinned} was excluded by DesignGuard/verifier: cannot size it")
            ranked, limit = [pinned], 1
        for cid in ranked:
            if len(sized_out) >= limit:
                break
            concept_candidate = session.candidate(context, cid)
            concept = concept_candidate.concept
            sized_id = f"{cid}-opt" if cid == pinned else f"{concept}-opt"
            if sized_id in excluded:
                skipped[cid] = f"{sized_id} rejected by the verifier"
                continue
            if cid != pinned and any(s.candidate.concept == concept for s in sized_out):
                continue
            parent = session.parent_concept(context, concept_candidate)
            depths = (
                [concept_candidate.depth_m or 0.0]
                if profile.own_point_only or cid == pinned
                else list(settings.depth_options_m)
            )
            degas = concept_candidate.gas_handling == GasHandling.DEGAS_USE
            reasons = {z: session.depth_check(context, z, degas) for z in depths}
            safe = [z for z in depths if not reasons[z]]
            if not safe and profile.own_point_only:
                shallower = [
                    z
                    for z in settings.depth_options_m
                    if z <= (concept_candidate.depth_m or 0.0)
                    and not session.depth_check(context, z, degas)
                ]
                safe = [max(shallower)] if shallower else []
            if not safe:
                skipped[cid] = "no verifiable depth option: " + "; ".join(
                    f"{z:g} m {why}" for z, why in reasons.items()
                )
                continue
            request = OptimiseRequest(
                concept=concept,
                parent_concept=parent,
                doublet_options=(
                    [concept_candidate.doublets]
                    if profile.own_point_only or cid == pinned
                    else None
                ),
                depth_options_m=safe,
                heating_return_degc=concept_candidate.heating_return_degc,
                cooling_return_degc=concept_candidate.cooling_return_degc,
                db30_answer=session.db30(context),
                top_k=profile.top_k,
                popsize=profile.popsize,
                maxiter=profile.maxiter,
                maxiter_outage=profile.maxiter_outage,
                dispatch_search=profile.dispatch_search,
                storage_sizes=None if profile.storage_scan else [],
                workers=profile.workers,
                day_indices=list(profile.day_indices) if profile.day_indices else None,
                write_outputs=False,
            )
            result = tl13_optimise_design(
                request, config_dir=context.config_dir, map_cache_dir=session.map_cache_dir
            ).data
            memo.optimisations[concept] = result
            _write_json(
                context.run_dir / f"optimisation_{concept}.json",
                result.model_dump(mode="json", exclude={"screening", "rejected", "pareto"})
                | {"n_screening": len(result.screening)},
            )
            best = result.best
            if best is None:
                skipped[cid] = "TL-13 found no design that meets demand"
                continue
            candidate = best.candidate.model_copy(
                update={
                    "id": sized_id,
                    "parent_id": concept_candidate.parent_id,
                    "description": (
                        f"TL-13 optimum of {concept} sized from the adopted design {cid} "
                        "(user review, DR-12)"
                        if cid == pinned
                        else f"TL-13 optimum of {concept} (sized from {cid})"
                    ),
                }
            )
            sized_out.append(
                SizedDesign(
                    candidate=candidate,
                    concept_candidate_id=cid,
                    parent_concept=parent,
                    outage_start_indices=best.outage_start_indices_used
                    or best.outage_start_indices,
                    evaluation=best,
                    depth_options_m=safe,
                    excluded_depths_m=[z for z in depths if z not in safe],
                )
            )
        for sized in sized_out:
            memo.sized[sized.candidate.id] = sized
            memo.screen.pop(sized.candidate.id, None)
            memo.full.pop(sized.candidate.id, None)
            memo.subsurface.pop(sized.candidate.id, None)
        session._save_sized(memo)
        kpis = [session.economics_kpis(context, s.candidate) for s in sized_out]
        by_id = {k.candidate_id: k for k in kpis}
        ranked_ids = ranking.rank_candidates(
            [s.candidate for s in sized_out], by_id, exclude_ids=excluded
        )
        # The collector has no absorption operating points (PH-15/16 not evaluated → the
        # verifier can only say UNVERIFIED): such designs rank after the verifiable ones.
        unverifiable = {
            s.candidate.id for s in sized_out if session.has_absorption(context, s.candidate)
        }
        shortlist = [i for i in ranked_ids if i not in unverifiable] + [
            i for i in ranked_ids if i in unverifiable
        ]
        skipped.update(
            {
                i: "absorption evidence not collected (PH-15/16): ranked after verifiable designs"
                for i in ranked_ids
                if i in unverifiable
            }
        )
        designs = [
            {
                "candidate_id": s.candidate.id,
                "from": s.concept_candidate_id,
                "doublets": s.candidate.doublets,
                "depth_m": s.candidate.depth_m,
                "design_flow_m3h": s.candidate.design_flow_m3h,
                "equipment_sizes": s.candidate.equipment_sizes,  # type: ignore[dict-item]
                "outage_start_indices": s.outage_start_indices,  # type: ignore[dict-item]
                "objective_per_mwh": s.evaluation.objective_per_mwh if s.evaluation else None,
                "excluded_depths_m": s.excluded_depths_m,  # type: ignore[dict-item]
            }
            for s in sized_out
        ]
        sized_by_id = {s.candidate.id: s.candidate for s in sized_out}
        text = "; ".join(
            f"{i} {sized_by_id[i].doublets}x{sized_by_id[i].depth_m or 0:.0f} m "
            f"LCOH {by_id[i].lcoh_per_mwh or 0:.1f} LCOC {by_id[i].lcoc_per_mwh or 0:.1f}"
            for i in shortlist
        )
        return ToolResult[OptimiseToolOutput](
            data=OptimiseToolOutput(
                shortlist=shortlist,
                kpi_evidence=ranking.has_kpi_evidence(by_id),
                candidates=[s.candidate for s in sized_out],
                kpis=kpis,
                designs=designs,
                skipped=skipped,
            ),
            summary=_short(f"TL-13 ({profile.name}): {text or 'no sized design'}"),
            warnings=[f"{k}: {v}" for k, v in skipped.items()],
            assumption_ids=["OPT-SPACE", "OPT-OBJ", "OPT-DE", "SUB-NPSH", "SUB-PSUC"],
            sources=["spec §9.1", "TL-13 optimise_design"],
        )

    # ---------------------------------------------------------------- P8
    def uncertainty(
        self, args: CandidateArgs, context: ToolContext
    ) -> ToolResult[UncertaintyToolOutput]:
        """TL-15 on the selected design (cases incl. DB-31, OAT, cost tornado, Monte Carlo)."""
        session, profile = self.session, self.session.profile
        candidate_id = args.candidate_id if args.candidate_id != "all" else context.selected_id
        if not candidate_id:
            raise ValueError("no candidate selected for the uncertainty analysis")
        candidate = session.candidate(context, candidate_id)
        report = tl15_uncertainty(
            UncertaintyRequest(
                candidate=candidate,
                outage_start_indices=session.outages(context, candidate_id),
                parent_concept=session.parent_concept(context, candidate),
                db30_answer=session.db30(context),
                samples=profile.mc_samples,
                one_at_a_time=profile.one_at_a_time,
                cases=profile.uq_cases,
                workers=profile.workers,
                day_indices=list(profile.day_indices) if profile.day_indices else None,
                write_outputs=False,
            ),
            config_dir=context.config_dir,
            map_cache_dir=session.map_cache_dir,
        ).data
        session.memo(context).uncertainty[candidate_id] = report
        name = _write_json(
            context.run_dir / f"uncertainty_{candidate_id}.json", report.model_dump(mode="json")
        )
        cases = [
            {
                "case": c.case,
                "assumption_id": c.assumption_id,
                "value": c.value,
                "status": c.evaluation.status,
                "lcoh_per_mwh": c.evaluation.lcoh_per_mwh,
                "lcoc_per_mwh": c.evaluation.lcoc_per_mwh,
                "delta_lcoh_per_mwh": c.delta_lcoh_per_mwh,
                "delta_lcoc_per_mwh": c.delta_lcoc_per_mwh,
                "unmet_mwh": c.unmet_mwh,
            }
            for c in report.cases
        ]
        pct = report.percentiles.get("lcoh_per_mwh", {})
        db31 = sorted({str(c["value"]) for c in cases if c["assumption_id"] == "DB-31"})
        return ToolResult[UncertaintyToolOutput](
            data=UncertaintyToolOutput(
                candidate_id=candidate_id,
                # Percentiles only: TL-15's base KPIs are a fast-mode year and would overwrite
                # the P7 full-mode KPIs of the selected design in the run state (WP-14 review).
                kpis=[KPISet(candidate_id=candidate_id, percentiles=report.percentiles)],
                percentiles=report.percentiles,
                probability_demand_met=report.probability_demand_met,
                probability_demand_met_robust=report.probability_demand_met_robust,
                installed_backup_mw=report.installed_backup_mw,
                robust_backup_mw=report.robust_backup_mw,
                cases=cases,
                samples=len(report.samples),
                failed_samples=report.failed_samples,
                file=name,
            ),
            summary=_short(
                f"TL-15 {candidate_id}: {len(report.samples)} MC samples, LCOH P10/P50/P90 "
                + "/".join(f"{pct[p]:.1f}" for p in ("p10", "p50", "p90") if p in pct)
                + f"; P(demand met) {report.probability_demand_met}; {len(cases)} cases"
                + (f" (DB-31 = {', '.join(db31)})" if db31 else "")
            ),
            warnings=report.warnings[:10],
            assumption_ids=["UQ-RANGES", "UQ-CASES", "UQ-MC", "DB-31"],
            sources=["spec §9.3", "TL-15 uncertainty"],
        )

    def robust_backup(
        self, args: CandidateArgs, context: ToolContext
    ) -> ToolResult[RobustBackupToolOutput]:
        """OPT-ROBUST-BACKUP: re-simulate and re-price the TL-15 design with its robust backups.

        P8 workflow step after TL-15 (DR-11 evidence). The variant ``<id>-robust`` = the design
        with ``robust_backup_mw`` (the UQ-MC percentile of each sample's required boiler /
        electric-chiller nameplate) runs through the TL-13 evaluation (guard → TL-12 fast year →
        TL-14), plus the TL-12 full year when the design has one (P7), so both designs' KPIs rest
        on the same basis; TL-14 then prices the added nameplate (capex, O&M, fuel). The variant
        joins the run unselected; DR-11 decides and the P9 DesignVerifier verifies the selection.
        """
        session = self.session
        memo = session.memo(context)
        candidate_id = args.candidate_id if args.candidate_id != "all" else context.selected_id
        if not candidate_id:
            raise ValueError("no candidate selected for the robust-backup step")
        rule = robust_backup_rule(context.config_dir)
        report = memo.uncertainty.get(candidate_id)
        path = context.run_dir / f"uncertainty_{candidate_id}.json"
        if report is None and path.is_file():  # resumed run: TL-15 output from its file
            report = UncertaintyReport.model_validate_json(path.read_text(encoding="utf-8"))
        if report is None:  # DR-11 then offers the installed backups only (no escalation)
            return self._robust_result(
                RobustBackupToolOutput(
                    candidate_id=candidate_id,
                    robust_backup_percentile=rule.percentile,
                    installed_backup_mw={},
                    robust_backup_mw={},
                    probability_demand_met=None,
                    probability_demand_met_robust=None,
                    reason=f"no TL-15 result for {candidate_id} (uncertainty ran on another design)",
                )
            )
        installed = session.candidate(context, candidate_id)
        robust_mw = dict(report.robust_backup_mw)
        installed_mw = dict(report.installed_backup_mw)
        added = {
            key: round(value - installed_mw.get(key, 0.0), 6)
            for key, value in robust_mw.items()
            if value > installed_mw.get(key, 0.0) + 1e-9
        }
        out = RobustBackupToolOutput(
            candidate_id=candidate_id,
            robust_backup_percentile=rule.percentile,
            installed_backup_mw=installed_mw,
            robust_backup_mw=robust_mw,
            added_backup_mw=added,
            probability_demand_met=report.probability_demand_met,
            probability_demand_met_robust=report.probability_demand_met_robust,
        )
        if not robust_mw:
            reason = "TL-15 has no successful Monte Carlo sample: no robust nameplates"
            return self._robust_result(out.model_copy(update={"reason": reason}))
        if not added:
            reason = "TL-15 robust need is within the installed backup nameplates"
            return self._robust_result(out.model_copy(update={"reason": reason}))
        robust_id = f"{candidate_id}{ROBUST_SUFFIX}"
        robust = installed.model_copy(
            update={
                "id": robust_id,
                "equipment_sizes": installed.equipment_sizes | robust_mw,
                "description": f"{candidate_id} with the TL-15 P{rule.percentile:g} robust "
                f"backups ({ROBUST_BACKUP_ID})",
            }
        )
        sized = memo.sized.get(candidate_id)
        memo.sized[robust_id] = SizedDesign(
            candidate=robust,
            concept_candidate_id=sized.concept_candidate_id if sized else candidate_id,
            parent_concept=session.parent_concept(context, installed),
            outage_start_indices=session.outages(context, candidate_id),
            depth_options_m=list(sized.depth_options_m) if sized else [],
            excluded_depths_m=list(sized.excluded_depths_m) if sized else [],
            robust_backup_of=candidate_id,
        )
        for cache in (memo.screen, memo.full, memo.subsurface, memo.economics):
            cache.pop(robust_id, None)
        session._save_sized(memo)
        try:
            screened = session.screen(context, robust)
            if screened.simulation is None or screened.evaluation.status != "ok":
                raise ValueError(screened.evaluation.reason)
            full = candidate_id in memo.full
            sim = session.full_year(context, robust) if full else screened.simulation
            kpis = _merge(
                sim.annual.kpis or KPISet(candidate_id=robust_id),
                session.economics_kpis(context, robust),
            )
            base_sim, _ = session.year(context, installed)
            base = _merge(
                base_sim.annual.kpis or KPISet(candidate_id=candidate_id),
                session.economics_kpis(context, installed),
            )
        except (tl09.ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
            # A variant that cannot be simulated or priced is not offered (no escalation).
            memo.sized.pop(robust_id, None)
            session._save_sized(memo)
            reason = f"variant not evaluated: {exc}"[:300]
            return self._robust_result(out.model_copy(update={"reason": reason}))
        unmet = (sim.annual.unmet_heating_mwh or 0.0) + (sim.annual.unmet_cooling_mwh or 0.0)

        def delta(name: str) -> float | None:
            new, old = getattr(kpis, name), getattr(base, name)
            return None if new is None or old is None else new - old

        both = [delta("lcoh_per_mwh"), delta("lcoc_per_mwh")]
        out = out.model_copy(
            update={
                "robust_candidate_id": robust_id,
                "year_mode": sim.mode,
                "delta_lcoh_per_mwh": both[0],
                "delta_lcoc_per_mwh": both[1],
                "delta_lcoh_plus_lcoc_per_mwh": (
                    None if all(v is None for v in both) else sum(v or 0.0 for v in both)
                ),
                "delta_capex_million": delta("capex_million"),
                "delta_co2_t_per_yr": delta("co2_t_per_yr"),
                "robust_meets_demand": screened.evaluation.meets_demand
                and unmet <= SIGNIFICANT_UNMET_MWH,
                "candidates": [robust],
                "kpis": [kpis],
            }
        )
        return self._robust_result(out)

    @staticmethod
    def _robust_result(out: RobustBackupToolOutput) -> ToolResult[RobustBackupToolOutput]:
        def prob(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.2f}"

        def signed(value: float | None) -> str:
            return "n/a" if value is None else f"{value:+.2f}"

        if out.robust_candidate_id is None:
            text = f"{ROBUST_BACKUP_ID} {out.candidate_id}: no robust variant ({out.reason})"
        else:
            added = ", ".join(f"{k} +{v:.2f}" for k, v in sorted(out.added_backup_mw.items()))
            text = (
                f"{ROBUST_BACKUP_ID} {out.candidate_id} → {out.robust_candidate_id} ({added} MW): "
                f"P(demand met) {prob(out.probability_demand_met)} → "
                f"{prob(out.probability_demand_met_robust)}; ΔLCOH "
                f"{signed(out.delta_lcoh_per_mwh)}, ΔLCOC {signed(out.delta_lcoc_per_mwh)} "
                f"USD/MWh, ΔCAPEX {signed(out.delta_capex_million)} MUSD ({out.year_mode} year)"
                + ("" if out.robust_meets_demand else "; variant has unmet demand")
            )
        return ToolResult[RobustBackupToolOutput](
            data=out,
            summary=_short(text + "; DR-11 decides"),
            warnings=[out.reason] if out.reason else [],
            assumption_ids=[ROBUST_BACKUP_ID, "UQ-MC", "OPT-SPACE"],
            sources=["spec §9.3 UQ-01/UQ-02", "TL-15 uncertainty", "TL-12", "TL-14"],
        )

    # ---------------------------------------------------------------- P9 / P10
    @staticmethod
    def read_results(args: CandidateArgs, context: ToolContext) -> ToolResult[ResultsView]:
        """Read-only view of the selected candidate's KPIs (reviewer, P9)."""
        candidate_id = None if args.candidate_id == "all" else args.candidate_id
        candidate_id = candidate_id or context.selected_id
        kpis = context.kpis.get(candidate_id) if candidate_id else None
        known = kpis is not None and kpis.lcoh_per_mwh is not None
        return ToolResult[ResultsView](
            data=ResultsView(
                candidate_id=candidate_id, kpis=kpis, shortlist=list(context.shortlist)
            ),
            summary=(
                f"read_results: candidate {candidate_id or 'none'}; KPIs "
                f"{'available' if known else 'not computed'}."
            ),
        )

    def plot_figures(self, args: NoArgs, context: ToolContext) -> ToolResult[FiguresOutput]:
        """TL-21 figures and tables of the run (``runs/<id>/figures``), from tool outputs only."""
        from gemini_agent.tools import plots

        view = plots.RunView.from_session(self.session, context)
        out_dir = context.run_dir / "figures"
        files, tables = plots.render_all(view, out_dir)
        return ToolResult[FiguresOutput](
            data=FiguresOutput(files=files, tables=tables),
            summary=_short(f"TL-21: {len(files)} figures, {len(tables)} tables in figures/"),
            sources=["spec §6 TL-21", "spec §13"],
        )

    def build_report(self, args: NoArgs, context: ToolContext) -> ToolResult[ReportOutput]:
        """Report draft from tool outputs + DRs; ingests durable user answers (MEM-01)."""
        from gemini_agent.tools import plots

        view = plots.RunView.from_session(self.session, context)
        text, sections = plots.report_markdown(view)
        path = context.run_dir / "report_draft.md"
        path.write_text(text, encoding="utf-8")
        ingested = 0
        store_path = context.run_dir / "events.sqlite"
        if store_path.is_file():
            store = SQLiteEventStore(store_path)
            try:
                ingested = len(
                    self.session.knowledge(context).ingest_events(store.list_events(context.run_id))
                )
            finally:
                store.close()
        return ToolResult[ReportOutput](
            data=ReportOutput(path=path.name, sections=sections),
            summary=_short(
                f"Report draft with {len(sections)} sections and "
                f"{len(context.decision_records)} DRs; {ingested} durable user answer(s) stored."
            ),
            sources=["spec §13"],
        )


# --------------------------------------------------------------------------- arg models
class ThermalArgs(StrictModel):
    """Arguments of ``thermal_breakthrough`` (TL-07); None → design basis / register."""

    spacing_m: float | None = Field(default=None, gt=0, description="None: SUB-SPACING.")
    flow_m3h: float | None = Field(default=None, ge=50.0, le=300.0, description="None: DB-11.")
    depth_m: float | None = Field(default=None, gt=0, description="None: DB-01.")


class InterferenceArgs(StrictModel):
    """Arguments of ``pressure_interference`` (TL-08)."""

    doublets: int | None = Field(
        default=None, ge=1, le=3, description="None: DB-10 maximum (worst case)."
    )
    flow_m3h: float | None = Field(default=None, ge=50.0, le=300.0, description="None: DB-11.")
    spacing_m: float | None = Field(default=None, gt=0, description="None: SUB-SPACING.")


def _annual_summary(sim: AnnualSimulation, evaluation: DesignEvaluation) -> AnnualSummary:
    return AnnualSummary(
        candidate_id=sim.candidate_id,
        mode=sim.mode,
        config_hash=sim.config_hash,
        simulated_days=len(sim.annual.days),
        outage_start_indices=list(sim.calendar.outage_start_indices),
        max_fast_full_discrepancy_frac=(
            sim.comparison.max_discrepancy_frac if sim.comparison else None
        ),
        invalid_full_days=len(sim.invalid_full_days),
        right_sized_mw=dict(evaluation.right_sized_mw),
        warnings=sim.warnings[:5],
        files={k: Path(v).name for k, v in sim.outputs.items()},
    )


# =========================================================================== catalogue
def real_specs(session: ToolSession) -> list[ToolSpec]:
    """Every catalogue entry bound to a real tool (``stub=False``)."""
    t = RealTools(session)

    def spec(
        name: str, description: str, args: type, func: Callable[..., Any] | None, owner: str
    ) -> ToolSpec:
        return ToolSpec(name, description, args, func, owner=owner, stub=False)

    def _knowledge(args: KnowledgeArgs, context: ToolContext) -> ToolResult[Any]:
        return knowledge_search(args.query, args.limit)

    specs = [
        spec(
            "knowledge_search",
            "Search reference cards and GEMINI docs; returns cited snippets.",
            KnowledgeArgs,
            _knowledge,
            "WP-10",
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
        spec(
            "load_design_basis",
            "Load design basis DB-01…19, derive DB-20…29 and list open design-basis gaps.",
            NoArgs,
            t.load_design_basis,
            "WP-04",
        ),
        spec(
            "profile_demand",
            "Demand peaks, monthly totals, load duration.",
            NoArgs,
            t.profile_demand,
            "WP-04",
        ),
        spec(
            "climate",
            "Daily dry-bulb, humidity, wet-bulb and design days.",
            NoArgs,
            t.climate,
            "WP-04",
        ),
        spec(
            "reservoir_conditions",
            "Reservoir temperature and pressure at a depth.",
            DepthArgs,
            t.reservoir_conditions,
            "WP-05",
        ),
        spec(
            "productivity_index",
            "Productivity and injectivity index with skin.",
            NoArgs,
            t.productivity_index,
            "WP-05",
        ),
        spec(
            "doublet_operating_point",
            "ESP and injection pump operating point for a flow.",
            FlowArgs,
            t.doublet_operating_point,
            "WP-05",
        ),
        spec(
            "thermal_breakthrough",
            "Thermal breakthrough time and production-temperature decline.",
            ThermalArgs,
            t.thermal_breakthrough,
            "WP-05",
        ),
        spec(
            "pressure_interference",
            "Pressure interference between doublets (default: 3 doublets, worst case).",
            InterferenceArgs,
            t.pressure_interference,
            "WP-05",
        ),
        spec(
            "generate_candidates",
            "Generate candidate configurations C0–C8 with their GEMINI projects.",
            NoArgs,
            t.generate_candidates,
            "WP-06",
        ),
        spec(
            "design_guard",
            "Check candidates against hard limits before simulation.",
            CandidateArgs,
            t.design_guard,
            "WP-06",
        ),
        spec(
            "simulate_annual",
            "Simulate 365 days for candidates (fast or full mode).",
            SimulateArgs,
            t.simulate_annual,
            "WP-07",
        ),
        spec(
            "economics",
            "CAPEX, OPEX, LCOH, LCOC, CO2 vs C0 and exergy for candidates.",
            CandidateArgs,
            t.economics,
            "WP-08",
        ),
        spec(
            "co2",
            "Annual CO2 emissions and reduction versus C0.",
            CandidateArgs,
            t.co2,
            "WP-08",
        ),
        spec("exergy", "Exergy efficiency KPI.", CandidateArgs, t.exergy, "WP-08"),
        spec(
            "sensitivity",
            "Cost sensitivity (tornado) of LCOH and LCOC.",
            CandidateArgs,
            t.sensitivity,
            "WP-08",
        ),
        spec(
            "optimise_design",
            "Size the top candidates and schedule maintenance windows.",
            OptimiseArgs,
            t.optimise_design,
            "WP-11",
        ),
        spec(
            "uncertainty",
            "Monte Carlo P10/P50/P90 and sensitivity cases on a design.",
            CandidateArgs,
            t.uncertainty,
            "WP-11",
        ),
        spec(
            "robust_backup",
            "Re-simulate and re-price a design with its TL-15 robust backups (DR-11 evidence).",
            CandidateArgs,
            t.robust_backup,
            "WP-11",
        ),
        *model_builder_specs(),
        spec(
            "read_results",
            "Read the selected candidate's KPIs (read-only).",
            CandidateArgs,
            t.read_results,
            "WP-10",
        ),
        spec(
            "plot_figures",
            "Plot supply vs demand, comparison, uncertainty and token charts.",
            NoArgs,
            t.plot_figures,
            "WP-14",
        ),
        spec(
            "build_report",
            "Build the report draft from tool outputs and Decision Records.",
            NoArgs,
            t.build_report,
            "WP-14",
        ),
    ]
    return specs


def real_toolbox(
    profile: ToolProfile | str | None = None,
    *,
    config_dir: Path | str | None = None,
    **session_kwargs: Any,
) -> Toolbox:
    """Toolbox with every tool real; ``verify_design`` is bound first to the session collector.

    The verifier is registered before the other bindings replace the catalogue (WP-09 decision:
    a real stub-free run must never meet a stub verifier at P9).
    """
    if not isinstance(profile, ToolProfile):
        profile = make_profile(profile, config_dir=config_dir)
    session = ToolSession(profile, **session_kwargs)
    box = Toolbox([verify_design_spec(session.evidence)])
    for spec in real_specs(session):
        box.register(spec)
    box.session = session  # type: ignore[attr-defined]
    return box


__all__ = [
    "DesignBasisToolOutput",
    "InterferenceArgs",
    "KPIBatch",
    "OptimiseToolOutput",
    "RealTools",
    "RobustBackupToolOutput",
    "SizedDesign",
    "ThermalArgs",
    "ToolProfile",
    "ToolSession",
    "UncertaintyToolOutput",
    "make_profile",
    "real_specs",
    "real_toolbox",
]
