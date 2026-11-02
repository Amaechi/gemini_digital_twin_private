"""TL-12 ``simulate_annual(mode)``: daily dispatch over the year, fast or full mode (spec §8).

* **Fast mode (SIM-04):** every simulated day is dispatched on :class:`FastPlant` (TL-06 maps
  for the wells, the TL-09 surface components evaluated directly) — 365 days in a few seconds.
* **Full mode (SIM-04):** the same daily plans (flows, HTHP on/off, absorption drive, bypass
  shares) are run in GEMINI every day with TL-11 :func:`simulate_plant_day` (TL-05 operating
  points, GEMINI project, TL-11 controls). The fast results are kept as ``fast_annual`` so the
  verifier's PD-06 can compare the modes; :func:`compare_modes` applies the same rule (per
  source and unmet demand relative to the day's demand, electricity relative; > 2 % warn,
  > 5 % fail).

Days and doublet availability come from the DB-30 calendar (SIM-02, :mod:`.dispatch`); demand
from the DB-18/DB-19 CSVs and ambient from the TL-02b daily climate profile (dry and wet bulb,
RC-07). Every ``DayResult`` carries the calendar availability (not merely "flow > 0"), the brine
heat drawn per user, the associated gas produced and unit-suffixed margins; consumer and burner
keys resolve with WP-08 ``classify_service`` (``service_by_key`` is returned explicitly too).
KPIs (``KPISet``) are filled from the daily results; annual CO₂ comes from WP-08
``co2_emissions`` (adds DB-37 grid electricity). Results go to ``runs/<run_id>/`` as one CSV per
mode and a JSON summary (Parquet needs pyarrow, which the project does not install). SIM-07: the
result's ``config_hash`` keys a ``DesignCache`` entry (namespace ``simulate_annual:<mode>:v1``)
when a cache is passed. SIM-06: for a whole simulated year, TL-07 (RC-02) gives each doublet's
breakthrough time and production temperature at the analysis horizon for its annual-mean flow
and flow-weighted mean production and injection temperatures (the dispatched ΔT, not DB-12's).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field, JsonValue, model_validator

from gemini_agent.memory.design_cache import DesignCache, config_hash
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    ConceptId,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet
from gemini_agent.tools.climate import build_climate_profile
from gemini_agent.tools.config_generator import (
    GENERATOR_ASSUMPTION_IDS,
    GENERATOR_VERSION,
)
from gemini_agent.tools.demand import read_demand_csv
from gemini_agent.tools.design_basis import SECONDS_PER_HOUR
from gemini_agent.tools.dispatch import (
    DISPATCH_ASSUMPTION_IDS,
    AvailabilityCalendar,
    DayPlan,
    FastDay,
    FastPlant,
    StorageBalance,
    apply_storage_discharge,
    availability_calendar,
    db30_rule,
    discharge_into_day,
    dispatch_day,
    spare_geothermal_mw,
    storage_ledger,
    storage_spec,
)
from gemini_agent.tools.doublet import DoubletDesign
from gemini_agent.tools.economics import (
    Co2Request,
    Service,
    classify_service,
    co2_emissions,
)
from gemini_agent.tools.resource import SubsurfaceBasis
from gemini_agent.tools.simulate_day import (
    HEAT_TOL_MW,
    DayConditions,
    simulate_plant_day,
)
from gemini_agent.tools.thermal import (
    ThermalBreakthroughRequest,
    thermal_breakthrough_report,
)
from gemini_agent.verify.design_verifier import (
    FAST_FULL_FAIL_FRAC,
    FAST_FULL_WARN_FRAC,
    EspPoint,
    HeatPumpPoint,
    HexPoint,
    PumpPoint,
    VerificationEvidence,
)

TOOL_ID = "TL-12"
CACHE_VERSION = 1
"""Bump when the dispatch rules or the fast model change (design-cache namespace)."""
DEFAULT_RUNS_ROOT = REPO_ROOT / "runs"
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$"
MAX_WARNINGS = 25
STORAGE_BALANCE_TOL_FRAC = 0.01
"""Seasonal storage end − start energy above 1 % of capacity is warned (RC-09 annual balance)."""
SOURCES = [
    "spec §8 SIM-01…07",
    "spec §2.4 DB-30",
    "spec §3.3",
    "RC-02",
    "RC-05",
    "RC-06",
    "RC-07",
    "RC-09",
]


# --------------------------------------------------------------------------- inputs
class SimulateAnnualRequest(StrictModel):
    """TL-12 input."""

    candidate: Candidate
    mode: Literal["fast", "full"] = "fast"
    db30_answer: JsonValue | None = Field(
        default=None, description="answers['DB-30'] (runner dict or 'A'/'B'); None: register."
    )
    outage_start_indices: list[int] | None = Field(
        default=None,
        description="Optimiser lever (DB-30 A): 0-based first outage day per doublet; None: "
        "lowest-load non-overlapping windows (SIM-OUTAGE).",
    )
    day_indices: list[int] | None = Field(
        default=None, description="Subset of the calendar's simulated days (e.g. a sample)."
    )
    design: DoubletDesign | None = None
    parent_concept: ConceptId | None = Field(default=None, description="For C6–C8.")
    compare_day_indices: list[int] | None = Field(
        default=None,
        description="Fast mode only: also run these days in full mode and compare (SIM-04).",
    )
    run_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    write_outputs: bool = True

    @model_validator(mode="after")
    def _check(self) -> SimulateAnnualRequest:
        if self.mode == "full" and self.compare_day_indices is not None:
            raise ValueError("compare_day_indices is for fast mode (full mode compares every day)")
        return self


class YearData(StrictModel):
    """Demand and climate of every day of the design year (DB-18, DB-19, DB-34a)."""

    year: int
    days: list[DayConditions]


def year_conditions(config_dir: Path | str | None = None) -> YearData:
    """All days of the design year: demand CSVs and the TL-02b daily dry/wet bulb."""
    basis = load_config("design_basis", config_dir)
    profile, _ = build_climate_profile(load_config("climate_nl", config_dir), basis.year)
    heating = read_demand_csv(basis.heating, basis.year)
    cooling = read_demand_csv(basis.cooling, basis.year)
    days = [
        DayConditions(
            date=date,
            day_index=i,
            heating_demand_mw=float(heating.iloc[i]),
            cooling_demand_mw=float(cooling.iloc[i]),
            ambient_dry_bulb_degc=profile.dry_bulb_degc[i],
            ambient_wet_bulb_degc=profile.wet_bulb_degc[i],
        )
        for i, date in enumerate(profile.dates)
    ]
    return YearData(year=basis.year, days=days)


# --------------------------------------------------------------------------- outputs
class DayModeComparison(StrictModel):
    """Worst fast-vs-full discrepancy of one day (PD-06 metric)."""

    day_index: int
    date: dt.date
    max_discrepancy_frac: float
    worst_item: str
    heat_supplied_fast_mw: float
    heat_supplied_full_mw: float
    status: Literal["pass", "warn", "fail"]


class ModeComparison(StrictModel):
    """SIM-04: fast vs full on the same days (> 2 % warning, > 5 % fail)."""

    days: list[DayModeComparison]
    max_discrepancy_frac: float
    max_heat_supplied_discrepancy_frac: float = Field(
        description="Total heat delivered, relative to the day's heating demand."
    )
    warn_frac: float = FAST_FULL_WARN_FRAC
    fail_frac: float = FAST_FULL_FAIL_FRAC
    status: Literal["pass", "warn", "fail"]


class FullModeEvidence(StrictModel):
    """Verifier evidence gathered from the full-mode days (WP-09 ``VerificationEvidence``)."""

    project: dict[str, JsonValue]
    hex_points: list[HexPoint] = Field(default_factory=list)
    heat_pumps: list[HeatPumpPoint] = Field(default_factory=list)
    pumps: list[PumpPoint] = Field(default_factory=list)
    esps: list[EspPoint] = Field(default_factory=list)
    brine_contact_materials: dict[str, str] = Field(default_factory=dict)
    co2_release_pressure_barg: float | None = None
    model_warnings: list[str] = Field(
        default_factory=list,
        description="WP-14: distinct model validity warnings of the year, first text + [×days].",
    )


class EndOfLifeCheck(StrictModel):
    """SIM-06: TL-07 at one doublet's annual-mean operation (RC-02 eq 1–3)."""

    doublet: int = Field(ge=1)
    mean_flow_m3h: float = Field(
        description="Σ daily flow / simulated days (outages count as 0). Under DB-30 B that is "
        "330 days: about 10 % above Σ/365, i.e. a conservative (earlier) breakthrough."
    )
    mean_production_degc: float = Field(description="Flow-weighted wellhead temperature.")
    mean_injection_degc: float = Field(description="Flow-weighted injection temperature.")
    breakthrough_time_yr: float
    horizon_yr: float
    breakthrough_after_horizon: bool
    temperature_at_horizon_degc: float


class AnnualSimulation(StrictModel):
    """TL-12 output."""

    candidate_id: str
    mode: Literal["fast", "full"]
    config_hash: str = Field(description="DesignCache key of the request (SIM-07).")
    calendar: AvailabilityCalendar
    annual: AnnualResult = Field(description="Results of ``mode`` (KPIs filled).")
    fast_annual: AnnualResult | None = Field(
        default=None, description="Full mode: the fast dispatch results of the same days."
    )
    plans: list[DayPlan]
    service_by_key: dict[str, Service] = Field(
        description="Every consumer/burner/user key → WP-08 service (economics allocation)."
    )
    storage: StorageBalance | None = None
    comparison: ModeComparison | None = None
    invalid_full_days: list[str] = Field(
        default_factory=list, description="Full-mode days GEMINI could not run (fast result kept)."
    )
    evidence: FullModeEvidence | None = None
    end_of_life: list[EndOfLifeCheck] = Field(
        default_factory=list, description="SIM-06, whole simulated years only."
    )
    outputs: dict[str, str] = Field(default_factory=dict, description="Files written.")
    elapsed_s: float
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _placement_load(plant: FastPlant, year: YearData) -> list[float]:
    """Geothermal-side load for outage placement: heating + cooling/COP_abs if absorption."""
    cop = plant.absorption_design_cop()
    return [d.heating_demand_mw + (d.cooling_demand_mw / cop if cop else 0.0) for d in year.days]


def compare_days(
    full: Sequence[DayResult],
    fast: Sequence[DayResult],
    *,
    invalid_day_indices: Collection[int] = (),
) -> ModeComparison:
    """Compare full and fast days by the SIM-04 / PD-06 rule; the worst item per day.

    Per source heat and cooling and unmet demand relative to the day's demand (or supply),
    electricity relative to the larger of the two. A day GEMINI could not run in full mode
    (``invalid_day_indices``) fails outright: its full result is the fast one and would compare
    equal.
    """
    by_index = {d.day_index: d for d in fast}
    rows = []
    worst_heat = 0.0
    for day in full:
        other = by_index.get(day.day_index)
        if other is None:
            raise ValueError(f"no fast result for day {day.day_index}")
        if day.day_index in invalid_day_indices:
            rows.append(
                DayModeComparison(
                    day_index=day.day_index,
                    date=day.date,
                    max_discrepancy_frac=1.0,
                    worst_item="full mode invalid (GEMINI could not run the day)",
                    heat_supplied_fast_mw=other.heat_supplied_mw,
                    heat_supplied_full_mw=day.heat_supplied_mw,
                    status="fail",
                )
            )
            continue
        items: list[tuple[float, str]] = []
        sides = (
            ("heat", day.heat_by_source_mw, other.heat_by_source_mw, day.heating_demand_mw,
             day.unmet_heating_mwh, other.unmet_heating_mwh),
            ("cooling", day.cool_by_source_mw, other.cool_by_source_mw, day.cooling_demand_mw,
             day.unmet_cooling_mwh, other.unmet_cooling_mwh),
        )  # fmt: skip
        for kind, a, b, demand, unmet_a, unmet_b in sides:
            scale = max(demand, sum(a.values()))
            if scale <= 0:
                continue
            for key in sorted(set(a) | set(b)):
                error = abs(a.get(key, 0.0) - b.get(key, 0.0)) / scale
                items.append((error, f"{kind} '{key}'"))
            items.append((abs(unmet_a - unmet_b) / (scale * HOURS_PER_DAY), f"unmet {kind}"))
        el_a, el_b = day.electricity_consumed_mw, other.electricity_consumed_mw
        if max(el_a, el_b) > 0:
            items.append((abs(el_a - el_b) / max(el_a, el_b), "electricity"))
        heat_error = abs(day.heat_supplied_mw - other.heat_supplied_mw) / max(
            day.heating_demand_mw, day.heat_supplied_mw, 1e-12
        )
        worst_heat = max(worst_heat, heat_error)
        error, item = max(items, default=(0.0, "-"))
        rows.append(
            DayModeComparison(
                day_index=day.day_index,
                date=day.date,
                max_discrepancy_frac=error,
                worst_item=item,
                heat_supplied_fast_mw=other.heat_supplied_mw,
                heat_supplied_full_mw=day.heat_supplied_mw,
                status=_status(error),
            )
        )
    worst = max((r.max_discrepancy_frac for r in rows), default=0.0)
    return ModeComparison(
        days=rows,
        max_discrepancy_frac=worst,
        max_heat_supplied_discrepancy_frac=worst_heat,
        status=_status(worst),
    )


def _status(error: float) -> Literal["pass", "warn", "fail"]:
    if error > FAST_FULL_FAIL_FRAC:
        return "fail"
    if error > FAST_FULL_WARN_FRAC:
        return "warn"
    return "pass"


HEAT_ONLY_KEYS = re.compile(r"^(hex_\d+|storage)$")
"""Keys WP-08's keyword rules do not know: the plate HEXs and C6 storage serve heating only."""


def service_map(days: Sequence[DayResult]) -> dict[str, Service]:
    """Service of every electricity, fuel and brine-user key (WP-08 ``classify_service``).

    Plate HEXs (``hex_<i>``) and C6 ``storage`` serve heating; any other unknown key is shared.
    """
    keys: set[str] = set()
    for day in days:
        keys |= set(day.electricity_by_consumer_mw) | set(day.gas_fuel_by_consumer_mw)
        keys |= set(day.brine_heat_drawn_mw)
    result: dict[str, Service] = {}
    for key in sorted(keys):
        service = classify_service(key)
        if service is None and HEAT_ONLY_KEYS.match(key):
            service = "heat"
        result[key] = service if service is not None else "shared"
    return result


def end_of_life_checks(
    annual: AnnualResult, depth_m: float | None, sub: SubsurfaceBasis
) -> list[EndOfLifeCheck]:
    """SIM-06: TL-07 per doublet at its annual-mean flow and flow-weighted temperatures."""
    days = annual.days
    if not days or depth_m is None or not days[0].doublet_flow_m3h:
        return []
    checks = []
    for i in range(len(days[0].doublet_flow_m3h)):
        flows = [d.doublet_flow_m3h[i] for d in days]
        total = sum(flows)
        if total <= 0:
            continue
        t_prod = (
            sum(q * d.production_temperature_degc[i] for q, d in zip(flows, days, strict=True))
            / total
        )
        t_inj = (
            sum(q * d.injection_temperature_degc[i] for q, d in zip(flows, days, strict=True))
            / total
        )
        report = thermal_breakthrough_report(
            sub,
            ThermalBreakthroughRequest(
                flow_m3h=total / len(days),
                depth_m=depth_m,
                wellhead_temperature_degc=t_prod,
                injection_temperature_degc=t_inj,
            ),
        )
        checks.append(
            EndOfLifeCheck(
                doublet=i + 1,
                mean_flow_m3h=report.flow_m3h,
                mean_production_degc=t_prod,
                mean_injection_degc=t_inj,
                breakthrough_time_yr=report.breakthrough_time_yr,
                horizon_yr=report.horizon_yr,
                breakthrough_after_horizon=report.breakthrough_after_horizon,
                temperature_at_horizon_degc=report.temperature_at_horizon_degc,
            )
        )
    return checks


def annual_kpis(
    annual: AnnualResult,
    plant: FastPlant,
    *,
    co2_t_per_yr: float | None,
    breakthrough_yr: float | None = None,
    storage_mw: float = 0.0,
) -> KPISet:
    """Return the simulation KPIs (spec §3.4 ENG-01a) from the daily results (economics: WP-08).

    Installed capacity = nameplates where the candidate or TL-09 gives one (plate HEX design duty
    per doublet, ``hthp_heating_mw``, peak boiler, chillers, C6 ``storage_mw``), else the
    largest daily output of that source (CHP, free cooling, HTHP without a nameplate). Capacity
    factor per doublet = Σ daily flow / (days × design flow). ``min_constraint_margins`` = the
    smallest daily value of every margin key (≥ 0 satisfied).
    """
    days = annual.days
    heat: Counter[str] = Counter()
    cool: Counter[str] = Counter()
    peak: Counter[str] = Counter()
    margins: dict[str, float] = {}
    for day in days:
        for key, mw in day.heat_by_source_mw.items():
            heat[key] += mw * HOURS_PER_DAY
        for key, mw in day.cool_by_source_mw.items():
            cool[key] += mw * HOURS_PER_DAY
        groups: Counter[str] = Counter()
        for key, mw in (day.heat_by_source_mw | day.cool_by_source_mw).items():
            groups[re.sub(r"_\d+$", "", key)] += mw
        for key, mw in groups.items():
            peak[key] = max(peak[key], mw)
        for key, value in day.margins.items():
            margins[key] = min(margins.get(key, value), value)
    meta = plant.meta
    installed_heat = plant.boiler_capacity_w / 1e6 + peak.get("chp", 0.0) + storage_mw
    if meta.hex_design is not None:
        installed_heat += meta.hex_design.duty_mw * plant.n_doublets
    if plant.has_hp:
        installed_heat += (
            plant.hp_nameplate_w / 1e6 if plant.hp_nameplate_w is not None else peak.get("hthp", 0)
        )
    installed_cool = (
        meta.equipment_mw.get("electric_chiller_mw", 0.0)
        + meta.equipment_mw.get("absorption_chiller_mw", 0.0)
        + peak.get("free_cooling", 0.0)
    )
    design_flow = float(plant.candidate.design_flow_m3h or 0.0)
    factors = []
    if days and design_flow > 0:
        for i in range(plant.n_doublets):
            total = sum(d.doublet_flow_m3h[i] for d in days if d.doublet_flow_m3h)
            factors.append(total / (len(days) * design_flow))
    return KPISet(
        candidate_id=annual.candidate_id,
        unmet_heating_days=annual.unmet_heating_days,
        unmet_heating_mwh=annual.unmet_heating_mwh,
        unmet_cooling_days=annual.unmet_cooling_days,
        unmet_cooling_mwh=annual.unmet_cooling_mwh,
        installed_heating_mw=installed_heat,
        installed_cooling_mw=installed_cool,
        heat_by_source_mwh=dict(heat),
        cool_by_source_mwh=dict(cool),
        doublet_capacity_factors_frac=factors,
        electricity_consumed_mwh=annual.electricity_consumed_mwh,
        electricity_generated_mwh=sum(d.electricity_generated_mw for d in days) * HOURS_PER_DAY,
        gas_fuel_mwh=sum(sum(d.gas_fuel_by_consumer_mw.values()) for d in days) * HOURS_PER_DAY,
        co2_t_per_yr=co2_t_per_yr,
        min_constraint_margins=margins,
        thermal_breakthrough_yr=breakthrough_yr,
    )


def _dedupe(warnings: Sequence[str]) -> list[str]:
    """Return unique warnings (digits collapsed), most frequent first, with their counts."""
    counts: Counter[str] = Counter()
    first: dict[str, str] = {}
    for text in warnings:
        key = re.sub(r"[-+]?\d+(\.\d+)?", "#", text)
        counts[key] += 1
        first.setdefault(key, text)
    return [
        f"{first[key]} [×{n}]" if n > 1 else first[key]
        for key, n in counts.most_common(MAX_WARNINGS)
    ]


def cache_config(
    request: SimulateAnnualRequest,
    calendar: AvailabilityCalendar,
    config_dir: Path | str | None = None,
) -> dict:
    """Return what the annual result depends on (SIM-07 key payload).

    Includes the register, design basis, limits and climate it reads (DSP-*, STO-REC-HT, DB-*)
    and the SHA-256 of the demand CSVs; a change of the dispatch rules or the fast model in code
    needs a ``CACHE_VERSION`` bump.
    """
    basis = load_config("design_basis", config_dir)
    demand = {
        side.demand_csv: hashlib.sha256(side.csv_path(REPO_ROOT).read_bytes()).hexdigest()
        for side in (basis.heating, basis.cooling)
    }
    return {
        "climate": load_config("climate_nl", config_dir).model_dump(mode="json"),
        "demand_sha256": demand,
        "assumptions": load_config("assumptions", config_dir).model_dump(mode="json"),
        "design_basis": load_config("design_basis", config_dir).model_dump(mode="json"),
        "limits": load_config("limits", config_dir).model_dump(mode="json"),
        "candidate": request.candidate.model_dump(mode="json"),
        "design": request.design.model_dump(mode="json") if request.design else None,
        "parent_concept": request.parent_concept,
        "calendar": calendar.model_dump(mode="json"),
        "day_indices": request.day_indices,
        "generator_version": GENERATOR_VERSION,
    }


# --------------------------------------------------------------------------- core
def run_annual(
    request: SimulateAnnualRequest,
    *,
    config_dir: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    year: YearData | None = None,
    plant: FastPlant | None = None,
) -> AnnualSimulation:
    """Dispatch the year (module doc); no files written (see :func:`simulate_annual`)."""
    start = time.perf_counter()
    candidate = request.candidate
    register = load_config("assumptions", config_dir)
    year = year or year_conditions(config_dir)
    if plant is not None and (
        plant.candidate != candidate
        or plant.design != request.design
        or plant.parent_concept != request.parent_concept
    ):
        raise ValueError("the prebuilt FastPlant is not the requested candidate/design/parent")
    plant = plant or FastPlant(
        candidate,
        design=request.design,
        parent_concept=request.parent_concept,
        config_dir=config_dir,
        map_cache_dir=map_cache_dir,
    )
    hp_rule = register.get("DSP-HP-MIN").value
    assert isinstance(hp_rule, dict)
    hp_min = float(hp_rule["min_part_load_frac"])
    hp_extra = float(hp_rule["max_extra_flow_frac"])
    abs_min = float(register.get("DSP-ABS-MIN").value)  # type: ignore[arg-type]
    rule = db30_rule(request.db30_answer, register=register)
    calendar = availability_calendar(
        candidate.doublets,
        year.year,
        _placement_load(plant, year),
        rule=rule,
        outage_start_indices=request.outage_start_indices,
        exclusion_load_mw=[d.heating_demand_mw + d.cooling_demand_mw for d in year.days],
    )
    indices = calendar.simulated_day_indices
    if request.day_indices is not None:
        unknown = sorted(set(request.day_indices) - set(indices))
        if unknown:
            raise ValueError(
                f"days {unknown[:5]} are not simulated under DB-30 {rule.interpretation}"
            )
        indices = sorted(set(request.day_indices))
    warnings: list[str] = []

    def dispatch(index: int, charge: float = 0.0) -> FastDay:
        return dispatch_day(
            plant,
            year.days[index],
            calendar.available(index),
            storage_charge_mw=charge,
            hp_min_frac=hp_min,
            absorption_min_frac=abs_min,
            hp_extra_flow_frac=hp_extra,
        )

    spec = storage_spec(candidate, register)
    # The C6 ledger is seasonal: it always runs over the whole calendar, even for a day subset.
    base_indices = calendar.simulated_day_indices if spec is not None else indices
    fast_days = {i: dispatch(i) for i in base_indices}
    storage = None
    if candidate.concept == "C6" and spec is None:
        warnings.append(
            "C6 without equipment_sizes storage_capacity_mwh and storage_power_mw: parent plant "
            "only, no storage dispatched (DSP-STORAGE)"
        )
    if spec is not None:
        boiler = {i: f.day.heat_by_source_mw.get("peak_boiler", 0.0) for i, f in fast_days.items()}
        # Tolerance, not 0: rounding leaves ~1e-15 MW of "boiler heat" on met days.
        charge = {
            i: spare_geothermal_mw(
                plant,
                year.days[i],
                calendar.available(i),
                spec.power_mw,
                drive_flow_m3s=fast_days[i].plan.absorption_drive_m3h / SECONDS_PER_HOUR,
            )
            for i in base_indices
            if boiler[i] <= HEAT_TOL_MW and fast_days[i].day.unmet_heating_mwh <= 0.0
        }
        discharge = {i: b for i, b in boiler.items() if b > HEAT_TOL_MW}
        storage = storage_ledger(spec, base_indices, charge, discharge)
        wanted = set(indices)
        for i, net in storage.net_mw_by_day.items():
            if i not in wanted:
                continue
            if net < 0:
                fast_days[i] = dispatch(i, -net)
            else:
                fast_days[i] = apply_storage_discharge(fast_days[i], net)
        warnings.append(
            "C6 storage: HT-ATES outlet temperature not modelled (discharge assumed at the supply "
            "temperature; a booster may be needed); the verifier fails PH-20 on the negative "
            "'storage' heat of charge days and PH-22 on the discharge (no brine/fuel input) "
            "until WP-09/WP-14 teach it about storage (DSP-STORAGE)"
        )
        if storage.charged_total_mwh <= 0.0 and storage.discharged_total_mwh <= 0.0:
            warnings.append(
                "C6 storage idle: no boiler heat to displace or no spare geothermal capacity "
                "(DSP-STORAGE discharges only against boiler heat)"
            )
    if plant.has_hp and not plant.hp_reference_w:
        warnings.append(
            "DSP-HP-MIN inactive: no hthp_heating_mw and no HTHP heat at the design point "
            "(reference 0), so HTHP trickles are not switched off; give a nameplate"
        )
    plans = [fast_days[i].plan for i in indices]
    fast_results = [fast_days[i].day for i in indices]
    for i in indices:
        warnings += fast_days[i].warnings + [
            f"{fast_days[i].plan.date}: {n}" for n in fast_days[i].plan.notes
        ]

    full_results: list[DayResult] = []
    invalid: list[str] = []
    evidence: FullModeEvidence | None = None
    comparison: ModeComparison | None = None
    full_indices = indices if request.mode == "full" else sorted(request.compare_day_indices or [])
    if full_indices:
        missing = sorted(set(full_indices) - set(indices))
        if missing:
            raise ValueError(f"comparison days {missing[:5]} are not simulated")
        full_results, invalid_by_day, evidence, full_warnings = _run_full(
            plant, year, calendar, {i: fast_days[i] for i in full_indices}
        )
        invalid = list(invalid_by_day.values())
        warnings += full_warnings
        fast_subset = [fast_days[i].day for i in full_indices]
        comparison = compare_days(full_results, fast_subset, invalid_day_indices=invalid_by_day)
        if comparison.status != "pass":
            warnings.append(
                "SIM-04 fast vs full: max daily discrepancy "
                f"{100 * comparison.max_discrepancy_frac:.2f} % ({comparison.status})"
            )

    def build(results: list[DayResult]) -> AnnualResult:
        return AnnualResult(
            candidate_id=candidate.id,
            year=year.year,
            interpretation=calendar.rule.interpretation,
            days=results,
        )

    fast_annual = build(fast_results)
    annual = build(full_results) if request.mode == "full" else fast_annual
    services = service_map(annual.days)
    co2 = co2_emissions(
        Co2Request(gas_handling=candidate.gas_handling, service_by_key=services),
        annual,
        config_dir=config_dir,
    )
    warnings += co2.warnings
    whole_year = [d.day_index for d in annual.days] == calendar.simulated_day_indices
    end_of_life = end_of_life_checks(annual, candidate.depth_m, plant.sub) if whole_year else []
    for check in end_of_life:
        if not check.breakthrough_after_horizon:
            warnings.append(
                f"SIM-06: doublet {check.doublet} breaks through after "
                f"{check.breakthrough_time_yr:.1f} yr < {check.horizon_yr:g} yr (TL-07)"
            )
    kpis = annual_kpis(
        annual,
        plant,
        co2_t_per_yr=co2.data.total_t,
        breakthrough_yr=min((c.breakthrough_time_yr for c in end_of_life), default=None),
        storage_mw=storage.spec.power_mw if storage is not None else 0.0,
    )
    annual = annual.model_copy(update={"kpis": kpis})
    head = []
    if invalid:
        head.append(
            f"{len(invalid)} full-mode day(s) GEMINI could not run: fast results kept and "
            f"flagged, comparison failed (first: {invalid[0]})"
        )
    if storage is not None and abs(storage.cyclic_mismatch_mwh) > STORAGE_BALANCE_TOL_FRAC * (
        storage.spec.capacity_mwh
    ):
        head.append(
            f"C6 storage not in annual balance: end − start {storage.cyclic_mismatch_mwh:.0f} MWh "
            f"(> {100 * STORAGE_BALANCE_TOL_FRAC:g} % of capacity, RC-09 seasonal ATES)"
        )
    if request.mode == "full":
        fast_kpis = annual_kpis(fast_annual, plant, co2_t_per_yr=None)
        fast_annual = fast_annual.model_copy(update={"kpis": fast_kpis})
    return AnnualSimulation(
        candidate_id=candidate.id,
        mode=request.mode,
        config_hash=config_hash(
            cache_config(request, calendar, config_dir), namespace=cache_namespace(request.mode)
        ),
        calendar=calendar,
        annual=annual,
        fast_annual=fast_annual if request.mode == "full" else None,
        plans=plans,
        service_by_key=services,
        storage=storage,
        comparison=comparison,
        invalid_full_days=invalid,
        evidence=evidence,
        end_of_life=end_of_life,
        elapsed_s=time.perf_counter() - start,
        warnings=head + _dedupe(warnings),
    )


def cache_namespace(mode: str) -> str:
    """Return the DesignCache namespace of TL-12 results (SIM-07)."""
    return f"simulate_annual:{mode}:v{CACHE_VERSION}"


def _run_full(
    plant: FastPlant,
    year: YearData,
    calendar: AvailabilityCalendar,
    fast_days: dict[int, FastDay],
) -> tuple[list[DayResult], dict[int, str], FullModeEvidence | None, list[str]]:
    """GEMINI every day with the fast plan (TL-11 controls on).

    A day GEMINI cannot run keeps the fast ``DayResult`` (flagged) so the year stays complete;
    it is returned in the invalid map, fails the comparison and is warned about (never silent).
    """
    results: list[DayResult] = []
    invalid: dict[int, str] = {}
    warnings: list[str] = []
    evidence: FullModeEvidence | None = None
    model_warnings: dict[str, list[Any]] = {}  # digits-free pattern → [first text, days]
    for index, fast in sorted(fast_days.items()):
        plan = fast.plan
        day = year.days[index]
        if plan.storage_net_mw < 0:  # charging: the plant serves demand + charge
            day = day.model_copy(update={"heating_demand_mw": plan.plant_heating_mw})
        sim = simulate_plant_day(
            plant.candidate,
            day,
            plan.plant_dispatch(),
            design=plant.design,
            parent_concept=plant.parent_concept,
            gb=plant.gb,
            sub=plant.sub,
        )
        if not sim.valid or sim.day is None:
            invalid[index] = f"{plan.date}: {'; '.join(sim.failures)[:200]}"
            warnings.append(f"full mode invalid on {invalid[index]} (fast result kept, flagged)")
            results.append(
                fast.day.model_copy(
                    update={"flags": [*fast.day.flags, "full mode invalid: fast result"]}
                )
            )
            continue
        result = sim.day.model_copy(update={"doublet_available": list(plan.available)})
        if plan.storage_net_mw > 0:
            applied = min(plan.storage_net_mw, _boiler(result))
            if applied < plan.storage_net_mw - 1e-6:
                warnings.append(
                    f"{plan.date}: full-mode boiler heat {_boiler(result):.3f} MW below the "
                    f"planned storage discharge {plan.storage_net_mw:.3f} MW: discharge cut to "
                    "the boiler heat (ledger keeps the fast-mode value)"
                )
            result = discharge_into_day(result, applied)
        elif plan.storage_net_mw < 0:
            demand = year.days[index].heating_demand_mw
            heat = {**result.heat_by_source_mw, "storage": plan.storage_net_mw}
            gap = demand - sum(heat.values())
            result = result.model_copy(
                update={
                    "heating_demand_mw": demand,
                    "heat_by_source_mw": heat,
                    "unmet_heating_mwh": max(gap, 0.0) * HOURS_PER_DAY if gap > 1e-6 else 0.0,
                }
            )
        results.append(DayResult.model_validate(result.model_dump()))
        warnings += [f"{plan.date}: {c}" for c in sim.controls] + sim.warnings
        if evidence is None:
            evidence = FullModeEvidence(
                project=sim.config.project,
                hex_points=sim.config.hex_points(),
                co2_release_pressure_barg=sim.config.release_pressure_barg,
            )
        evidence.pumps += sim.pumps
        evidence.esps += sim.esps
        evidence.heat_pumps += sim.heat_pumps
        evidence.brine_contact_materials.update(sim.brine_contact_materials)
        for text in sim.model_warnings:
            model_warnings.setdefault(re.sub(r"[-+]?\d[\d.]*", "#", text), [text, 0])[1] += 1
    if evidence is not None:
        evidence.model_warnings = [f"{t} [×{n}]" for t, n in model_warnings.values()]
    return results, invalid, evidence, warnings


def _boiler(day: DayResult) -> float:
    return day.heat_by_source_mw.get("peak_boiler", 0.0)


def verification_evidence(
    sim: AnnualSimulation, candidate: Candidate, **extra: Any
) -> VerificationEvidence:
    """WP-09 ``VerificationEvidence`` from a TL-12 result (full mode: both modes for PD-06).

    ``extra`` passes the other evidence (subsurface, scaling, absorption points, ESP points with
    p_b, …) that WP-14 collects from TL-05/07/08/22. Raises ``ValueError`` when full-mode days
    were invalid (their full result would be the fast one).
    """
    if sim.candidate_id != candidate.id:
        raise ValueError(f"simulation of '{sim.candidate_id}' given for '{candidate.id}'")
    if sim.invalid_full_days:
        raise ValueError(
            f"{len(sim.invalid_full_days)} full-mode day(s) could not be run in GEMINI "
            f"(first: {sim.invalid_full_days[0]}): no full-mode evidence for them"
        )
    ev = sim.evidence
    full = sim.mode == "full"
    data: dict[str, Any] = {
        "candidate": candidate,
        "project": ev.project if ev is not None else None,
        "annual": sim.annual if full else None,
        "fast_annual": sim.fast_annual if full else sim.annual,
        "kpis": sim.annual.kpis,
        "stub_evidence": False,
    }
    if ev is not None:
        data |= {
            "hex_points": ev.hex_points,
            "heat_pumps": ev.heat_pumps,
            "pumps": ev.pumps,
            "esps": ev.esps,
            "brine_contact_materials": ev.brine_contact_materials,
            "co2_release_pressure_barg": ev.co2_release_pressure_barg,
            "model_warnings": ev.model_warnings,
        }
    owned = sorted(set(extra) & {"candidate", "annual", "fast_annual", "kpis", "stub_evidence"})
    if owned:
        raise ValueError(f"evidence keys {owned} come from the TL-12 result, not from extra")
    data |= extra
    return VerificationEvidence(**data)


# --------------------------------------------------------------------------- files
def _day_row(day: DayResult, plan: DayPlan | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day.date.isoformat(),
        "day_index": day.day_index,
        "ambient_dry_bulb_degc": day.ambient_dry_bulb_degc,
        "ambient_wet_bulb_degc": day.ambient_wet_bulb_degc,
        "heating_demand_mw": day.heating_demand_mw,
        "cooling_demand_mw": day.cooling_demand_mw,
        "heat_supplied_mw": day.heat_supplied_mw,
        "cool_supplied_mw": day.cool_supplied_mw,
        "unmet_heating_mwh": day.unmet_heating_mwh,
        "unmet_cooling_mwh": day.unmet_cooling_mwh,
        "heating_supply_degc": day.heating_supply_degc,
        "cooling_supply_degc": day.cooling_supply_degc,
        "electricity_consumed_mw": day.electricity_consumed_mw,
        "electricity_generated_mw": day.electricity_generated_mw,
        "associated_gas_produced_nm3": day.associated_gas_produced_nm3,
        "co2_direct_kg": day.co2_kg,
    }
    for i, ok in enumerate(day.doublet_available):
        n = i + 1
        row[f"doublet_{n}_available"] = ok
        row[f"doublet_{n}_flow_m3h"] = day.doublet_flow_m3h[i]
        row[f"doublet_{n}_production_degc"] = day.production_temperature_degc[i]
        row[f"doublet_{n}_injection_degc"] = day.injection_temperature_degc[i]
        row[f"doublet_{n}_brine_cooling_k"] = (
            day.production_temperature_degc[i] - day.injection_temperature_degc[i]
        )
    for prefix, values in (
        ("heat", day.heat_by_source_mw),
        ("cool", day.cool_by_source_mw),
        ("elec", day.electricity_by_consumer_mw),
        ("brine", day.brine_heat_drawn_mw),
        ("fuel", day.gas_fuel_by_consumer_mw),
    ):
        for key, mw in values.items():
            row[f"{prefix}_{key}_mw"] = mw
    for key, kg in day.co2_by_source_kg.items():
        row[f"co2_{key}_kg"] = kg
    if plan is not None:
        row["heat_pumps_on"] = plan.heat_pumps_on
        row["absorption_drive_m3h"] = plan.absorption_drive_m3h
        row["storage_net_mw"] = plan.storage_net_mw
        row["dispatch_notes"] = " | ".join(plan.notes)
    for key, value in day.margins.items():
        row[f"margin_{key}"] = value
    row["flags"] = " | ".join(day.flags)
    return row


def write_outputs(sim: AnnualSimulation, run_dir: Path) -> dict[str, str]:
    """CSV per mode (one row per day) and a JSON summary in ``run_dir``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    plans = {p.day_index: p for p in sim.plans}
    written: dict[str, str] = {}
    sets = [(sim.mode, sim.annual)]
    if sim.fast_annual is not None:
        sets.append(("fast", sim.fast_annual))
    for mode, annual in sets:
        frame = pd.DataFrame([_day_row(d, plans.get(d.day_index)) for d in annual.days])
        path = run_dir / f"annual_{sim.candidate_id}_{mode}.csv"
        frame.to_csv(path, index=False)
        written[f"csv_{mode}"] = str(path)
    summary = {
        "tool": TOOL_ID,
        "candidate_id": sim.candidate_id,
        "mode": sim.mode,
        "config_hash": sim.config_hash,
        "year": sim.annual.year,
        "interpretation": sim.annual.interpretation,
        "simulated_days": len(sim.annual.days),
        "outage_windows": [
            [a.isoformat(), b.isoformat()] for a, b in sim.calendar.outage_windows()
        ],
        "outage_placement": sim.calendar.placement,
        "db30": sim.calendar.rule.model_dump(mode="json"),
        "kpis": sim.annual.kpis.model_dump(mode="json") if sim.annual.kpis else None,
        "comparison": (
            sim.comparison.model_dump(mode="json", exclude={"days"}) if sim.comparison else None
        ),
        "storage": (
            sim.storage.model_dump(mode="json", exclude={"net_mw_by_day"}) if sim.storage else None
        ),
        "invalid_full_days": sim.invalid_full_days,
        "end_of_life": [c.model_dump(mode="json") for c in sim.end_of_life],
        "service_by_key": sim.service_by_key,
        "elapsed_s": sim.elapsed_s,
        "warnings": sim.warnings,
        "assumption_ids": sorted(set(ASSUMPTION_IDS)),
        "files": {k: Path(v).name for k, v in written.items()},
    }
    path = run_dir / f"annual_{sim.candidate_id}_{sim.mode}_summary.json"
    path.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    written["summary"] = str(path)
    return written


ASSUMPTION_IDS: tuple[str, ...] = tuple(
    sorted(
        set(DISPATCH_ASSUMPTION_IDS)
        | set(GENERATOR_ASSUMPTION_IDS)
        | {"DB-37", "DB-34a", "SUB-SPACING", "SUB-RHOC"}
    )
)


# --------------------------------------------------------------------------- tool
def _summary(sim: AnnualSimulation) -> str:
    k = sim.annual.kpis
    assert k is not None
    heat = sum(k.heat_by_source_mwh.values()) / 1000.0
    boiler = k.heat_by_source_mwh.get("peak_boiler", 0.0) / 1000.0
    text = (
        f"{TOOL_ID} {sim.candidate_id} {sim.mode} DB-30 {sim.annual.interpretation} "
        f"{len(sim.annual.days)} d: heat {heat:.1f} GWh (boiler {boiler:.2f}), unmet "
        f"{k.unmet_heating_days}/{k.unmet_cooling_days} d, elec "
        f"{k.electricity_consumed_mwh / 1e3:.2f} GWh, CO2 {k.co2_t_per_yr or 0:.0f} t"
    )
    if sim.comparison is not None:
        text += f", fast/full {100 * sim.comparison.max_discrepancy_frac:.2f} %"
    if sim.invalid_full_days:
        text = f"{text}, INVALID full days {len(sim.invalid_full_days)}"
    return text[:300]


def simulate_annual(
    request: SimulateAnnualRequest,
    *,
    config_dir: Path | str | None = None,
    runs_root: Path | str | None = None,
    map_cache_dir: Path | str | None = None,
    cache: DesignCache | None = None,
) -> ToolResult[AnnualSimulation]:
    """TL-12: dispatch the year in ``request.mode`` and write ``runs/<run_id>/`` (module doc).

    With ``cache`` the KPIs are stored under ``config_hash`` (SIM-07); a caller that only needs
    KPIs reads them back with ``cache.get(key)`` before calling (the full daily results are not
    cached).
    """
    sim = run_annual(request, config_dir=config_dir, map_cache_dir=map_cache_dir)
    if request.write_outputs:
        run_id = request.run_id or f"annual-{sim.candidate_id}-{sim.mode}-{sim.config_hash[:8]}"
        root = Path(runs_root) if runs_root is not None else DEFAULT_RUNS_ROOT
        sim = sim.model_copy(update={"outputs": write_outputs(sim, root / run_id)})
    summary = _summary(sim)
    if cache is not None:
        cache.put(
            cache_config(request, sim.calendar, config_dir),
            namespace=cache_namespace(sim.mode),
            kpis=sim.annual.kpis,
            summary=summary,
            candidate_id=sim.candidate_id,
            run_id=request.run_id,
        )
    return ToolResult[AnnualSimulation](
        data=sim,
        summary=summary,
        warnings=sim.warnings,
        assumption_ids=list(ASSUMPTION_IDS),
        sources=list(SOURCES),
    )


__all__ = [
    "ASSUMPTION_IDS",
    "TOOL_ID",
    "AnnualSimulation",
    "DayModeComparison",
    "EndOfLifeCheck",
    "FullModeEvidence",
    "ModeComparison",
    "SimulateAnnualRequest",
    "YearData",
    "annual_kpis",
    "cache_config",
    "cache_namespace",
    "compare_days",
    "end_of_life_checks",
    "run_annual",
    "service_map",
    "simulate_annual",
    "verification_evidence",
    "write_outputs",
    "year_conditions",
]
