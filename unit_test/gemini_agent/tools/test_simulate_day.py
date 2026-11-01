"""Tests for TL-11 ``simulate_day`` and its DesignVerifier evidence (WP-06; spec §6, §10.1)."""

from __future__ import annotations

import datetime as dt
import functools

import pytest

from gemini_agent.schemas.candidate import EQUIPMENT_SIZE_SUFFIXES
from gemini_agent.schemas.common import HOURS_PER_DAY, SUMMARY_MAX_CHARS
from gemini_agent.tools.config_generator import (
    PlantDispatch,
    generate_plant,
    load_generator_basis,
)
from gemini_agent.tools.design_basis import brine_heat_mw
from gemini_agent.tools.resource import load_subsurface_basis
from gemini_agent.tools.simulate_day import (
    DayConditions,
    SimulateDayRequest,
    brine_cooling_failures,
    day_conditions,
    simulate_config_day,
    simulate_day,
    simulate_plant_day,
    verification_evidence,
)
from gemini_agent.verify.design_verifier import CheckStatus, DesignVerifier
from gemini_simulator.project_loader import build_project
from unit_test.gemini_agent.tools.plant_factory import (
    CONCEPT_CASES,
    make_candidate,
)

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
        assert tp - ti <= gb.brine_cooling_max_k + 1e-6  # DB-12 is a hard limit
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


def test_closed_form_heat_pumps_run_a_c2_day_without_the_workaround():
    """C2 design case: WP-03 closed form (Lorenz, HP-ETA-L), no bypass, COP in RC-04's 3.5–4.5."""
    _, sim = simulated("C2", 2, 2500.0, None)
    assert sim.valid and sim.hp_flow_frac == 1.0 and not sim.controls
    assert not any("bypass" in w for w in sim.warnings)
    for name in ("hthp_1", "hthp_2"):
        out = sim.outputs[name]
        assert sim.day.margins[f"{name}_rc04_limits_margin_count"] == 0.0
        assert (out["solver"], out["running"], out["within_limits"]) == ("closed_form", True, True)
        assert 3.5 <= out["COP"] <= 4.5  # RC-04 typical HTHP band at the design lift
        assert out["Th_out"] == pytest.approx(100.0)
        assert sim.day.brine_heat_drawn_mw[name] == pytest.approx(out["source_heat_w"] / 1e6)
        params = sim.config.project["components"][name]["parameters"]
        assert (params["solver"], params["mode"], params["eta_lorenz"]) == (
            "closed_form",
            "lorenz",
            0.5,
        )
    assert [p.name for p in sim.heat_pumps] == ["hthp_1", "hthp_2"]
    assert sim.heat_pumps[0].cop == pytest.approx(sim.outputs["hthp_1"]["COP"])


def test_source_limited_heat_pump_holds_the_brine_floor_without_bypass():
    """One doublet at the 30 MW peak: the HP is source-limited (DB-12 floor), the boiler trims."""
    gb, sub = bases()
    peak, _ = days()
    sim = simulate_plant_day(make_candidate("C2", 1), peak, gb=gb, sub=sub)
    assert sim.valid and sim.hp_flow_frac == 1.0 and not sim.controls
    out = sim.outputs["hthp_1"]
    assert out["operating_branch"] == "source_floor" and out["Th_out"] < 100.0
    brine_dt = sim.day.production_temperature_degc[0] - sim.day.injection_temperature_degc[0]
    assert brine_dt == pytest.approx(40.0, abs=1e-6)
    assert sim.day.heating_supply_degc == pytest.approx(100.0, abs=1e-6)
    assert sim.day.heat_by_source_mw["peak_boiler"] > 0


def test_missing_hthp_datasheet_is_flagged_every_heat_pump_day():
    """No HTHP datasheet is cited: the MISSING values are flagged in the day result."""
    _, sim = simulated("C2", 2, 2500.0, None)
    assert len(sim.day.flags) == 2
    assert all("HTHP datasheet MISSING" in flag for flag in sim.day.flags)
    assert all(f.within_limits is True and f.missing_inputs for f in sim.heat_pump_flags)


def test_out_of_limits_heat_pump_point_is_flagged_not_hidden():
    """A point outside its limits stays in the day result: flag, negative margin, PH-14 fails."""
    gb, sub = bases()
    peak, _ = days()
    candidate = make_candidate("C2", 2)
    config = generate_plant(candidate, peak.loads(), gb=gb, sub=sub)
    params = config.project["components"]["hthp_1"]["parameters"]
    # Test fixture only (no real datasheet): a 30 K lift limit the design point exceeds.
    params.update({"datasheet": "test fixture: hypothetical HTHP datasheet", "max_lift_k": 30.0})
    sim = simulate_config_day(candidate, peak, config, gb=gb, sub=sub)
    assert sim.valid and sim.day is not None
    flag = next(f for f in sim.heat_pump_flags if f.name == "hthp_1")
    assert flag.within_limits is False and any("lift" in r for r in flag.reasons)
    text = next(f for f in sim.day.flags if f.startswith("hthp_1"))
    assert "OUTSIDE RC-04 limits" in text
    assert sim.day.margins["hthp_1_lift_margin_k"] < 0
    assert sim.day.margins["hthp_1_rc04_limits_margin_count"] == -1.0
    assert sim.day.margins["hthp_2_rc04_limits_margin_count"] == 0.0
    assert "hthp_1_cop_cap_margin_frac" not in sim.day.margins  # the model enforces the cap
    point = next(p for p in sim.heat_pumps if p.name == "hthp_1")
    assert point.max_lift_k == 30.0
    report = DesignVerifier.from_config().verify(verification_evidence(candidate, [sim]))
    status = {c.check_id: c.status for c in report.checks}
    assert status["PH-14"] == CheckStatus.FAIL and status["PH-21"] == CheckStatus.FAIL


def test_heat_pump_bypass_is_only_a_capacity_control():
    """With an HP nameplate the share through the HPs is cut to it; the reason is recorded."""
    gb, sub = bases()
    peak, _ = days()
    small = make_candidate("C2", 2, equipment_sizes={"hthp_heating_mw": 6.0})
    sim = simulate_plant_day(small, peak, gb=gb, sub=sub)
    assert sim.valid and sim.hp_flow_frac < 1.0
    assert any("capacity control" in c for c in sim.controls)
    hp_heat = sim.day.heat_by_source_mw["hthp_1"] + sim.day.heat_by_source_mw["hthp_2"]
    assert hp_heat <= 6.0 + 1e-6 and hp_heat > 5.9
    assert sim.day.margins["hthp_capacity_margin_mw"] >= -1e-6
    assert sim.day.heating_supply_degc == pytest.approx(100.0, abs=1e-6)
    free = simulate_plant_day(small, peak, gb=gb, sub=sub, control_heat_pump_capacity=False)
    assert free.hp_flow_frac == 1.0 and free.day.margins["hthp_capacity_margin_mw"] < 0


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
        "PH-13", "PH-14", "PH-17", "PH-18", "PH-22", "PH-24", "PD-08",
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
    candidate = make_candidate("C4", 2, depth_m=3200.0)
    raw = simulate_plant_day(candidate, peak, gb=gb, sub=sub, control_plate_hex=False)
    assert not raw.valid and raw.day is None and "DB-12" in raw.failures[0]
    with pytest.raises(ValueError, match="invalid"):
        verification_evidence(candidate, [raw])
    other, sim = simulated("C1", 1, 2500.0, None)
    with pytest.raises(ValueError, match="given for"):
        verification_evidence(candidate, [sim])
    with pytest.raises(ValueError, match="no simulated days"):
        verification_evidence(other, [])


# --------------------------------------------------------------------------- reviewer findings
def test_hot_brine_plate_hex_is_held_to_db12_and_100c():
    """C4 at 3.2 km on the peak day: the plate-HEX share is cut so ΔT ≤ 40 K and supply = 100 °C."""
    gb, sub = bases()
    peak, _ = days()
    candidate = make_candidate("C4", 2, depth_m=3200.0)
    sim = simulate_plant_day(candidate, peak, gb=gb, sub=sub)
    assert sim.valid and sim.hex_flow_frac < 1.0
    for tp, ti in zip(
        sim.day.production_temperature_degc, sim.day.injection_temperature_degc, strict=True
    ):
        assert tp - ti <= 40.0 + 1e-6
    assert sim.day.heating_supply_degc == pytest.approx(100.0, abs=1e-5)
    assert sim.heat_surplus_mw == 0.0
    assert any("plate-HEX share reduced" in c for c in sim.controls)
    assert any("flashing is not checked" in w for w in sim.warnings)  # 105 °C let down to 0 bar(g)
    off_design = simulate_plant_day(
        make_candidate("C1", 1, depth_m=3200.0),
        day_conditions(dt.date(2027, 4, 15)),
        PlantDispatch(doublet_flows_m3h=[100.0]),
        gb=gb,
        sub=sub,
    )
    brine_dt = off_design.day.production_temperature_degc[0] - (
        off_design.day.injection_temperature_degc[0]
    )
    assert off_design.valid and brine_dt <= 40.0 + 1e-6


def test_uncontrolled_hex_breaks_db12_and_is_a_failure():
    """Without the plate-HEX control the C4 peak day cools the brine 41.3 K: a failure."""
    gb, _ = bases()
    peak, _ = days()
    config = generate_plant(make_candidate("C4", 2, depth_m=3200.0), peak.loads(), gb=gb)
    solver = build_project(config.project)
    solver.run()
    failures = brine_cooling_failures(solver, config, gb)
    assert len(failures) == 2 and "DB-12: doublet 1 brine cooled 41.3" in failures[0]


def test_chp_surplus_is_reported_when_all_gas_must_be_burned():
    """C1 × 3 at design flow in summer: the CHP alone exceeds the demand (DB-36: no venting)."""
    _, sim = simulated("C1", 3, 2500.0, None, "summer")
    assert sim.valid and sim.hex_flow_frac == 0.0
    assert sim.heat_surplus_mw > 0.5
    assert sim.day.margins["heat_surplus_margin_mw"] == pytest.approx(-sim.heat_surplus_mw)
    assert any("lower the doublet flows" in w for w in sim.warnings)


def test_candidate_return_temperatures_are_used():
    """DB-31/DB-32 come from the candidate (user answer), not silently from the register."""
    gb, sub = bases()
    _, summer = days()
    warm = make_candidate("C2", 2)
    cold = warm.model_copy(update={"heating_return_degc": 45.0, "cooling_return_degc": 24.0})
    dispatch = PlantDispatch(doublet_flows_m3h=[50.0, 50.0])
    cold_sim = simulate_plant_day(cold, summer, dispatch, gb=gb, sub=sub)
    warm_sim = simulate_plant_day(warm, summer, dispatch, gb=gb, sub=sub)
    assert cold_sim.config.config_hash != warm_sim.config.config_hash
    assert cold_sim.config.hex_design.district_in_degc == 45.0
    assert cold_sim.day.heating_return_degc == 45.0 and cold_sim.day.cooling_return_degc == 24.0
    chiller = cold_sim.config.project["components"]["electric_chiller"]["parameters"]
    assert chiller["chilled_water_temperature_in_degc"] == 24.0
    assert cold_sim.config.district_flow_m3s == pytest.approx(
        warm_sim.config.district_flow_m3s * 45.0 / 55.0
    )


def test_heat_pump_uses_register_approaches_and_flags_sink_limit():
    """HP-APPROACH reaches the model; a datasheet sink-temperature limit becomes a margin."""
    gb, sub = bases()
    peak, _ = days()
    candidate = make_candidate("C2", 2)
    config = generate_plant(candidate, peak.loads(), gb=gb, sub=sub)
    params = config.project["components"]["hthp_1"]["parameters"]
    assert (params["condenser_approach_k"], params["evaporator_approach_k"]) == (4.0, 4.0)
    # Test fixture only: a sink limit below the 100 °C target the point must reach.
    params.update(
        {
            "datasheet": "test fixture: hypothetical HTHP datasheet",
            "max_sink_temperature_degc": 95.0,
        }
    )
    sim = simulate_config_day(candidate, peak, config, gb=gb, sub=sub)
    assert sim.day.margins["hthp_1_sink_temperature_margin_k"] == pytest.approx(-5.0)
    assert sim.day.margins["hthp_1_rc04_limits_margin_count"] == -1.0
    assert any(f.startswith("hthp_1") and "OUTSIDE" in f for f in sim.day.flags)


def test_idle_heat_pump_below_its_floor_is_valid():
    """Physics check: brine already below the HP floor (35 °C return, DB-12 control) → HP idles."""
    gb, sub = bases()
    peak, _ = days()
    cold = make_candidate("C2", 2).model_copy(update={"heating_return_degc": 35.0})
    sim = simulate_plant_day(
        cold, peak, PlantDispatch(doublet_flows_m3h=[50.0, 50.0]), gb=gb, sub=sub
    )
    assert sim.valid, sim.failures
    out = sim.outputs["hthp_1"]
    assert out["running"] is False and out["power_th"] == 0.0
    params = sim.config.project["components"]["hthp_1"]["parameters"]
    assert out["Ts_out"] < params["Ts_in_minimum"]  # passed unchanged, floor not applied
    assert "hthp_1_rc04_limits_margin_count" not in sim.day.margins  # idle: not a violation


def test_heat_pump_equipment_notes_and_cop_plausibility_are_surfaced():
    """RC-04 notes (multi-pass ΔT > 11 K, cap) and an out-of-band COP reach the warnings."""
    _, peak = simulated("C2", 2, 2500.0, None)
    assert any(w.startswith("hthp_1: ") and "11 K" in w for w in peak.warnings)
    _, summer = simulated("C2", 2, 2500.0, None, "summer")  # design flows: low-lift trickle
    out = summer.outputs["hthp_1"]
    assert out["running"] and out["COP"] > 4.5
    assert any("outside RC-04's typical HTHP band" in w for w in summer.warnings)
    assert not any("typical HTHP band" in w for w in peak.warnings)  # 3.86 at design
