"""WP-07 dispatch: DB-30 availability calendar, fast plant model and daily merit order (spec §8).

Three parts, all deterministic:

* **Availability calendar (SIM-02, DB-30).** Interpretation A: each doublet has one planned
  outage of ``planned_outage_days_per_doublet`` (35) consecutive days, never overlapping another
  doublet's; demand must be met on all 365 days. The outage starts are an optimiser-ready
  parameter (``outage_start_indices``, WP-11); by default they are placed greedily in the
  lowest-load non-overlapping windows of the geothermal-side load (SIM-OUTAGE). Interpretation B
  (``answers['DB-30']`` = "B", as the runner stores it): no outage, only
  ``demand_met_days_per_yr`` (330) days are simulated — the year minus its lowest-load window
  (SIM-DB30B).
* **Fast plant model (SIM-04 fast mode).** Wells from the TL-06 doublet performance map
  (``doublet_performance_map(...).data.interpolate``: wellhead temperature, ESP and
  injection-pump electric power, ESP intake margin; the map's injection pump is evaluated at
  TL-05's T_wh − DB-12 basis, which with GEMINI's constant brine PVT does not change the pump
  power — the reported injection temperature is the dispatched one); the surface plant from the
  *same* GEMINI
  component models and parameters TL-09 writes (``plan_plant`` at the design point), evaluated
  once each in the CFG-LAYOUT order — no project build and no TL-05 solve per day. The controls
  mirror TL-11: plate-HEX bypass share for DB-12/DB-16, heat-pump share for the
  ``hthp_heating_mw`` nameplate, peak boiler to 100 °C up to its nameplate.
* **Daily dispatch (SIM-03).** Cooling: free cooling first (when the day's wet bulb allows,
  RC-07), then absorption (if present and the residual is at least DSP-ABS-MIN of its
  nameplate; drive brine from the first active doublet, at least its COOL-ABS-DRV minimum flow),
  then the electric chiller. Heat: the district water passes plate HEX → CHP (all associated gas,
  DB-36) → HTHP (to 100 °C, up to its nameplate) → peak boiler, so the merit order is the
  topology; the dispatch decides the doublet flows (DSP-FLOW): the smallest equal flow on the
  available doublets at which HEX + CHP + HTHP meet the plant load without the boiler, a doublet
  being switched off when the equal share would fall below the DB-11 minimum; the flow never
  exceeds the candidate design flow. HTHPs below DSP-HP-MIN of their reference heat are switched
  off when HEX + CHP alone can carry the load. The boiler covers the rest; what it cannot is
  unmet demand (recorded, never hidden).

C6 seasonal storage (DSP-STORAGE) is a ledger on WP-03's ``ThermalStorage``: charged with spare
geothermal capacity on days without boiler heat (the plant serves ``demand + charge``) and
discharged ahead of the peak boiler (:func:`apply_storage_discharge`: boiler heat, fuel and CO₂
fall one for one). ``heat_by_source_mw['storage']`` holds the net heat (negative while
charging), as ``ThermalStorage.net_heat_to_plant_w`` prescribes; the WP-09 verifier fails PH-20
on that negative value and PH-22 on the discharge (no brine/fuel/electricity key) until it
learns about storage (WP-09/WP-14).

Units: flows m³/h at the doublet boundary, m³/s inside GEMINI; powers W inside, MW (day
average) in ``DayResult``; temperatures °C.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import Field, JsonValue, model_validator

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import HOURS_PER_DAY, StrictModel
from gemini_agent.schemas.config import AssumptionsRegister, load_config
from gemini_agent.schemas.results import DayResult
from gemini_agent.tools.config_generator import (
    DISTRICT_FLUID,
    GeneratedConfig,
    GeneratorBasis,
    PlantDispatch,
    PlantSpec,
    design_loads,
    district_flow_m3s,
    load_generator_basis,
    plan_plant,
    tower_water_degc,
)
from gemini_agent.tools.design_basis import SECONDS_PER_HOUR
from gemini_agent.tools.doublet import (
    FLOW_TOLERANCE_FRAC,
    DoubletDesign,
    DoubletPerformanceMap,
    PerformanceMapRequest,
    doublet_performance_map,
    resolve_doublet,
)
from gemini_agent.tools.resource import SubsurfaceBasis, load_subsurface_basis
from gemini_agent.tools.simulate_day import (
    CONTROL_TOL_K,
    HEAT_TOL_MW,
    HP_CAPACITY_TOL_MW,
    SHARE_BISECTION_STEPS,
    DayConditions,
    _flag_text,
    _heat_pump_margins,
    _heat_pump_screening,
)
from gemini_model.mixer.mixer import Mixer
from gemini_model.thermal_storage.thermal_storage import ThermalStorage
from gemini_simulator.registry import create_component

DB30_ID = "DB-30"
DISPATCH_ASSUMPTION_IDS: tuple[str, ...] = (
    "DB-30",
    "SIM-OUTAGE",
    "SIM-DB30B",
    "SIM-FAST",
    "DSP-FLOW",
    "DSP-HP-MIN",
    "DSP-ABS-MIN",
    "DSP-STORAGE",
)
SECONDS_PER_DAY = HOURS_PER_DAY * SECONDS_PER_HOUR
FLOW_BISECTION_STEPS = 12
"""Doublet-flow resolution: 250 m³/h / 4096 ≈ 0.06 m³/h."""
STORAGE_BISECTION_STEPS = 8
"""Spare-capacity resolution for C6 charging: storage power / 256."""
DISPATCH_TOL_MW = 1e-6
"""Geothermal shortfall (MW) below which the plant load counts as met without the boiler."""
STORAGE_CYCLES = 2
"""Ledger passes: the second starts from the first's end state (cyclic seasonal balance)."""


# =========================================================================== DB-30 calendar
class Db30Rule(StrictModel):
    """DB-30 as applied: from the register or from the user's answer (``answers['DB-30']``)."""

    interpretation: Literal["A", "B"]
    planned_outage_days_per_doublet: int = Field(ge=0)
    outages_may_overlap: bool = False
    demand_met_days_per_yr: int = Field(ge=1, le=366)
    source: Literal["register", "answer"] = "register"

    @model_validator(mode="after")
    def _check(self) -> Db30Rule:
        if self.interpretation == "B" and self.planned_outage_days_per_doublet:
            raise ValueError("DB-30 interpretation B has no planned outage")
        return self


def _db30_b(default: dict[str, Any]) -> dict[str, Any]:
    """Interpretation B from the register's A entry (same derivation as the WP-10 runner)."""
    return {
        **default,
        "interpretation": "B",
        "planned_outage_days_per_doublet": 0,
        "demand_met_days_per_yr": default["doublet_available_days_per_yr"],
    }


def db30_rule(
    answer: JsonValue | None = None,
    *,
    register: AssumptionsRegister | None = None,
    config_dir: Path | str | None = None,
) -> Db30Rule:
    """DB-30 rule from ``answers['DB-30']`` (the runner's dict, or "A"/"B"), else the register."""
    register = register or load_config("assumptions", config_dir)
    default = register.get(DB30_ID).value
    if not isinstance(default, dict):
        raise TypeError("DB-30 register value must be a mapping")
    source: Literal["register", "answer"] = "register"
    data: dict[str, Any] = dict(default)
    if isinstance(answer, str):
        choice = answer.strip().upper()
        if choice not in {"A", "B"}:
            raise ValueError(f"DB-30 answer must be A or B, got {answer!r}")
        data = _db30_b(default) if choice == "B" else dict(default)
        source = "answer"
    elif isinstance(answer, dict):
        data = {**default, **answer}
        if data.get("interpretation") == "B" and "planned_outage_days_per_doublet" not in answer:
            data = _db30_b(data)
        source = "answer"
    elif answer is not None:
        raise ValueError(f"DB-30 answer must be a mapping or 'A'/'B', got {type(answer).__name__}")
    return Db30Rule(
        interpretation=data["interpretation"],
        planned_outage_days_per_doublet=int(data["planned_outage_days_per_doublet"]),
        outages_may_overlap=bool(data.get("outages_may_overlap", False)),
        demand_met_days_per_yr=int(data["demand_met_days_per_yr"]),
        source=source,
    )


class AvailabilityCalendar(StrictModel):
    """Which doublets are available on which day, and which days are simulated (SIM-02)."""

    year: int
    n_days: int = Field(ge=1, le=366)
    doublets: int = Field(ge=0)
    rule: Db30Rule
    outage_days_per_doublet: int = Field(ge=0)
    outage_start_indices: list[int] = Field(
        default_factory=list, description="Per doublet (0-based day of the outage's first day)."
    )
    placement: Literal["lowest_load", "given", "none"]
    excluded_start_index: int | None = Field(
        default=None, description="Interpretation B: first day of the window not simulated."
    )
    excluded_days: int = Field(default=0, ge=0)
    simulated_day_indices: list[int]

    @model_validator(mode="after")
    def _check(self) -> AvailabilityCalendar:
        starts = self.outage_start_indices
        length = self.outage_days_per_doublet
        if starts and len(starts) != self.doublets:
            raise ValueError(f"{len(starts)} outage starts for {self.doublets} doublet(s)")
        for s in starts:
            if not 0 <= s <= self.n_days - length:
                raise ValueError(f"outage start {s} + {length} days leaves the year")
        if not self.rule.outages_may_overlap:
            ordered = sorted(starts)
            for a, b in itertools.pairwise(ordered):
                if b < a + length:
                    raise ValueError(f"outages starting on days {a} and {b} overlap (DB-30)")
        days = self.simulated_day_indices
        if days != sorted(set(days)) or (days and not 0 <= days[0] <= days[-1] < self.n_days):
            raise ValueError("simulated_day_indices must be unique, increasing and in the year")
        return self

    def available(self, day_index: int) -> list[bool]:
        """Per-doublet availability on ``day_index`` (False during its planned outage)."""
        if not self.outage_start_indices:
            return [True] * self.doublets
        length = self.outage_days_per_doublet
        return [not s <= day_index < s + length for s in self.outage_start_indices]

    def outage_windows(self) -> list[tuple[dt.date, dt.date]]:
        """Inclusive (first, last) outage dates per doublet."""
        jan_1 = dt.date(self.year, 1, 1)
        length = self.outage_days_per_doublet
        return [
            (jan_1 + dt.timedelta(days=s), jan_1 + dt.timedelta(days=s + length - 1))
            for s in self.outage_start_indices
        ]


def lowest_load_starts(load_mw: Sequence[float], length: int, count: int) -> list[int]:
    """Greedy non-overlapping windows of ``length`` days with the lowest total load (SIM-OUTAGE).

    The first window is the year's lowest (earliest on ties, no wrap — as TL-02); each next one is
    the lowest that overlaps none chosen so far. Raises ``ValueError`` if they cannot all fit.
    """
    values = np.asarray(load_mw, dtype=float)
    if count <= 0 or length <= 0:
        return []
    if count * length > len(values):
        raise ValueError(f"{count} windows of {length} days do not fit in {len(values)} days")
    sums = np.convolve(values, np.ones(length), mode="valid")
    chosen: list[int] = []
    for _ in range(count):
        allowed = np.ones(len(sums), dtype=bool)
        for c in chosen:
            allowed[max(0, c - length + 1) : c + length] = False
        if not allowed.any():
            raise ValueError("no room for another non-overlapping outage window")
        masked = np.where(allowed, sums, np.inf)
        chosen.append(int(np.argmin(masked)))
    return chosen


def availability_calendar(
    doublets: int,
    year: int,
    placement_load_mw: Sequence[float],
    *,
    rule: Db30Rule,
    outage_start_indices: Sequence[int] | None = None,
    exclusion_load_mw: Sequence[float] | None = None,
) -> AvailabilityCalendar:
    """Build the DB-30 calendar (module doc); ``outage_start_indices`` is the optimiser's lever.

    Interpretation B drops the lowest window of ``exclusion_load_mw`` (default: the placement
    load; TL-12 passes heating + cooling demand so B keeps the cooling design days, SIM-DB30B).
    """
    n_days = len(placement_load_mw)
    expected = (dt.date(year, 12, 31) - dt.date(year, 1, 1)).days + 1
    if n_days != expected:
        raise ValueError(f"placement load has {n_days} days, year {year} has {expected}")
    length = rule.planned_outage_days_per_doublet if rule.interpretation == "A" else 0
    starts: list[int] = []
    placement: Literal["lowest_load", "given", "none"] = "none"
    if doublets and length:
        if outage_start_indices is not None:
            starts = [int(s) for s in outage_start_indices]
            placement = "given"
        else:
            starts = lowest_load_starts(placement_load_mw, length, doublets)
            placement = "lowest_load"
    elif outage_start_indices:
        raise ValueError("outage starts given but DB-30 has no planned outage here")
    excluded = max(0, n_days - rule.demand_met_days_per_yr) if rule.interpretation == "B" else 0
    exclusion = placement_load_mw if exclusion_load_mw is None else exclusion_load_mw
    if len(exclusion) != n_days:
        raise ValueError(f"exclusion load has {len(exclusion)} days, expected {n_days}")
    excluded_start = lowest_load_starts(exclusion, excluded, 1)[0] if excluded else None
    simulated = [
        d
        for d in range(n_days)
        if excluded_start is None or not excluded_start <= d < excluded_start + excluded
    ]
    return AvailabilityCalendar(
        year=year,
        n_days=n_days,
        doublets=doublets,
        rule=rule,
        outage_days_per_doublet=length,
        outage_start_indices=starts,
        placement=placement,
        excluded_start_index=excluded_start,
        excluded_days=excluded,
        simulated_day_indices=simulated,
    )


# =========================================================================== fast plant model
@dataclass(frozen=True)
class DoubletState:
    """One active doublet at a flow, from the TL-06 map (RC-01 rule powers)."""

    flow_m3h: float
    wellhead_temperature_degc: float
    esp_electric_w: float
    injection_pump_electric_w: float
    intake_margin_bar: float | None


@dataclass
class FastPoint:
    """Outputs of one fast evaluation (powers in W; see :meth:`FastPlant.evaluate`)."""

    flows_m3h: list[float]
    active: list[int]
    wells: dict[int, DoubletState]
    district_flow_m3s: float
    hex_share: float | None
    hp_share: float | None
    hp_on: bool
    drive_frac: float | None
    boiler_gas_kg_s: float = 0.0
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    brine_out_degc: dict[int, float] = field(default_factory=dict)
    preheat_degc: float | None = None
    supply_degc: float | None = None
    associated_gas_nm3_s: float = 0.0
    chp_gas_kg_s: float = 0.0
    hp_params: dict[int, dict[str, Any]] = field(default_factory=dict)
    controls: list[str] = field(default_factory=list)

    def heat_w(self, name: str) -> float:
        """Return the useful heat of a district heat source (W), 0 if absent."""
        out = self.outputs.get(name)
        if out is None:
            return 0.0
        key = "heat_duty" if name.startswith("hex_") else "power_th"
        return float(out.get(key, 0.0))

    def hp_heat_w(self) -> float:
        """Total heat-pump heat (W)."""
        return sum(self.heat_w(f"hthp_{i}") for i in self.active if f"hthp_{i}" in self.outputs)

    def geothermal_heat_w(self, hp_cap_w: float | None = None) -> float:
        """HEX + CHP + HTHP heat (W); the HTHP part capped at ``hp_cap_w`` when given."""
        hex_heat = sum(self.heat_w(f"hex_{i}") for i in self.active)
        hp = self.hp_heat_w()
        if hp_cap_w is not None:
            hp = min(hp, hp_cap_w)
        return hex_heat + self.heat_w("chp") + hp


def _mix(flows: Sequence[float], temperatures: Sequence[float]) -> tuple[float, float]:
    """GEMINI ``Mixer`` rule: flow-weighted temperature (mean when the total flow is ~0)."""
    total = float(sum(flows))
    if total < Mixer._MIN_TOTAL_FLOW:
        return total, float(sum(temperatures) / len(temperatures))
    return total, float(sum(f * t for f, t in zip(flows, temperatures, strict=True)) / total)


def _calc(model: Any, u: dict[str, Any]) -> dict[str, Any]:
    model.calculate_output(u)
    return dict(model.output)


class FastPlant:
    """Reduced model of one candidate: TL-06 map wells + TL-09 surface components (module doc).

    Built once per candidate. The component parameters are those :func:`plan_plant` writes at
    the design point (design flow on all doublets, DB-18/DB-19 peaks); per day only the boundary
    values change (flows, wellhead temperature, district flow, ambient, loads, shares, boiler
    gas) and the heat-pump brine floor ``Ts_in_minimum = max(T_wh − DB-12, 100 − VER-HP-LIFT)``
    at the day's wellhead temperature (CFG-HP).
    """

    def __init__(
        self,
        candidate: Candidate,
        *,
        design: DoubletDesign | None = None,
        parent_concept: str | None = None,
        gb: GeneratorBasis | None = None,
        sub: SubsurfaceBasis | None = None,
        config_dir: Path | str | None = None,
        map_cache_dir: Path | str | None = None,
        use_map_cache: bool = True,
    ) -> None:
        """Plan the design-point plant (TL-09) and load or build the TL-06 doublet map."""
        self.candidate = candidate
        self.design = design
        self.parent_concept = parent_concept
        self.gb = gb or load_generator_basis(config_dir)
        self.sub = sub or load_subsurface_basis(config_dir)
        self.config_dir = config_dir
        spec, meta = plan_plant(
            candidate,
            design_loads(self.gb),
            PlantDispatch(),
            design=design,
            parent_concept=parent_concept,
            gb=self.gb,
            sub=self.sub,
        )
        self.spec: PlantSpec = spec
        self.meta: GeneratedConfig = meta
        train = spec.doublets[0] if spec.doublets else None
        self.n_doublets = candidate.doublets
        self.has_hex = train is not None and train.hex is not None
        self.has_hp = train is not None and train.heat_pump is not None
        self.degassed = train is not None and train.separator is not None
        district = spec.district
        assert district is not None
        self.chp_params = district.chp
        self.boiler_params = district.boiler
        cooling = spec.cooling
        assert cooling is not None
        self.cooling_spec = cooling
        self.has_free_cooling = cooling.free_cooling is not None
        self.has_absorption = cooling.absorption is not None
        self.return_degc = meta.heating_return_degc
        self.boiler_capacity_w = meta.equipment_mw.get("peak_boiler_mw", 0.0) * 1e6
        nameplate = candidate.equipment_sizes.get("hthp_heating_mw")
        self.hp_nameplate_w = None if nameplate is None else float(nameplate) * 1e6
        self.absorption_nameplate_w = meta.equipment_mw.get("absorption_chiller_mw", 0.0) * 1e6
        self.performance_map: DoubletPerformanceMap | None = None
        self.flow_min_m3h = self.sub.flow_min_m3h
        self.flow_max_m3h = 0.0
        self.reservoir_temperature_degc = meta.reservoir_temperature_degc
        self.release_pressure_barg = meta.release_pressure_barg
        self._wells: dict[float, DoubletState] = {}
        if train is not None:
            self.performance_map = doublet_performance_map(
                PerformanceMapRequest(design=self.map_design()),
                config_dir=config_dir,
                cache_dir=map_cache_dir,
                use_cache=use_map_cache,
            ).data
            low = self.performance_map.feasible_flow_min_m3h
            high = self.performance_map.feasible_flow_max_m3h
            if low is None or high is None:
                raise ValueError(f"{candidate.id}: the doublet map has no feasible flow")
            self.flow_min_m3h = max(self.sub.flow_min_m3h, low)
            self.flow_max_m3h = min(float(candidate.design_flow_m3h or 0.0), high)
            if self.flow_max_m3h < self.flow_min_m3h:
                raise ValueError(
                    f"{candidate.id}: design flow {candidate.design_flow_m3h} m³/h below the "
                    f"minimum feasible flow {self.flow_min_m3h:g} m³/h"
                )
        self._models: dict[str, Any] = {}
        self.hp_reference_w = self.hp_nameplate_w
        if self.has_hp and self.hp_reference_w is None:
            self.hp_reference_w = self._design_hp_heat_w()

    # ------------------------------------------------------------------ setup helpers
    def map_design(self) -> DoubletDesign:
        """Doublet design as TL-09 resolves it (C7: producer WHP held at the SUB-PBUB setpoint)."""
        base = (self.design or DoubletDesign()).model_copy(
            update={"depth_m": self.candidate.depth_m}
        )
        if not self.degassed:
            setpoint = resolve_doublet(base, self.sub).bubble_point.intake_setpoint_barg
            given = base.producer_wellhead_pressure_barg
            if given is None or given < setpoint:
                base = base.model_copy(update={"producer_wellhead_pressure_barg": setpoint})
            # Pressurised loop: no letdown (no separator, lossless filters and HEX in TL-09), so
            # the brine reaches the injection pump at the producer WHP, not at SUB-PSUC — in
            # GEMINI's pressure chain whatever suction the design names.
            base = base.model_copy(
                update={"injection_pump_suction_barg": base.producer_wellhead_pressure_barg}
            )
        return base

    def _model(self, name: str, kind: str, params: dict[str, Any] | None) -> Any:
        model = self._models.get(name)
        if model is None:
            model = create_component(kind)
            if params:
                model.update_parameters(dict(params))
            self._models[name] = model
        return model

    def _design_hp_heat_w(self) -> float:
        """HTHP heat at the DB-18 peak with all doublets at the design flow (DSP-HP-MIN basis)."""
        loads = design_loads(self.gb)
        day = DayConditions(
            date=dt.date(2000, 1, 1),
            day_index=0,
            heating_demand_mw=loads.heating_demand_mw,
            cooling_demand_mw=0.0,
            ambient_dry_bulb_degc=loads.ambient_dry_bulb_degc,
            ambient_wet_bulb_degc=loads.ambient_wet_bulb_degc,
        )
        flows = [self.flow_max_m3h] * self.n_doublets
        return self.evaluate(day, flows, hp_on=True, drive_flow_m3s=0.0).hp_heat_w()

    def well(self, flow_m3h: float) -> DoubletState:
        """Doublet state at ``flow_m3h`` from the TL-06 map (cached per flow)."""
        key = round(flow_m3h, 9)
        state = self._wells.get(key)
        if state is None:
            assert self.performance_map is not None
            m = self.performance_map
            try:
                margin: float | None = m.interpolate("intake_margin_bar", flow_m3h)
            except ValueError:
                margin = None
            state = DoubletState(
                flow_m3h=flow_m3h,
                wellhead_temperature_degc=m.interpolate("wellhead_temperature_degc", flow_m3h),
                esp_electric_w=m.interpolate("esp_electric_power_w", flow_m3h),
                injection_pump_electric_w=m.interpolate(
                    "injection_pump_electric_power_w", flow_m3h
                ),
                intake_margin_bar=margin,
            )
            self._wells[key] = state
        return state

    def absorption_design_cop(self) -> float | None:
        """GM-04 COP at the design drive temperature and DB-34b ambient (outage placement)."""
        if not self.has_absorption or self.performance_map is None:
            return None
        loads = design_loads(self.gb)
        absorber = self._model(
            "absorption_design", "absorption_chiller", self.cooling_spec.absorption
        )
        out = _calc(
            absorber,
            {
                "drive_temperature_in_degc": self.well(self.flow_max_m3h).wellhead_temperature_degc,
                "cooling_water_temperature_in_degc": tower_water_degc(
                    loads.ambient_dry_bulb_degc,
                    loads.ambient_wet_bulb_degc,
                    self.cooling_spec.heat_rejection,
                ),
                "cooling_load_w": self.absorption_nameplate_w,
            },
        )
        drive = float(out.get("drive_heat_w") or 0.0)
        return float(out["cooling_power_w"]) / drive if drive > 0 else None

    # ------------------------------------------------------------------ evaluation
    def evaluate(
        self,
        day: DayConditions,
        flows_m3h: Sequence[float],
        *,
        hp_on: bool = True,
        drive_flow_m3s: float = 0.0,
        hex_share: float = 1.0,
        hp_share: float = 1.0,
        boiler_gas_kg_s: float = 0.0,
        plant_heating_mw: float | None = None,
        cooling_ambient_tower_degc: float | None = None,
    ) -> FastPoint:
        """One pass through the CFG-LAYOUT chain with the given set-points (no controls)."""
        flows = [float(q) for q in flows_m3h]
        if len(flows) != self.n_doublets:
            raise ValueError(f"{len(flows)} flows for {self.n_doublets} doublet(s)")
        active = [i + 1 for i, q in enumerate(flows) if q > 0]
        heating_mw = day.heating_demand_mw if plant_heating_mw is None else plant_heating_mw
        district_flow = district_flow_m3s(
            max(heating_mw, 0.0), self.gb.heating_supply_degc, self.return_degc
        )
        hp_running = self.has_hp and hp_on and bool(active)
        point = FastPoint(
            flows_m3h=flows,
            active=active,
            wells={i: self.well(flows[i - 1]) for i in active},
            district_flow_m3s=district_flow,
            hex_share=hex_share if self.has_hex and active else None,
            hp_share=hp_share if hp_running else None,
            hp_on=hp_running,
            drive_frac=None,
            boiler_gas_kg_s=boiler_gas_kg_s,
        )
        out = point.outputs
        # ---- cooling chain: free cooling → absorption → electric chiller; one tower (RC-05/6/7)
        cooling = self.cooling_spec
        load: float = day.cooling_demand_mw * 1e6
        chilled_in: float | None = None
        rejected: list[float] = []
        if self.has_free_cooling:
            fc = self._model("free_cooling", "free_cooling", cooling.free_cooling)
            out["free_cooling"] = _calc(
                fc, {"ambient_wet_bulb_degc": day.ambient_wet_bulb_degc, "cooling_load_w": load}
            )
            load = float(out["free_cooling"]["residual_cooling_w"])
            chilled_in = float(out["free_cooling"]["chilled_water_temperature_out_degc"])
            rejected.append(float(out["free_cooling"]["heat_rejected_w"]))
        drive: tuple[float, float] | None = None
        if self.has_absorption and active:
            first = active[0]
            q_m3s = flows[first - 1] / SECONDS_PER_HOUR
            drive_m3s = min(drive_flow_m3s, q_m3s)
            point.drive_frac = drive_m3s / q_m3s if q_m3s > 0 else 0.0
            absorber = self._model("absorption_chiller", "absorption_chiller", cooling.absorption)
            tower_in = cooling_ambient_tower_degc
            if tower_in is None:
                tower_in = tower_water_degc(
                    day.ambient_dry_bulb_degc, day.ambient_wet_bulb_degc, cooling.heat_rejection
                )
            u: dict[str, Any] = {
                "drive_temperature_in_degc": point.wells[first].wellhead_temperature_degc,
                "drive_flow_rate_m3s": drive_m3s,
                "cooling_water_temperature_in_degc": tower_in,
                "cooling_load_w": load,
            }
            if chilled_in is not None:
                u["chilled_water_temperature_in_degc"] = chilled_in
            out["absorption_chiller"] = _calc(absorber, u)
            a = out["absorption_chiller"]
            load = float(a["unmet_cooling_w"])
            rejected.append(float(a["heat_rejected_w"]))
            drive = (float(a["drive_flow_rate_m3s"]), float(a["drive_temperature_out_degc"]))
        chiller = self._model("electric_chiller", "electric_chiller", cooling.electric_chiller)
        u = {
            "ambient_dry_bulb_degc": day.ambient_dry_bulb_degc,
            "ambient_wet_bulb_degc": day.ambient_wet_bulb_degc,
            "cooling_load_w": load,
        }
        if chilled_in is not None:
            u["chilled_water_temperature_in_degc"] = chilled_in
        out["electric_chiller"] = _calc(chiller, u)
        rejected.append(float(out["electric_chiller"]["heat_rejected_w"]))
        tower = self._model("heat_rejection", "heat_rejection", cooling.heat_rejection)
        out["heat_rejection"] = _calc(
            tower,
            {
                "ambient_dry_bulb_degc": day.ambient_dry_bulb_degc,
                "ambient_wet_bulb_degc": day.ambient_wet_bulb_degc,
                "heat_load_w": sum(rejected),
            },
        )

        # ---- brine trains up to the HEX outlet (separator, absorption split, plate HEX)
        train = self.spec.doublets[0] if self.spec.doublets else None
        total_q = sum(flows[i - 1] for i in active)
        fracs = {i: flows[i - 1] / total_q for i in active} if total_q > 0 else {}
        gas_kg_s = 0.0
        hex_flow = district_flow * hex_share if point.hex_share is not None else 0.0
        hex_branch: list[tuple[float, float]] = []
        brine: dict[int, tuple[float, float]] = {}
        for i in active:
            assert train is not None
            state = point.wells[i]
            q_m3s = flows[i - 1] / SECONDS_PER_HOUR
            t_wh = state.wellhead_temperature_degc
            if train.separator is not None:
                sep = self._model(f"separator_{i}", "separator", train.separator)
                out[f"separator_{i}"] = _calc(
                    sep,
                    {
                        "flow_rate": q_m3s,
                        "pressure": float(train.wellhead_pressure_barg),
                        "temperature": t_wh,
                    },
                )
                gas_kg_s += float(out[f"separator_{i}"]["gas_mass_flow_rate"])
                point.associated_gas_nm3_s += float(out[f"separator_{i}"]["gas_flow_rate"])
            hex_q = q_m3s
            if drive is not None and i == active[0]:
                hex_q = q_m3s - (point.drive_frac or 0.0) * q_m3s
            flow_t = (hex_q, t_wh)
            if self.has_hex:
                hx = self._model(f"hex_{i}", "heat_exchanger", train.hex)
                out[f"hex_{i}"] = _calc(
                    hx,
                    {
                        "pressure_in": float(train.wellhead_pressure_barg),
                        "primary_temperature_in": t_wh,
                        "primary_flow_rate": hex_q,
                        "secondary_flow_rate": hex_flow * fracs[i],
                        "secondary_temperature_in": self.return_degc,
                    },
                )
                h = out[f"hex_{i}"]
                hex_branch.append((hex_flow * fracs[i], float(h["secondary_temperature_out"])))
                flow_t = (hex_q, float(h["primary_temperature_out"]))
            if drive is not None and i == active[0]:
                flow_t = _mix([flow_t[0], drive[0]], [flow_t[1], drive[1]])
            brine[i] = flow_t

        # ---- district: [bypass] plate HEXs → CHP → [bypass] HTHPs → peak boiler
        temp = self.return_degc
        if hex_branch:
            _, t_hex = _mix([f for f, _ in hex_branch], [t for _, t in hex_branch])
            _, temp = _mix([hex_flow, district_flow - hex_flow], [t_hex, self.return_degc])
        if self.chp_params is not None and active:
            chp = self._model("chp", "chp", self.chp_params)
            out["chp"] = _calc(
                chp,
                {
                    "gas_flow_rate": gas_kg_s,
                    "water_flow_rate": district_flow,
                    "temperature_in": temp,
                },
            )
            temp = float(out["chp"]["temperature_out"])
            point.chp_gas_kg_s = gas_kg_s
        point.preheat_degc = temp if active else None
        if hp_running:
            assert train is not None and train.heat_pump is not None
            hp_flow = district_flow * hp_share
            branch: list[tuple[float, float]] = []
            for i in active:
                params = dict(train.heat_pump)
                params["Ts_in_minimum"] = max(
                    point.wells[i].wellhead_temperature_degc - self.gb.brine_cooling_max_k,
                    self.gb.heating_supply_degc - self.gb.hp_max_lift_k,
                )
                hp = self._model(f"hthp_{i}", "heatpump", params)
                hp.update_parameters(params)
                point.hp_params[i] = params
                out[f"hthp_{i}"] = _calc(
                    hp,
                    {
                        "Th_in": temp,
                        "Ts_in": brine[i][1],
                        "hot_flow_rate": hp_flow * fracs[i],
                        "source_flow_rate": brine[i][0],
                    },
                )
                o = out[f"hthp_{i}"]
                branch.append((hp_flow * fracs[i], float(o["Th_out"])))
                brine[i] = (brine[i][0], float(o["Ts_out"]))
            _, t_hp = _mix([f for f, _ in branch], [t for _, t in branch])
            _, temp = _mix([hp_flow, district_flow - hp_flow], [t_hp, temp])
        boiler = self._model("peak_boiler", "boiler", self.boiler_params)
        out["peak_boiler"] = _calc(
            boiler,
            {
                "gas_flow_rate": boiler_gas_kg_s,
                "water_flow_rate": district_flow,
                "temperature_in": temp,
            },
        )
        point.supply_degc = float(out["peak_boiler"]["temperature_out"])
        point.brine_out_degc = {i: brine[i][1] for i in active}
        return point

    def carries(
        self,
        day: DayConditions,
        flows_m3h: Sequence[float],
        plant_heating_mw: float,
        *,
        hp_on: bool = True,
        drive_flow_m3s: float = 0.0,
    ) -> bool:
        """Return True if HEX + CHP + HTHP (≤ nameplate) meet the load without the boiler.

        The plate-HEX DB-12/DB-16 control is applied first when the uncontrolled point breaks
        it (a hot brine cooled more than 40 K must give up HEX duty, so more flow is needed).
        """
        kwargs: dict[str, Any] = {
            "hp_on": hp_on,
            "drive_flow_m3s": drive_flow_m3s,
            "plant_heating_mw": plant_heating_mw,
        }
        point = self.evaluate(day, flows_m3h, **kwargs)
        problems = self.hex_problems(point)
        if any("cools the brine" in p for p in problems):
            point = self.run(day, flows_m3h, boiler=False, hp_capacity=False, **kwargs)
        elif point.preheat_degc is not None and point.preheat_degc >= (
            self.gb.heating_supply_degc - CONTROL_TOL_K
        ):
            return True
        shortfall = plant_heating_mw * 1e6 - point.geothermal_heat_w(self.hp_nameplate_w)
        return shortfall <= DISPATCH_TOL_MW * 1e6

    def hex_problems(self, point: FastPoint) -> list[str]:
        """TL-11 plate-HEX control targets: brine ≥ T_wh − DB-12 and preheat ≤ DB-16."""
        problems = []
        for i in point.active:
            h = point.outputs.get(f"hex_{i}")
            if h is None:
                continue
            cooled = point.wells[i].wellhead_temperature_degc - float(h["primary_temperature_out"])
            if cooled > self.gb.brine_cooling_max_k + CONTROL_TOL_K:
                problems.append(f"hex_{i} cools the brine {cooled:.2f} K")
        if point.preheat_degc is not None and point.preheat_degc > (
            self.gb.heating_supply_degc + CONTROL_TOL_K
        ):
            problems.append(f"district water {point.preheat_degc:.2f} °C > 100 °C")
        return problems

    def run(
        self,
        day: DayConditions,
        flows_m3h: Sequence[float],
        *,
        hp_on: bool = True,
        drive_flow_m3s: float = 0.0,
        plant_heating_mw: float | None = None,
        boiler: bool = True,
        hp_capacity: bool = True,
    ) -> FastPoint:
        """Evaluate with the TL-11 controls: HEX share, HTHP capacity share, boiler to 100 °C."""
        kwargs: dict[str, Any] = {
            "hp_on": hp_on,
            "drive_flow_m3s": drive_flow_m3s,
            "plant_heating_mw": plant_heating_mw,
        }
        tower_in = None
        if self.has_absorption:
            tower_in = tower_water_degc(
                day.ambient_dry_bulb_degc,
                day.ambient_wet_bulb_degc,
                self.cooling_spec.heat_rejection,
            )
        kwargs["cooling_ambient_tower_degc"] = tower_in
        point = self.evaluate(day, flows_m3h, **kwargs)
        hex_share = 1.0
        controls: list[str] = []
        if point.hex_share is not None:
            problems = self.hex_problems(point)
            if problems:
                if self.hex_problems(self.evaluate(day, flows_m3h, hex_share=0.0, **kwargs)):
                    hex_share = 0.0
                else:
                    low, high = 0.0, 1.0
                    for _ in range(SHARE_BISECTION_STEPS):
                        mid = 0.5 * (low + high)
                        trial = self.evaluate(day, flows_m3h, hex_share=mid, **kwargs)
                        if self.hex_problems(trial):
                            high = mid
                        else:
                            low = mid
                    hex_share = low
                controls.append(
                    f"plate-HEX share {hex_share:.4f} ({problems[0]}; DB-12/DB-16 control)"
                )
                point = self.evaluate(day, flows_m3h, hex_share=hex_share, **kwargs)
        hp_share = 1.0
        cap = self.hp_nameplate_w
        if hp_capacity and point.hp_on and cap is not None:
            heat = point.hp_heat_w()
            if heat > cap + HP_CAPACITY_TOL_MW * 1e6:
                low, high = 0.0, 1.0
                for _ in range(SHARE_BISECTION_STEPS):
                    mid = 0.5 * (low + high)
                    trial = self.evaluate(
                        day, flows_m3h, hex_share=hex_share, hp_share=mid, **kwargs
                    )
                    if trial.hp_heat_w() > cap + HP_CAPACITY_TOL_MW * 1e6:
                        high = mid
                    else:
                        low = mid
                hp_share = low
                controls.append(
                    f"heat-pump share {hp_share:.4f}: heat {heat / 1e6:.2f} MW > nameplate "
                    f"{cap / 1e6:g} MW (capacity control)"
                )
                point = self.evaluate(
                    day, flows_m3h, hex_share=hex_share, hp_share=hp_share, **kwargs
                )
        if boiler and self.boiler_params is not None:
            params = self.boiler_params
            t_in = float(point.outputs["peak_boiler"]["temperature_in"])
            need_w = (
                DISTRICT_FLUID["fluid_density"]
                * DISTRICT_FLUID["specific_heat"]
                * point.district_flow_m3s
                * (self.gb.heating_supply_degc - t_in)
            )
            heat_w = min(max(need_w, 0.0), self.boiler_capacity_w)
            gas = heat_w / (float(params["efficiency_factor"]) * float(params["caloric_value"]))
            if gas > 0:
                point = self.evaluate(
                    day,
                    flows_m3h,
                    hex_share=hex_share,
                    hp_share=hp_share,
                    boiler_gas_kg_s=gas,
                    **kwargs,
                )
        point.controls = controls
        return point


# =========================================================================== daily dispatch
class DayPlan(StrictModel):
    """The dispatch decided for one day (fast mode) — what full mode re-runs in GEMINI."""

    day_index: int
    date: dt.date
    available: list[bool]
    flows_m3h: list[float]
    heat_pumps_on: bool
    absorption_drive_frac: float | None = None
    absorption_drive_m3h: float = 0.0
    hex_flow_frac: float | None = None
    hp_flow_frac: float | None = None
    plant_heating_mw: float = Field(
        description="Heating load the plant serves: demand, + the storage charge on charge days."
    )
    storage_net_mw: float = Field(
        default=0.0,
        description="Net storage heat to the network (> 0 discharge ahead of the boiler, < 0 "
        "charge served by the plant).",
    )
    notes: list[str] = Field(default_factory=list)

    def plant_dispatch(self) -> PlantDispatch:
        """TL-09/TL-11 ``PlantDispatch`` of this plan (full mode)."""
        return PlantDispatch(
            doublet_flows_m3h=list(self.flows_m3h),
            absorption_drive_frac=self.absorption_drive_frac,
            hex_flow_frac=1.0 if self.hex_flow_frac is None else self.hex_flow_frac,
            hp_flow_frac=1.0 if self.hp_flow_frac is None else self.hp_flow_frac,
            heat_pumps_on=self.heat_pumps_on,
        )


class FastDay(StrictModel):
    """Fast-mode outcome of one day: the plan, the ``DayResult`` and the control notes."""

    plan: DayPlan
    day: DayResult
    controls: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _free_cooling_outputs(plant: FastPlant, day: DayConditions) -> dict[str, Any]:
    fc = plant._model("free_cooling_need", "free_cooling", plant.cooling_spec.free_cooling)
    return _calc(
        fc,
        {
            "ambient_wet_bulb_degc": day.ambient_wet_bulb_degc,
            "cooling_load_w": day.cooling_demand_mw * 1e6,
        },
    )


def _absorption_drive_m3s(
    plant: FastPlant, day: DayConditions, residual_w: float
) -> tuple[float, str | None]:
    """Smallest given drive flow (m³/s) at which the absorber meets ``residual_w`` (GM-04).

    A flow is acceptable when the absorber delivers the target cooling *and* cools the drive
    brine by at most DB-12 (40 K; the drive outlet mixes into the doublet's injection stream).
    Bisection between the COOL-ABS-DRV minimum drive flow (+ TL-05 flow tolerance) and the
    doublet's maximum flow, the drive at the wellhead temperature of a doublet running at that
    drive flow (the part-load drive heat depends on the load only, not on the drive temperature).
    The target is the residual capped at the capacity at maximum flow. Returns ``(0, note)``
    (absorption off, electric chiller) when no flow up to the maximum satisfies both.
    """
    absorber = plant._model("absorption_need", "absorption_chiller", plant.cooling_spec.absorption)
    base: dict[str, Any] = {
        "cooling_water_temperature_in_degc": tower_water_degc(
            day.ambient_dry_bulb_degc,
            day.ambient_wet_bulb_degc,
            plant.cooling_spec.heat_rejection,
        ),
        "cooling_load_w": residual_w,
    }
    if plant.has_free_cooling:
        fc = _free_cooling_outputs(plant, day)
        base["chilled_water_temperature_in_degc"] = fc["chilled_water_temperature_out_degc"]

    def output(flow_m3h: float) -> dict[str, Any]:
        q = max(flow_m3h, plant.flow_min_m3h)
        u = {
            **base,
            "drive_temperature_in_degc": plant.well(q).wellhead_temperature_degc,
            "drive_flow_rate_m3s": flow_m3h / SECONDS_PER_HOUR,
        }
        return _calc(absorber, u)

    high_out = output(plant.flow_max_m3h)
    target = min(residual_w, float(high_out["cooling_power_w"]))
    if target <= 0:
        return 0.0, None
    # Full mode drives the absorber with frac × GEMINI's flow, which TL-05 holds only within
    # FLOW_TOLERANCE_FRAC of the target: keep the drive that much above the COOL-ABS-DRV minimum.
    minimum = (
        float(high_out.get("minimum_drive_flow_rate_m3s") or 0.0)
        * SECONDS_PER_HOUR
        * (1.0 + FLOW_TOLERANCE_FRAC)
    )
    limit_k = plant.gb.brine_cooling_max_k

    def ok(flow_m3h: float) -> bool:
        out = output(flow_m3h)
        cools = float(out["cooling_power_w"]) >= target - HEAT_TOL_MW * 1e6
        return cools and float(out.get("drive_delta_t_k") or 0.0) <= limit_k

    if minimum >= plant.flow_max_m3h:
        return 0.0, (
            f"absorption off: COOL-ABS-DRV minimum drive {minimum:.0f} m³/h ≥ maximum doublet "
            f"flow {plant.flow_max_m3h:g} m³/h; electric chiller"
        )
    if not ok(plant.flow_max_m3h):
        return 0.0, (
            f"absorption off: at {plant.flow_max_m3h:g} m³/h the drive brine would be cooled "
            f"> DB-12 {limit_k:g} K for {target / 1e6:.2f} MW; electric chiller"
        )
    low, high = minimum, plant.flow_max_m3h
    if ok(low):
        return low / SECONDS_PER_HOUR, None
    for _ in range(FLOW_BISECTION_STEPS):
        mid = 0.5 * (low + high)
        if ok(mid):
            high = mid
        else:
            low = mid
    return high / SECONDS_PER_HOUR, None


def _equal_flows(available: Sequence[bool], k: int, q: float) -> list[float]:
    flows, used = [], 0
    for ok in available:
        if ok and used < k:
            flows.append(q)
            used += 1
        else:
            flows.append(0.0)
    return flows


def dispatch_day(
    plant: FastPlant,
    day: DayConditions,
    available: Sequence[bool],
    *,
    storage_charge_mw: float = 0.0,
    hp_min_frac: float,
    absorption_min_frac: float,
    hp_extra_flow_frac: float = 0.0,
) -> FastDay:
    """Merit-order dispatch of one day on the fast model (module doc, SIM-03).

    ``storage_charge_mw`` (C6) is served by the plant on top of the demand; a storage discharge
    is applied afterwards with :func:`apply_storage_discharge`.
    """
    if storage_charge_mw < 0:
        raise ValueError("storage_charge_mw must be >= 0 (discharge: apply_storage_discharge)")
    notes: list[str] = []
    plant_heat = day.heating_demand_mw + storage_charge_mw
    n_avail = sum(1 for ok in available if ok)
    # ---- cooling: free cooling first; absorption for the residual if worth it
    drive_m3s = 0.0
    if plant.has_absorption and n_avail and day.cooling_demand_mw > 0:
        residual = day.cooling_demand_mw * 1e6
        if plant.has_free_cooling:
            residual = float(_free_cooling_outputs(plant, day)["residual_cooling_w"])
        minimum_load = absorption_min_frac * plant.absorption_nameplate_w
        if residual > 0 and residual >= minimum_load:
            drive_m3s, drive_note = _absorption_drive_m3s(plant, day, residual)
            if drive_note:
                notes.append(drive_note)
        elif residual > 0:
            notes.append(
                f"absorption off: residual {residual / 1e6:.3f} MW < DSP-ABS-MIN "
                f"{minimum_load / 1e6:.3f} MW; electric chiller"
            )
    drive_m3h = drive_m3s * SECONDS_PER_HOUR
    q_lo = max(plant.flow_min_m3h, min(drive_m3h, plant.flow_max_m3h))
    q_hi = plant.flow_max_m3h

    def meets(flows: list[float], hp_on: bool) -> bool:
        return plant.carries(day, flows, plant_heat, hp_on=hp_on, drive_flow_m3s=drive_m3s)

    def solve(hp_on: bool) -> tuple[list[float], bool]:
        """Return (flows, met without boiler) for the DSP-FLOW rule."""
        if n_avail == 0 or (plant_heat <= HEAT_TOL_MW and drive_m3s <= 0):
            return [0.0] * plant.n_doublets, plant_heat <= HEAT_TOL_MW
        best: list[float] | None = None
        for k in range(n_avail, 0, -1):
            low_flows = _equal_flows(available, k, q_lo)
            if meets(low_flows, hp_on):
                best = low_flows  # k at the minimum already meets: try fewer doublets
                continue
            high_flows = _equal_flows(available, k, q_hi)
            if not meets(high_flows, hp_on):
                if best is not None:
                    return best, True
                return high_flows, False  # all available doublets at maximum: boiler fills
            low, high = q_lo, q_hi
            for _ in range(FLOW_BISECTION_STEPS):
                mid = 0.5 * (low + high)
                if meets(_equal_flows(available, k, mid), hp_on):
                    high = mid
                else:
                    low = mid
            return _equal_flows(available, k, high), True
        assert best is not None
        return best, True

    hp_on = plant.has_hp
    flows, met = solve(hp_on)
    point = plant.run(
        day, flows, hp_on=hp_on, drive_flow_m3s=drive_m3s, plant_heating_mw=plant_heat
    )
    if hp_on and point.hp_on and plant.hp_reference_w:
        hp_heat = point.hp_heat_w()
        minimum = hp_min_frac * plant.hp_reference_w
        if 0.0 < hp_heat < minimum:
            # HTHPs off when HEX + CHP carry the load with at most hp_extra_flow_frac more brine
            # (the merit order puts the HTHP before CHP must-run heat, so no large flow increase
            # is bought); otherwise the daily average below the minimum part load means on/off
            # cycling within the day (DSP-HP-MIN).
            off_flows, off_met = solve(False) if met else (flows, False)
            limit = sum(flows) * (1.0 + hp_extra_flow_frac)
            if off_met and sum(off_flows) <= limit + 1e-9:
                notes.append(
                    f"HTHPs off: {hp_heat / 1e6:.3f} MW < DSP-HP-MIN {minimum / 1e6:.3f} MW; HEX + "
                    f"CHP carry the load at {sum(off_flows):.1f} m³/h (was {sum(flows):.1f})"
                )
                hp_on, flows = False, off_flows
                point = plant.run(
                    day, flows, hp_on=False, drive_flow_m3s=drive_m3s, plant_heating_mw=plant_heat
                )
            else:
                notes.append(
                    f"HTHP heat {hp_heat / 1e6:.3f} MW below DSP-HP-MIN {minimum / 1e6:.3f} MW: "
                    "on/off cycling within the day (HEX + CHP alone need more brine or cannot "
                    "carry the load)"
                )
    if not met:
        notes.append("geothermal at maximum flow on the available doublets; peak boiler fills")
    plan = DayPlan(
        day_index=day.day_index,
        date=day.date,
        available=list(available),
        flows_m3h=flows,
        heat_pumps_on=hp_on and plant.has_hp,
        absorption_drive_frac=point.drive_frac if plant.has_absorption else None,
        absorption_drive_m3h=drive_m3h,
        hex_flow_frac=point.hex_share,
        hp_flow_frac=point.hp_share,
        plant_heating_mw=plant_heat,
        storage_net_mw=-storage_charge_mw,
        notes=notes,
    )
    result, warnings = fast_day_result(plant, day, point, plan)
    return FastDay(plan=plan, day=result, controls=point.controls, warnings=warnings)


# =========================================================================== DayResult
def fast_day_result(
    plant: FastPlant, day: DayConditions, point: FastPoint, plan: DayPlan
) -> tuple[DayResult, list[str]]:
    """``DayResult`` of a fast point, with the same keys and margins as TL-11's full mode."""
    gb, out = plant.gb, point.outputs
    margins: dict[str, float] = {}
    warnings: list[str] = []
    flags: list[str] = []
    t_res = plant.reservoir_temperature_degc or 0.0
    flows, t_prod, t_inj = [], [], []
    electricity: dict[str, float] = {}
    for k in range(plant.n_doublets):
        i = k + 1
        if i not in point.active:
            flows.append(0.0)
            t_prod.append(t_res)
            t_inj.append(t_res)
            continue
        w = point.wells[i]
        q = point.flows_m3h[k]
        flows.append(q)
        t_prod.append(w.wellhead_temperature_degc)
        t_inj.append(point.brine_out_degc[i])
        electricity[f"esp_{i}"] = w.esp_electric_w / 1e6
        electricity[f"injection_pump_{i}"] = w.injection_pump_electric_w / 1e6
        if w.intake_margin_bar is not None:
            margins[f"esp_{i}_intake_margin_bar"] = w.intake_margin_bar
        margins[f"doublet_{i}_brine_cooling_margin_k"] = gb.brine_cooling_max_k - (
            w.wellhead_temperature_degc - point.brine_out_degc[i]
        )
        margins[f"doublet_{i}_flow_margin_m3h"] = min(
            q - plant.sub.flow_min_m3h, plant.sub.flow_max_m3h - q
        )
        margins[f"doublet_{i}_injection_balance_margin_m3h"] = 0.0
    heat: dict[str, float] = {}
    drawn: dict[str, float] = {}
    for i in point.active:
        if f"hex_{i}" in out:
            duty = float(out[f"hex_{i}"]["heat_duty"]) / 1e6
            heat[f"hex_{i}"] = duty
            drawn[f"hex_{i}"] = duty
    for i in point.active:
        name = f"hthp_{i}"
        if name not in out:
            continue
        o = out[name]
        heat[name] = float(o["power_th"]) / 1e6
        electricity[name] = float(o["power_el"]) / 1e6
        drawn[name] = float(o.get("source_heat_w", o["power_th"] - o["power_el"])) / 1e6
        flag, _ = _heat_pump_screening(name, o)
        if flag is not None:
            flags.append(_flag_text(flag))
        if o.get("running"):
            warnings += [f"{name}: {w}" for w in o.get("validity_warnings") or []]
        _heat_pump_margins(name, o, params={"parameters": point.hp_params[i]}, margins=margins)
    gas_fuel: dict[str, float] = {}
    co2: dict[str, float] = {}
    generated = 0.0
    for name, params in (("chp", plant.chp_params), ("peak_boiler", plant.boiler_params)):
        if name not in out or params is None:
            continue
        o = out[name]
        heat[name] = float(o["power_th"]) / 1e6
        gas_kg_s = point.boiler_gas_kg_s if name == "peak_boiler" else point.chp_gas_kg_s
        gas_fuel[name] = gas_kg_s * float(params["caloric_value"]) / 1e6
        co2[name] = float(o["emission"]) * SECONDS_PER_DAY
        if name == "chp":
            generated = float(o["power_el"]) / 1e6
    if plan.storage_net_mw:
        heat["storage"] = plan.storage_net_mw
    # Cooling.
    cool: dict[str, float] = {}
    if "free_cooling" in out:
        cool["free_cooling"] = float(out["free_cooling"]["free_cooling_w"]) / 1e6
        electricity["free_cooling"] = float(out["free_cooling"]["power_el"]) / 1e6
    if "absorption_chiller" in out:
        a = out["absorption_chiller"]
        cool["absorption_chiller"] = float(a["cooling_power_w"]) / 1e6
        drawn["absorption_chiller"] = float(a["drive_heat_w"]) / 1e6
        electricity["absorption_chiller"] = float(a["power_el"]) / 1e6
        drive_m3s = float(a["drive_flow_rate_m3s"])
        if drive_m3s > 0:
            margins["absorption_drive_cooling_margin_k"] = gb.brine_cooling_max_k - float(
                a["drive_delta_t_k"]
            )
            minimum = float(a.get("minimum_drive_flow_rate_m3s") or 0.0)
            margins["absorption_drive_flow_margin_m3h"] = (drive_m3s - minimum) * SECONDS_PER_HOUR
    chiller = out["electric_chiller"]
    cool["electric_chiller"] = float(chiller["cooling_power_w"]) / 1e6
    electricity["electric_chiller"] = float(chiller["power_el"]) / 1e6
    cooling_supply = float(chiller["chilled_water_temperature_out_degc"])
    electricity["heat_rejection"] = float(out["heat_rejection"]["power_el"]) / 1e6
    warnings += [
        f"{day.date}: DB-12 {key} = {value:.2f} K < 0"
        for key, value in margins.items()
        if "cooling_margin_k" in key and value < -CONTROL_TOL_K
    ]
    unmet_cooling = max(day.cooling_demand_mw - sum(cool.values()), 0.0)
    unmet_cooling = unmet_cooling if unmet_cooling > HEAT_TOL_MW else 0.0
    if day.cooling_demand_mw > 0:
        margins["cooling_supply_margin_k"] = gb.cooling_supply_degc - cooling_supply
    # Heating balance (against the network demand; storage heat is a source).
    supplied = sum(heat.values())
    gap = day.heating_demand_mw - supplied
    unmet_heating = gap if gap > HEAT_TOL_MW else 0.0
    surplus = -gap if -gap > HEAT_TOL_MW else 0.0
    if day.heating_demand_mw > 0 and point.supply_degc is not None:
        margins["heating_supply_margin_k"] = point.supply_degc - gb.heating_supply_degc
        margins["heat_surplus_margin_mw"] = -surplus
    if plant.hp_nameplate_w is not None and any(k.startswith("hthp_") for k in heat):
        margins["hthp_capacity_margin_mw"] = plant.hp_nameplate_w / 1e6 - sum(
            v for k, v in heat.items() if k.startswith("hthp_")
        )
    margins["peak_boiler_capacity_margin_mw"] = plant.boiler_capacity_w / 1e6 - heat.get(
        "peak_boiler", 0.0
    )
    result = DayResult(
        day_index=day.day_index,
        date=day.date,
        ambient_dry_bulb_degc=day.ambient_dry_bulb_degc,
        ambient_wet_bulb_degc=day.ambient_wet_bulb_degc,
        heating_demand_mw=day.heating_demand_mw,
        cooling_demand_mw=day.cooling_demand_mw,
        doublet_available=list(plan.available),
        doublet_flow_m3h=flows,
        injection_flow_m3h=list(flows),
        production_temperature_degc=t_prod,
        injection_temperature_degc=t_inj,
        heating_supply_degc=point.supply_degc,
        heating_return_degc=plant.return_degc,
        cooling_supply_degc=cooling_supply,
        cooling_return_degc=plant.meta.cooling_return_degc,
        heat_by_source_mw=heat,
        cool_by_source_mw=cool,
        electricity_by_consumer_mw=electricity,
        brine_heat_drawn_mw=drawn,
        electricity_generated_mw=generated,
        gas_fuel_by_consumer_mw=gas_fuel,
        associated_gas_produced_nm3=point.associated_gas_nm3_s * SECONDS_PER_DAY,
        co2_by_source_kg=co2,
        unmet_heating_mwh=unmet_heating * HOURS_PER_DAY,
        unmet_cooling_mwh=unmet_cooling * HOURS_PER_DAY,
        margins=margins,
        flags=flags,
    )
    return result, warnings


# =========================================================================== C6 storage
class StorageSpec(StrictModel):
    """C6 seasonal HT-ATES sizing (``equipment_sizes``) and recovery (STO-REC-HT)."""

    capacity_mwh: float = Field(gt=0, description="Injected-energy capacity (WP-03 rule).")
    power_mw: float = Field(gt=0, description="Charge and discharge power limit.")
    discharge_efficiency_frac: float = Field(gt=0, le=1)


class StorageBalance(StrictModel):
    """Annual storage ledger (``ThermalStorage``): seasonal ATES needs a heat balance (RC-09)."""

    spec: StorageSpec
    initial_energy_mwh: float
    final_energy_mwh: float
    charged_total_mwh: float = Field(description="Heat injected (plant side).")
    discharged_total_mwh: float = Field(description="Heat delivered to the network.")
    charge_days: int
    discharge_days: int
    net_mw_by_day: dict[int, float] = Field(
        description="Day index → net storage heat to the network (> 0 discharge)."
    )

    @property
    def cyclic_mismatch_mwh(self) -> float:
        """End − start energy of the reported pass (≈ 0 for a balanced seasonal store, RC-09)."""
        return self.final_energy_mwh - self.initial_energy_mwh


def storage_spec(candidate: Candidate, register: AssumptionsRegister) -> StorageSpec | None:
    """Storage of a C6 candidate: both ``storage_capacity_mwh`` and ``storage_power_mw`` given."""
    capacity = candidate.equipment_sizes.get("storage_capacity_mwh")
    power = candidate.equipment_sizes.get("storage_power_mw")
    if candidate.concept != "C6" or not capacity or not power:
        return None
    recovery = float(register.get("STO-REC-HT").value)  # type: ignore[arg-type]
    return StorageSpec(capacity_mwh=capacity, power_mw=power, discharge_efficiency_frac=recovery)


def apply_storage_discharge(fast: FastDay, discharge_mw: float) -> FastDay:
    """Storage discharge ahead of the peak boiler at full district flow (DSP-STORAGE).

    HEX, CHP and HTHP are unchanged; the boiler's heat, fuel and CO₂ fall by the discharge
    (GEMINI's boiler is linear in fuel). ``heat_by_source_mw['storage']`` holds the discharge.
    """
    plan = fast.plan.model_copy(update={"storage_net_mw": discharge_mw})
    return fast.model_copy(update={"plan": plan, "day": discharge_into_day(fast.day, discharge_mw)})


def discharge_into_day(day: DayResult, discharge_mw: float) -> DayResult:
    """Replace ``discharge_mw`` of peak-boiler heat by storage heat in a ``DayResult``."""
    boiler = day.heat_by_source_mw.get("peak_boiler", 0.0)
    if discharge_mw <= 0:
        return day
    if discharge_mw > boiler + HEAT_TOL_MW:
        raise ValueError(f"discharge {discharge_mw:.3f} MW exceeds the boiler heat {boiler:.3f}")
    share = max(boiler - discharge_mw, 0.0) / boiler if boiler > 0 else 0.0
    heat = {**day.heat_by_source_mw, "peak_boiler": boiler * share, "storage": discharge_mw}
    fuel = dict(day.gas_fuel_by_consumer_mw)
    co2 = dict(day.co2_by_source_kg)
    fuel["peak_boiler"] = fuel.get("peak_boiler", 0.0) * share
    co2["peak_boiler"] = co2.get("peak_boiler", 0.0) * share
    margins = dict(day.margins)
    if "peak_boiler_capacity_margin_mw" in margins:
        margins["peak_boiler_capacity_margin_mw"] += boiler - heat["peak_boiler"]
    return DayResult.model_validate(
        {
            **day.model_dump(),
            "heat_by_source_mw": heat,
            "gas_fuel_by_consumer_mw": fuel,
            "co2_by_source_kg": co2,
            "margins": margins,
        }
    )


def storage_ledger(
    spec: StorageSpec,
    day_indices: Sequence[int],
    charge_mw: dict[int, float],
    discharge_mw: dict[int, float],
) -> StorageBalance:
    """Run ``ThermalStorage`` over the days (cyclic: STORAGE_CYCLES passes, DSP-STORAGE)."""
    model = ThermalStorage()
    model.update_parameters(
        {
            "capacity_mwh": spec.capacity_mwh,
            "storage_type": "ht_ates",
            "discharge_efficiency": spec.discharge_efficiency_frac,
            "max_charge_power_w": spec.power_mw * 1e6,
            "max_discharge_power_w": spec.power_mw * 1e6,
        }
    )
    energy = 0.0
    net: dict[int, float] = {}
    initial = 0.0
    charged = discharged = 0.0
    for _ in range(STORAGE_CYCLES):
        initial = energy
        state = model.initialize_state({"energy_mwh": energy})
        net = {}
        for d in day_indices:
            u = {
                "charge_power_w": charge_mw.get(d, 0.0) * 1e6,
                "discharge_power_w": discharge_mw.get(d, 0.0) * 1e6,
            }
            model.calculate_output(u, state)
            state = model.update_state(u, state)
            value = (model.output["discharge_power_w"] - model.output["charge_power_w"]) / 1e6
            if abs(value) > HEAT_TOL_MW:
                net[d] = value
        energy = state["energy_j"] / 3.6e9
        charged = state["charged_j"] / 3.6e9
        discharged = state["discharged_j"] / 3.6e9
    return StorageBalance(
        spec=spec,
        initial_energy_mwh=initial,
        final_energy_mwh=energy,
        charged_total_mwh=charged,
        discharged_total_mwh=discharged,
        charge_days=sum(1 for v in net.values() if v < 0),
        discharge_days=sum(1 for v in net.values() if v > 0),
        net_mw_by_day=net,
    )


def spare_geothermal_mw(
    plant: FastPlant,
    day: DayConditions,
    available: Sequence[bool],
    limit_mw: float,
    *,
    drive_flow_m3s: float = 0.0,
) -> float:
    """Extra plant load (≤ ``limit_mw``) the available doublets carry at maximum without boiler.

    ``drive_flow_m3s`` is the day's absorption drive (its brine is not available to the HEX).
    """
    flows = [plant.flow_max_m3h if ok else 0.0 for ok in available]
    if not any(flows) or limit_mw <= 0:
        return 0.0

    def carries(extra: float) -> bool:
        return plant.carries(
            day,
            flows,
            day.heating_demand_mw + extra,
            hp_on=plant.has_hp,
            drive_flow_m3s=drive_flow_m3s,
        )

    if carries(limit_mw):
        return limit_mw
    if not carries(0.0):
        return 0.0
    low, high = 0.0, limit_mw
    for _ in range(STORAGE_BISECTION_STEPS):
        mid = 0.5 * (low + high)
        if carries(mid):
            low = mid
        else:
            high = mid
    return low


__all__ = [
    "DB30_ID",
    "DISPATCH_ASSUMPTION_IDS",
    "AvailabilityCalendar",
    "DayPlan",
    "Db30Rule",
    "DoubletState",
    "FastDay",
    "FastPlant",
    "FastPoint",
    "StorageBalance",
    "StorageSpec",
    "apply_storage_discharge",
    "availability_calendar",
    "db30_rule",
    "discharge_into_day",
    "dispatch_day",
    "fast_day_result",
    "lowest_load_starts",
    "spare_geothermal_mw",
    "storage_ledger",
    "storage_spec",
]
