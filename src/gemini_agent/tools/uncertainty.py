"""TL-15 ``uncertainty``: sensitivity cases and Monte Carlo on a selected design (spec §9.3).

The design (candidate sizes and outage schedule) is fixed; the world around it varies. Every case
and sample is a :class:`~gemini_agent.tools.optimise.Scenario` evaluated by the TL-13
:class:`~gemini_agent.tools.optimise.Evaluator` (guard → TL-12 fast year → TL-14 economics), so
the numbers come from the same deterministic tools as the optimisation.

* **Ranges (UQ-RANGES).** Each uncertain input names the register entry or card that owns its
  range (DB-03/06/38, DB-31, DB-34b, RC-02 item 3, VER-HP-ETA, HP-APPROACH, COOL-ABS-B, COOL-ELC,
  SUB-PBUB with VER-ESP-MARGIN/VER-ESP-PB, SUB-PSUC, SUB-II, SUB-SPACING, DB-35 cost ranges,
  ECO-REAL). Nothing is invented here.
* **One-at-a-time sensitivity.** Each input at its low and high value, the rest at base: a
  tornado of LCOH + LCOC (plus LCOH, LCOC, CO₂ and unmet demand per bar).
* **Discrete cases (UQ-CASES).** ECO-CO2-GAS 51.53 kg/GJ, DB-31 return 45/65/75 °C (eval S6),
  CHP-EL-FRAC 0.50, SUB-PBUB pure-water bound, DB-34b warm climate and DB-30 interpretation B.
* **Cost tornado.** WP-08 ``cost_sensitivity`` on the base year (LCOH and LCOC, top 8).
* **Monte Carlo (UQ-MC).** Seeded Latin-hypercube samples (≥ 200 for the report) of every input
  and every cost item the base case reads; P10/P50/P90 of LCOH, LCOC, CO₂, unmet days/MWh and the
  breakthrough time; probability of meeting demand; drivers ranked by |Spearman ρ|. Results go
  into ``KPISet.percentiles`` and ``KPISet.min_constraint_margins`` (worst over base + samples).

Mechanisms: subsurface and register values are written to a scenario copy of ``config/`` (so the
TL-06 map, TL-09, TL-12 and TL-14 all read them); performance factors are GEMINI parameter
overrides on the fast plant (electric chiller ``carnot_efficiency``, absorption
``duhring_slope``, heat pump ``max_carnot_fraction``); demand and climate scale/shift the
design year; the II factor is a ``DoubletDesign`` field (WP-05); cost items scale the CostBook.
"""

from __future__ import annotations

import csv
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import Field, JsonValue
from scipy.stats import norm, qmc, spearmanr

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import (
    SUMMARY_MAX_CHARS,
    ConceptId,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools.economics import (
    NOT_SENSITIVITY_INPUTS,
    CapexRequest,
    CostBook,
    EconomicsRequest,
    SensitivityReport,
    cost_sensitivity,
    real_energy_escalation,
)
from gemini_agent.tools.optimise import (
    SHORTFALL_CODES,
    DesignEvaluation,
    Evaluator,
    Scenario,
    breach_code,
    capex_request,
    evaluate_scenarios,
)
from gemini_agent.tools.simulate_annual import YearData, year_conditions

TOOL_ID = "TL-15"
DEFAULT_RUNS_ROOT = REPO_ROOT / "runs"
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$"
UQ_ASSUMPTION_IDS: tuple[str, ...] = ("UQ-RANGES", "UQ-CASES", "UQ-MC", "DB-35", "ECO-REAL")
SOURCES = ["spec §9.3 UQ-01…03", "spec §9.2 ECO-06", "RC-02", "RC-04", "RC-05", "RC-06", "RC-10"]
Z_P90 = float(norm.ppf(0.9))
"""Standard-normal 90th percentile (lognormal fitted to the 10th/90th percentiles)."""
HP_DATASHEET_LABEL = (
    "WP-11 sensitivity case: register VER-HP-ETA range (RC-04 parameter table), not a datasheet"
)
"""heatpump_basic needs a citation next to max_carnot_fraction; this names the register range."""
ESCALATION_INPUT = "energy_escalation_real_frac"
PERCENTILE_KPIS: tuple[str, ...] = (
    "lcoh_per_mwh",
    "lcoc_per_mwh",
    "lcoh_plus_lcoc_per_mwh",
    "co2_t_per_yr",
    "capex_million",
    "unmet_heating_days",
    "unmet_cooling_days",
    "unmet_heating_mwh",
    "unmet_cooling_mwh",
    "thermal_breakthrough_yr",
    "required_peak_boiler_mw",
    "required_electric_chiller_mw",
)
"""``required_*`` = installed nameplate + the sample's largest daily unmet MW: the boiler and the
electric chiller are last in the merit order and only cap their output (OPT-SPACE), so that is
exactly the nameplate at which the sample would meet demand."""
DRIVER_KPIS: tuple[str, ...] = (
    "lcoh_per_mwh",
    "lcoc_per_mwh",
    "lcoh_plus_lcoc_per_mwh",
    "co2_t_per_yr",
    "unmet_heating_mwh",
    "unmet_cooling_mwh",
    "required_peak_boiler_mw",
    "required_electric_chiller_mw",
)
BACKUP_KPIS = {
    "peak_boiler_mw": ("required_peak_boiler_mw", "max_daily_unmet_heating_mw"),
    "electric_chiller_mw": ("required_electric_chiller_mw", "max_daily_unmet_cooling_mw"),
}


# =========================================================================== inputs
class UncertainInput(StrictModel):
    """One uncertain input with its range and the entry/card that owns the range."""

    name: str
    distribution: Literal["uniform", "lognormal"]
    low: float
    high: float
    base: float
    source: str
    note: str = ""

    def from_unit(self, u: float) -> float:
        """Map u ∈ (0, 1) through the inverse CDF (lognormal: low/high = 10th/90th pct)."""
        if self.distribution == "uniform":
            return self.low + u * (self.high - self.low)
        mu = 0.5 * (math.log(self.low) + math.log(self.high))
        sigma = (math.log(self.high) - math.log(self.low)) / (2.0 * Z_P90)
        return math.exp(mu + sigma * float(norm.ppf(min(max(u, 1e-12), 1 - 1e-12))))


class BaseValues(StrictModel):
    """Register/design-basis values the scenario mapping needs."""

    gradient_k_per_km: float
    esp_margin: float
    verifier_bubble_pressure_bara: float
    warm_dry_bulb_shift_k: float
    warm_wet_bulb_shift_k: float


def _mapping(value: JsonValue) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _number(register: Any, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{assumption_id} must hold a number, got {value!r}")
    return float(value)


def load_uncertain_inputs(
    config_dir: Path | str | None = None,
) -> tuple[list[UncertainInput], BaseValues]:
    """UQ-RANGES with the base value of every input read from its owning entry."""
    register = load_config("assumptions", config_dir)
    basis = load_config("design_basis", config_dir)
    climate = load_config("climate_nl", config_dir)
    ranges = _mapping(register.get("UQ-RANGES").value)
    hp_approach = _mapping(register.get("HP-APPROACH").value)
    if hp_approach["condenser_approach_k"] != hp_approach["evaporator_approach_k"]:
        raise ValueError("HP-APPROACH: the sensitivity moves both approaches together")
    bases: dict[str, float] = {
        "permeability_md": basis.reservoir.permeability_md,
        "geothermal_gradient_k_per_km": basis.reservoir.geothermal_gradient_k_per_km,
        "skin": float(_mapping(register.get("DB-38").value)["skin"]),
        "heating_demand_scale": 1.0,
        "cooling_demand_scale": 1.0,
        "heating_return_degc": _number(register, "DB-31"),
        "warm_climate_frac": 0.0,
        "production_temperature_shift_k": 0.0,
        "hp_carnot_fraction": _number(register, "VER-HP-ETA"),
        "hp_approach_k": float(hp_approach["condenser_approach_k"]),
        "absorption_duhring_slope": _number(register, "COOL-ABS-B"),
        "electric_chiller_carnot_efficiency": float(
            _mapping(register.get("COOL-ELC").value)["carnot_efficiency"]
        ),
        "esp_intake_margin_factor": float(
            _mapping(register.get("SUB-PBUB").value)["intake_margin_factor"]
        ),
        "injection_pump_suction_barg": _number(register, "SUB-PSUC"),
        "injectivity_factor": 1.0,
        "well_spacing_m": _number(register, "SUB-SPACING"),
    }
    inputs = []
    for name, spec in ranges.items():
        if name == "cost_items":
            continue
        if name not in bases:
            raise ValueError(f"UQ-RANGES input '{name}' has no scenario mapping")
        inputs.append(
            UncertainInput(
                name=name,
                distribution=spec["distribution"],
                low=float(spec["low"]),
                high=float(spec["high"]),
                base=bases[name],
                source=str(spec["source"]),
                note=str(spec.get("note", "")),
            )
        )
    base = BaseValues(
        gradient_k_per_km=basis.reservoir.geothermal_gradient_k_per_km,
        esp_margin=bases["esp_intake_margin_factor"],
        verifier_bubble_pressure_bara=_number(register, "VER-ESP-PB"),
        warm_dry_bulb_shift_k=climate.warm_case.dry_bulb_degc - climate.design_point.dry_bulb_degc,
        warm_wet_bulb_shift_k=climate.warm_case.wet_bulb_degc - climate.design_point.wet_bulb_degc,
    )
    return inputs, base


def scenario_for(
    values: dict[str, float],
    base: BaseValues,
    *,
    depth_m: float,
    name: str,
    cost_factors: dict[str, float] | None = None,
    db30_answer: JsonValue | None = None,
    capex: CapexRequest | None = None,
) -> Scenario:
    """Map input values (UQ-RANGES names) to a :class:`Scenario` (module doc)."""
    design_basis: dict[str, float] = {}
    assumptions: dict[str, JsonValue] = {}
    overrides: dict[str, dict[str, JsonValue]] = {}
    doublet: dict[str, float] = {}
    fields: dict[str, Any] = {}
    gradient = values.get("geothermal_gradient_k_per_km")
    shift = values.get("production_temperature_shift_k", 0.0)
    if gradient is not None or shift:
        # RC-02 heterogeneity: the production temperature moves by `shift` at the design depth.
        g = base.gradient_k_per_km if gradient is None else gradient
        design_basis["reservoir.geothermal_gradient_k_per_km"] = g + shift / (depth_m / 1000.0)
    for key, value in values.items():
        if key == "permeability_md":
            design_basis["reservoir.permeability_md"] = value
        elif key == "skin":
            assumptions["DB-38.skin"] = value
        elif key == "heating_demand_scale":
            fields["heating_demand_scale"] = value
        elif key == "cooling_demand_scale":
            fields["cooling_demand_scale"] = value
        elif key == "heating_return_degc":
            fields["heating_return_degc"] = value
        elif key == "warm_climate_frac":
            fields["dry_bulb_shift_k"] = value * base.warm_dry_bulb_shift_k
            fields["wet_bulb_shift_k"] = value * base.warm_wet_bulb_shift_k
        elif key == "hp_carnot_fraction":
            overrides["heatpump"] = {"max_carnot_fraction": value, "datasheet": HP_DATASHEET_LABEL}
        elif key == "hp_approach_k":
            assumptions["HP-APPROACH.condenser_approach_k"] = value
            assumptions["HP-APPROACH.evaporator_approach_k"] = value
        elif key == "absorption_duhring_slope":
            overrides["absorption_chiller"] = {"duhring_slope": value}
        elif key == "electric_chiller_carnot_efficiency":
            overrides["electric_chiller"] = {"carnot_efficiency": value}
        elif key == "esp_intake_margin_factor":
            # SUB-PBUB margin = VER-ESP-MARGIN (TL-05 enforces it); VER-ESP-PB follows the margin.
            assumptions["SUB-PBUB.intake_margin_factor"] = value
            assumptions["VER-ESP-MARGIN"] = value
            assumptions["VER-ESP-PB"] = base.verifier_bubble_pressure_bara * value / base.esp_margin
        elif key == "injection_pump_suction_barg":
            assumptions["SUB-PSUC"] = value
        elif key == "injectivity_factor":
            doublet["injectivity_factor"] = value
        elif key == "well_spacing_m":
            assumptions["SUB-SPACING"] = value
        elif key == ESCALATION_INPUT:
            fields["energy_escalation_real_frac"] = value
        elif key not in ("geothermal_gradient_k_per_km", "production_temperature_shift_k"):
            raise ValueError(f"unknown uncertain input '{key}'")
    return Scenario(
        name=name,
        design_basis=design_basis,
        assumptions=assumptions,
        model_overrides=overrides,
        doublet_design=doublet,
        cost_factors=dict(cost_factors or {}),
        db30_answer=db30_answer,
        capex=capex,
        **fields,
    )


def cost_factor_ranges(
    evaluation_items: Sequence[str], book: CostBook
) -> dict[str, tuple[float, float]]:
    """Multiplier range of every cost input the base case read (DB-35, as ``cost_sensitivity``)."""
    ranges: dict[str, tuple[float, float]] = {}
    for name in evaluation_items:
        if (
            name in NOT_SENSITIVITY_INPUTS
            or "benchmark" in name
            or name.startswith("heat_price_cap")
        ):
            continue
        cost = book.usd(name)
        if isinstance(cost.value, dict):
            frac = book.min_sensitivity_frac
            ranges[name] = (1.0 - frac, 1.0 + frac)
            continue
        base = cost.scalar()
        if base == 0 or cost.sensitivity_low is None or cost.sensitivity_high is None:
            continue
        ranges[name] = (cost.sensitivity_low / base, cost.sensitivity_high / base)
    return ranges


# =========================================================================== results
class SensitivityBar(StrictModel):
    """One input at its low and high value, the rest at base (tornado of LCOH + LCOC)."""

    input_name: str
    source: str
    base_value: float
    low_value: float
    high_value: float
    objective_low_per_mwh: float | None = Field(description="Incl. the OPT-OBJ unmet penalty.")
    objective_high_per_mwh: float | None
    cost_low_per_mwh: float | None = Field(description="LCOH + LCOC at the low value (no penalty).")
    cost_high_per_mwh: float | None
    lcoh_low_per_mwh: float | None
    lcoh_high_per_mwh: float | None
    lcoc_low_per_mwh: float | None
    lcoc_high_per_mwh: float | None
    co2_low_t_per_yr: float | None
    co2_high_t_per_yr: float | None
    unmet_low_mwh: float | None
    unmet_high_mwh: float | None
    status_low: str
    status_high: str
    swing_per_mwh: float = Field(
        description="|cost_high − cost_low| (LCOH + LCOC, USD/MWh); 0 if unknown. Demand risk is "
        "in unmet_low/high_mwh (not mixed into the swing)."
    )


class SensitivityCase(StrictModel):
    """A discrete case (UQ-CASES) against the base design."""

    case: str
    assumption_id: str
    value: JsonValue
    evaluation: DesignEvaluation
    delta_objective_per_mwh: float | None
    delta_lcoh_per_mwh: float | None
    delta_lcoc_per_mwh: float | None
    delta_co2_t_per_yr: float | None
    delta_cost_per_mwh: float | None = Field(
        default=None, description="Δ(LCOH + LCOC) without the unmet penalty."
    )
    unmet_mwh: float | None = Field(default=None, description="Unmet heating + cooling, MWh/yr.")


class MonteCarloSample(StrictModel):
    """One sample: input values and the KPIs (None when the design could not run)."""

    index: int
    values: dict[str, float]
    cost_factors: dict[str, float]
    status: str
    reason: str = ""
    kpis: dict[str, float | None]
    meets_demand: bool


class DriverRank(StrictModel):
    """An input ranked by |Spearman ρ| with one KPI (UQ-02 dominant risk drivers)."""

    kpi: str
    input_name: str
    spearman_rho: float


class UncertaintyRequest(StrictModel):
    """TL-15 input (``None`` fields take the UQ-MC register values)."""

    candidate: Candidate
    outage_start_indices: list[int] | None = None
    parent_concept: ConceptId | None = None
    db30_answer: JsonValue | None = None
    samples: int | None = Field(default=None, ge=0, description="None: UQ-MC (200).")
    seed: int | None = None
    inputs: list[str] | None = Field(default=None, description="Subset of UQ-RANGES; None: all.")
    vary_costs: bool = True
    one_at_a_time: bool = True
    cases: bool = True
    cost_tornado: bool = True
    workers: int = Field(default=1, ge=1)
    day_indices: list[int] | None = Field(
        default=None, description="Quick checks only (economics cover those days); None: year."
    )
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    write_outputs: bool = True


class UncertaintyReport(StrictModel):
    """TL-15 output."""

    candidate_id: str
    seed: int
    base: DesignEvaluation
    inputs: list[UncertainInput]
    cost_ranges: dict[str, tuple[float, float]]
    one_at_a_time: list[SensitivityBar] = Field(description="Sorted by swing, largest first.")
    cases: list[SensitivityCase]
    cost_tornado: dict[str, SensitivityReport] = Field(default_factory=dict)
    samples: list[MonteCarloSample]
    failed_samples: int
    probability_demand_met: float | None = Field(description="At the installed nameplates.")
    installed_backup_mw: dict[str, float] = Field(default_factory=dict)
    robust_backup_mw: dict[str, float] = Field(
        default_factory=dict,
        description="UQ-MC robust percentile of the required boiler / electric-chiller nameplates.",
    )
    probability_demand_met_robust: float | None = Field(
        default=None, description="Share of samples meeting demand at robust_backup_mw."
    )
    robust: DesignEvaluation | None = Field(
        default=None, description="The base year with robust_backup_mw (cost of robustness)."
    )
    percentiles: dict[str, dict[str, float]]
    drivers: dict[str, list[DriverRank]]
    kpis: KPISet = Field(
        description="Base KPIs + percentiles; min_constraint_margins = worst over base + samples."
    )
    elapsed_s: float
    outputs: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# =========================================================================== evaluation
def _kpi_values(e: DesignEvaluation, installed: dict[str, float]) -> dict[str, float | None]:
    if e.status != "ok":
        return {name: None for name in PERCENTILE_KPIS}
    required = {
        kpi: installed[key] + float(getattr(e, unmet) or 0.0)
        for key, (kpi, unmet) in BACKUP_KPIS.items()
    }
    return required | {
        "lcoh_per_mwh": e.lcoh_per_mwh,
        "lcoc_per_mwh": e.lcoc_per_mwh,
        "lcoh_plus_lcoc_per_mwh": (e.lcoh_per_mwh or 0.0) + (e.lcoc_per_mwh or 0.0),
        "co2_t_per_yr": e.co2_t_per_yr,
        "capex_million": e.capex_million,
        "unmet_heating_days": float(e.unmet_heating_days or 0),
        "unmet_cooling_days": float(e.unmet_cooling_days or 0),
        "unmet_heating_mwh": e.unmet_heating_mwh,
        "unmet_cooling_mwh": e.unmet_cooling_mwh,
        "thermal_breakthrough_yr": e.thermal_breakthrough_yr,
    }


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _cost(e: DesignEvaluation) -> float | None:
    """LCOH + LCOC without the unmet penalty; None if the design could not run."""
    if e.status != "ok" or (e.lcoh_per_mwh is None and e.lcoc_per_mwh is None):
        return None
    return (e.lcoh_per_mwh or 0.0) + (e.lcoc_per_mwh or 0.0)


def _objective(e: DesignEvaluation) -> float | None:
    """LCOH + LCOC + penalty of a design that ran; None if it could not run."""
    return e.objective_per_mwh if e.status == "ok" else None


def sample_inputs(
    inputs: Sequence[UncertainInput],
    cost_ranges: dict[str, tuple[float, float]],
    escalation: UncertainInput | None,
    n: int,
    seed: int,
) -> list[tuple[dict[str, float], dict[str, float]]]:
    """Seeded Latin-hypercube samples: (input values, cost multipliers) per sample (UQ-MC)."""
    physical = list(inputs) + ([escalation] if escalation is not None else [])
    names = list(cost_ranges)
    dims = len(physical) + len(names)
    if n <= 0 or dims == 0:
        return []
    units = qmc.LatinHypercube(d=dims, seed=seed).random(n)
    out = []
    for row in units:
        values = {
            spec.name: float(spec.from_unit(float(u)))
            for spec, u in zip(physical, row[: len(physical)], strict=True)
        }
        factors = {
            name: lo + float(u) * (hi - lo)
            for (name, (lo, hi)), u in zip(cost_ranges.items(), row[len(physical) :], strict=True)
        }
        out.append((values, factors))
    return out


def percentile_table(
    samples: Sequence[MonteCarloSample], spec: dict[str, int]
) -> dict[str, dict[str, float]]:
    """P10/P50/P90 of each KPI over the samples that ran (UQ-MC: low/median/high)."""
    table: dict[str, dict[str, float]] = {}
    for kpi in PERCENTILE_KPIS:
        values = [s.kpis[kpi] for s in samples if s.kpis.get(kpi) is not None]
        if not values:
            continue
        array = np.asarray(values, dtype=float)
        table[kpi] = {label: float(np.percentile(array, q)) for label, q in spec.items()}
    return table


def driver_ranking(samples: Sequence[MonteCarloSample], top_n: int) -> dict[str, list[DriverRank]]:
    """Rank inputs by |Spearman ρ| with each KPI (constant KPIs or inputs are skipped)."""
    ran = [s for s in samples if s.status == "ok"]
    if len(ran) < 3:
        return {}
    names = sorted(
        {k for s in ran for k in s.values} | {f"cost:{k}" for s in ran for k in s.cost_factors}
    )
    columns = {
        name: np.asarray(
            [s.cost_factors[name[5:]] if name.startswith("cost:") else s.values[name] for s in ran],
            dtype=float,
        )
        for name in names
    }
    out: dict[str, list[DriverRank]] = {}
    for kpi in DRIVER_KPIS:
        y = np.asarray([s.kpis.get(kpi) for s in ran], dtype=float)
        if not np.all(np.isfinite(y)) or np.ptp(y) == 0.0:
            continue
        ranks = []
        for name, x in columns.items():
            if np.ptp(x) == 0.0:
                continue
            rho = float(spearmanr(x, y).statistic)
            if math.isfinite(rho):
                ranks.append(DriverRank(kpi=kpi, input_name=name, spearman_rho=rho))
        ranks.sort(key=lambda r: abs(r.spearman_rho), reverse=True)
        out[kpi] = ranks[:top_n]
    return out


CaseSpec = tuple[str, str, JsonValue, Scenario, bool]
"""(case, assumption id, value, scenario, keep the outage schedule)."""


def _cases(
    register: Any,
    base_values: BaseValues,
    depth_m: float,
    db30_answer: JsonValue | None,
    capex: CapexRequest | None,
) -> list[CaseSpec]:
    """UQ-CASES as scenarios.

    Alternatives of the *world* (ECO-CO2-GAS, SUB-PBUB bound, warm climate, DB-30 B) keep the base
    design's installed equipment (``capex``); alternatives of the *design* (DB-31 return
    temperature, CHP-EL-FRAC engine) re-derive it from their own plant.
    """
    spec = _mapping(register.get("UQ-CASES").value)
    bubble = _mapping(register.get("SUB-PBUB").value)
    out: list[CaseSpec] = []

    def add(
        name: str, assumption_id: str, value: JsonValue, keep: bool = True, **fields: Any
    ) -> None:
        fields.setdefault("db30_answer", db30_answer)
        out.append((name, assumption_id, value, Scenario(name=name, **fields), keep))

    for value in spec.get("grid_gas_co2_kg_per_gj", []):
        add(
            f"ECO-CO2-GAS={value:g}",
            "ECO-CO2-GAS",
            value,
            assumptions={"ECO-CO2-GAS": value},
            capex=capex,
        )
    for value in spec.get("heating_return_degc", []):
        add(f"DB-31={value:g}", "DB-31", value, heating_return_degc=value)
    for value in spec.get("chp_electrical_fraction", []):
        add(f"CHP-EL-FRAC={value:g}", "CHP-EL-FRAC", value, assumptions={"CHP-EL-FRAC": value})
    for value in spec.get("bubble_point_basis", []):
        if value != bubble["basis"]:
            edits: dict[str, JsonValue] = {
                "SUB-PBUB.basis": value,
                "SUB-PBUB.second_bound": bubble["basis"],
            }
            add(f"SUB-PBUB.basis={value}", "SUB-PBUB", value, assumptions=edits, capex=capex)
    for value in spec.get("warm_climate", []):
        if value:
            warm = scenario_for(
                {"warm_climate_frac": 1.0},
                base_values,
                depth_m=depth_m,
                name="DB-34b=warm",
                db30_answer=db30_answer,
                capex=capex,
            )
            out.append(("DB-34b=warm", "DB-34b", value, warm, True))
    for value in spec.get("db30_interpretation", []):
        add(f"DB-30={value}", "DB-30", value, keep=False, db30_answer=value, capex=capex)
    return out


def analyse(
    request: UncertaintyRequest,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    year: YearData | None = None,
) -> UncertaintyReport:
    """Run TL-15 (module doc); no files written."""
    start = time.perf_counter()
    cfg = Path(config_dir) if config_dir is not None else None
    register = load_config("assumptions", cfg)
    mc = _mapping(register.get("UQ-MC").value)
    seed = request.seed if request.seed is not None else int(mc["seed"])
    n_samples = request.samples if request.samples is not None else int(mc["samples"])
    candidate, outages = request.candidate, request.outage_start_indices
    depth = float(candidate.depth_m or 0.0)
    inputs, base_values = load_uncertain_inputs(cfg)
    if request.inputs is not None:
        unknown = sorted(set(request.inputs) - {i.name for i in inputs})
        if unknown:
            raise ValueError(f"unknown UQ-RANGES inputs {unknown}")
        inputs = [i for i in inputs if i.name in request.inputs]
    if mc["sampling"] != "latin_hypercube" or mc["driver_ranking"] != "spearman_rank_correlation":
        raise ValueError(
            "UQ-MC: only latin_hypercube sampling and Spearman ranking are implemented"
        )
    year = year or year_conditions(cfg)
    warnings: list[str] = []
    base_eval = Evaluator.build(
        scenario=Scenario(db30_answer=request.db30_answer),
        config_dir=cfg,
        map_cache_dir=map_cache_dir,
        parent_concept=request.parent_concept,
        year=year,
        day_indices=request.day_indices,
        reject_limit_breaches=False,
    )
    base_run = base_eval.run(candidate, outages, stage="uncertainty-base", keep=True)
    base = base_run.evaluation
    if base.status != "ok" or base_run.simulation is None or base_run.economics is None:
        raise ValueError(f"base design cannot be evaluated: {base.reason}")
    if base.limit_breaches:
        warnings.append(f"base design breaches in operation: {'; '.join(base.limit_breaches)}")
    assert base_run.plant is not None
    base_capex = capex_request(base_run.plant, base_run.simulation.annual)
    equipment = base_run.plant.meta.equipment_mw
    installed = {key: float(equipment.get(key, 0.0)) for key in BACKUP_KPIS}
    book = CostBook.load(cfg)
    cost_ranges = (
        cost_factor_ranges([i.name for i in base_run.economics.cost_items], book)
        if request.vary_costs
        else {}
    )
    nominal = book.stated_range("energy_escalation_frac")
    escalation = None
    if request.vary_costs and nominal is not None:
        low, high = (real_energy_escalation(book, e) for e in nominal)
        escalation = UncertainInput(
            name=ESCALATION_INPUT,
            distribution="uniform",
            low=low,
            high=high,
            base=real_energy_escalation(book),
            source="ECO-REAL: RC-10 energy escalation range minus general inflation",
            note="real escalation of energy and CO2 prices",
        )
    context = {
        "config_dir": cfg,
        "map_cache_dir": map_cache_dir,
        "parent_concept": request.parent_concept,
        "year": year,
        "day_indices": request.day_indices,
        "stage": "uncertainty",
        "reject_limit_breaches": False,
    }

    def scenario(
        values: dict[str, float], name: str, factors: dict[str, float] | None = None
    ) -> Scenario:
        return scenario_for(
            values,
            base_values,
            depth_m=depth,
            name=name,
            cost_factors=factors,
            db30_answer=request.db30_answer,
            capex=base_capex,
        )

    # ---- one-at-a-time sensitivity ---------------------------------------------------------
    bars: list[SensitivityBar] = []
    if request.one_at_a_time:
        oat_inputs = inputs + ([escalation] if escalation is not None else [])
        tasks = []
        for spec in oat_inputs:
            for label, value in (("low", spec.low), ("high", spec.high)):
                tasks.append(
                    (scenario({spec.name: value}, f"{spec.name}:{label}"), candidate, outages)
                )
        results = evaluate_scenarios(tasks, context, request.workers)
        for k, spec in enumerate(oat_inputs):
            lo, hi = results[2 * k], results[2 * k + 1]
            ol, oh = _objective(lo), _objective(hi)
            cl, ch = _cost(lo), _cost(hi)
            bars.append(
                SensitivityBar(
                    input_name=spec.name,
                    source=spec.source,
                    base_value=spec.base,
                    low_value=spec.low,
                    high_value=spec.high,
                    objective_low_per_mwh=ol,
                    objective_high_per_mwh=oh,
                    cost_low_per_mwh=cl,
                    cost_high_per_mwh=ch,
                    lcoh_low_per_mwh=lo.lcoh_per_mwh,
                    lcoh_high_per_mwh=hi.lcoh_per_mwh,
                    lcoc_low_per_mwh=lo.lcoc_per_mwh,
                    lcoc_high_per_mwh=hi.lcoc_per_mwh,
                    co2_low_t_per_yr=lo.co2_t_per_yr,
                    co2_high_t_per_yr=hi.co2_t_per_yr,
                    unmet_low_mwh=(
                        None
                        if lo.status != "ok"
                        else (lo.unmet_heating_mwh or 0) + (lo.unmet_cooling_mwh or 0)
                    ),
                    unmet_high_mwh=(
                        None
                        if hi.status != "ok"
                        else (hi.unmet_heating_mwh or 0) + (hi.unmet_cooling_mwh or 0)
                    ),
                    status_low=lo.status,
                    status_high=hi.status,
                    swing_per_mwh=abs(ch - cl) if cl is not None and ch is not None else 0.0,
                )
            )
        bars.sort(key=lambda b: b.swing_per_mwh, reverse=True)
        for b in bars:
            worst = max(b.unmet_low_mwh or 0.0, b.unmet_high_mwh or 0.0)
            if worst > 0:
                warnings.append(
                    f"sensitivity {b.input_name}: up to {worst:.0f} MWh/yr unmet with the "
                    "installed backup nameplates (see robust_backup_mw)"
                )
            if b.status_low != "ok" or b.status_high != "ok":
                warnings.append(
                    f"sensitivity {b.input_name}: a bound could not run "
                    f"({b.status_low}/{b.status_high})"
                )

    # ---- discrete cases ----------------------------------------------------------------------
    cases: list[SensitivityCase] = []
    if request.cases:
        specs = _cases(register, base_values, depth, request.db30_answer, base_capex)
        results = evaluate_scenarios(
            [(scn, candidate, outages if keep else None) for _, _, _, scn, keep in specs],
            context,
            request.workers,
        )
        for (case, assumption_id, value, _, _), e in zip(specs, results, strict=True):
            ok = e.status == "ok"
            cases.append(
                SensitivityCase(
                    case=case,
                    assumption_id=assumption_id,
                    value=value,
                    evaluation=e,
                    delta_objective_per_mwh=(
                        _delta(e.objective_per_mwh, base.objective_per_mwh) if ok else None
                    ),
                    delta_lcoh_per_mwh=_delta(e.lcoh_per_mwh, base.lcoh_per_mwh) if ok else None,
                    delta_lcoc_per_mwh=_delta(e.lcoc_per_mwh, base.lcoc_per_mwh) if ok else None,
                    delta_co2_t_per_yr=_delta(e.co2_t_per_yr, base.co2_t_per_yr) if ok else None,
                    delta_cost_per_mwh=_delta(_cost(e), _cost(base)) if ok else None,
                    unmet_mwh=(
                        (e.unmet_heating_mwh or 0.0) + (e.unmet_cooling_mwh or 0.0) if ok else None
                    ),
                )
            )
            if not ok:
                warnings.append(f"case {case}: {e.status} — {e.reason[:120]}")

    # ---- WP-08 cost tornado ------------------------------------------------------------------
    tornado: dict[str, SensitivityReport] = {}
    if request.cost_tornado:
        sim = base_run.simulation
        eco_request = EconomicsRequest(
            capex=base_capex,
            gas_handling=candidate.gas_handling,
            service_by_key=sim.service_by_key,
        )
        for kpi in ("lcoh", "lcoc"):
            tornado[kpi] = cost_sensitivity(
                eco_request, sim.annual, kpi=kpi, top_n=8, config_dir=cfg  # type: ignore[arg-type]
            ).data

    # ---- Monte Carlo -------------------------------------------------------------------------
    draws = sample_inputs(inputs, cost_ranges, escalation, n_samples, seed)
    results = evaluate_scenarios(
        [(scenario(v, f"mc-{i}", f), candidate, outages) for i, (v, f) in enumerate(draws)],
        context,
        request.workers,
    )
    samples = [
        MonteCarloSample(
            index=i,
            values=v,
            cost_factors=f,
            status=e.status,
            reason=e.reason if e.status != "ok" else "",
            kpis=_kpi_values(e, installed),
            meets_demand=e.meets_demand,
        )
        for i, ((v, f), e) in enumerate(zip(draws, results, strict=True))
    ]
    failed = sum(1 for s in samples if s.status != "ok")
    if failed:
        warnings.append(
            f"{failed}/{len(samples)} samples could not run (counted as demand not met)"
        )
    breached = [e for e in results if e.limit_breaches]
    if breached:
        warnings.append(
            f"{len(breached)}/{len(samples)} samples breach a hard limit in operation (counted as "
            f"demand not met; first: {breached[0].limit_breaches[0]})"
        )
    spec = {str(k): int(v) for k, v in _mapping(mc["percentiles"]).items()}
    percentiles = percentile_table(samples, spec)
    drivers = driver_ranking(samples, int(mc["top_drivers"]))
    probability = sum(s.meets_demand for s in samples) / len(samples) if samples else None
    robust_mw: dict[str, float] = {}
    robust_probability = None
    robust_eval = None
    quantile = float(mc["robust_backup_percentile"])
    ran = [smp for smp in samples if smp.status == "ok"]
    if ran:
        step = base_eval.settings.size_resolution_mw
        for key, (kpi, _) in BACKUP_KPIS.items():
            need = float(np.percentile([smp.kpis[kpi] for smp in ran], quantile))
            robust_mw[key] = max(installed[key], math.ceil(need / step - 1e-9) * step)
            robust_mw[key] = round(robust_mw[key], 6)
        # Larger backups fix capacity shortfalls (incl. their DB-16/17 supply margins), not a
        # DB-11/12/14 breach: such a sample does not meet demand at any backup size.
        unfixable = {
            i
            for i, e in enumerate(results)
            if any(breach_code(b) not in SHORTFALL_CODES for b in e.limit_breaches)
        }
        robust_probability = sum(
            smp.index not in unfixable
            and all(smp.kpis[kpi] <= robust_mw[key] + 1e-9 for key, (kpi, _) in BACKUP_KPIS.items())
            for smp in ran
        ) / len(samples)
        if robust_mw != installed:
            sized = candidate.model_copy(
                update={"equipment_sizes": candidate.equipment_sizes | robust_mw}
            )
            robust_eval = base_eval.evaluate(sized, outages, stage="uncertainty-robust")
    margins = dict(base.kpis.min_constraint_margins) if base.kpis else {}
    for e in results:
        if e.status == "ok" and e.kpis is not None:
            for key, value in e.kpis.min_constraint_margins.items():
                margins[key] = min(margins.get(key, value), value)
    assert base.kpis is not None
    kpis = base.kpis.model_copy(
        update={"percentiles": percentiles, "min_constraint_margins": margins}
    )
    return UncertaintyReport(
        candidate_id=candidate.id,
        seed=seed,
        base=base,
        inputs=inputs + ([escalation] if escalation is not None else []),
        cost_ranges=cost_ranges,
        one_at_a_time=bars,
        cases=cases,
        cost_tornado=tornado,
        samples=samples,
        failed_samples=failed,
        probability_demand_met=probability,
        installed_backup_mw=installed,
        robust_backup_mw=robust_mw,
        probability_demand_met_robust=robust_probability,
        robust=robust_eval,
        percentiles=percentiles,
        drivers=drivers,
        kpis=kpis,
        elapsed_s=time.perf_counter() - start,
        warnings=warnings,
    )


# =========================================================================== outputs
def _write_outputs(report: UncertaintyReport, run_dir: Path) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = run_dir / "uncertainty.json"
    summary.write_text(report.model_dump_json(indent=1), encoding="utf-8")
    table = run_dir / "uncertainty_samples.csv"
    value_names = sorted({k for s in report.samples for k in s.values})
    cost_names = sorted({k for s in report.samples for k in s.cost_factors})
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "index",
                "status",
                "meets_demand",
                *value_names,
                *(f"cost:{c}" for c in cost_names),
                *PERCENTILE_KPIS,
            ]
        )
        for s in report.samples:
            writer.writerow(
                [s.index, s.status, s.meets_demand]
                + [s.values.get(k) for k in value_names]
                + [s.cost_factors.get(k) for k in cost_names]
                + [s.kpis.get(k) for k in PERCENTILE_KPIS]
            )
    return {"summary": str(summary), "samples": str(table)}


def _fmt(table: dict[str, dict[str, float]], kpi: str, spec: str = ".0f") -> str:
    row = table.get(kpi)
    if not row:
        return "n/a"
    return "/".join(format(row[k], spec) for k in ("p10", "p50", "p90") if k in row)


def _summary(report: UncertaintyReport) -> str:
    p = report.percentiles
    top = next(iter(report.drivers.get("lcoh_plus_lcoc_per_mwh", [])), None)
    bar = report.one_at_a_time[0] if report.one_at_a_time else None
    prob = report.probability_demand_met
    robust = report.robust_backup_mw
    text = (
        f"{TOOL_ID} {report.candidate_id}: {len(report.samples)} samples (seed {report.seed}); "
        f"P10/P50/P90 LCOH {_fmt(p, 'lcoh_per_mwh', '.1f')}, LCOC {_fmt(p, 'lcoc_per_mwh', '.1f')} "
        f"USD/MWh, CO2 {_fmt(p, 'co2_t_per_yr')} t/yr; P(demand met) "
        f"{'n/a' if prob is None else f'{100 * prob:.0f} %'}"
        + (
            f" (boiler {robust['peak_boiler_mw']:.2f}/chiller {robust['electric_chiller_mw']:.2f}"
            f" MW: {100 * (report.probability_demand_met_robust or 0):.0f} %)"
            if robust
            else ""
        )
        + (f"; top driver {top.input_name} (ρ {top.spearman_rho:+.2f})" if top else "")
        + (f"; largest OAT swing {bar.input_name} {bar.swing_per_mwh:.1f}" if bar else "")
    )
    return text[:SUMMARY_MAX_CHARS]


def uncertainty(
    request: UncertaintyRequest,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    runs_root: Path | str | None = None,
) -> ToolResult[UncertaintyReport]:
    """TL-15: sensitivity cases, cost tornado and seeded Monte Carlo on one design (§9.3)."""
    report = analyse(request, config_dir=config_dir, map_cache_dir=map_cache_dir)
    if request.write_outputs:
        run_id = request.run_id or f"uncertainty-{request.candidate.id}-seed{report.seed}"
        root = Path(runs_root) if runs_root is not None else DEFAULT_RUNS_ROOT
        report = report.model_copy(update={"outputs": _write_outputs(report, root / run_id)})
    return ToolResult[UncertaintyReport](
        data=report,
        summary=_summary(report),
        warnings=report.warnings[:25],
        assumption_ids=list(UQ_ASSUMPTION_IDS),
        sources=list(SOURCES),
    )


__all__ = [
    "TOOL_ID",
    "BaseValues",
    "DriverRank",
    "MonteCarloSample",
    "SensitivityBar",
    "SensitivityCase",
    "UncertainInput",
    "UncertaintyReport",
    "UncertaintyRequest",
    "analyse",
    "cost_factor_ranges",
    "driver_ranking",
    "load_uncertain_inputs",
    "percentile_table",
    "sample_inputs",
    "scenario_for",
    "uncertainty",
]
