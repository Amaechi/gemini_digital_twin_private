"""Tests for WP-07 dispatch: DB-30 calendar, fast plant model, merit order, storage ledger."""

from __future__ import annotations

import datetime as dt

import pytest

from gemini_agent.runner.design_runner import _db30_interpretation_b
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.demand import lowest_window, read_demand_csv
from gemini_agent.tools.dispatch import (
    AvailabilityCalendar,
    Db30Rule,
    FastPlant,
    StorageSpec,
    _absorption_drive_m3s,
    apply_storage_discharge,
    availability_calendar,
    db30_rule,
    dispatch_day,
    lowest_load_starts,
    storage_ledger,
    storage_spec,
)
from gemini_agent.tools.economics import classify_service
from gemini_agent.tools.simulate_day import day_conditions, simulate_plant_day
from unit_test.gemini_agent.tools.plant_factory import basis, make_candidate

HAND_C2 = {"hthp_heating_mw": 14.0, "peak_boiler_mw": 6.0, "electric_chiller_mw": 10.0}
"""WP-07 acceptance: hand-sized C2 (2 doublets, HTHP 14 MW, boiler 6 MW, chillers 10 MW)."""
_REGISTER = load_config("assumptions")
HP_MIN = float(_REGISTER.get("DSP-HP-MIN").value["min_part_load_frac"])  # type: ignore[index]
ABS_MIN = float(_REGISTER.get("DSP-ABS-MIN").value)  # type: ignore[arg-type]
PEAK, APRIL, JULY, OCTOBER = (dt.date(2027, m, d) for m, d in ((1, 15), (4, 15), (7, 15), (10, 1)))


@pytest.fixture(scope="module")
def map_cache(tmp_path_factory: pytest.TempPathFactory):
    """TL-06 maps cached once for this module (not under runs/)."""
    return tmp_path_factory.mktemp("doublet_maps")


@pytest.fixture(scope="module")
def plants(map_cache):
    """Fast plants of the concept cases used below (built once)."""
    gb, sub = basis()
    cases = {
        "C2": make_candidate("C2", 2, candidate_id="C2-hand", equipment_sizes=HAND_C2),
        "C3": make_candidate("C3", 2),
        "C4": make_candidate("C4", 2, depth_m=3200.0),
        "C7": make_candidate("C7", 2, parent_id="C2-2d"),
        "C0": make_candidate("C0", 0, equipment_sizes={"peak_boiler_mw": 6.0}),
        "C4-5MW": make_candidate(
            "C4",
            2,
            depth_m=3200.0,
            candidate_id="C4-5MW",
            equipment_sizes={"absorption_chiller_mw": 5.0},
        ),
        "C2-deep": make_candidate(
            "C2",
            2,
            depth_m=3200.0,
            candidate_id="C2-deep",
            equipment_sizes={"hthp_heating_mw": 4.0},
        ),
    }
    return {key: FastPlant(c, gb=gb, sub=sub, map_cache_dir=map_cache) for key, c in cases.items()}


def _dispatch(plant: FastPlant, date: dt.date, available=None, **kw):
    available = available if available is not None else [True] * plant.n_doublets
    return dispatch_day(
        plant,
        day_conditions(date),
        available,
        hp_min_frac=kw.pop("hp_min_frac", HP_MIN),
        absorption_min_frac=kw.pop("absorption_min_frac", ABS_MIN),
        **kw,
    )


# --------------------------------------------------------------------------- DB-30
def test_db30_rule_register_and_answers():
    """Register default A; answers['DB-30'] as the runner stores it, or 'A'/'B'."""
    register = load_config("assumptions")
    default = db30_rule(register=register)
    assert (default.interpretation, default.planned_outage_days_per_doublet) == ("A", 35)
    assert (default.demand_met_days_per_yr, default.source) == (365, "register")
    stored = _db30_interpretation_b(register.get("DB-30").value)
    for answer in (stored, "B", " b "):
        rule = db30_rule(answer, register=register)
        assert (rule.interpretation, rule.planned_outage_days_per_doublet) == ("B", 0)
        assert (rule.demand_met_days_per_yr, rule.source) == (330, "answer")
    assert db30_rule("A", register=register).interpretation == "A"
    assert db30_rule({"interpretation": "B"}, register=register).demand_met_days_per_yr == 330
    with pytest.raises(ValueError, match="A or B"):
        db30_rule("C", register=register)
    with pytest.raises(ValueError, match="no planned outage"):
        Db30Rule(interpretation="B", planned_outage_days_per_doublet=35, demand_met_days_per_yr=330)


def test_lowest_load_starts_greedy_non_overlapping():
    """First window = the lowest; the next is the lowest that does not overlap; no wrap."""
    load = [10.0] * 100
    load[20:30] = [1.0] * 10
    load[25:35] = [0.5] * 10
    starts = lowest_load_starts(load, 10, 2)
    assert starts[0] == 25
    assert abs(starts[1] - starts[0]) >= 10
    assert starts[1] == 15  # [15, 25) holds five 1.0 days: the lowest window left of 25
    with pytest.raises(ValueError, match="do not fit"):
        lowest_load_starts(load, 40, 3)
    assert lowest_load_starts(load, 10, 0) == []


def test_calendar_interpretation_a_lowest_load():
    """Heat-only placement: doublet 1 in TL-02's lowest 35-day window; never two off at once."""
    db = load_config("design_basis")
    heating = read_demand_csv(db.heating, db.year)
    rule = db30_rule()
    cal = availability_calendar(3, db.year, heating.tolist(), rule=rule)
    window = lowest_window(heating, 35)
    assert cal.outage_windows()[0] == (window.start_date, window.end_date)
    assert cal.outage_windows()[0] == (dt.date(2027, 7, 9), dt.date(2027, 8, 12))
    assert cal.placement == "lowest_load" and len(cal.simulated_day_indices) == 365
    for d in range(365):
        off = cal.available(d).count(False)
        assert off <= 1
    assert sum(cal.available(d).count(False) for d in range(365)) == 3 * 35
    assert AvailabilityCalendar.model_validate(cal.model_dump()) == cal


def test_calendar_given_starts_validated():
    """outage_start_indices is the optimiser lever; overlap or leaving the year is refused."""
    load = [1.0] * 365
    rule = db30_rule()
    cal = availability_calendar(2, 2027, load, rule=rule, outage_start_indices=[0, 330])
    assert cal.placement == "given"
    assert cal.available(0) == [False, True] and cal.available(330) == [True, False]
    assert cal.available(35) == [True, True] and cal.available(364) == [True, False]
    with pytest.raises(ValueError, match="overlap"):
        availability_calendar(2, 2027, load, rule=rule, outage_start_indices=[10, 30])
    with pytest.raises(ValueError, match="leaves the year"):
        availability_calendar(1, 2027, load, rule=rule, outage_start_indices=[331])
    with pytest.raises(ValueError, match="outage starts"):
        availability_calendar(1, 2027, load, rule=rule, outage_start_indices=[1, 100])
    with pytest.raises(ValueError, match="has 365"):
        availability_calendar(1, 2027, load[:300], rule=rule)


def test_calendar_interpretation_b():
    """B: no outage, 330 simulated days = the year minus its lowest-load 35-day window."""
    db = load_config("design_basis")
    heating = read_demand_csv(db.heating, db.year)
    cal = availability_calendar(2, db.year, heating.tolist(), rule=db30_rule("B"))
    assert cal.outage_start_indices == [] and cal.placement == "none"
    assert len(cal.simulated_day_indices) == 330 and cal.excluded_days == 35
    start = (lowest_window(heating, 35).start_date - dt.date(db.year, 1, 1)).days
    assert cal.excluded_start_index == start
    assert start not in cal.simulated_day_indices
    assert all(cal.available(d) == [True, True] for d in range(365))
    with pytest.raises(ValueError, match="no planned outage"):
        availability_calendar(
            2, db.year, heating.tolist(), rule=db30_rule("B"), outage_start_indices=[0, 40]
        )


# --------------------------------------------------------------------------- fast model
@pytest.mark.parametrize("concept", ["C2", "C3", "C4", "C7"])
@pytest.mark.parametrize("date", [PEAK, APRIL, JULY])
def test_fast_matches_full_per_source(plants, concept, date):
    """SIM-04: the fast plan replayed in GEMINI agrees per source within 2 % of demand."""
    plant = plants[concept]
    fast = _dispatch(plant, date)
    full = simulate_plant_day(
        plant.candidate,
        day_conditions(date),
        fast.plan.plant_dispatch(),
        gb=plant.gb,
        sub=plant.sub,
    )
    assert full.valid, full.failures
    f, g = fast.day, full.day
    assert g is not None
    for kind, a, b, demand in (
        ("heat", f.heat_by_source_mw, g.heat_by_source_mw, f.heating_demand_mw),
        ("cool", f.cool_by_source_mw, g.cool_by_source_mw, f.cooling_demand_mw),
    ):
        scale = max(demand, sum(b.values()))
        for key in set(a) | set(b):
            assert abs(a.get(key, 0.0) - b.get(key, 0.0)) <= 0.02 * scale, (kind, key)
    assert f.electricity_consumed_mw == pytest.approx(g.electricity_consumed_mw, rel=0.02)
    assert sum(f.brine_heat_drawn_mw.values()) == pytest.approx(
        sum(g.brine_heat_drawn_mw.values()), rel=0.02, abs=1e-3
    )
    assert f.associated_gas_produced_nm3 == pytest.approx(g.associated_gas_produced_nm3, rel=0.02)
    assert set(f.electricity_by_consumer_mw) == set(g.electricity_by_consumer_mw)


def test_fast_brine_balance_and_limits(plants):
    """Brine heat drawn = ρ·Q·c_p·ΔT (PH-22 within 1 %), ΔT ≤ 40 K, flows in DB-11, Q_inj = Q."""
    plant = plants["C3"]
    rho, cp = 1030.0, 4100.0
    for date in (PEAK, APRIL, JULY, OCTOBER):
        day = _dispatch(plant, date).day
        brine = sum(
            rho * q / 3600.0 * cp * (tp - ti) / 1e6
            for q, tp, ti in zip(
                day.doublet_flow_m3h,
                day.production_temperature_degc,
                day.injection_temperature_degc,
                strict=True,
            )
            if q > 0
        )
        assert sum(day.brine_heat_drawn_mw.values()) == pytest.approx(brine, rel=0.01)
        for q, tp, ti in zip(
            day.doublet_flow_m3h,
            day.production_temperature_degc,
            day.injection_temperature_degc,
            strict=True,
        ):
            assert q == 0.0 or 50.0 <= q <= 300.0
            assert tp - ti <= 40.0 + 1e-6
        assert day.injection_flow_m3h == day.doublet_flow_m3h
        assert all(v >= -1e-6 for v in day.margins.values()), day.margins


# --------------------------------------------------------------------------- merit order
def test_heat_merit_order_peak_day(plants):
    """Peak day, hand-sized C2: HEX → CHP → HTHP carry it, boiler off, flows equal and ≤ design."""
    fast = _dispatch(plants["C2"], PEAK)
    heat = fast.day.heat_by_source_mw
    assert heat["hex_1"] > heat["hthp_1"] > 0 and heat["chp"] > 0
    assert heat["peak_boiler"] == 0.0 and fast.day.unmet_heating_mwh == 0.0
    q1, q2 = fast.plan.flows_m3h
    assert q1 == q2 and 50.0 <= q1 <= 300.0
    assert fast.day.heating_supply_degc == pytest.approx(100.0, abs=0.05)
    assert sum(heat[k] for k in heat if k.startswith("hthp")) <= 14.0 + 1e-6


def test_minimum_flow_rule(plants):
    """DSP-FLOW: the chosen flow is the smallest that keeps the boiler off (lower needs it)."""
    plant = plants["C2"]
    day = day_conditions(APRIL)
    fast = _dispatch(plant, APRIL)
    q = fast.plan.flows_m3h[0]
    assert fast.day.heat_by_source_mw["peak_boiler"] <= 1e-6
    lower = plant.run(day, [q - 2.0, q - 2.0], plant_heating_mw=day.heating_demand_mw)
    assert lower.heat_w("peak_boiler") > 1e3


def test_summer_switches_doublets_off_and_outage(plants):
    """July: the equal share falls below 50 m³/h, so one doublet runs; outage doublet stays off."""
    fast = _dispatch(plants["C2"], JULY)
    assert min(fast.plan.flows_m3h) == 0.0
    out = _dispatch(plants["C2"], PEAK, available=[False, True])
    assert out.plan.flows_m3h[0] == 0.0 and out.plan.flows_m3h[1] > 0
    assert out.day.doublet_available == [False, True]
    assert out.plan.flows_m3h[1] == pytest.approx(300.0)  # one doublet at its maximum
    # one doublet + the 6 MW boiler cannot carry the January peak (why outages go in summer)
    assert out.day.heat_by_source_mw["peak_boiler"] == pytest.approx(6.0)
    assert out.day.unmet_heating_mwh > 0
    assert any("peak boiler fills" in n for n in out.plan.notes)


def test_cooling_merit_order(plants):
    """Free cooling first, absorption next, electric chiller last (SIM-03 (1))."""
    october = _dispatch(plants["C3"], OCTOBER).day  # cool wet bulb: free cooling covers it
    assert october.cool_by_source_mw["free_cooling"] == pytest.approx(october.cooling_demand_mw)
    assert october.cool_by_source_mw["electric_chiller"] == 0.0
    july = _dispatch(plants["C3"], JULY)
    cool = july.day.cool_by_source_mw
    assert cool["free_cooling"] > 0 and cool["absorption_chiller"] > 0
    assert cool["electric_chiller"] == pytest.approx(0.0, abs=1e-6)
    assert july.plan.absorption_drive_m3h >= 138.0
    assert july.day.brine_heat_drawn_mw["absorption_chiller"] > 0
    no_abs = _dispatch(plants["C2"], JULY).day.cool_by_source_mw
    assert no_abs["electric_chiller"] > 0 and "absorption_chiller" not in no_abs


def test_absorption_drive_is_smallest_meeting_flow(plants):
    """COOL-ABS-DRV: drive ≥ minimum × 1.01; a larger residual needs more drive (bisection)."""
    plant = plants["C3"]
    day = day_conditions(load_config("design_basis").cooling.peak_date)
    small, note = _absorption_drive_m3s(plant, day, 0.5e6)
    large, _ = _absorption_drive_m3s(plant, day, 8.0e6)
    assert note is None
    assert small * 3600.0 == pytest.approx(138.49 * 1.01, rel=1e-3)  # minimum + TL-05 tolerance
    assert small < large <= plant.flow_max_m3h / 3600.0
    assert _absorption_drive_m3s(plant, day, 0.0) == (0.0, None)


def test_absorption_drive_respects_db12(plants):
    """A 5 MW absorber on 105 °C brine: the drive flow keeps the brine cooling ≤ 40 K (DB-12)."""
    plant = plants["C4-5MW"]
    for date in (JULY, load_config("design_basis").cooling.peak_date):
        fast = _dispatch(plant, date)
        assert fast.day.cool_by_source_mw["absorption_chiller"] > 0
        for key, value in fast.day.margins.items():
            if "cooling_margin_k" in key:
                assert value >= -1e-6, (date, key, value)
        assert "absorption_drive_cooling_margin_k" in fast.day.margins
        assert not any("DB-12" in w for w in fast.warnings)
        full = simulate_plant_day(
            plant.candidate,
            day_conditions(date),
            fast.plan.plant_dispatch(),
            gb=plant.gb,
            sub=plant.sub,
        )
        assert full.valid, full.failures


def test_absorption_minimum_load(plants):
    """DSP-ABS-MIN: a residual below the minimum load goes to the electric chiller."""
    fast = _dispatch(plants["C3"], JULY, absorption_min_frac=0.99)
    assert fast.day.cool_by_source_mw["absorption_chiller"] == 0.0
    assert fast.day.cool_by_source_mw["electric_chiller"] > 0
    assert any("DSP-ABS-MIN" in n for n in fast.plan.notes)


def test_hp_turndown_cycling_note(plants):
    """DSP-HP-MIN: 100 °C needs the HTHP at 85 °C brine, so a low HTHP load is cycling (kept on)."""
    plant = plants["C2"]
    base = _dispatch(plant, JULY, hp_min_frac=0.0)
    assert base.plan.heat_pumps_on and not any("DSP-HP-MIN" in n for n in base.plan.notes)
    strict = _dispatch(plant, JULY, hp_min_frac=1.0, hp_extra_flow_frac=0.10)
    assert strict.plan.heat_pumps_on
    assert any("on/off cycling" in n for n in strict.plan.notes)
    assert strict.plan.flows_m3h == base.plan.flows_m3h


def test_hp_turndown_switches_off_trickle(plants):
    """DSP-HP-MIN: with 105 °C brine a 3 kW HTHP trickle goes off for ≤ 10 % more brine."""
    plant = plants["C2-deep"]
    day = day_conditions(PEAK)
    kept = _dispatch(plant, PEAK, hp_extra_flow_frac=0.0)
    assert kept.plan.heat_pumps_on and any("on/off cycling" in n for n in kept.plan.notes)
    hp = sum(v for k, v in kept.day.heat_by_source_mw.items() if k.startswith("hthp"))
    assert 0 < hp < 0.1 * 4.0
    off = _dispatch(plant, PEAK, hp_extra_flow_frac=0.10)
    assert not off.plan.heat_pumps_on and any("HTHPs off" in n for n in off.plan.notes)
    assert not any(k.startswith("hthp") for k in off.day.heat_by_source_mw)
    assert sum(kept.plan.flows_m3h) < sum(off.plan.flows_m3h) <= 1.1 * sum(kept.plan.flows_m3h)
    assert off.day.unmet_heating_mwh == 0.0
    assert off.day.heat_by_source_mw["peak_boiler"] < 0.01  # HEX-share control resolution
    full = simulate_plant_day(
        plant.candidate, day, off.plan.plant_dispatch(), gb=plant.gb, sub=plant.sub
    )
    assert full.valid and full.day is not None
    assert not any(k.startswith("hthp") for k in full.day.heat_by_source_mw)
    assert full.day.unmet_heating_mwh == 0.0


def test_unmet_recorded_when_boiler_too_small(plants):
    """C0 with a 6 MW boiler cannot meet the peak: the shortfall is unmet heat, never hidden."""
    fast = _dispatch(plants["C0"], PEAK)
    day = fast.day
    assert day.heat_by_source_mw["peak_boiler"] == pytest.approx(6.0)
    assert day.unmet_heating_mwh == pytest.approx((day.heating_demand_mw - 6.0) * 24.0)
    assert day.margins["heating_supply_margin_k"] < 0
    assert day.doublet_flow_m3h == []


def test_keys_resolve_with_wp08_classify(plants):
    """Every electricity/fuel/brine-user key resolves with WP-08 rules (plate HEXs: heat only)."""
    keys = set()
    for plant in plants.values():
        for date in (PEAK, JULY):
            day = _dispatch(plant, date).day
            keys |= set(day.electricity_by_consumer_mw) | set(day.gas_fuel_by_consumer_mw)
            keys |= set(day.brine_heat_drawn_mw)
    unknown = {k for k in keys if classify_service(k) is None}
    assert unknown <= {"hex_1", "hex_2"}
    assert classify_service("injection_pump_1") == "shared"
    assert classify_service("heat_rejection") == "cool"


def test_pressurised_loop_injection_pump(plants):
    """C7: the brine reaches the injector at the producer WHP (no letdown), as in GEMINI."""
    plant = plants["C7"]
    assert plant.map_design().injection_pump_suction_barg == pytest.approx(
        plant.map_design().producer_wellhead_pressure_barg
    )
    day = _dispatch(plant, PEAK).day
    assert day.electricity_by_consumer_mw["injection_pump_1"] == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- storage
def test_storage_ledger_balance():
    """Storage ledger: limits, recovery and the cyclic second pass (DSP-STORAGE)."""
    spec = StorageSpec(capacity_mwh=240.0, power_mw=2.0, discharge_efficiency_frac=0.5)
    days = list(range(10))
    charge = {d: 5.0 for d in range(5)}  # capped at 2 MW: 48 MWh/day
    discharge = {d: 1.0 for d in range(5, 10)}
    bal = storage_ledger(spec, days, charge, discharge)
    assert bal.charged_total_mwh == pytest.approx(5 * 48.0)
    assert bal.discharged_total_mwh == pytest.approx(5 * 24.0)
    assert bal.final_energy_mwh == pytest.approx(
        bal.initial_energy_mwh + bal.charged_total_mwh - bal.discharged_total_mwh / 0.5
    )
    assert all(bal.net_mw_by_day[d] == pytest.approx(-2.0) for d in range(5))
    assert all(bal.net_mw_by_day[d] == pytest.approx(1.0) for d in range(5, 10))
    assert (bal.charge_days, bal.discharge_days) == (5, 5)


def test_storage_discharge_displaces_boiler(plants):
    """Discharge ahead of the boiler: HEX/HTHP unchanged, boiler heat, fuel and CO₂ fall 1:1."""
    fast = _dispatch(plants["C2"], PEAK, available=[False, True])
    boiler = fast.day.heat_by_source_mw["peak_boiler"]
    out = apply_storage_discharge(fast, 2.0)
    heat = out.day.heat_by_source_mw
    assert heat["storage"] == 2.0 and heat["peak_boiler"] == pytest.approx(boiler - 2.0)
    assert heat["hex_2"] == fast.day.heat_by_source_mw["hex_2"]
    share = (boiler - 2.0) / boiler
    for field in ("gas_fuel_by_consumer_mw", "co2_by_source_kg"):
        before = getattr(fast.day, field)["peak_boiler"]
        assert getattr(out.day, field)["peak_boiler"] == pytest.approx(before * share)
    assert out.day.heat_supplied_mw == pytest.approx(fast.day.heat_supplied_mw)
    assert out.plan.storage_net_mw == 2.0
    with pytest.raises(ValueError, match="exceeds the boiler heat"):
        apply_storage_discharge(fast, boiler + 1.0)
    no_boiler = _dispatch(plants["C2"], PEAK)  # boiler off: a sub-tolerance discharge is safe
    assert no_boiler.day.heat_by_source_mw["peak_boiler"] == 0.0
    tiny = apply_storage_discharge(no_boiler, 1e-7)
    assert tiny.day.heat_by_source_mw["peak_boiler"] == 0.0
    with pytest.raises(ValueError, match="storage_charge_mw"):
        _dispatch(plants["C2"], PEAK, storage_charge_mw=-1.0)


def test_storage_spec_only_for_sized_c6():
    """C6 needs storage_capacity_mwh and storage_power_mw (no default); recovery STO-REC-HT."""
    register = load_config("assumptions")
    sizes = {"storage_capacity_mwh": 480.0, "storage_power_mw": 15.0}
    spec = storage_spec(make_candidate("C6", 2, parent_id="C3-2d", equipment_sizes=sizes), register)
    assert spec is not None and spec.power_mw == pytest.approx(15.0)
    assert spec.discharge_efficiency_frac == pytest.approx(0.63)
    capacity_only = {"storage_capacity_mwh": 480.0}
    c6 = make_candidate("C6", 2, parent_id="C3-2d", equipment_sizes=capacity_only)
    assert storage_spec(c6, register) is None
    assert storage_spec(make_candidate("C6", 2, parent_id="C3-2d"), register) is None
    assert (
        storage_spec(make_candidate("C2", 2, equipment_sizes={"storage_capacity_mwh": 1}), register)
        is None
    )
