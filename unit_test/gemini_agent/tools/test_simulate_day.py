"""Tests for TL-11 ``simulate_day`` and its DesignVerifier evidence (WP-06; spec §6, §10.1)."""

from __future__ import annotations

import datetime as dt
import functools

import pytest

from gemini_agent.schemas.candidate import EQUIPMENT_SIZE_SUFFIXES
from gemini_agent.schemas.common import HOURS_PER_DAY, SUMMARY_MAX_CHARS
from gemini_agent.tools.config_generator import PlantDispatch, load_generator_basis
from gemini_agent.tools.design_basis import brine_heat_mw
from gemini_agent.tools.resource import load_subsurface_basis
from gemini_agent.tools.simulate_day import (
    DayConditions,
    SimulateDayRequest,
    day_conditions,
    simulate_day,
    simulate_plant_day,
    verification_evidence,
)
from gemini_agent.verify.design_verifier import CheckStatus, DesignVerifier
from unit_test.gemini_agent.tools.plant_factory import CONCEPT_CASES, make_candidate

SUMMER = dt.date(2027, 7, 19)


@functools.cache
def bases():
    """Load the generator and subsurface bases once."""
    return load_generator_basis(), load_subsurface_basis()


@functools.cache
def days():
    """DB-18 peak heating day and DB-19 peak cooling day (CSV + climate profile)."""
    return day_conditions(), day_conditions(SUMMER)


@functools.cache
def simulated(concept, doublets, depth, parent, which="peak", flow=None):
    """One simulated day of a concept case (cached)."""
    gb, sub = bases()
    candidate = make_candidate(concept, doublets, depth_m=depth, parent_id=parent)
    day = days()[0 if which == "peak" else 1]
    dispatch = PlantDispatch(doublet_flows_m3h=[flow] * doublets) if flow else None
    return candidate, simulate_plant_day(candidate, day, dispatch, gb=gb, sub=sub)


def test_day_conditions_from_csv_and_climate():
    """Day conditions come from the demand CSVs and the TL-02b climate profile."""
    peak, summer = days()
    assert peak.date == dt.date(2027, 1, 28) and peak.day_index == 27
    assert peak.heating_demand_mw == 30.0
    assert summer.cooling_demand_mw == 10.0 and summer.day_index == 199
    assert summer.ambient_wet_bulb_degc < summer.ambient_dry_bulb_degc
    with pytest.raises(ValueError, match="does not match"):
        DayConditions(
            date=dt.date(2027, 1, 2),
            day_index=0,
            heating_demand_mw=1.0,
            cooling_demand_mw=0.0,
            ambient_dry_bulb_degc=5.0,
            ambient_wet_bulb_degc=4.0,
        )


def _balances(sim, gb):
    day = sim.day
    for label, demand, supplied, unmet in (
        ("heat", day.heating_demand_mw, day.heat_supplied_mw, day.unmet_heating_mwh),
        ("cool", day.cooling_demand_mw, day.cool_supplied_mw, day.unmet_cooling_mwh),
    ):
        covered = (
            supplied * HOURS_PER_DAY
            + unmet
            - sim.heat_surplus_mw * HOURS_PER_DAY * (label == "heat")
        )
        assert covered == pytest.approx(demand * HOURS_PER_DAY, rel=1e-6, abs=1e-6), label
    available = sum(
        brine_heat_mw(q, 1030.0, 4100.0, tp - ti)
        for q, tp, ti in zip(
            day.doublet_flow_m3h,
            day.production_temperature_degc,
            day.injection_temperature_degc,
            strict=True,
        )
    )
    # 1e-4: HeatpumpBasic solves its energy balance by Nelder–Mead (verifier PH-22 allows 1 %).
    assert sum(day.brine_heat_drawn_mw.values()) == pytest.approx(available, rel=1e-4, abs=1e-9)
    for key, drawn in day.brine_heat_drawn_mw.items():
        if key in day.heat_by_source_mw:
            expected = drawn + day.electricity_by_consumer_mw.get(key, 0.0)
            assert day.heat_by_source_mw[key] == pytest.approx(expected, rel=1e-9)
    for q, q_inj in zip(day.doublet_flow_m3h, day.injection_flow_m3h, strict=True):
        assert q_inj == q
    for tp, ti in zip(day.production_temperature_degc, day.injection_temperature_degc, strict=True):
        assert tp - ti <= gb.brine_cooling_max_k + 1e-6 or day.margins
    assert all(key.endswith(EQUIPMENT_SIZE_SUFFIXES) for key in day.margins)


@pytest.mark.parametrize("which", ["peak", "summer"])
@pytest.mark.parametrize(("concept", "doublets", "depth", "parent"), CONCEPT_CASES)
def test_every_generated_config_runs_one_day(concept, doublets, depth, parent, which):
    """Every generated config runs one day; heat, cooling and brine balances close (PH-01/22)."""
    _, sim = simulated(concept, doublets, depth, parent, which)
    assert sim.valid, sim.failures
    gb, _ = bases()
    _balances(sim, gb)
    day = sim.day
    assert len(day.doublet_flow_m3h) == doublets
    if day.heat_supplied_mw >= day.heating_demand_mw - 1e-9:
        assert day.heating_supply_degc >= 100.0 - 1e-6
    if day.cooling_demand_mw > 0:
        assert day.cooling_supply_degc == 20.0
    if doublets:
        assert sim.esps and sim.pumps and day.co2_by_source_kg
    assert sim.config.config_hash


def test_peak_day_c2_two_doublets_meets_demand_without_boiler():
    """C2 with 2 doublets covers the 30 MW peak with HEX + HP + CHP, no boiler."""
    _, sim = simulated("C2", 2, 2500.0, None)
    day = sim.day
    assert day.unmet_heating_mwh == 0.0 and sim.heat_surplus_mw == 0.0
    assert day.heating_supply_degc == pytest.approx(100.0, abs=1e-6)
    assert day.heat_by_source_mw["peak_boiler"] == pytest.approx(0.0, abs=1e-6)
    assert day.heat_by_source_mw["hthp_1"] > 0 and sim.hp_flow_frac == 1.0
    assert day.electricity_generated_mw > 0  # CHP on associated gas
    assert day.gas_fuel_by_consumer_mw["chp"] == pytest.approx(2 * 1.91, abs=0.04)
    # 2 doublets × 300 m³/h × 0.8 Nm³/m³ × 24 h
    assert day.associated_gas_produced_nm3 == pytest.approx(2 * 300 * 0.8 * 24, rel=0.01)


def test_esp_power_follows_rc01_rule_on_gemini_pressures():
    """ESP power = Q·Δp/0.70/(η_motor η_VFD) on GEMINI's intake/discharge pressures (DB-40)."""
    _, sim = simulated("C1", 1, 2500.0, None)
    _, sub = bases()
    esp = next(p for p in sim.pumps if p.name == "esp_1")
    out = sim.outputs["producer_well_1"]
    dp_pa = (out["esp_discharge_pressure"] - out["esp_intake_pressure"]) * 1e5
    assert esp.pressure_rise_pa == pytest.approx(dp_pa)
    assert esp.shaft_power_w == pytest.approx(esp.flow_m3h / 3600 * dp_pa / 0.70)
    electric = sim.day.electricity_by_consumer_mw["esp_1"] * 1e6
    assert electric == pytest.approx(esp.shaft_power_w / (sub.motor_efficiency_frac * 0.97))
    injection = next(p for p in sim.pumps if p.name == "injection_pump_1")
    assert injection.shaft_power_w == pytest.approx(
        injection.flow_m3h / 3600 * injection.pressure_rise_pa / 0.70
    )


def test_peak_boiler_fires_to_100c_and_reports_unmet_above_nameplate():
    """The peak boiler holds 100 °C up to its nameplate; the rest is unmet heat."""
    gb, sub = bases()
    peak, _ = days()
    _, sim = simulated("C0", 0, 2500.0, None)
    assert sim.day.heat_by_source_mw == {"peak_boiler": pytest.approx(30.0)}
    assert sim.day.gas_fuel_by_consumer_mw["peak_boiler"] == pytest.approx(30.0 / 0.90)
    assert sim.day.co2_by_source_kg["peak_boiler"] == pytest.approx(
        30.0 / 0.90 * 1e6 * 56.6e-9 * 86400
    )
    small = make_candidate("C0", 0, equipment_sizes={"peak_boiler_mw": 20.0})
    limited = simulate_plant_day(small, peak, gb=gb, sub=sub)
    assert limited.day.unmet_heating_mwh == pytest.approx(10.0 * 24)
    assert limited.day.margins["heating_supply_margin_k"] < 0


def test_heat_pumps_are_bypassed_when_the_brine_cannot_supply_them():
    """Source-limited heat pumps get a reduced district share instead of a diverged solve."""
    gb, sub = bases()
    peak, _ = days()
    candidate = make_candidate("C2", 1)
    sim = simulate_plant_day(candidate, peak, gb=gb, sub=sub)
    assert sim.valid and 0.0 < sim.hp_flow_frac < 1.0
    assert any("bypassed" in w for w in sim.warnings)
    brine_dt = sim.day.production_temperature_degc[0] - sim.day.injection_temperature_degc[0]
    assert brine_dt == pytest.approx(40.0, abs=0.05)  # source floor (DB-12) binds
    assert sim.day.heating_supply_degc == pytest.approx(100.0, abs=1e-6)
    raw = simulate_plant_day(candidate, peak, gb=gb, sub=sub, control_heat_pumps=False)
    assert not raw.valid and raw.day is None and "hthp_1" in raw.failures[0]


def test_doublet_off_reports_zero_flow():
    """An off doublet is reported unavailable with zero flow."""
    gb, sub = bases()
    _, summer = days()
    candidate = make_candidate("C2", 2)
    sim = simulate_plant_day(
        candidate, summer, PlantDispatch(doublet_flows_m3h=[100.0, 0.0]), gb=gb, sub=sub
    )
    assert sim.valid
    assert sim.day.doublet_available == [True, False]
    assert sim.day.doublet_flow_m3h[1] == 0.0
    assert sim.day.doublet_flow_m3h[0] == pytest.approx(100.0, rel=0.01)


def test_tool_wrapper_summary():
    """TL-11 tool result: summary ≤ 300 characters, DB-37 noted (grid CO₂ left to WP-08)."""
    result = simulate_day(SimulateDayRequest(candidate=make_candidate("C3", 2)))
    assert result.data.valid
    assert result.summary.startswith("TL-11 C3-2d 2027-01-28")
    assert len(result.summary) <= SUMMARY_MAX_CHARS
    assert "DB-37" in result.assumption_ids


# --------------------------------------------------------------------------- verifier contract
def test_verification_evidence_from_simulate_day_results():
    """Interface contract with WP-09: field names, bar(g) p_b, margins, evidence types."""
    candidate, peak = simulated("C2", 2, 2500.0, None)
    _, summer = simulated("C2", 2, 2500.0, None, "summer", 50.0)  # summer at minimum flow
    evidence = verification_evidence(candidate, [summer, peak])
    assert [p.name for p in evidence.hex_points] == ["hex_1", "hex_2"]  # design points
    assert [d.day_index for d in evidence.annual.days] == [27, 199]
    assert evidence.stub_evidence is False and evidence.project == peak.config.project
    verifier = DesignVerifier.from_config()
    # bare bubble point in bar(g); verifier margin reproduces TL-05's intake setpoint
    p_b = evidence.co2_release_pressure_barg
    assert p_b == pytest.approx(45.04, abs=0.05)
    setpoint = peak.config.doublets[0].esp_intake_setpoint_barg
    assert verifier.required_pressure_barg(p_b) == pytest.approx(setpoint, rel=1e-9)
    assert all(e.release_pressure_barg == p_b for e in evidence.esps)
    report = verifier.verify(evidence)
    status = {c.check_id: c.status for c in report.checks}
    for check_id in (
        "SY-01", "SY-02", "SY-03", "SE-01", "SE-02", "SE-03", "SE-04",
        "PH-01", "PH-02", "PH-03", "PH-04", "PH-05", "PH-06", "PH-07", "PH-08", "PH-09",
        "PH-17", "PH-18", "PH-22", "PH-24", "PD-08",
    ):  # fmt: skip
        assert status[check_id] == CheckStatus.PASS, (check_id, report.summary)
    assert status["PD-10"] == CheckStatus.NOT_APPLICABLE  # degassed loop
    failed = [c.check_id for c in report.checks if c.status == CheckStatus.FAIL]
    assert not failed, [c.detail for c in report.checks if c.status == CheckStatus.FAIL]


def test_c7_evidence_fails_plate_hex_pressure_rating():
    """The C7 flag is consistent with the verifier: PH-09 fails, design REJECTED."""
    candidate, sim = simulated("C7", 2, 2500.0, "C2-2d")
    report = DesignVerifier.from_config().verify(verification_evidence(candidate, [sim]))
    status = {c.check_id: c.status for c in report.checks}
    assert status["PH-09"] == CheckStatus.FAIL
    assert report.verdict.value == "REJECTED"


def test_verification_evidence_refuses_invalid_or_foreign_days():
    """Evidence is refused for invalid days, another candidate's days or no days."""
    gb, sub = bases()
    peak, _ = days()
    candidate = make_candidate("C2", 1)
    raw = simulate_plant_day(candidate, peak, gb=gb, sub=sub, control_heat_pumps=False)
    with pytest.raises(ValueError, match="invalid"):
        verification_evidence(candidate, [raw])
    other, sim = simulated("C1", 1, 2500.0, None)
    with pytest.raises(ValueError, match="given for"):
        verification_evidence(candidate, [sim])
    with pytest.raises(ValueError, match="no simulated days"):
        verification_evidence(other, [])
