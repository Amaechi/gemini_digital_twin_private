"""TL-13 ``optimise_design``: outer sizing of a concept by enumeration + seeded DE (spec §9.1).

* **Integers enumerated (OPT-03).** Every (doublet count, depth) of OPT-SPACE is screened once at
  the CFG-SIZING sizes (DB-18/DB-19 peaks, HTHP without a nameplate) and the DB-11 maximum flow;
  the best ``top_k`` combinations are refined.
* **Continuous sizes by seeded differential evolution (OPT-03, OPT-DE), two stages per
  combination.** Size stage: design flow, HTHP nameplate (heat-pump concepts) and absorption
  chiller (the priced absorption/electric split; the electric chiller takes the rest). Peak
  boiler and electric chiller only cap their output, so their cost-optimal nameplate given the
  rest is the largest daily residual: they are right-sized and priced, not searched (OPT-SPACE).
  Outage stage (DB-30 A): one outage start per doublet at the size-stage optimum (the
  maintenance windows; sorted-gap transform, so every vector is a valid non-overlapping
  schedule), seeded with the SIM-OUTAGE default; the plant is reused and its daily evaluations
  memoised, so only days whose availability changes are recomputed. Deferred updating and a map
  that evaluates the population (serially or in a process pool) make the result identical for
  the same seed whatever the worker count.
* **Objective (OPT-02, OPT-OBJ).** LCOH + LCOC of WP-08 ``economics`` (30-yr base period) on the
  WP-07 fast-mode year; unmet demand > 0 adds the OPT-OBJ penalty. Every design goes through
  WP-06 ``generate_plant`` and ``DesignGuard`` *before* it is simulated (golden rule 4): a hard-
  limit violation is recorded with the guard's reason and gets the barrier value — it is never
  traded off against cost and can never be selected. Designs the generator or dispatch cannot
  build (e.g. no feasible TL-06 flow) are recorded as ``infeasible`` the same way.
* **After the search.** The optimised outage schedule is compared with the SIM-OUTAGE default;
  the DSP-* dispatch levers are priced at their register sensitivity bounds; the CO₂ Pareto set
  (2–3 points) is taken from every evaluated design that meets demand; C6 storage sizes can be
  scanned but not priced (RC-10 has no HT-ATES cost).

Every number comes from a tool (TL-06/09/10/12/14); this module only searches. The shared
:class:`Scenario`/:class:`Evaluator` machinery is reused by TL-15 (``uncertainty.py``).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import multiprocessing
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

import numpy as np
import yaml
from pydantic import Field, JsonValue
from scipy.optimize import differential_evolution

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    SUMMARY_MAX_CHARS,
    ConceptId,
    GasHandling,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import REPO_ROOT, default_config_dir, load_config
from gemini_agent.schemas.results import AnnualResult, KPISet
from gemini_agent.tools.config_generator import (
    DERIVED_CONCEPTS,
    ConfigError,
    GeneratorBasis,
    concept_layout,
    design_loads,
    generate_plant,
    load_generator_basis,
    tower_water_degc,
)
from gemini_agent.tools.dispatch import FastPlant, db30_rule, dispatch_day
from gemini_agent.tools.doublet import DoubletDesign
from gemini_agent.tools.economics import (
    CapexRequest,
    CostBook,
    EconomicsReport,
    EconomicsRequest,
    HexDuty,
    economics,
)
from gemini_agent.tools.resource import SubsurfaceBasis, load_subsurface_basis
from gemini_agent.tools.simulate_annual import (
    AnnualSimulation,
    SimulateAnnualRequest,
    YearData,
    run_annual,
    year_conditions,
)
from gemini_agent.tools.simulate_day import CONTROL_TOL_K, HEAT_TOL_MW
from gemini_agent.verify.design_guard import DesignGuard
from gemini_simulator.registry import create_component

TOOL_ID = "TL-13"
DEFAULT_RUNS_ROOT = REPO_ROOT / "runs"
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$"
OPT_ASSUMPTION_IDS: tuple[str, ...] = (
    "OPT-SPACE",
    "OPT-OBJ",
    "OPT-DE",
    "CFG-SIZING",
    "SIM-OUTAGE",
    "DB-30",
    "DSP-HP-MIN",
    "DSP-ABS-MIN",
)
SOURCES = ["spec §9.1 OPT-01…04", "spec §9.2 ECO-01", "RC-10", "DB-10", "DB-11", "DB-30"]
MAX_REJECTED_KEPT = 50
"""Rejected/infeasible evaluations kept in the result (all of them go to the CSV)."""
SIZE_KEYS = ("hthp_heating_mw", "absorption_chiller_mw", "electric_chiller_mw", "peak_boiler_mw")
SIGNIFICANT_UNMET_MWH = 0.0
"""Unmet demand above this counts as 'not meeting demand' (exactly zero: OPT-02)."""
RUN_MEMO_MAX = 20000
"""Cap on memoised ``FastPlant.run`` results per plant (a few per day and schedule)."""
PLANT_CACHE_SIZE = 2
"""Fast plants kept per evaluator for reuse (outage-schedule search on fixed sizes)."""
BACKUP_KEYS = ("peak_boiler_mw", "electric_chiller_mw")
"""Cap-only units right-sized by TL-13 (OPT-SPACE): their nameplate only limits output."""
HARD_LIMIT_MARGINS: tuple[tuple[str, str], ...] = (
    (r"^doublet_\d+_flow_margin_m3h$", "DB-11"),
    (r"^doublet_\d+_brine_cooling_margin_k$", "DB-12"),
    (r"^absorption_drive_cooling_margin_k$", "DB-12"),
    (r"^doublet_\d+_injection_balance_margin_m3h$", "DB-14"),
    (r"^heating_supply_margin_k$", "DB-16"),
    (r"^cooling_supply_margin_k$", "DB-17"),
)
"""WP-07 daily margins that are CLAUDE.md hard limits (golden rule 4: never violated silently).

DB-16/DB-17 supply margins fall below zero exactly when the plant's capacity falls short (WP-07
counts that shortfall as unmet demand, which OPT-OBJ penalises); a supply breach *without*
unmet demand, and any DB-11/DB-12/DB-14 breach, is a violation and is rejected."""
SHORTFALL_CODES = {"DB-16": "unmet_heating_mwh", "DB-17": "unmet_cooling_mwh"}
MARGIN_TOL = CONTROL_TOL_K
"""Numerical tolerance on a hard-limit margin (TL-11 control tolerance, 1e-6 K or m³/h)."""
MAX_FLAGS = 10


# =========================================================================== settings
class OptimiserSettings(StrictModel):
    """OPT-SPACE, OPT-OBJ and OPT-DE register values."""

    doublet_options: list[int]
    depth_options_m: list[float]
    min_equipment_mw: float
    flow_resolution_m3h: float
    size_resolution_mw: float
    unmet_penalty_base_per_mwh: float
    unmet_penalty_per_unmet_mwh: float
    rejected_objective: float
    pareto_points: int
    seed: int
    strategy: str
    popsize: int
    maxiter: int
    maxiter_outage: int
    mutation: tuple[float, float]
    recombination: float
    tol: float
    init: str
    polish: Literal[False] = Field(
        description="scipy's polish calls the objective outside the recording map: unsupported."
    )
    updating: Literal["deferred"] = Field(
        description="Deferred updating: the population is evaluated as a batch (serial = pool)."
    )
    top_k: int


def load_optimiser_settings(config_dir: Path | str | None = None) -> OptimiserSettings:
    """Read OPT-SPACE / OPT-OBJ / OPT-DE from the assumptions register."""
    register = load_config("assumptions", config_dir)
    space: dict[str, Any] = register.get("OPT-SPACE").value
    objective: dict[str, Any] = register.get("OPT-OBJ").value
    de: dict[str, Any] = register.get("OPT-DE").value
    assert isinstance(space, dict) and isinstance(objective, dict) and isinstance(de, dict)
    return OptimiserSettings(
        doublet_options=[int(n) for n in space["doublet_options"]],
        depth_options_m=[float(z) for z in space["depth_options_m"]],
        min_equipment_mw=float(space["min_equipment_mw"]),
        flow_resolution_m3h=float(space["flow_resolution_m3h"]),
        size_resolution_mw=float(space["size_resolution_mw"]),
        unmet_penalty_base_per_mwh=float(objective["unmet_penalty_base_per_mwh"]),
        unmet_penalty_per_unmet_mwh=float(objective["unmet_penalty_per_unmet_mwh"]),
        rejected_objective=float(objective["rejected_objective"]),
        pareto_points=int(objective["pareto_points"]),
        seed=int(de["seed"]),
        strategy=str(de["strategy"]),
        popsize=int(de["popsize"]),
        maxiter=int(de["maxiter"]),
        maxiter_outage=int(de["maxiter_outage"]),
        mutation=tuple(float(m) for m in de["mutation"]),
        recombination=float(de["recombination"]),
        tol=float(de["tol"]),
        init=str(de["init"]),
        polish=de["polish"],
        updating=de["updating"],
        top_k=int(de["top_k"]),
    )


# =========================================================================== scenarios
class Scenario(StrictModel):
    """Perturbation of the base case (TL-15 cases and samples); the default is the base case.

    ``design_basis`` keys are dotted paths in ``design_basis.yaml``; ``assumptions`` keys are an
    assumption id (the whole value) or ``"ID.key"`` (one key of a mapping value). Both are written
    to a scenario copy of ``config/`` so every tool reads the same values. ``model_overrides``
    merge into the GEMINI component parameters of the fast plant (by component kind).
    """

    name: str = "base"
    design_basis: dict[str, float] = Field(default_factory=dict)
    assumptions: dict[str, JsonValue] = Field(default_factory=dict)
    model_overrides: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    doublet_design: dict[str, float] = Field(
        default_factory=dict, description="DoubletDesign fields, e.g. injectivity_factor."
    )
    heating_demand_scale: float = Field(default=1.0, gt=0)
    cooling_demand_scale: float = Field(default=1.0, gt=0)
    dry_bulb_shift_k: float = 0.0
    wet_bulb_shift_k: float = 0.0
    heating_return_degc: float | None = Field(default=None, description="Candidate DB-31 case.")
    cost_factors: dict[str, float] = Field(
        default_factory=dict, description="CostBook item → multiplier (DB-35 range)."
    )
    energy_escalation_real_frac: float | None = Field(
        default=None, description="ECO-REAL override (RC-10 range); None: register."
    )
    db30_answer: JsonValue | None = None
    capex: CapexRequest | None = Field(
        default=None,
        description="Installed equipment held fixed (TL-15: the base design is built, the world "
        "varies); None: derived from the scenario's plant and year (capex_request).",
    )

    @property
    def edits_config(self) -> bool:
        """True if the scenario needs its own copy of ``config/``."""
        return bool(self.design_basis or self.assumptions)


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    node = data
    for key in keys[:-1]:
        if key not in node or not isinstance(node[key], dict):
            raise KeyError(f"design_basis path '{path}' does not exist")
        node = node[key]
    if keys[-1] not in node:
        raise KeyError(f"design_basis path '{path}' does not exist")
    node[keys[-1]] = value


def write_scenario_config(
    scenario: Scenario, target: Path, base_config_dir: Path | str | None = None
) -> Path:
    """Copy ``config/`` to ``target`` and apply the scenario's design-basis/register edits."""
    source = Path(base_config_dir) if base_config_dir is not None else default_config_dir()
    target.mkdir(parents=True, exist_ok=True)
    for path in source.glob("*.yaml"):
        shutil.copy2(path, target / path.name)
    if scenario.design_basis:
        path = target / "design_basis.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, value in scenario.design_basis.items():
            _set_dotted(data, key, value)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if scenario.assumptions:
        path = target / "assumptions.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in data["assumptions"]}
        for key, value in scenario.assumptions.items():
            entry_id, _, sub = key.partition(".")
            if entry_id not in entries:
                raise KeyError(f"register entry '{entry_id}' does not exist")
            entry = entries[entry_id]
            if sub:
                if not isinstance(entry["value"], dict) or sub not in entry["value"]:
                    raise KeyError(f"register entry '{entry_id}' has no key '{sub}'")
                entry["value"][sub] = value
            else:
                entry["value"] = value
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target


def scenario_year(year: YearData, scenario: Scenario) -> YearData:
    """Scale demand and shift the daily ambient of the design year (UQ-01, DB-34b).

    The ambient shift (DB-34b warm case) applies to cooling-season days only (cooling demand > 0):
    DB-34b is a summer design point, not an annual offset, and heating demand is not re-derived.
    """
    if (
        scenario.heating_demand_scale == 1.0
        and scenario.cooling_demand_scale == 1.0
        and scenario.dry_bulb_shift_k == 0.0
        and scenario.wet_bulb_shift_k == 0.0
    ):
        return year
    days = []
    for day in year.days:
        wet = day.ambient_wet_bulb_degc
        dry_shift = scenario.dry_bulb_shift_k if day.cooling_demand_mw > 0.0 else 0.0
        wet_shift = scenario.wet_bulb_shift_k if day.cooling_demand_mw > 0.0 else 0.0
        days.append(
            day.model_copy(
                update={
                    "heating_demand_mw": day.heating_demand_mw * scenario.heating_demand_scale,
                    "cooling_demand_mw": day.cooling_demand_mw * scenario.cooling_demand_scale,
                    "ambient_dry_bulb_degc": day.ambient_dry_bulb_degc + dry_shift,
                    "ambient_wet_bulb_degc": None if wet is None else wet + wet_shift,
                }
            )
        )
    return YearData(year=year.year, days=days)


# =========================================================================== fast plant hook
class ScenarioPlant(FastPlant):
    """WP-07 :class:`FastPlant` whose GEMINI component parameters take scenario overrides.

    ``model_overrides`` maps a component kind (``electric_chiller``, ``heatpump``,
    ``absorption_chiller``, …) to parameters merged over the TL-09 values when the model is
    created (e.g. COOL-ELC η, VER-HP-ETA, COOL-ABS-B in TL-15). Empty overrides = FastPlant.

    The plant's ``carries`` and ``run`` results are memoised by their exact arguments (day
    conditions, flows, set-points): the fast plant is deterministic and stateless between calls,
    so a plant reused for another outage schedule recomputes only the days whose availability
    changed (TL-13 maintenance-window search). ``run`` memo entries are capped (memory).
    """

    def __init__(
        self,
        candidate: Candidate,
        *,
        model_overrides: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Store the overrides, then build the WP-07 fast plant."""
        self.model_overrides = {k: dict(v) for k, v in (model_overrides or {}).items()}
        self._memo_carries: dict[tuple, bool] = {}
        self._memo_run: dict[tuple, Any] = {}
        super().__init__(candidate, **kwargs)

    @staticmethod
    def _key(day: Any, flows_m3h: Sequence[float], args: tuple, kwargs: dict[str, Any]) -> tuple:
        return (
            day.day_index,
            day.heating_demand_mw,
            day.cooling_demand_mw,
            day.ambient_dry_bulb_degc,
            day.ambient_wet_bulb_degc,
            tuple(float(q) for q in flows_m3h),
            args,
            tuple(sorted(kwargs.items())),
        )

    def carries(self, day: Any, flows_m3h: Sequence[float], *args: Any, **kwargs: Any) -> bool:
        """``FastPlant.carries``, memoised."""
        key = self._key(day, flows_m3h, args, kwargs)
        if key not in self._memo_carries:
            self._memo_carries[key] = super().carries(day, flows_m3h, *args, **kwargs)
        return self._memo_carries[key]

    def run(self, day: Any, flows_m3h: Sequence[float], *args: Any, **kwargs: Any) -> Any:
        """``FastPlant.run``, memoised (results are not mutated after they are returned)."""
        key = self._key(day, flows_m3h, args, kwargs)
        point = self._memo_run.get(key)
        if point is None:
            point = super().run(day, flows_m3h, *args, **kwargs)
            if len(self._memo_run) >= RUN_MEMO_MAX:
                self._memo_run.clear()
            self._memo_run[key] = point
        return point

    def _model(self, name: str, kind: str, params: dict[str, Any] | None) -> Any:
        extra = self.model_overrides.get(kind)
        if extra:
            params = {**(params or {}), **extra}
        return super()._model(name, kind, params)


def absorption_capacity_fraction(plant: FastPlant) -> float | None:
    """RC-05 capacity fraction F at the design drive and DB-34b ambient (WP-08 ``CapexRequest``).

    Same inputs as ``FastPlant.absorption_design_cop``: drive = wellhead temperature at the
    plant's maximum flow, cooling water from the tower at the design ambient (RC-07).
    """
    if not plant.has_absorption or plant.performance_map is None:
        return None
    params = dict(plant.cooling_spec.absorption or {})
    params.update(getattr(plant, "model_overrides", {}).get("absorption_chiller", {}))
    model = create_component("absorption_chiller")
    model.update_parameters(params)
    loads = design_loads(plant.gb)
    model.calculate_output(
        {
            "drive_temperature_in_degc": plant.well(plant.flow_max_m3h).wellhead_temperature_degc,
            "cooling_water_temperature_in_degc": tower_water_degc(
                loads.ambient_dry_bulb_degc,
                loads.ambient_wet_bulb_degc,
                plant.cooling_spec.heat_rejection,
            ),
            "cooling_load_w": plant.absorption_nameplate_w,
        }
    )
    fraction = model.get_output().get("capacity_fraction")
    return float(fraction) if fraction else None


def _peak_mw(annual: AnnualResult, attr: str, prefix: str) -> float:
    peaks = [
        sum(mw for key, mw in getattr(day, attr).items() if key.startswith(prefix))
        for day in annual.days
    ]
    return max(peaks, default=0.0)


def capex_request(
    plant: FastPlant, annual: AnnualResult, installed_mw: dict[str, float] | None = None
) -> CapexRequest:
    """Map a fast plant and its year to WP-08 :class:`CapexRequest` (installed sizes, MW).

    Plate HEX per doublet from the TL-09 design (brine ṁ at the design flow, design ΔT; Ti
    plates); HTHP = nameplate, else the larger of the design-point and the largest daily HTHP
    heat; CHP from its largest daily heat (it burns all associated gas, CHP-EL-FRAC); boiler and
    chillers = TL-09 nameplates; absorption with RC-05 F on brine (titanium generator tubes);
    free cooling = largest daily duty plus the tower capacity it needs beyond the chiller plant's
    (:func:`free_cooling_tower_mw`).
    ``installed_mw`` overrides ``peak_boiler_mw`` / ``electric_chiller_mw`` (TL-13 right-sizing).
    """
    installed = dict(installed_mw or {})
    candidate = plant.candidate
    meta = plant.meta
    hexes: list[HexDuty] = []
    if meta.hex_design is not None:
        h = meta.hex_design
        mass_flow = h.brine_flow_m3h * plant.sub.density_kg_m3 / 3600.0
        hexes = [
            HexDuty(
                name=f"hex_{i + 1}",
                mass_flow_kg_s=mass_flow,
                delta_t_k=h.brine_in_degc - h.brine_out_degc,
            )
            for i in range(plant.n_doublets)
        ]
    hthp = 0.0
    if plant.has_hp:
        nameplate = candidate.equipment_sizes.get("hthp_heating_mw")
        hthp = (
            float(nameplate)
            if nameplate is not None
            else max(
                (plant.hp_reference_w or 0.0) / 1e6, _peak_mw(annual, "heat_by_source_mw", "hthp")
            )
        )
    chiller_mw = installed.get(
        "electric_chiller_mw", meta.equipment_mw.get("electric_chiller_mw", 0.0)
    )
    absorption_mw = meta.equipment_mw.get("absorption_chiller_mw", 0.0)
    fraction = absorption_capacity_fraction(plant) if absorption_mw > 0 else None
    if absorption_mw > 0 and not fraction:
        absorption_mw = 0.0
    return CapexRequest(
        candidate_id=candidate.id,
        doublets=candidate.doublets,
        depth_m=candidate.depth_m,
        plate_hexes=hexes,
        hthp_heating_mw=hthp,
        chp_thermal_mw=_peak_mw(annual, "heat_by_source_mw", "chp"),
        gas_boiler_mw=installed.get("peak_boiler_mw", meta.equipment_mw.get("peak_boiler_mw", 0.0)),
        electric_chiller_mw=chiller_mw,
        absorption_chiller_mw=absorption_mw,
        absorption_capacity_fraction=fraction if absorption_mw > 0 else None,
        absorption_titanium_tubes=absorption_mw > 0,
        free_cooling_mw=_peak_mw(annual, "cool_by_source_mw", "free_cooling"),
        cooling_tower_mw=(
            free_cooling_tower_mw(annual, chiller_mw) if plant.has_free_cooling else 0.0
        ),
    )


def free_cooling_tower_mw(annual: AnnualResult, chiller_mw: float) -> float:
    """Tower capacity beyond the RC-05 electric-chiller plant's own tower (WP-08 ``CapexRequest``).

    Daily heat rejected = free-cooling duty + electric-chiller duty + its compressor power; the
    chiller plant's tower rejects its nameplate × (1 + 1/COP) at the COP of its largest daily duty.
    The free-cooling HEX and the extra tower share the same water loop (RC-07).
    """
    best_day = max(annual.days, key=lambda d: d.cool_by_source_mw.get("electric_chiller", 0.0))
    duty = best_day.cool_by_source_mw.get("electric_chiller", 0.0)
    power = best_day.electricity_by_consumer_mw.get("electric_chiller", 0.0)
    plant_tower = chiller_mw * (1.0 + power / duty) if duty > 0 and power > 0 else chiller_mw
    rejection = max(
        (
            d.cool_by_source_mw.get("free_cooling", 0.0)
            + d.cool_by_source_mw.get("electric_chiller", 0.0)
            + d.electricity_by_consumer_mw.get("electric_chiller", 0.0)
            for d in annual.days
        ),
        default=0.0,
    )
    return max(0.0, rejection - plant_tower)


# =========================================================================== evaluation
EvalStatus = Literal["ok", "rejected", "infeasible"]


class DesignEvaluation(StrictModel):
    """One candidate (+ outage schedule) through guard → fast year → economics → objective."""

    key: str = Field(description="Hash of the design (id excluded) + scenario name.")
    stage: str = ""
    scenario: str = "base"
    candidate: Candidate
    outage_start_indices: list[int] | None = None
    status: EvalStatus
    reason: str = ""
    violations: list[str] = Field(default_factory=list, description="Guard violation codes.")
    objective_per_mwh: float = Field(
        description="LCOH + LCOC + unmet penalty (USD/MWh); barrier if not ok."
    )
    penalty_per_mwh: float = 0.0
    lcoh_per_mwh: float | None = None
    lcoc_per_mwh: float | None = None
    capex_million: float | None = None
    co2_t_per_yr: float | None = None
    unmet_heating_mwh: float | None = None
    unmet_cooling_mwh: float | None = None
    unmet_heating_days: int | None = None
    unmet_cooling_days: int | None = None
    max_daily_unmet_heating_mw: float | None = Field(
        default=None, description="Largest day-average unmet heat (extra boiler MW needed)."
    )
    max_daily_unmet_cooling_mw: float | None = Field(
        default=None, description="Largest day-average unmet cooling (extra chiller MW needed)."
    )
    hp_reference_mw: float | None = Field(
        default=None, description="HTHP heat at the design point (FastPlant reference)."
    )
    boiler_peak_mw: float | None = Field(default=None, description="Largest daily boiler heat.")
    boiler_heat_mwh: float | None = None
    outage_start_indices_used: list[int] = Field(
        default_factory=list, description="Calendar outage starts (default placement if None)."
    )
    thermal_breakthrough_yr: float | None = None
    limit_breaches: list[str] = Field(
        default_factory=list,
        description="Hard-limit margins < 0 in the year (key: value, DB id); never accepted.",
    )
    flags: list[str] = Field(
        default_factory=list, description="TL-12 warnings of the year (first ones)."
    )
    right_sized_mw: dict[str, float] = Field(
        default_factory=dict,
        description="Backup nameplates sized to the largest daily residual (OPT-SPACE); priced.",
    )
    kpis: KPISet | None = None
    elapsed_s: float = 0.0

    @property
    def meets_demand(self) -> bool:
        """Status ok and zero unmet heating and cooling (OPT-02)."""
        return (
            self.status == "ok"
            and not self.limit_breaches
            and (self.unmet_heating_mwh or 0.0) <= SIGNIFICANT_UNMET_MWH
            and (self.unmet_cooling_mwh or 0.0) <= SIGNIFICANT_UNMET_MWH
        )


def breach_code(breach: str) -> str:
    """DB id of a ``limit_breaches`` entry (``"key = value (DB-xx)"``)."""
    return breach.rsplit("(", 1)[-1].rstrip(")")


def limit_breaches(margins: dict[str, float]) -> list[str]:
    """Hard-limit margins below −MARGIN_TOL as ``"key = value (DB-xx)"`` (golden rule 4)."""
    out = []
    for key, value in sorted(margins.items()):
        for pattern, code in HARD_LIMIT_MARGINS:
            if re.match(pattern, key) and value < -MARGIN_TOL:
                out.append(f"{key} = {value:.4g} ({code})")
    return out


def design_key(candidate: Candidate, outages: Sequence[int] | None, scenario: str = "base") -> str:
    """Hash of everything that defines an evaluation except the candidate id."""
    payload = {
        "candidate": candidate.model_dump(mode="json", exclude={"id", "description"}),
        "outages": list(outages) if outages is not None else None,
        "scenario": scenario,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class EvaluationResult:
    """Full outputs of one evaluation (kept only when asked: they are large)."""

    evaluation: DesignEvaluation
    simulation: AnnualSimulation | None = None
    economics: EconomicsReport | None = None
    plant: FastPlant | None = None


@dataclass
class Evaluator:
    """Picklable evaluation context: guard → TL-12 fast year → TL-14 economics → objective."""

    settings: OptimiserSettings
    year: YearData
    gb: GeneratorBasis
    sub: SubsurfaceBasis
    guard: DesignGuard
    book: CostBook
    config_dir: Path | None = None
    map_cache_dir: Path | None = None
    use_map_cache: bool = True
    scenario: Scenario = field(default_factory=Scenario)
    design: DoubletDesign | None = None
    parent_concept: ConceptId | None = None
    day_indices: list[int] | None = None
    """Simulated-day subset for quick checks only (economics then cover those days); None: year."""
    right_size_backups: bool = False
    """TL-13: price a boiler/electric chiller without nameplate at its largest daily output."""
    reject_limit_breaches: bool = True
    """TL-13: a year with a hard-limit margin < 0 is rejected; TL-15 records it instead."""
    _plants: dict[str, FastPlant] = field(default_factory=dict, repr=False)

    def __getstate__(self) -> dict[str, Any]:
        """Pickle without the plant cache (process pools)."""
        state = dict(self.__dict__)
        state["_plants"] = {}
        return state

    @classmethod
    def build(
        cls,
        *,
        scenario: Scenario | None = None,
        config_dir: Path | str | None = None,
        map_cache_dir: Path | str | None = None,
        use_map_cache: bool = True,
        parent_concept: ConceptId | None = None,
        year: YearData | None = None,
        settings: OptimiserSettings | None = None,
        day_indices: Sequence[int] | None = None,
        right_size_backups: bool = False,
        reject_limit_breaches: bool = True,
    ) -> Evaluator:
        """Load the bases from ``config_dir`` (already the scenario copy if it edits config)."""
        scenario = scenario or Scenario()
        cfg = Path(config_dir) if config_dir is not None else None
        book = CostBook.load(cfg)
        for name, factor in scenario.cost_factors.items():
            book = book.scaled(name, factor)
        return cls(
            settings=settings or load_optimiser_settings(cfg),
            year=scenario_year(year or year_conditions(cfg), scenario),
            gb=load_generator_basis(cfg),
            sub=load_subsurface_basis(cfg),
            guard=DesignGuard.from_config(cfg),
            book=book,
            config_dir=cfg,
            map_cache_dir=Path(map_cache_dir) if map_cache_dir is not None else None,
            use_map_cache=use_map_cache,
            scenario=scenario,
            design=DoubletDesign(**scenario.doublet_design) if scenario.doublet_design else None,
            parent_concept=parent_concept,
            day_indices=list(day_indices) if day_indices is not None else None,
            right_size_backups=right_size_backups,
            reject_limit_breaches=reject_limit_breaches,
        )

    # ------------------------------------------------------------------ helpers
    def _plant(self, candidate: Candidate) -> FastPlant:
        key = candidate.model_dump_json()
        plant = self._plants.get(key)
        if plant is None:
            plant = ScenarioPlant(
                candidate,
                model_overrides=self.scenario.model_overrides,
                design=self.design,
                parent_concept=self.parent_concept,
                gb=self.gb,
                sub=self.sub,
                config_dir=self.config_dir,
                map_cache_dir=self.map_cache_dir,
                use_map_cache=self.use_map_cache,
            )
            while len(self._plants) >= PLANT_CACHE_SIZE:
                self._plants.pop(next(iter(self._plants)))
            self._plants[key] = plant
        return plant

    def _dispatch_kwargs(self) -> dict[str, float]:
        register = load_config("assumptions", self.config_dir)
        hp: dict[str, Any] = register.get("DSP-HP-MIN").value  # type: ignore[assignment]
        return {
            "hp_min_frac": float(hp["min_part_load_frac"]),
            "hp_extra_flow_frac": float(hp["max_extra_flow_frac"]),
            "absorption_min_frac": float(register.get("DSP-ABS-MIN").value),  # type: ignore
        }

    def design_day_chiller_mw(self, plant: FastPlant) -> float:
        """Electric-chiller duty on the DB-19 peak day at the DB-34b design ambient (RC-06/07).

        DB-34b is the chiller and heat-rejection sizing point; the design year's smoothed
        normals never reach it, so the year alone would undersize the chiller.
        """
        loads = design_loads(self.gb)
        peak = max(self.year.days, key=lambda d: d.cooling_demand_mw)
        day = peak.model_copy(
            update={
                "cooling_demand_mw": loads.cooling_demand_mw,
                "ambient_dry_bulb_degc": loads.ambient_dry_bulb_degc,
                "ambient_wet_bulb_degc": loads.ambient_wet_bulb_degc,
            }
        )
        fast = dispatch_day(plant, day, [True] * plant.n_doublets, **self._dispatch_kwargs())
        result = fast.day
        return result.cool_by_source_mw.get("electric_chiller", 0.0) + (
            result.unmet_cooling_mwh / HOURS_PER_DAY
        )

    def right_size(
        self, candidate: Candidate, annual: AnnualResult, plant: FastPlant
    ) -> dict[str, float]:
        """Backup nameplates = largest daily output, rounded up (OPT-SPACE; cap-only units).

        The electric chiller also covers the DB-34b design day (``design_day_chiller_mw``).
        """
        if not self.right_size_backups:
            return {}
        step = self.settings.size_resolution_mw
        sizes = {}
        for key, attr, name, floor in (
            ("peak_boiler_mw", "heat_by_source_mw", "peak_boiler", 0.0),
            (
                "electric_chiller_mw",
                "cool_by_source_mw",
                "electric_chiller",
                self.settings.min_equipment_mw,
            ),
        ):
            if key in candidate.equipment_sizes:
                continue
            peak = max((getattr(d, attr).get(name, 0.0) for d in annual.days), default=0.0)
            if key == "electric_chiller_mw":
                peak = max(peak, self.design_day_chiller_mw(plant))
            peak = peak if peak > HEAT_TOL_MW else 0.0  # rounding residue of an idle unit
            sizes[key] = max(peak, floor, round(math.ceil(peak / step - 1e-9) * step, 6))
        return sizes

    def _barrier(
        self,
        candidate: Candidate,
        outages: Sequence[int] | None,
        status: EvalStatus,
        reason: str,
        start: float,
        *,
        stage: str,
        violations: Sequence[str] = (),
    ) -> EvaluationResult:
        return EvaluationResult(
            DesignEvaluation(
                key=design_key(candidate, outages, self.scenario.name),
                stage=stage,
                scenario=self.scenario.name,
                candidate=candidate,
                outage_start_indices=list(outages) if outages is not None else None,
                status=status,
                reason=reason[:300],
                violations=list(violations),
                objective_per_mwh=self.settings.rejected_objective,
                elapsed_s=time.perf_counter() - start,
            )
        )

    def penalty(self, unmet_mwh: float) -> float:
        """Return the OPT-OBJ unmet-demand surcharge, in USD/MWh added to the objective."""
        if unmet_mwh <= SIGNIFICANT_UNMET_MWH:
            return 0.0
        s = self.settings
        return s.unmet_penalty_base_per_mwh + s.unmet_penalty_per_unmet_mwh * unmet_mwh

    def _candidate(self, candidate: Candidate) -> Candidate:
        if self.scenario.heating_return_degc is None:
            return candidate
        return candidate.model_copy(
            update={"heating_return_degc": self.scenario.heating_return_degc}
        )

    # ------------------------------------------------------------------ main
    def run(
        self,
        candidate: Candidate,
        outages: Sequence[int] | None = None,
        *,
        stage: str = "",
        keep: bool = False,
    ) -> EvaluationResult:
        """Evaluate one design; never raises for a bad design (status + reason instead)."""
        start = time.perf_counter()
        candidate = self._candidate(candidate)
        # Golden rule 4: candidate-level hard limits (DB-10, DB-11, …) first, so a violation is
        # rejected with the guard's reason, then the structural checks on the TL-09 project.
        pre = self.guard.validate(candidate)
        if not pre.approved:
            return self._barrier(
                candidate,
                outages,
                "rejected",
                f"TL-10 {pre.reason}",
                start,
                stage=stage,
                violations=[v.code for v in pre.violations],
            )
        try:
            config = generate_plant(
                candidate,
                design=self.design,
                parent_concept=self.parent_concept,
                gb=self.gb,
                sub=self.sub,
                config_dir=self.config_dir,
            )
        except (ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
            return self._barrier(
                candidate,
                outages,
                "infeasible",
                f"TL-09 {type(exc).__name__}: {exc}",
                start,
                stage=stage,
            )
        guard = self.guard.validate(candidate, config)
        if not guard.approved:
            return self._barrier(
                candidate,
                outages,
                "rejected",
                f"TL-10 {guard.reason}",
                start,
                stage=stage,
                violations=[v.code for v in guard.violations],
            )
        try:
            plant = self._plant(candidate)
            sim = run_annual(
                SimulateAnnualRequest(
                    candidate=candidate,
                    db30_answer=self.scenario.db30_answer,
                    outage_start_indices=list(outages) if outages is not None else None,
                    design=self.design,
                    parent_concept=self.parent_concept,
                    day_indices=self.day_indices,
                    write_outputs=False,
                ),
                config_dir=self.config_dir,
                map_cache_dir=self.map_cache_dir,
                year=self.year,
                plant=plant,
            )
            annual = sim.annual
            right = self.right_size(candidate, annual, plant)
            eco = economics(
                EconomicsRequest(
                    capex=self.scenario.capex or capex_request(plant, annual, right),
                    gas_handling=candidate.gas_handling,
                    service_by_key=sim.service_by_key,
                    energy_escalation_real_frac=self.scenario.energy_escalation_real_frac,
                ),
                annual,
                config_dir=self.config_dir,
                book=self.book,
            ).data
        except (ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
            return self._barrier(
                candidate,
                outages,
                "infeasible",
                f"TL-12/TL-14 {type(exc).__name__}: {exc}",
                start,
                stage=stage,
            )
        kpis = eco.kpis
        unmet = annual.unmet_heating_mwh + annual.unmet_cooling_mwh
        penalty = self.penalty(unmet)
        lcoh, lcoc = kpis.lcoh_per_mwh, kpis.lcoc_per_mwh
        if lcoh is None and lcoc is None:
            return self._barrier(
                candidate,
                outages,
                "infeasible",
                "no LCOH/LCOC (no energy delivered)",
                start,
                stage=stage,
            )
        breaches = limit_breaches(kpis.min_constraint_margins)
        violations = [
            b
            for b in breaches
            if not (
                breach_code(b) in SHORTFALL_CODES
                and getattr(annual, SHORTFALL_CODES[breach_code(b)]) > 0.0
            )
        ]
        if violations and self.reject_limit_breaches:
            return self._barrier(
                candidate,
                outages,
                "rejected",
                "hard limit breached in operation (WP-07 margins): " + "; ".join(violations),
                start,
                stage=stage,
                violations=sorted({breach_code(b) for b in violations}),
            )
        boiler = [d.heat_by_source_mw.get("peak_boiler", 0.0) for d in annual.days]
        evaluation = DesignEvaluation(
            key=design_key(candidate, outages, self.scenario.name),
            stage=stage,
            scenario=self.scenario.name,
            candidate=candidate,
            outage_start_indices=list(outages) if outages is not None else None,
            status="ok",
            reason="approved by TL-10; "
            + ("meets demand" if unmet <= 0 else "unmet demand")
            + (f"; hard-limit breaches: {'; '.join(breaches)}" if breaches else ""),
            objective_per_mwh=(lcoh or 0.0) + (lcoc or 0.0) + penalty,
            penalty_per_mwh=penalty,
            lcoh_per_mwh=lcoh,
            lcoc_per_mwh=lcoc,
            capex_million=kpis.capex_million,
            co2_t_per_yr=kpis.co2_t_per_yr,
            unmet_heating_mwh=annual.unmet_heating_mwh,
            unmet_cooling_mwh=annual.unmet_cooling_mwh,
            unmet_heating_days=annual.unmet_heating_days,
            unmet_cooling_days=annual.unmet_cooling_days,
            max_daily_unmet_heating_mw=max(
                (d.unmet_heating_mwh / HOURS_PER_DAY for d in annual.days), default=0.0
            ),
            max_daily_unmet_cooling_mw=max(
                (d.unmet_cooling_mwh / HOURS_PER_DAY for d in annual.days), default=0.0
            ),
            hp_reference_mw=(plant.hp_reference_w or 0.0) / 1e6 if plant.has_hp else None,
            boiler_peak_mw=max(boiler, default=0.0),
            boiler_heat_mwh=sum(boiler) * HOURS_PER_DAY,
            outage_start_indices_used=list(sim.calendar.outage_start_indices),
            thermal_breakthrough_yr=kpis.thermal_breakthrough_yr,
            right_sized_mw=right,
            limit_breaches=breaches,
            flags=sim.warnings[:MAX_FLAGS],
            kpis=kpis,
            elapsed_s=time.perf_counter() - start,
        )
        if keep:
            return EvaluationResult(evaluation, sim, eco, plant)
        return EvaluationResult(evaluation)

    def evaluate(
        self, candidate: Candidate, outages: Sequence[int] | None = None, *, stage: str = ""
    ) -> DesignEvaluation:
        """:meth:`run` without the large outputs."""
        return self.run(candidate, outages, stage=stage).evaluation


@contextmanager
def scenario_evaluator(
    scenario: Scenario,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    parent_concept: ConceptId | None = None,
    year: YearData | None = None,
    day_indices: Sequence[int] | None = None,
    right_size_backups: bool = False,
    reject_limit_breaches: bool = True,
):
    """Yield an :class:`Evaluator` for ``scenario`` (temporary config copy if it edits config).

    A scenario that changes the subsurface builds fresh TL-06 maps (not cached on disk).
    """
    if not scenario.edits_config:
        yield Evaluator.build(
            scenario=scenario,
            config_dir=config_dir,
            map_cache_dir=map_cache_dir,
            parent_concept=parent_concept,
            year=year,
            day_indices=day_indices,
            right_size_backups=right_size_backups,
            reject_limit_breaches=reject_limit_breaches,
        )
        return
    with tempfile.TemporaryDirectory(prefix="geoagent-scn-") as tmp:
        cfg = write_scenario_config(scenario, Path(tmp) / "config", config_dir)
        yield Evaluator.build(
            scenario=scenario,
            config_dir=cfg,
            map_cache_dir=Path(tmp) / "maps",
            use_map_cache=False,
            parent_concept=parent_concept,
            year=year,
            day_indices=day_indices,
            right_size_backups=right_size_backups,
            reject_limit_breaches=reject_limit_breaches,
        )


def _fork_context() -> Any:
    """Return the fork start method (cheap, inherits loaded state), or None: run serially."""
    if "fork" not in multiprocessing.get_all_start_methods():
        return None
    return multiprocessing.get_context("fork")


ScenarioTask = tuple[Scenario, Candidate, list[int] | None]
_SCENARIO_CONTEXT: dict[str, Any] = {}


def _init_scenario_context(context: dict[str, Any]) -> None:
    _SCENARIO_CONTEXT.clear()
    _SCENARIO_CONTEXT.update(context)


def _evaluate_scenario(task: ScenarioTask) -> DesignEvaluation:
    scenario, candidate, outages = task
    context = dict(_SCENARIO_CONTEXT)
    stage = context.pop("stage", "scenario")
    with scenario_evaluator(scenario, **context) as evaluator:
        return evaluator.evaluate(candidate, outages, stage=stage)


def evaluate_scenarios(
    tasks: Sequence[ScenarioTask], context: dict[str, Any], workers: int = 1
) -> list[DesignEvaluation]:
    """Evaluate (scenario, candidate, outages) tasks in order, serially or in a fork pool.

    ``context`` holds the :func:`scenario_evaluator` keywords (config_dir, map_cache_dir,
    parent_concept, year, day_indices, right_size_backups) and an optional ``stage`` label.
    """
    ctx = _fork_context()
    if workers > 1 and len(tasks) > 1 and ctx is not None:
        with ctx.Pool(
            min(workers, len(tasks)), initializer=_init_scenario_context, initargs=(context,)
        ) as pool:
            return pool.map(_evaluate_scenario, list(tasks), chunksize=1)
    _init_scenario_context(context)
    return [_evaluate_scenario(task) for task in tasks]


# =========================================================================== parallel map
_WORKER_EVALUATOR: Evaluator | None = None


def _init_worker(evaluator: Evaluator) -> None:
    global _WORKER_EVALUATOR
    _WORKER_EVALUATOR = evaluator


def _work(task: tuple[Candidate, list[int] | None, str]) -> DesignEvaluation:
    assert _WORKER_EVALUATOR is not None
    candidate, outages, stage = task
    return _WORKER_EVALUATOR.evaluate(candidate, outages, stage=stage)


class EvaluationPool:
    """Evaluate designs serially or in a fork pool, caching by :func:`design_key`.

    Modified by GeoAgent team for SPE GeoHackathon 2026, 2026-11-02 (WP-14): the pool is
    terminated (not drained) when the ``with`` block exits on an error, so a timed-out or killed
    run leaves no orphan workers.
    """

    def __init__(self, evaluator: Evaluator, workers: int = 1) -> None:
        """Start ``workers`` processes (1 = serial, in this process)."""
        self.evaluator = evaluator
        self.workers = max(1, int(workers))
        self.cache: dict[str, DesignEvaluation] = {}
        self.history: list[DesignEvaluation] = []
        self._pool = None
        ctx = _fork_context()
        if self.workers > 1 and ctx is not None:
            self._pool = ctx.Pool(self.workers, initializer=_init_worker, initargs=(evaluator,))

    def close(self, *, terminate: bool = False) -> None:
        """Stop the worker processes (``terminate``: do not wait for running tasks)."""
        if self._pool is not None:
            pool, self._pool = self._pool, None
            if terminate:
                pool.terminate()
            else:
                pool.close()
            pool.join()

    def __enter__(self) -> Self:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: object, *exc: object) -> None:
        """Context manager exit: stop the pool; on an error (or SIGTERM → SystemExit) at once."""
        self.close(terminate=exc_type is not None)

    def evaluate(
        self, designs: Sequence[tuple[Candidate, list[int] | None]], *, stage: str = ""
    ) -> list[DesignEvaluation]:
        """Evaluate ``designs`` in order (cached ones are not re-run)."""
        scenario = self.evaluator.scenario.name
        keys = [design_key(c, o, scenario) for c, o in designs]
        todo: dict[str, tuple[Candidate, list[int] | None, str]] = {}
        for key, (candidate, outages) in zip(keys, designs, strict=True):
            if key not in self.cache and key not in todo:
                todo[key] = (candidate, outages, stage)
        tasks = list(todo.values())
        if self._pool is not None and len(tasks) > 1:
            results = self._pool.map(_work, tasks, chunksize=1)
        else:
            results = [self.evaluator.evaluate(c, o, stage=s) for c, o, s in tasks]
        for key, result in zip(todo, results, strict=True):
            self.cache[key] = result
            self.history.append(result)
        return [self.cache[key] for key in keys]


# =========================================================================== search space
class Variable(StrictModel):
    """One DE variable (OPT-SPACE)."""

    name: str
    low: float
    high: float
    integer: bool = False


class SearchSpace(StrictModel):
    """DE variables of one (concept, doublets, depth) combination and stage."""

    stage: Literal["size", "outage"] = "size"
    concept: ConceptId
    doublets: int
    depth_m: float
    variables: list[Variable]
    base_flow_m3h: float | None = Field(
        default=None, description="Design flow when it is not a variable (outage stage)."
    )
    fixed_sizes: dict[str, float] = Field(default_factory=dict)
    outage_days: int = 0
    n_days: int = 365

    def decode(
        self, x: Sequence[float], settings: OptimiserSettings
    ) -> tuple[float, dict[str, float], list[int] | None]:
        """Return (design flow, equipment sizes, outage starts) for a DE vector (rounded)."""
        flow = float(self.base_flow_m3h or 0.0)
        sizes = dict(self.fixed_sizes)
        gaps: list[int] = []
        for var, value in zip(self.variables, x, strict=True):
            if var.name == "design_flow_m3h":
                step = settings.flow_resolution_m3h
                flow = round(min(var.high, max(var.low, round(value / step) * step)), 6)
            elif var.name.startswith("outage_"):
                gaps.append(int(np.rint(value)))
            else:
                step = settings.size_resolution_mw
                sizes[var.name] = round(min(var.high, max(var.low, round(value / step) * step)), 6)
        outages = None
        if gaps:
            outages = [u + i * self.outage_days for i, u in enumerate(sorted(gaps))]
        return flow, sizes, outages

    def encode_outages(self, starts: Sequence[int]) -> list[int]:
        """Inverse of the sorted-gap transform (for seeding/explanation)."""
        return [s - i * self.outage_days for i, s in enumerate(sorted(starts))]


def _gas_handling(concept: str) -> GasHandling:
    return GasHandling.PRESSURISED_REINJECT if concept == "C7" else GasHandling.DEGAS_USE


def make_design_candidate(
    concept: ConceptId,
    doublets: int,
    depth_m: float,
    flow_m3h: float,
    sizes: dict[str, float],
    *,
    heating_return_degc: float,
    cooling_return_degc: float,
    parent_id: str | None = None,
    candidate_id: str | None = None,
) -> Candidate:
    """Candidate of a search point (id from a short content hash unless given)."""
    clean = {k: float(v) for k, v in sorted(sizes.items())}
    if candidate_id is None:
        blob = json.dumps([concept, doublets, depth_m, flow_m3h, clean], sort_keys=True)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]
        candidate_id = f"{concept}-{doublets}x{depth_m:.0f}-{digest}"
    return Candidate(
        id=candidate_id,
        concept=concept,
        parent_id=parent_id,
        doublets=doublets,
        depth_m=depth_m,
        design_flow_m3h=flow_m3h,
        equipment_sizes=clean,
        gas_handling=_gas_handling(concept),
        heating_return_degc=heating_return_degc,
        cooling_return_degc=cooling_return_degc,
        description="TL-13 search point",
    )


# =========================================================================== request/result
class StorageSize(StrictModel):
    """One C6 HT-ATES size to scan (DSP-STORAGE)."""

    storage_capacity_mwh: float = Field(gt=0)
    storage_power_mw: float = Field(gt=0)


class OptimiseRequest(StrictModel):
    """TL-13 input (``None`` fields take the register / design-basis values)."""

    concept: ConceptId = "C2"
    parent_concept: ConceptId | None = Field(default=None, description="For C6–C8.")
    doublet_options: list[int] | None = Field(default=None, description="None: OPT-SPACE.")
    depth_options_m: list[float] | None = Field(default=None, description="None: OPT-SPACE.")
    heating_return_degc: float | None = Field(default=None, description="None: DB-31.")
    cooling_return_degc: float | None = Field(default=None, description="None: DB-32.")
    db30_answer: JsonValue | None = None
    top_k: int | None = Field(default=None, ge=1, description="None: OPT-DE top_k.")
    seed: int | None = Field(default=None, description="None: OPT-DE seed.")
    popsize: int | None = Field(default=None, ge=1)
    maxiter: int | None = Field(default=None, ge=0, description="Size stage; None: OPT-DE.")
    maxiter_outage: int | None = Field(
        default=None, ge=0, description="Outage stage; None: OPT-DE."
    )
    optimise_outages: bool = True
    dispatch_search: bool = True
    storage_sizes: list[StorageSize] | None = Field(
        default=None,
        description="C6 scan of the best design (unpriced); [] skips; None: derived from the "
        "best design's boiler heat.",
    )
    workers: int = Field(default=1, ge=1)
    day_indices: list[int] | None = Field(
        default=None,
        description="Quick checks only: simulate these days (economics cover them only); None: "
        "the whole DB-30 year.",
    )
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    write_outputs: bool = True


class DERun(StrictModel):
    """Differential evolution on one integer combination and stage (sizes or outages)."""

    stage: Literal["size", "outage"]
    doublets: int
    depth_m: float
    variables: list[Variable]
    fixed_sizes: dict[str, float]
    best_key: str | None
    best_objective_per_mwh: float
    nfev: int
    nit: int
    message: str
    elapsed_s: float


class DispatchVariant(StrictModel):
    """A DSP-* register value priced on the best design."""

    lever: str
    value: float
    evaluation: DesignEvaluation
    delta_objective_per_mwh: float | None


class OptimisationResult(StrictModel):
    """TL-13 output."""

    concept: ConceptId
    seed: int
    workers: int
    screening: list[DesignEvaluation]
    runs: list[DERun]
    best: DesignEvaluation | None = Field(description="Lowest objective that meets demand.")
    default_outage: DesignEvaluation | None = Field(
        default=None, description="Best sizes with the SIM-OUTAGE default schedule."
    )
    dispatch_variants: list[DispatchVariant] = Field(default_factory=list)
    pareto: list[DesignEvaluation] = Field(default_factory=list)
    storage_scan: list[DesignEvaluation] = Field(default_factory=list)
    rejected: list[DesignEvaluation] = Field(
        default_factory=list, description="Guard-rejected / infeasible (first ones, with reason)."
    )
    n_evaluations: int
    n_rejected: int
    n_infeasible: int
    elapsed_s: float
    outputs: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# =========================================================================== stages
def screening_designs(
    request: OptimiseRequest,
    settings: OptimiserSettings,
    *,
    flow_max_m3h: float,
    heating_return_degc: float,
    cooling_return_degc: float,
) -> list[Candidate]:
    """One CFG-SIZING candidate per (doublets, depth) at the DB-11 maximum flow (OPT-03)."""
    doublets = request.doublet_options or settings.doublet_options
    depths = request.depth_options_m or settings.depth_options_m
    parent = f"{request.parent_concept}-opt" if request.parent_concept else None
    return [
        make_design_candidate(
            request.concept,
            n,
            z,
            flow_max_m3h,
            {},
            heating_return_degc=heating_return_degc,
            cooling_return_degc=cooling_return_degc,
            parent_id=parent if request.concept in DERIVED_CONCEPTS else None,
        )
        for n in doublets
        for z in depths
    ]


def size_space(
    screen: DesignEvaluation, evaluator: Evaluator, request: OptimiseRequest
) -> SearchSpace:
    """Size-stage variables of one screened combination (OPT-SPACE).

    Design flow, HTHP nameplate (heat-pump concepts) and absorption chiller (the priced
    absorption/electric split). Boiler and electric chiller only cap their output, so TL-13 sizes
    them to the largest daily residual instead of searching them (exact inner optimum).
    """
    candidate = screen.candidate
    s = evaluator.settings
    limits = load_config("limits", evaluator.config_dir)
    _, layout, _ = concept_layout(candidate, request.parent_concept)
    variables = [
        Variable(name="design_flow_m3h", low=limits.flow_min_active_m3h, high=limits.flow_max_m3h)
    ]
    fixed: dict[str, float] = {}

    def add(name: str, low: float, high: float) -> None:
        if high > low:
            variables.append(Variable(name=name, low=low, high=high))
        else:
            fixed[name] = low

    if layout.heat_pump:
        add(
            "hthp_heating_mw",
            s.min_equipment_mw,
            max(s.min_equipment_mw, screen.hp_reference_mw or 0),
        )
    if layout.absorption and candidate.doublets > 0:
        add("absorption_chiller_mw", s.min_equipment_mw, evaluator.gb.cooling_peak_mw)
    return SearchSpace(
        stage="size",
        concept=candidate.concept,
        doublets=candidate.doublets,
        depth_m=float(candidate.depth_m or 0.0),
        variables=variables,
        fixed_sizes=fixed,
        n_days=len(evaluator.year.days),
    )


def outage_space(
    best: DesignEvaluation, evaluator: Evaluator, request: OptimiseRequest
) -> SearchSpace | None:
    """Outage-stage variables (DB-30 A): one start per doublet at the size-stage optimum."""
    candidate = best.candidate
    rule = db30_rule(
        evaluator.scenario.db30_answer or request.db30_answer, config_dir=evaluator.config_dir
    )
    days = rule.planned_outage_days_per_doublet if rule.interpretation == "A" else 0
    if not (request.optimise_outages and days and candidate.doublets):
        return None
    n_days = len(evaluator.year.days)
    span = n_days - candidate.doublets * days
    return SearchSpace(
        stage="outage",
        concept=candidate.concept,
        doublets=candidate.doublets,
        depth_m=float(candidate.depth_m or 0.0),
        variables=[
            Variable(name=f"outage_{i + 1}", low=0, high=span, integer=True)
            for i in range(candidate.doublets)
        ],
        base_flow_m3h=candidate.design_flow_m3h,
        fixed_sizes={k: v for k, v in candidate.equipment_sizes.items() if k not in BACKUP_KEYS},
        outage_days=days,
        n_days=n_days,
    )


class _RecordingMap:
    """DE ``workers`` map: evaluates the population via :class:`EvaluationPool`, keeps records."""

    def __init__(
        self,
        pool: EvaluationPool,
        space: SearchSpace,
        make: Callable[[float, dict[str, float]], Candidate],
        stage: str,
    ) -> None:
        self.pool = pool
        self.space = space
        self.make = make
        self.stage = stage
        self.records: list[DesignEvaluation] = []

    def __call__(self, func: Callable[..., float], iterable: Iterable[np.ndarray]) -> list[float]:
        designs = []
        for x in iterable:
            flow, sizes, outages = self.space.decode(x, self.pool.evaluator.settings)
            designs.append((self.make(flow, sizes), outages))
        results = self.pool.evaluate(designs, stage=self.stage)
        self.records.extend(results)
        return [r.objective_per_mwh for r in results]


def _never_called(x: np.ndarray) -> float:  # pragma: no cover - the map evaluates
    raise RuntimeError("the DE objective is evaluated by the recording map")


def run_de(
    space: SearchSpace,
    pool: EvaluationPool,
    request: OptimiseRequest,
    *,
    heating_return_degc: float,
    cooling_return_degc: float,
    seed: int,
    maxiter: int,
    x0: Sequence[float] | None = None,
) -> tuple[DERun, list[DesignEvaluation]]:
    """Seeded differential evolution over one space (OPT-DE; deferred updating; ``x0`` seeded)."""
    s = pool.evaluator.settings
    start = time.perf_counter()
    parent = f"{request.parent_concept}-opt" if request.parent_concept else None

    def make(flow: float, sizes: dict[str, float]) -> Candidate:
        return make_design_candidate(
            space.concept,
            space.doublets,
            space.depth_m,
            flow,
            sizes,
            heating_return_degc=heating_return_degc,
            cooling_return_degc=cooling_return_degc,
            parent_id=parent if space.concept in DERIVED_CONCEPTS else None,
        )

    label = f"de-{space.stage}:{space.doublets}x{space.depth_m:.0f}"
    mapper = _RecordingMap(pool, space, make, label)
    bounds = [(v.low, v.high) for v in space.variables]
    if x0 is not None:
        x0 = [min(hi, max(lo, float(v))) for v, (lo, hi) in zip(x0, bounds, strict=True)]
    result = differential_evolution(
        _never_called,
        bounds=bounds,
        strategy=s.strategy,
        maxiter=maxiter,
        popsize=request.popsize if request.popsize is not None else s.popsize,
        tol=s.tol,
        mutation=s.mutation,
        recombination=s.recombination,
        seed=seed,
        polish=s.polish,
        init=s.init,
        updating=s.updating,
        workers=mapper,
        integrality=[v.integer for v in space.variables],
        x0=x0,
    )
    feasible = [r for r in mapper.records if r.meets_demand]
    best = min(feasible, key=lambda r: r.objective_per_mwh, default=None)
    run = DERun(
        stage=space.stage,
        doublets=space.doublets,
        depth_m=space.depth_m,
        variables=space.variables,
        fixed_sizes=space.fixed_sizes,
        best_key=best.key if best else None,
        best_objective_per_mwh=best.objective_per_mwh if best else float(result.fun),
        nfev=len(mapper.records),
        nit=int(result.nit),
        message=str(result.message),
        elapsed_s=time.perf_counter() - start,
    )
    return run, mapper.records


def pareto_points(evaluations: Sequence[DesignEvaluation], count: int) -> list[DesignEvaluation]:
    """Up to ``count`` non-dominated (objective, CO₂) designs that meet demand (OPT-02, OPT-OBJ).

    Picks the lowest objective, the lowest CO₂ and the point closest to the normalised utopia.
    """
    pool: dict[str, DesignEvaluation] = {}
    for e in evaluations:
        if e.meets_demand and e.co2_t_per_yr is not None:
            pool.setdefault(e.key, e)
    items = sorted(pool.values(), key=lambda e: (e.objective_per_mwh, e.co2_t_per_yr))
    front: list[DesignEvaluation] = []
    best_co2 = math.inf
    for e in items:
        assert e.co2_t_per_yr is not None
        if e.co2_t_per_yr < best_co2:
            front.append(e)
            best_co2 = e.co2_t_per_yr
    if not front:
        return []
    cheapest = front[0]
    cleanest = min(front, key=lambda e: (e.co2_t_per_yr, e.objective_per_mwh))
    lo_obj, hi_obj = cheapest.objective_per_mwh, cleanest.objective_per_mwh
    lo_co2, hi_co2 = cleanest.co2_t_per_yr or 0.0, cheapest.co2_t_per_yr or 0.0

    def distance(e: DesignEvaluation) -> float:
        a = (e.objective_per_mwh - lo_obj) / (hi_obj - lo_obj) if hi_obj > lo_obj else 0.0
        b = ((e.co2_t_per_yr or 0.0) - lo_co2) / (hi_co2 - lo_co2) if hi_co2 > lo_co2 else 0.0
        return math.hypot(a, b)

    knee = min(front, key=distance)
    chosen: list[DesignEvaluation] = []
    for e in (cheapest, knee, cleanest):
        if e.key not in {c.key for c in chosen}:
            chosen.append(e)
    return chosen[:count]


def _dispatch_levers(
    candidate: Candidate, parent_concept: str | None, config_dir: Path | None
) -> list[tuple[str, float]]:
    """DSP-* levers of the concept at their OPT-SPACE bounds (from the DSP-* sensitivity text)."""
    _, layout, _ = concept_layout(candidate, parent_concept)
    register = load_config("assumptions", config_dir)
    space: dict[str, Any] = register.get("OPT-SPACE").value  # type: ignore[assignment]
    levers: list[tuple[str, float]] = []
    for lever, bounds in space["dispatch_levers"].items():
        entry_id, _, key = lever.partition(".")
        if (entry_id == "DSP-HP-MIN" and not layout.heat_pump) or (
            entry_id == "DSP-ABS-MIN" and not layout.absorption
        ):
            continue
        value = register.get(entry_id).value
        base = float(value[key] if key else value)  # type: ignore[index,arg-type]
        levers += [(lever, float(v)) for v in bounds if float(v) != base]
    return levers


def _write_outputs(
    result: OptimisationResult, history: Sequence[DesignEvaluation], run_dir: Path
) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = run_dir / "optimisation.json"
    summary.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    table = run_dir / "optimisation_evaluations.csv"
    fields = [
        "key", "stage", "scenario", "candidate_id", "doublets", "depth_m", "design_flow_m3h",
        *SIZE_KEYS, "outage_start_indices", "status", "objective", "lcoh_per_mwh",
        "lcoc_per_mwh", "capex_million", "co2_t_per_yr", "unmet_heating_mwh",
        "unmet_cooling_mwh", "reason", "elapsed_s",
    ]  # fmt: skip
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for e in history:
            c = e.candidate
            row: dict[str, Any] = {
                "key": e.key,
                "stage": e.stage,
                "scenario": e.scenario,
                "candidate_id": c.id,
                "doublets": c.doublets,
                "depth_m": c.depth_m,
                "design_flow_m3h": c.design_flow_m3h,
                "outage_start_indices": " ".join(map(str, e.outage_start_indices_used)),
                "status": e.status,
                "objective": e.objective_per_mwh,
                "lcoh_per_mwh": e.lcoh_per_mwh,
                "lcoc_per_mwh": e.lcoc_per_mwh,
                "capex_million": e.capex_million,
                "co2_t_per_yr": e.co2_t_per_yr,
                "unmet_heating_mwh": e.unmet_heating_mwh,
                "unmet_cooling_mwh": e.unmet_cooling_mwh,
                "reason": e.reason,
                "elapsed_s": round(e.elapsed_s, 3),
            }
            for key in SIZE_KEYS:
                row[key] = c.equipment_sizes.get(key)
            writer.writerow(row)
    return {"summary": str(summary), "evaluations": str(table)}


def _summary(result: OptimisationResult) -> str:
    head = f"{TOOL_ID} {result.concept}: "
    b = result.best
    tail = (
        f"; {result.n_evaluations} evals ({result.n_rejected} rejected, "
        f"{result.n_infeasible} infeasible), {result.elapsed_s:.0f} s, seed {result.seed}"
    )
    if b is None:
        return (head + "no design meets demand within the hard limits" + tail)[:SUMMARY_MAX_CHARS]
    c = b.candidate

    def fmt(value: float | None, spec: str = ".1f") -> str:
        return "n/a" if value is None else format(value, spec)

    sizes = ", ".join(f"{k.removesuffix('_mw')} {v:.2f}" for k, v in c.equipment_sizes.items())
    unmet = (b.unmet_heating_mwh or 0.0) + (b.unmet_cooling_mwh or 0.0)
    text = (
        f"{head}best {c.id}: {c.doublets}×{fmt(c.depth_m, '.0f')} m, "
        f"{fmt(c.design_flow_m3h, '.0f')} m³/h, {sizes} MW; LCOH {fmt(b.lcoh_per_mwh)} + LCOC "
        f"{fmt(b.lcoc_per_mwh)} = {b.objective_per_mwh:.1f} USD/MWh, CO2 "
        f"{fmt(b.co2_t_per_yr, '.0f')} t/yr, unmet {unmet:.0f} MWh" + tail
    )
    return text[:SUMMARY_MAX_CHARS]


def optimise(
    request: OptimiseRequest,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    year: YearData | None = None,
) -> tuple[OptimisationResult, list[DesignEvaluation]]:
    """Run TL-13 (module doc); returns the result and every evaluation (no files written)."""
    start = time.perf_counter()
    cfg = Path(config_dir) if config_dir is not None else None
    evaluator = Evaluator.build(
        scenario=Scenario(db30_answer=request.db30_answer),
        config_dir=cfg,
        map_cache_dir=map_cache_dir,
        parent_concept=request.parent_concept,
        year=year,
        day_indices=request.day_indices,
        right_size_backups=True,
    )
    s = evaluator.settings
    register = load_config("assumptions", cfg)
    limits = load_config("limits", cfg)
    heating_return = (
        request.heating_return_degc
        if request.heating_return_degc is not None
        else float(register.get("DB-31").value)  # type: ignore[arg-type]
    )
    cooling_return = (
        request.cooling_return_degc
        if request.cooling_return_degc is not None
        else float(register.get("DB-32").value)  # type: ignore[arg-type]
    )
    seed = request.seed if request.seed is not None else s.seed
    top_k = request.top_k or s.top_k
    maxiter = request.maxiter if request.maxiter is not None else s.maxiter
    maxiter_outage = (
        request.maxiter_outage if request.maxiter_outage is not None else s.maxiter_outage
    )
    context: dict[str, Any] = {
        "config_dir": cfg,
        "map_cache_dir": map_cache_dir,
        "parent_concept": request.parent_concept,
        "year": evaluator.year,
        "day_indices": request.day_indices,
        "right_size_backups": True,
    }
    returns = {"heating_return_degc": heating_return, "cooling_return_degc": cooling_return}
    warnings: list[str] = []
    runs: list[DERun] = []
    best: DesignEvaluation | None = None
    default_outage: DesignEvaluation | None = None
    dispatch_variants: list[DispatchVariant] = []
    storage_scan: list[DesignEvaluation] = []
    with EvaluationPool(evaluator, request.workers) as pool:
        screening = pool.evaluate(
            [
                (c, None)
                for c in screening_designs(request, s, flow_max_m3h=limits.flow_max_m3h, **returns)
            ],
            stage="screen",
        )
        ranked = sorted(
            (e for e in screening if e.status == "ok"), key=lambda e: e.objective_per_mwh
        )
        if not ranked:
            warnings.append("no screened (doublets, depth) combination could be built/approved")
        for rank, screen in enumerate(ranked[:top_k]):
            space = size_space(screen, evaluator, request)
            # Seed the population with the screened design (max flow, HTHP at its design heat).
            x0 = [v.high for v in space.variables]
            run, records = run_de(
                space, pool, request, seed=seed + 2 * rank, maxiter=maxiter, x0=x0, **returns
            )
            runs.append(run)
            fits = [r for r in records if r.meets_demand]
            size_best = min(fits, key=lambda r: r.objective_per_mwh, default=None)
            outages = outage_space(size_best, evaluator, request) if size_best else None
            if size_best is not None and outages is not None:
                # Seed with the SIM-OUTAGE default schedule: the search can only improve on it.
                x0 = outages.encode_outages(size_best.outage_start_indices_used)
                run, _ = run_de(
                    outages,
                    pool,
                    request,
                    seed=seed + 2 * rank + 1,
                    maxiter=maxiter_outage,
                    x0=x0,
                    **returns,
                )
                runs.append(run)
        history = list(pool.history)
    searched = min(
        (e for e in history if e.meets_demand), key=lambda e: e.objective_per_mwh, default=None
    )
    if searched is None:
        warnings.append(
            "no evaluated design meets demand: the result lists the closest ones; enlarge the "
            "budget or the bounds (OPT-DE)"
        )
    else:
        c = searched.candidate
        named = c.model_copy(
            update={
                "id": f"{c.concept}-{c.doublets}x{c.depth_m:.0f}-opt",
                "equipment_sizes": c.equipment_sizes | searched.right_sized_mw,
                "description": "TL-13 optimum (LCOH + LCOC, zero unmet demand)",
            }
        )
        final = evaluator.evaluate(named, searched.outage_start_indices, stage="final")
        history.append(final)
        best = final if final.meets_demand else None
        if best is None or not math.isclose(
            final.objective_per_mwh, searched.objective_per_mwh, rel_tol=1e-6
        ):
            warnings.append(
                f"final run with the right-sized nameplates: {final.status}, "
                f"{final.objective_per_mwh:.3f} vs {searched.objective_per_mwh:.3f} USD/MWh in the "
                f"search ({final.reason[:120]})" + ("; no optimum reported" if best is None else "")
            )
        # Post-search alternatives of the searched design (backups right-sized for each).
        tasks: list[tuple[str, float, ScenarioTask]] = []
        if searched.outage_start_indices is not None:
            base = Scenario(db30_answer=request.db30_answer)
            tasks.append(("default-outage", 0.0, (base, c, None)))
        if request.dispatch_search:
            for lever, value in _dispatch_levers(c, request.parent_concept, cfg):
                scenario = Scenario(
                    name=f"{lever}={value:g}",
                    assumptions={lever: value},
                    db30_answer=request.db30_answer,
                )
                tasks.append((lever, value, (scenario, c, searched.outage_start_indices)))
        results = evaluate_scenarios(
            [task for _, _, task in tasks], context | {"stage": "post"}, request.workers
        )
        for (lever, value, _), e in zip(tasks, results, strict=True):
            if lever == "default-outage":
                default_outage = e.model_copy(update={"stage": "default-outage"})
                history.append(default_outage)
                continue
            e = e.model_copy(update={"stage": "dispatch"})
            history.append(e)
            delta = e.objective_per_mwh - searched.objective_per_mwh if e.status == "ok" else None
            dispatch_variants.append(
                DispatchVariant(
                    lever=lever, value=value, evaluation=e, delta_objective_per_mwh=delta
                )
            )
        if best is not None:
            storage_scan = _storage_scan(request, named, best, evaluator, register, warnings)
            history += storage_scan
    rejected = [e for e in history if e.status != "ok"]
    result = OptimisationResult(
        concept=request.concept,
        seed=seed,
        workers=request.workers,
        screening=screening,
        runs=runs,
        best=best,
        default_outage=default_outage,
        dispatch_variants=dispatch_variants,
        pareto=pareto_points(_priced_designs(history), s.pareto_points),
        storage_scan=storage_scan,
        rejected=rejected[:MAX_REJECTED_KEPT],
        n_evaluations=len(history),
        n_rejected=sum(1 for e in rejected if e.status == "rejected"),
        n_infeasible=sum(1 for e in rejected if e.status == "infeasible"),
        elapsed_s=time.perf_counter() - start,
        warnings=warnings,
    )
    return result, history


def _priced_designs(history: Sequence[DesignEvaluation]) -> list[DesignEvaluation]:
    """Base-scenario evaluations with their right-sized nameplates written into the candidate.

    So a Pareto point is the design that was priced; the named optimum comes first (it wins
    the de-duplication by design key against its search twin).
    """
    out = []
    for e in sorted((e for e in history if e.scenario == "base"), key=lambda e: e.stage != "final"):
        if e.right_sized_mw:
            sized = e.candidate.equipment_sizes | e.right_sized_mw
            e = e.model_copy(
                update={
                    "candidate": e.candidate.model_copy(update={"equipment_sizes": sized}),
                    "key": design_key(
                        e.candidate.model_copy(update={"equipment_sizes": sized}),
                        e.outage_start_indices,
                        e.scenario,
                    ),
                }
            )
        out.append(e)
    return out


def _storage_scan(
    request: OptimiseRequest,
    named: Candidate,
    best: DesignEvaluation,
    evaluator: Evaluator,
    register: Any,
    warnings: list[str],
) -> list[DesignEvaluation]:
    """C6 child of the optimum at a few storage sizes (unpriced; RC-10 has no HT-ATES cost)."""
    if named.concept in DERIVED_CONCEPTS or request.storage_sizes == []:
        return []
    sizes = request.storage_sizes
    if sizes is None:
        if not best.boiler_heat_mwh or not best.boiler_peak_mw:
            warnings.append(
                "C6 storage scan skipped: the optimum burns no boiler gas, so DSP-STORAGE has "
                "nothing to displace"
            )
            return []
        recovery = float(register.get("STO-REC-HT").value)
        # Sizes derived from the optimum: discharge power = its largest daily boiler heat; capacity
        # = half / all of its annual boiler heat before recovery losses (STO-REC-HT).
        sizes = [
            StorageSize(
                storage_capacity_mwh=frac * best.boiler_heat_mwh / recovery,
                storage_power_mw=best.boiler_peak_mw,
            )
            for frac in (0.5, 1.0)
        ]
    scan = []
    with scenario_evaluator(
        Scenario(name="c6-scan", db30_answer=request.db30_answer),
        config_dir=evaluator.config_dir,
        map_cache_dir=evaluator.map_cache_dir,
        parent_concept=named.concept,
        year=evaluator.year,
        day_indices=evaluator.day_indices,
    ) as c6_eval:
        for size in sizes:
            child = named.model_copy(
                update={
                    "id": f"C6-{named.id}-{size.storage_capacity_mwh:.0f}"[:64],
                    "concept": "C6",
                    "parent_id": named.id,
                    "equipment_sizes": named.equipment_sizes | size.model_dump(),
                    "description": "C6 storage scan (unpriced: no HT-ATES cost on RC-10)",
                }
            )
            e = c6_eval.evaluate(child, best.outage_start_indices, stage="storage-scan")
            scan.append(e.model_copy(update={"reason": e.reason + "; storage capex not priced"}))
    warnings.append(
        "C6 storage sizes scanned but not optimised: RC-10/cost_assumptions have no HT-ATES cost "
        "(follow-up WP-08/WP-15)"
    )
    return scan


def optimise_design(
    request: OptimiseRequest,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    runs_root: Path | str | None = None,
) -> ToolResult[OptimisationResult]:
    """TL-13: enumerate integers, seeded DE on sizes and outages, LCOH + LCOC objective (§9.1)."""
    result, history = optimise(request, config_dir=config_dir, map_cache_dir=map_cache_dir)
    if request.write_outputs:
        run_id = request.run_id or f"optimise-{request.concept}-seed{result.seed}"
        root = Path(runs_root) if runs_root is not None else DEFAULT_RUNS_ROOT
        outputs = _write_outputs(result, history, root / run_id)
        result = result.model_copy(update={"outputs": outputs})
    return ToolResult[OptimisationResult](
        data=result,
        summary=_summary(result),
        warnings=result.warnings,
        assumption_ids=list(OPT_ASSUMPTION_IDS),
        sources=list(SOURCES),
    )


__all__ = [
    "TOOL_ID",
    "DERun",
    "DesignEvaluation",
    "DispatchVariant",
    "EvaluationPool",
    "EvaluationResult",
    "Evaluator",
    "OptimisationResult",
    "OptimiseRequest",
    "OptimiserSettings",
    "Scenario",
    "ScenarioPlant",
    "ScenarioTask",
    "SearchSpace",
    "StorageSize",
    "Variable",
    "absorption_capacity_fraction",
    "breach_code",
    "capex_request",
    "design_key",
    "evaluate_scenarios",
    "limit_breaches",
    "load_optimiser_settings",
    "make_design_candidate",
    "optimise",
    "optimise_design",
    "outage_space",
    "pareto_points",
    "run_de",
    "scenario_evaluator",
    "scenario_year",
    "screening_designs",
    "size_space",
    "write_scenario_config",
]
