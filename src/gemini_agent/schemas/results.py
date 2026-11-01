"""Simulation results and KPIs (spec §3.4 ENG-01a, §8 SIM).

Time base: one record per day. Powers are **day-average MW** (the demand CSVs in
``data/demand/`` are day-average MW); daily energies are MWh/day; CO₂ is kg/day.
Aggregations here are plain unit conversions (MW × 24 h); all physics lives in the tools.
They are plain properties (not serialised) so that dump → validate round-trips stay strict.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field, model_validator

from gemini_agent.schemas.common import HOURS_PER_DAY, StrictModel

PER_DOUBLET_FIELDS: tuple[str, ...] = (
    "doublet_available",
    "doublet_flow_m3h",
    "injection_flow_m3h",
    "production_temperature_degc",
    "injection_temperature_degc",
)


class DayResult(StrictModel):
    """Result of one simulated day for one candidate."""

    day_index: int = Field(ge=0, le=365, description="0-based day of the year.")
    date: dt.date
    # Ambient (DB-34a)
    ambient_dry_bulb_degc: float
    ambient_wet_bulb_degc: float | None = None
    # Demand (DB-18, DB-19)
    heating_demand_mw: float = Field(ge=0)
    cooling_demand_mw: float = Field(ge=0)
    # Per-doublet values (list index = doublet number; empty lists when no doublets)
    doublet_available: list[bool] = Field(
        default_factory=list, description="False on planned-outage days (DB-30)."
    )
    doublet_flow_m3h: list[float] = Field(
        default_factory=list, description="Production flow per doublet; 0 when off."
    )
    injection_flow_m3h: list[float] = Field(
        default_factory=list, description="Injection flow per doublet (DB-14: equals production)."
    )
    production_temperature_degc: list[float] = Field(default_factory=list)
    injection_temperature_degc: list[float] = Field(default_factory=list)
    # Network temperatures (DB-16, DB-17, DB-31, DB-32)
    heating_supply_degc: float | None = None
    heating_return_degc: float | None = None
    cooling_supply_degc: float | None = None
    cooling_return_degc: float | None = None
    # Energy balances (keys are equipment/source names, unit in the field name)
    heat_by_source_mw: dict[str, float] = Field(
        default_factory=dict,
        description="Heat delivered to the district-heating network only (not absorption drive).",
    )
    cool_by_source_mw: dict[str, float] = Field(default_factory=dict)
    electricity_by_consumer_mw: dict[str, float] = Field(default_factory=dict)
    brine_heat_drawn_mw: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Heat taken from the geothermal brine per user (direct HEX, heat-pump evaporator, "
            "absorption drive); the DesignVerifier balances it against rho*Q*c_p*dT (WP-09)."
        ),
    )
    electricity_generated_mw: float = Field(default=0.0, ge=0, description="e.g. CHP output.")
    gas_fuel_by_consumer_mw: dict[str, float] = Field(
        default_factory=dict, description="Fuel input (LHV) per burner: boiler, CHP, …"
    )
    associated_gas_produced_nm3: float = Field(default=0.0, ge=0, description="Per day (DB-09).")
    co2_by_source_kg: dict[str, float] = Field(default_factory=dict, description="Per day.")
    # Shortfalls and constraint margins
    unmet_heating_mwh: float = Field(default=0.0, ge=0)
    unmet_cooling_mwh: float = Field(default=0.0, ge=0)
    margins: dict[str, float] = Field(
        default_factory=dict,
        description="Constraint id -> margin in the unit of its suffix; >= 0 means satisfied.",
    )
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions a reader must see that are not unit-suffixed margins, e.g. a heat-pump "
            "point outside its RC-04 limits or HTHP datasheet values MISSING (WP-06)."
        ),
    )

    @model_validator(mode="after")
    def _check_per_doublet_lengths(self) -> DayResult:
        lengths = {name: len(getattr(self, name)) for name in PER_DOUBLET_FIELDS}
        non_empty = {length for length in lengths.values() if length}
        if len(non_empty) > 1:
            raise ValueError(f"per-doublet lists have inconsistent lengths: {lengths}")
        for name in ("doublet_flow_m3h", "injection_flow_m3h"):
            if any(q < 0 for q in getattr(self, name)):
                raise ValueError(f"{name} must be >= 0")
        return self

    @property
    def heat_supplied_mw(self) -> float:
        """Sum of heat delivered by all sources (day-average MW)."""
        return float(sum(self.heat_by_source_mw.values()))

    @property
    def cool_supplied_mw(self) -> float:
        """Sum of cooling delivered by all sources (day-average MW)."""
        return float(sum(self.cool_by_source_mw.values()))

    @property
    def electricity_consumed_mw(self) -> float:
        """Sum of electricity drawn by all consumers (day-average MW)."""
        return float(sum(self.electricity_by_consumer_mw.values()))

    @property
    def co2_kg(self) -> float:
        """Total CO₂ emitted on this day (kg)."""
        return float(sum(self.co2_by_source_kg.values()))


class KPISet(StrictModel):
    """KPIs reported for every candidate (spec §3.4 ENG-01a). ``None`` = not computed yet.

    Money fields are scale-suffixed (``_million``, ``_per_mwh``) and expressed in ``currency``
    (set by the economics tool from ``config/cost_assumptions.yaml``). The currency is not in the
    field names on purpose: the cost basis is an open organiser question (spec §17 Q5).
    """

    candidate_id: str
    # Demand coverage
    unmet_heating_days: int | None = Field(default=None, ge=0)
    unmet_heating_mwh: float | None = Field(default=None, ge=0)
    unmet_cooling_days: int | None = Field(default=None, ge=0)
    unmet_cooling_mwh: float | None = Field(default=None, ge=0)
    # Capacities and energy by source
    installed_heating_mw: float | None = Field(default=None, ge=0)
    installed_cooling_mw: float | None = Field(default=None, ge=0)
    heat_by_source_mwh: dict[str, float] = Field(default_factory=dict)
    cool_by_source_mwh: dict[str, float] = Field(default_factory=dict)
    doublet_capacity_factors_frac: list[float] = Field(default_factory=list)
    # Energy carriers and emissions
    electricity_consumed_mwh: float | None = None
    electricity_generated_mwh: float | None = None
    gas_fuel_mwh: float | None = Field(default=None, description="Fuel burned, LHV basis.")
    co2_t_per_yr: float | None = None
    co2_reduction_vs_c0_frac: float | None = Field(
        default=None, description="(CO2_C0 - CO2) / CO2_C0; 0.6 = 60 % lower than C0."
    )
    # Economics (RC-10)
    currency: str | None = Field(default=None, description="e.g. 'USD' (assumption ECO-CUR).")
    capex_million: float | None = Field(default=None, description="Million `currency`.")
    opex_million_per_yr: float | None = Field(default=None, description="Million `currency`/yr.")
    lcoh_per_mwh: float | None = Field(default=None, description="`currency` per MWh heat.")
    lcoc_per_mwh: float | None = Field(default=None, description="`currency` per MWh cooling.")
    # Added by WP-08 (extend, never rename): ECO-01 20-yr case and carrier detail
    lcoh_20yr_per_mwh: float | None = Field(
        default=None, description="LCOH over the 20-yr analysis period (ECO-01 reports both)."
    )
    lcoc_20yr_per_mwh: float | None = Field(
        default=None, description="LCOC over the 20-yr analysis period."
    )
    electricity_purchased_mwh: float | None = Field(
        default=None, ge=0, description="Daily max(0, consumed − generated) (ECO-ELEC-NET)."
    )
    grid_gas_fuel_mwh: float | None = Field(
        default=None, ge=0, description="Purchased grid gas (LHV); gas_fuel_mwh − associated gas."
    )
    # Exergy (RC-11, DB-34d) — the dead state must be stated with the KPI
    exergy_efficiency_frac: float | None = Field(
        default=None,
        description=(
            "Functional exergy efficiency: (E_heat + E_cold + W_generated) / (brine exergy + "
            "electricity + fuel exergy) (RC-11; EXG-KPI)."
        ),
    )
    exergy_dead_state: str | None = None
    exergy_utilisation_frac: float | None = Field(
        default=None,
        description="Spec §10.1 utilisation (W_net + E_heat + E_cold)/(m·e_res); None if no brine.",
    )
    # Robustness
    min_constraint_margins: dict[str, float] = Field(default_factory=dict)
    thermal_breakthrough_yr: float | None = Field(default=None, description="t_b (RC-02).")
    percentiles: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="KPI name -> {'p10': .., 'p50': .., 'p90': ..} from the uncertainty run.",
    )


class AnnualResult(StrictModel):
    """Daily results of one candidate over a year, plus its KPIs once computed."""

    candidate_id: str
    year: int
    interpretation: Literal["A", "B"] = Field(
        description="DB-30: A = 365 days with 35-day outage per doublet; B = 330 days, no outage."
    )
    days: list[DayResult]
    kpis: KPISet | None = None

    @model_validator(mode="after")
    def _check_days(self) -> AnnualResult:
        indices = [day.day_index for day in self.days]
        if indices != sorted(set(indices)):
            raise ValueError("days must have unique, increasing day_index")
        jan_1 = dt.date(self.year, 1, 1)
        for day in self.days:
            if day.date != jan_1 + dt.timedelta(days=day.day_index):
                raise ValueError(f"day_index {day.day_index} does not match date {day.date}")
        if self.kpis is not None and self.kpis.candidate_id != self.candidate_id:
            raise ValueError("kpis.candidate_id does not match candidate_id")
        return self

    @property
    def heat_delivered_mwh(self) -> float:
        """Annual heat delivered (MWh) = Σ day-average MW × 24 h."""
        return float(sum(day.heat_supplied_mw for day in self.days) * HOURS_PER_DAY)

    @property
    def cool_delivered_mwh(self) -> float:
        """Annual cooling delivered (MWh) = Σ day-average MW × 24 h."""
        return float(sum(day.cool_supplied_mw for day in self.days) * HOURS_PER_DAY)

    @property
    def electricity_consumed_mwh(self) -> float:
        """Annual electricity consumption (MWh)."""
        return float(sum(day.electricity_consumed_mw for day in self.days) * HOURS_PER_DAY)

    @property
    def unmet_heating_mwh(self) -> float:
        """Annual unmet heating (MWh)."""
        return float(sum(day.unmet_heating_mwh for day in self.days))

    @property
    def unmet_cooling_mwh(self) -> float:
        """Annual unmet cooling (MWh)."""
        return float(sum(day.unmet_cooling_mwh for day in self.days))

    @property
    def unmet_heating_days(self) -> int:
        """Number of days with any unmet heating."""
        return sum(1 for day in self.days if day.unmet_heating_mwh > 0.0)

    @property
    def unmet_cooling_days(self) -> int:
        """Number of days with any unmet cooling."""
        return sum(1 for day in self.days if day.unmet_cooling_mwh > 0.0)

    @property
    def co2_t(self) -> float:
        """Annual CO₂ (tonnes)."""
        return float(sum(day.co2_kg for day in self.days) / 1000.0)
