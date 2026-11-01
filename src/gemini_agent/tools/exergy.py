"""TL-14 ``exergy``: exergy KPI with the dead state T₀ = daily ambient dry-bulb (DB-34d).

Equations (RC-11; DiPippo Ch10 / §9.7, ``VERIFY`` equation numbers on the card):

* liquid flow exergy (constant c): ``e = c [(T − T₀) − T₀ ln(T/T₀)]``;
* heat exergy: ``E_Q = Q (1 − T₀/T)``;
* heat delivered by a stream cooled from T_in to T_out:
  ``E_heat = ṁ c [(T_in − T_out) − T₀ ln(T_in/T_out)]`` — for the district network
  ``ṁ c = Q/(T_s − T_r)``, i.e. ``E_heat = Q [1 − T₀ ln(T_s/T_r)/(T_s − T_r)]``;
* cold delivered at T < T₀: ``E_cold = Q_cool (T₀/T_lm − 1)`` with T_lm the log-mean of the
  chilled supply/return, zero when ``T_lm ≥ T₀`` (20/26 °C cooling is above T₀ most days);
* utilisation (spec §10.1): ``η_u = (W_net + E_heat + E_cold)/(ṁ_brine · e_res)`` with
  ``W_net = W_generated − W_consumed`` and e_res at the produced brine temperature;
* functional efficiency: desired exergy out / exergy spent =
  ``(E_heat + E_cold + W_generated) / (ṁ_brine e_res + W_consumed + φ · fuel LHV)``,
  φ = 1.04 (EXG-FUEL). This is ``KPISet.exergy_efficiency_frac`` (EXG-KPI): it is defined for
  every candidate (C0 has no brine) and charges every input, so it stays ≤ 1, whereas η_u
  credits heat from gas and heat pumps to the brine and can exceed 1.

T₀ is the daily dry-bulb of TL-02b ``climate`` (``tools/climate.py``) and is stated with the KPI.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    SUMMARY_MAX_CHARS,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import AssumptionsRegister, load_config
from gemini_agent.schemas.results import AnnualResult, KPISet
from gemini_agent.tools.climate import ClimateProfile, build_climate_profile

TOOL_ID = "TL-14"
KELVIN_OFFSET = 273.15
J_PER_MWH = 3.6e9
SECONDS_PER_DAY = 86400.0
ASSUMPTION_DEAD_STATE = "DB-34d"
ASSUMPTION_FUEL_EXERGY = "EXG-FUEL"
ASSUMPTION_BRINE_CP = "DB-33"
ASSUMPTION_HEATING_RETURN = "DB-31"
ASSUMPTION_COOLING_RETURN = "DB-32"
AMBIENT_TOLERANCE_K = 0.5
"""Warn when the simulated day's ambient differs from the climate-profile T₀ by more."""
ASSUMPTION_IDS = ["DB-34d", "EXG-FUEL", "EXG-KPI", "DB-33", "DB-31", "DB-32"]


# --------------------------------------------------------------------------- equations
def _check_kelvin(*temperatures_k: float) -> None:
    if any(t <= 0 for t in temperatures_k):
        raise ValueError("temperatures must be absolute (K) and > 0")


def liquid_specific_exergy_j_per_kg(t_k: float, t0_k: float, c_j_per_kg_k: float) -> float:
    """``e = c [(T − T₀) − T₀ ln(T/T₀)]`` (RC-11, DiPippo eq 10.11–10.12, incompressible)."""
    _check_kelvin(t_k, t0_k)
    return c_j_per_kg_k * ((t_k - t0_k) - t0_k * math.log(t_k / t0_k))


def heat_exergy_w(q_w: float, t_k: float, t0_k: float) -> float:
    """``E_Q = Q (1 − T₀/T)`` (RC-11, DiPippo eq 10.15)."""
    _check_kelvin(t_k, t0_k)
    return q_w * (1.0 - t0_k / t_k)


def stream_heat_exergy_w(
    mass_flow_kg_s: float, c_j_per_kg_k: float, t_in_k: float, t_out_k: float, t0_k: float
) -> float:
    """``E_heat = ṁ c [(T_in − T_out) − T₀ ln(T_in/T_out)]`` (RC-11, DiPippo eq 9.28–9.29)."""
    _check_kelvin(t_in_k, t_out_k, t0_k)
    return mass_flow_kg_s * c_j_per_kg_k * ((t_in_k - t_out_k) - t0_k * math.log(t_in_k / t_out_k))


def delivered_heat_exergy_factor(t_supply_k: float, t_return_k: float, t0_k: float) -> float:
    """``E_heat/Q = 1 − T₀ ln(T_s/T_r)/(T_s − T_r)`` for a network cooled from T_s to T_r."""
    _check_kelvin(t_supply_k, t_return_k, t0_k)
    if t_supply_k <= t_return_k:
        raise ValueError("supply must be hotter than return")
    return 1.0 - t0_k * math.log(t_supply_k / t_return_k) / (t_supply_k - t_return_k)


def log_mean_temperature_k(t1_k: float, t2_k: float) -> float:
    """Log-mean of two absolute temperatures (arithmetic mean limit when equal)."""
    _check_kelvin(t1_k, t2_k)
    if math.isclose(t1_k, t2_k):
        return 0.5 * (t1_k + t2_k)
    return (t1_k - t2_k) / math.log(t1_k / t2_k)


def cold_exergy_w(q_cool_w: float, t_supply_k: float, t_return_k: float, t0_k: float) -> float:
    """``E_cold = Q (T₀/T_lm − 1)`` when the log-mean T_lm < T₀, else 0 (RC-11)."""
    t_lm = log_mean_temperature_k(t_supply_k, t_return_k)
    _check_kelvin(t0_k)
    return q_cool_w * (t0_k / t_lm - 1.0) if t_lm < t0_k else 0.0


# --------------------------------------------------------------------------- KPI
class ExergyReport(StrictModel):
    """Annual exergy balance of one candidate (MWh) and the two efficiencies."""

    candidate_id: str
    dead_state: str = Field(description="T₀ statement (DB-34d), copied to KPISet.")
    t0_mean_degc: float | None
    t0_min_degc: float | None
    t0_max_degc: float | None
    heat_delivered_mwh: float
    heat_exergy_mwh: float
    cool_delivered_mwh: float
    cold_exergy_mwh: float
    cold_exergy_days: int = Field(description="Days with log-mean chilled temperature below T₀.")
    electricity_consumed_mwh: float
    electricity_generated_mwh: float
    fuel_lhv_mwh: float
    fuel_exergy_factor: float
    fuel_exergy_mwh: float
    brine_exergy_mwh: float = Field(description="Σ ṁ e(T_production) Δt (exergy produced).")
    brine_exergy_reinjected_mwh: float = Field(description="Σ ṁ e(T_injection) Δt.")
    utilisation_frac: float | None
    functional_efficiency_frac: float | None
    ambient_mismatch_days: int = Field(
        description="Days where DayResult.ambient_dry_bulb_degc differs from T0 by > 0.5 K."
    )
    kpis: KPISet


def _register_float(register: AssumptionsRegister, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"assumption {assumption_id} must be a number, got {value!r}")
    return float(value)


def _given(value: float | None, default: float) -> float:
    return default if value is None else value


def exergy_balance(
    annual: AnnualResult,
    dry_bulb_degc: Sequence[float],
    *,
    brine_density_kg_m3: float,
    brine_cp_j_per_kg_k: float,
    heating_supply_degc: float,
    heating_return_degc: float,
    cooling_supply_degc: float,
    cooling_return_degc: float,
    fuel_exergy_factor: float,
    dead_state: str,
    base_kpis: KPISet | None = None,
) -> ExergyReport:
    """Annual exergy terms from the daily results; T₀ = ``dry_bulb_degc[day_index]`` (DB-34d).

    Network temperatures come from each ``DayResult`` when set, else the given defaults
    (DB-16/DB-31 heating, DB-17/DB-32 cooling). The exergy fields are written into
    ``base_kpis`` (default ``annual.kpis``), so pass the economics KPIs to merge both.
    """
    totals = dict.fromkeys(
        ("heat", "e_heat", "cool", "e_cold", "w_in", "w_out", "fuel", "e_brine", "e_inj"), 0.0
    )
    cold_days = 0
    ambient_mismatch = 0
    t0_used: list[float] = []
    for day in annual.days:
        if day.day_index >= len(dry_bulb_degc):
            raise ValueError(f"no dry-bulb temperature for day {day.day_index}")
        t0 = dry_bulb_degc[day.day_index] + KELVIN_OFFSET
        t0_used.append(t0 - KELVIN_OFFSET)
        if abs(day.ambient_dry_bulb_degc - t0_used[-1]) > AMBIENT_TOLERANCE_K:
            ambient_mismatch += 1
        heat_mwh = day.heat_supplied_mw * HOURS_PER_DAY
        if heat_mwh > 0:
            t_s = _given(day.heating_supply_degc, heating_supply_degc) + KELVIN_OFFSET
            t_r = _given(day.heating_return_degc, heating_return_degc) + KELVIN_OFFSET
            totals["e_heat"] += heat_mwh * delivered_heat_exergy_factor(t_s, t_r, t0)
        totals["heat"] += heat_mwh
        cool_mwh = day.cool_supplied_mw * HOURS_PER_DAY
        if cool_mwh > 0:
            t_s = _given(day.cooling_supply_degc, cooling_supply_degc) + KELVIN_OFFSET
            t_r = _given(day.cooling_return_degc, cooling_return_degc) + KELVIN_OFFSET
            e_cold = cold_exergy_w(cool_mwh, t_s, t_r, t0)
            cold_days += e_cold > 0
            totals["e_cold"] += e_cold
        totals["cool"] += cool_mwh
        totals["w_in"] += day.electricity_consumed_mw * HOURS_PER_DAY
        totals["w_out"] += day.electricity_generated_mw * HOURS_PER_DAY
        totals["fuel"] += sum(day.gas_fuel_by_consumer_mw.values()) * HOURS_PER_DAY
        for i, flow_m3h in enumerate(day.doublet_flow_m3h):
            if flow_m3h <= 0:
                continue
            m_dot = flow_m3h * brine_density_kg_m3 / 3600.0
            mwh_per_w_day = SECONDS_PER_DAY / J_PER_MWH
            t_prod = day.production_temperature_degc[i] + KELVIN_OFFSET
            totals["e_brine"] += (
                m_dot
                * liquid_specific_exergy_j_per_kg(t_prod, t0, brine_cp_j_per_kg_k)
                * mwh_per_w_day
            )
            if day.injection_temperature_degc:
                t_inj = day.injection_temperature_degc[i] + KELVIN_OFFSET
                totals["e_inj"] += (
                    m_dot
                    * liquid_specific_exergy_j_per_kg(t_inj, t0, brine_cp_j_per_kg_k)
                    * mwh_per_w_day
                )
    fuel_exergy = fuel_exergy_factor * totals["fuel"]
    useful = totals["e_heat"] + totals["e_cold"]
    utilisation = None
    if totals["e_brine"] > 0:
        utilisation = (totals["w_out"] - totals["w_in"] + useful) / totals["e_brine"]
    spent = totals["e_brine"] + totals["w_in"] + fuel_exergy
    functional = (useful + totals["w_out"]) / spent if spent > 0 else None
    base = base_kpis if base_kpis is not None else annual.kpis
    if base is not None and base.candidate_id != annual.candidate_id:
        raise ValueError("base_kpis.candidate_id does not match annual.candidate_id")
    kpis = KPISet.model_validate(
        (base.model_dump() if base else {"candidate_id": annual.candidate_id})
        | {
            "exergy_efficiency_frac": functional,
            "exergy_utilisation_frac": utilisation,
            "exergy_dead_state": dead_state,
        }
    )
    return ExergyReport(
        candidate_id=annual.candidate_id,
        dead_state=dead_state,
        t0_mean_degc=sum(t0_used) / len(t0_used) if t0_used else None,
        t0_min_degc=min(t0_used, default=None),
        t0_max_degc=max(t0_used, default=None),
        heat_delivered_mwh=totals["heat"],
        heat_exergy_mwh=totals["e_heat"],
        cool_delivered_mwh=totals["cool"],
        cold_exergy_mwh=totals["e_cold"],
        cold_exergy_days=cold_days,
        electricity_consumed_mwh=totals["w_in"],
        electricity_generated_mwh=totals["w_out"],
        fuel_lhv_mwh=totals["fuel"],
        fuel_exergy_factor=fuel_exergy_factor,
        fuel_exergy_mwh=fuel_exergy,
        brine_exergy_mwh=totals["e_brine"],
        brine_exergy_reinjected_mwh=totals["e_inj"],
        utilisation_frac=utilisation,
        functional_efficiency_frac=functional,
        ambient_mismatch_days=ambient_mismatch,
        kpis=kpis,
    )


def _dead_state_text(profile: ClimateProfile) -> str:
    return (
        f"T0 = daily dry-bulb (DB-34d), {profile.station} {profile.year}: mean "
        f"{profile.annual_mean_dry_bulb_degc:.1f} °C, range {profile.coldest_day.dry_bulb_degc:.1f}"
        f"–{profile.warmest_day.dry_bulb_degc:.1f} °C"
    )


def exergy_kpi(
    annual: AnnualResult,
    *,
    climate: ClimateProfile | None = None,
    config_dir: Path | str | None = None,
    base_kpis: KPISet | None = None,
) -> ToolResult[ExergyReport]:
    """TL-14 ``exergy``: annual exergy KPI with T₀ = daily dry-bulb from ``climate`` (DB-34d).

    ``climate`` defaults to the TL-02b profile built from ``config/climate_nl.yaml``; its year
    must match ``annual.year``.
    """
    register = load_config("assumptions", config_dir)
    basis = load_config("design_basis", config_dir)
    dead_state_rule = register.get(ASSUMPTION_DEAD_STATE).value
    if dead_state_rule != "daily_dry_bulb":
        raise ValueError(f"DB-34d = {dead_state_rule!r}; only 'daily_dry_bulb' is implemented")
    if climate is None:
        climate, _ = build_climate_profile(load_config("climate_nl", config_dir), annual.year)
    if climate.year != annual.year:
        raise ValueError(f"climate year {climate.year} != annual year {annual.year}")
    report = exergy_balance(
        annual,
        climate.dry_bulb_degc,
        brine_density_kg_m3=basis.brine.density_kg_m3,
        brine_cp_j_per_kg_k=_register_float(register, ASSUMPTION_BRINE_CP),
        heating_supply_degc=basis.heating.supply_temperature_degc,
        heating_return_degc=_register_float(register, ASSUMPTION_HEATING_RETURN),
        cooling_supply_degc=basis.cooling.supply_temperature_degc,
        cooling_return_degc=_register_float(register, ASSUMPTION_COOLING_RETURN),
        fuel_exergy_factor=_register_float(register, ASSUMPTION_FUEL_EXERGY),
        dead_state=_dead_state_text(climate),
        base_kpis=base_kpis,
    )

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{100 * value:.1f} %"

    summary = (
        f"{annual.candidate_id}: exergy efficiency {pct(report.functional_efficiency_frac)} "
        f"(utilisation {pct(report.utilisation_frac)}); E_heat {report.heat_exergy_mwh:.0f} MWh "
        f"of {report.heat_delivered_mwh:.0f} MWh heat, E_cold {report.cold_exergy_mwh:.0f} MWh; "
        f"T0 = daily dry-bulb (DB-34d)"
    )
    warnings = []
    if report.ambient_mismatch_days:
        warnings.append(
            f"{report.ambient_mismatch_days} day(s) with simulated ambient ≠ climate T0 by > "
            f"{AMBIENT_TOLERANCE_K} K: dead state from the climate profile (DB-34d)"
        )
    if report.cool_delivered_mwh > 0 and report.cold_exergy_days == 0:
        warnings.append("cooling delivered above T0 on every day: cold exergy 0 (RC-11)")
    return ToolResult[ExergyReport](
        data=report,
        summary=summary[:SUMMARY_MAX_CHARS],
        warnings=warnings,
        assumption_ids=list(ASSUMPTION_IDS),
        sources=["RC-11", "spec §10.1", "DB-34d"],
    )


__all__ = [
    "TOOL_ID",
    "ExergyReport",
    "cold_exergy_w",
    "delivered_heat_exergy_factor",
    "exergy_balance",
    "exergy_kpi",
    "heat_exergy_w",
    "liquid_specific_exergy_j_per_kg",
    "log_mean_temperature_k",
    "stream_heat_exergy_w",
]
