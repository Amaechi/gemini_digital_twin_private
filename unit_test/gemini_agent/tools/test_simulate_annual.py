"""Tests for TL-12 ``simulate_annual`` (WP-07 acceptance, SIM-01…07) and the WP-08/09 hand-off."""

from __future__ import annotations

import json

import pandas as pd
import pytest

import gemini_agent.tools.simulate_annual as sa
from gemini_agent.memory.design_cache import DesignCache, config_hash
from gemini_agent.runner.design_runner import _db30_interpretation_b
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.results import AnnualResult
from gemini_agent.tools.dispatch import FastPlant
from gemini_agent.tools.economics import (
    CapexRequest,
    Co2Request,
    EconomicsRequest,
    HexDuty,
    classify_service,
    co2_emissions,
    economics,
)
from gemini_agent.tools.exergy import exergy_kpi
from gemini_agent.tools.simulate_annual import (
    SimulateAnnualRequest,
    cache_config,
    cache_namespace,
    compare_days,
    run_annual,
    simulate_annual,
    verification_evidence,
)
from gemini_agent.verify.design_verifier import CheckStatus, DesignVerifier
from unit_test.gemini_agent.tools.plant_factory import make_candidate

HAND_C2 = {"hthp_heating_mw": 14.0, "peak_boiler_mw": 6.0, "electric_chiller_mw": 10.0}
"""WP-07 acceptance: hand-sized C2 (2 doublets, HTHP 14 MW, boiler 6 MW, chillers 10 MW)."""
SAMPLE_DAYS = [14, 45, 75, 104, 135, 150, 166, 190, 200, 230, 260, 290, 320, 350]
"""14-day fast vs full sample: peak day, shoulders, both summer outages, autumn, December."""


@pytest.fixture(scope="module")
def map_cache(tmp_path_factory: pytest.TempPathFactory):
    """TL-06 maps cached once for this module (not under runs/)."""
    return tmp_path_factory.mktemp("doublet_maps")


@pytest.fixture(scope="module")
def runs_root(tmp_path_factory: pytest.TempPathFactory):
    """Stand-in for ``runs/`` (git-ignored in the repository)."""
    return tmp_path_factory.mktemp("runs")


@pytest.fixture(scope="module")
def c2():
    """Return the hand-sized C2 candidate."""
    return make_candidate("C2", 2, candidate_id="C2-hand", equipment_sizes=HAND_C2)


@pytest.fixture(scope="module")
def fast_year(c2, map_cache, runs_root):
    """TL-12 fast mode over the year, files written to ``runs_root/<run_id>``."""
    request = SimulateAnnualRequest(candidate=c2, mode="fast", run_id="wp07-fast")
    return simulate_annual(request, runs_root=runs_root, map_cache_dir=map_cache)


@pytest.fixture(scope="module")
def full_sample(c2, map_cache, runs_root):
    """TL-12 full mode on the 14-day sample (GEMINI every day)."""
    request = SimulateAnnualRequest(
        candidate=c2, mode="full", day_indices=SAMPLE_DAYS, run_id="wp07-full"
    )
    return simulate_annual(request, runs_root=runs_root, map_cache_dir=map_cache)


# --------------------------------------------------------------------------- acceptance
def test_fast_year_acceptance(fast_year):
    """365 DayResults, hand-sized C2 has 0 unmet days, fast mode < 10 s (WP-07 acceptance)."""
    sim = fast_year.data
    annual = sim.annual
    assert len(annual.days) == 365 and annual.interpretation == "A"
    assert [d.day_index for d in annual.days] == list(range(365))
    assert annual.unmet_heating_days == 0 and annual.unmet_cooling_days == 0
    assert sim.elapsed_s < 10.0
    assert annual.heat_delivered_mwh == pytest.approx(
        sum(d.heating_demand_mw for d in annual.days) * 24.0, rel=1e-6
    )
    assert annual.cool_delivered_mwh == pytest.approx(
        sum(d.cooling_demand_mw for d in annual.days) * 24.0, rel=1e-6
    )
    assert AnnualResult.model_validate(annual.model_dump()) == annual
    assert len(fast_year.summary) <= 300 and "TL-12" in fast_year.summary


def test_fast_year_calendar_and_flows(fast_year):
    """SIM-02/03: non-overlapping 35-day outages honoured, flows in DB-11 or off, ΔT ≤ 40 K."""
    sim = fast_year.data
    windows = sim.calendar.outage_windows()
    assert len(windows) == 2 and sim.calendar.placement == "lowest_load"
    off_days = 0
    for day in sim.annual.days:
        assert day.doublet_available == sim.calendar.available(day.day_index)
        assert day.doublet_available.count(False) <= 1
        for ok, q, tp, ti in zip(
            day.doublet_available,
            day.doublet_flow_m3h,
            day.production_temperature_degc,
            day.injection_temperature_degc,
            strict=True,
        ):
            assert q == 0.0 or 50.0 <= q <= 300.0
            if not ok:
                assert q == 0.0
                off_days += 1
            if q > 0:
                assert tp - ti <= 40.0 + 1e-6
        assert day.injection_flow_m3h == day.doublet_flow_m3h
    assert off_days == 70


def test_fast_year_kpis(fast_year):
    """Check the KPIs from the daily results; CO₂ from WP-08 ``co2_emissions`` (+ DB-37)."""
    sim = fast_year.data
    k = sim.annual.kpis
    assert k is not None and k.candidate_id == "C2-hand"
    assert k.unmet_heating_days == 0 and k.unmet_heating_mwh == 0.0
    assert sum(k.heat_by_source_mwh.values()) == pytest.approx(sim.annual.heat_delivered_mwh)
    assert k.heat_by_source_mwh["peak_boiler"] == pytest.approx(0.0, abs=1.0)
    assert k.heat_by_source_mwh["hex_1"] > 0 and k.heat_by_source_mwh["hthp_1"] > 0
    assert k.cool_by_source_mwh["free_cooling"] > k.cool_by_source_mwh["electric_chiller"] > 0
    assert len(k.doublet_capacity_factors_frac) == 2
    assert all(0 < f < 1 for f in k.doublet_capacity_factors_frac)
    assert k.electricity_consumed_mwh == pytest.approx(sim.annual.electricity_consumed_mwh)
    assert k.electricity_generated_mwh > 0 and k.gas_fuel_mwh > 0
    assert k.co2_t_per_yr is not None and k.co2_t_per_yr > sim.annual.co2_t  # + grid power
    assert k.installed_heating_mw >= 14.0 + 6.0
    assert all(v >= -1e-6 for v in k.min_constraint_margins.values()), k.min_constraint_margins
    assert sum(d.associated_gas_produced_nm3 for d in sim.annual.days) > 0
    assert all(d.brine_heat_drawn_mw for d in sim.annual.days)
    # SIM-06: TL-07 at the annual-mean operation of each doublet
    assert [c.doublet for c in sim.end_of_life] == [1, 2]
    for check, factor in zip(sim.end_of_life, k.doublet_capacity_factors_frac, strict=True):
        assert check.mean_flow_m3h == pytest.approx(300.0 * factor)
        assert check.mean_production_degc - check.mean_injection_degc <= 40.0
    assert k.thermal_breakthrough_yr == min(c.breakthrough_time_yr for c in sim.end_of_life)


def test_outputs_written(fast_year, runs_root):
    """CSV (one row per day) and a JSON summary under runs/<run_id>/ (SIM-05)."""
    out = fast_year.data.outputs
    assert set(out) == {"csv_fast", "summary"}
    run_dir = runs_root / "wp07-fast"
    frame = pd.read_csv(run_dir / "annual_C2-hand_fast.csv")
    assert len(frame) == 365
    for column in (
        "heat_hex_1_mw",
        "elec_esp_1_mw",
        "brine_hthp_1_mw",
        "doublet_1_flow_m3h",
        "doublet_1_brine_cooling_k",
        "associated_gas_produced_nm3",
        "margin_doublet_1_brine_cooling_margin_k",
        "margin_heating_supply_margin_k",
        "ambient_wet_bulb_degc",
    ):
        assert column in frame.columns
    summary = json.loads((run_dir / "annual_C2-hand_fast_summary.json").read_text())
    assert summary["simulated_days"] == 365 and summary["interpretation"] == "A"
    assert summary["kpis"]["unmet_heating_days"] == 0
    assert len(summary["outage_windows"]) == 2
    assert "DSP-FLOW" in summary["assumption_ids"]


def test_fast_vs_full_14_days(full_sample):
    """SIM-04 acceptance: fast and full agree within 2 % daily heat on a 14-day sample."""
    sim = full_sample.data
    comparison = sim.comparison
    assert comparison is not None and len(comparison.days) == 14
    assert comparison.max_heat_supplied_discrepancy_frac <= 0.02
    assert comparison.max_discrepancy_frac <= 0.02  # per source, unmet and electricity too
    assert comparison.status == "pass"
    assert sim.invalid_full_days == []
    assert sim.fast_annual is not None and len(sim.fast_annual.days) == 14
    assert sim.end_of_life == []  # a 14-day sample is not a year (SIM-06)
    assert [d.day_index for d in sim.annual.days] == SAMPLE_DAYS
    assert sim.annual.unmet_heating_days == 0
    for day in sim.annual.days:  # calendar availability, not "flow > 0"
        assert day.doublet_available == sim.calendar.available(day.day_index)
    assert {"csv_full", "csv_fast", "summary"} <= set(sim.outputs)


def test_compare_days_flags_disagreement(full_sample):
    """The comparison flags > 2 % as warn and > 5 % as fail (PD-06 rule)."""
    sim = full_sample.data
    full = sim.annual.days[:2]
    fast = [d.model_copy() for d in sim.fast_annual.days[:2]]  # type: ignore[union-attr]
    shifted = dict(fast[0].heat_by_source_mw)
    shifted["hex_1"] += 0.03 * fast[0].heating_demand_mw
    fast[0] = fast[0].model_copy(update={"heat_by_source_mw": shifted})
    assert compare_days(full, fast).status == "warn"
    shifted["hex_1"] += 0.05 * fast[0].heating_demand_mw
    fast[0] = fast[0].model_copy(update={"heat_by_source_mw": shifted})
    assert compare_days(full, fast).status == "fail"
    with pytest.raises(ValueError, match="no fast result"):
        compare_days(full, fast[:1])


# --------------------------------------------------------------------------- DB-30 variants
def test_interpretation_b_from_answer(c2, map_cache):
    """answers['DB-30'] = B (runner form): 330 days, no outage, same totals rule."""
    register = load_config("assumptions")
    answer = _db30_interpretation_b(register.get("DB-30").value)
    sim = run_annual(
        SimulateAnnualRequest(candidate=c2, db30_answer=answer, write_outputs=False),
        map_cache_dir=map_cache,
    )
    assert sim.annual.interpretation == "B" and len(sim.annual.days) == 330
    assert sim.calendar.rule.source == "answer"
    assert all(all(d.doublet_available) for d in sim.annual.days)
    simulated = [d.day_index for d in sim.annual.days]
    assert sim.calendar.excluded_start_index not in simulated
    basis = load_config("design_basis")
    for peak in (basis.heating.peak_date, basis.cooling.peak_date):  # design days kept
        assert (peak - basis.heating.peak_date.replace(month=1, day=1)).days in simulated


def test_given_outages_and_bad_days(c2, map_cache):
    """The optimiser's outage starts are used; overlapping ones and unknown days are refused."""
    request = SimulateAnnualRequest(
        candidate=c2, outage_start_indices=[100, 200], day_indices=[99, 100, 134, 135, 200]
    )
    sim = run_annual(request, map_cache_dir=map_cache)
    flows = {d.day_index: d.doublet_flow_m3h for d in sim.annual.days}
    assert flows[100][0] == 0.0 and flows[134][0] == 0.0 and flows[135][0] > 0
    assert flows[200][1] == 0.0 and flows[99][0] > 0
    with pytest.raises(ValueError, match="overlap"):
        run_annual(
            SimulateAnnualRequest(candidate=c2, outage_start_indices=[100, 110]),
            map_cache_dir=map_cache,
        )
    b_sim = run_annual(
        SimulateAnnualRequest(candidate=c2, db30_answer="B", day_indices=[0]),
        map_cache_dir=map_cache,
    )
    excluded = b_sim.calendar.excluded_start_index
    with pytest.raises(ValueError, match="not simulated"):
        run_annual(
            SimulateAnnualRequest(candidate=c2, db30_answer="B", day_indices=[excluded]),
            map_cache_dir=map_cache,
        )
    with pytest.raises(ValueError):
        SimulateAnnualRequest(candidate=c2, run_id="../escape")


# --------------------------------------------------------------------------- concepts
def test_absorption_concept_year(map_cache):
    """C3: absorption runs after free cooling; its drive is brine heat drawn (cooling service)."""
    c3 = make_candidate("C3", 2)
    sim = run_annual(SimulateAnnualRequest(candidate=c3), map_cache_dir=map_cache)
    k = sim.annual.kpis
    assert k is not None and k.cool_by_source_mwh["absorption_chiller"] > 0
    assert k.unmet_cooling_days == 0
    drawn = sum(d.brine_heat_drawn_mw.get("absorption_chiller", 0.0) for d in sim.annual.days)
    assert drawn > 0
    assert sim.service_by_key["absorption_chiller"] == "cool"
    assert sim.service_by_key["hex_1"] == "heat" and sim.service_by_key["esp_1"] == "shared"
    for key, service in sim.service_by_key.items():
        assert classify_service(key) in (service, None)
    # outage placement counts absorption drive heat (cooling / GM-04 COP)
    assert sim.calendar.outage_windows() != [] and sim.calendar.placement == "lowest_load"


C6_SIZES = {
    "storage_capacity_mwh": 3000.0,
    "storage_power_mw": 5.0,
    "peak_boiler_mw": 30.0,
    "hthp_heating_mw": 4.0,
}


def _c6(**sizes: float):
    return make_candidate("C6", 2, parent_id="C3-2d", equipment_sizes={**C6_SIZES, **sizes})


@pytest.fixture(scope="module")
def c6_year(map_cache):
    """Fast year of a sized C6 (storage ledger) and of its C3 parent without storage."""
    c6 = _c6()
    sim = run_annual(SimulateAnnualRequest(candidate=c6), map_cache_dir=map_cache)
    parent = c6.model_copy(update={"concept": "C3", "parent_id": None})
    base = run_annual(SimulateAnnualRequest(candidate=parent), map_cache_dir=map_cache)
    return c6, sim, base


def test_c6_storage_ledger_year(c6_year):
    """C6 with storage: ledger totals, net 'storage' heat key, network demand still met."""
    _, sim, base = c6_year
    bal = sim.storage
    assert bal is not None and bal.charged_total_mwh > 0 and bal.discharged_total_mwh > 0
    assert bal.final_energy_mwh == pytest.approx(
        bal.initial_energy_mwh + bal.charged_total_mwh - bal.discharged_total_mwh / 0.63, abs=1e-3
    )
    nets = {d.day_index: d.heat_by_source_mw.get("storage", 0.0) for d in sim.annual.days}
    assert min(nets.values()) == pytest.approx(-5.0) and max(nets.values()) > 0
    assert sim.annual.unmet_heating_days == 0
    for day in sim.annual.days:
        assert day.heat_supplied_mw == pytest.approx(day.heating_demand_mw, abs=1e-5)
    assert any("DSP-STORAGE" in w for w in sim.warnings)
    boiler = sim.annual.kpis.heat_by_source_mwh["peak_boiler"]  # type: ignore[union-attr]
    base_boiler = base.annual.kpis.heat_by_source_mwh["peak_boiler"]  # type: ignore[union-attr]
    assert base_boiler - boiler == pytest.approx(bal.discharged_total_mwh, rel=1e-6)


def test_c6_rounding_days_charge(c6_year, map_cache):
    """Days with ~1e-15 MW of rounding 'boiler heat' are charge days, not lost (HEAT_TOL_MW)."""
    _, _, base = c6_year
    noise = [
        d.day_index for d in base.annual.days if 0 < d.heat_by_source_mw["peak_boiler"] <= 1e-6
    ]
    assert noise  # the rounding days exist in the parent plant
    big = run_annual(
        SimulateAnnualRequest(candidate=_c6(storage_capacity_mwh=30000.0)),
        map_cache_dir=map_cache,
    )
    nets = big.storage.net_mw_by_day  # type: ignore[union-attr]
    charged = [i for i in noise if nets.get(i, 0.0) < 0]
    # A strict "boiler <= 0" test charged none of them; later ones may meet a full store.
    assert len(charged) >= len(noise) // 2


def test_c6_storage_full_mode_uses_year_ledger(c6_year, map_cache):
    """A day subset (full mode) replays the whole-year ledger, not a ledger of the subset."""
    c6, year_sim, _ = c6_year
    nets = year_sim.storage.net_mw_by_day  # type: ignore[union-attr]
    discharge_day = min(i for i, v in nets.items() if v > 0)
    charge_day = min(i for i, v in nets.items() if v < 0)
    idle_day = next(i for i in range(365) if i not in nets)
    days = sorted({discharge_day, charge_day, idle_day})
    sim = run_annual(
        SimulateAnnualRequest(candidate=c6, mode="full", day_indices=days),
        map_cache_dir=map_cache,
    )
    assert sim.storage == year_sim.storage  # same seasonal ledger
    for plan in sim.plans:
        assert plan.storage_net_mw == pytest.approx(nets.get(plan.day_index, 0.0))
    for day in sim.annual.days:
        assert day.heat_by_source_mw.get("storage", 0.0) == pytest.approx(
            nets.get(day.day_index, 0.0), rel=1e-3
        )
        assert day.heat_supplied_mw == pytest.approx(day.heating_demand_mw, abs=1e-3)
        assert day.unmet_heating_mwh == 0.0
    assert sim.comparison is not None and sim.comparison.status == "pass"


def test_hp_min_inactive_without_reference(map_cache):
    """DSP-HP-MIN needs a reference: hot brine without a nameplate warns that it is inactive."""
    deep = make_candidate("C2", 2, depth_m=3200.0, candidate_id="C2-deep-no-nameplate")
    sim = run_annual(
        SimulateAnnualRequest(candidate=deep, day_indices=[14]), map_cache_dir=map_cache
    )
    assert any("DSP-HP-MIN inactive" in w for w in sim.warnings)


def test_c6_without_storage_size_warns(map_cache):
    """C6 without a storage size runs the parent plant and says so."""
    c6 = make_candidate("C6", 2, parent_id="C3-2d")
    sim = run_annual(SimulateAnnualRequest(candidate=c6, day_indices=[14]), map_cache_dir=map_cache)
    assert sim.storage is None and any("storage_capacity_mwh" in w for w in sim.warnings)


def test_invalid_full_day_is_never_silent(c2, map_cache, monkeypatch):
    """A day GEMINI cannot run fails the comparison, is warned and blocks the evidence."""
    real = sa.simulate_plant_day

    def broken(candidate, day, *args, **kwargs):
        sim = real(candidate, day, *args, **kwargs)
        if day.day_index == 190:
            return sim.model_copy(update={"valid": False, "failures": ["forced"], "day": None})
        return sim

    monkeypatch.setattr(sa, "simulate_plant_day", broken)
    result = simulate_annual(
        SimulateAnnualRequest(
            candidate=c2, mode="full", day_indices=[14, 190], write_outputs=False
        ),
        map_cache_dir=map_cache,
    )
    sim = result.data
    assert len(sim.invalid_full_days) == 1 and "forced" in sim.invalid_full_days[0]
    assert sim.comparison is not None and sim.comparison.status == "fail"
    row = next(r for r in sim.comparison.days if r.day_index == 190)
    assert row.status == "fail" and "invalid" in row.worst_item
    assert "full mode invalid: fast result" in sim.annual.days[1].flags
    assert result.warnings[0].startswith("1 full-mode day(s) GEMINI could not run")
    assert "INVALID full days 1" in result.summary
    with pytest.raises(ValueError, match="could not be run"):
        verification_evidence(sim, c2)


def test_request_and_plant_guards(c2, map_cache):
    """compare_day_indices is fast-mode only; a prebuilt plant must match the request."""
    with pytest.raises(ValueError, match="fast mode"):
        SimulateAnnualRequest(candidate=c2, mode="full", compare_day_indices=[1])
    plant = FastPlant(make_candidate("C3", 2), map_cache_dir=map_cache)
    with pytest.raises(ValueError, match="prebuilt FastPlant"):
        run_annual(SimulateAnnualRequest(candidate=c2, day_indices=[1]), plant=plant)


def test_idle_storage_warns(map_cache):
    """A store that never charges or discharges in the reported pass says so (DSP-STORAGE)."""
    c6 = make_candidate(
        "C6",
        2,
        parent_id="C3-2d",
        equipment_sizes={"storage_capacity_mwh": 10.0, "storage_power_mw": 20.0},
    )
    sim = run_annual(
        SimulateAnnualRequest(candidate=c6, day_indices=[14, 190]), map_cache_dir=map_cache
    )
    assert sim.storage is not None
    assert sim.storage.charged_total_mwh == 0.0 and sim.storage.discharged_total_mwh == 0.0
    assert any("storage idle" in w for w in sim.warnings)


def test_cache_key_follows_config(c2, map_cache, config_copy, edit_yaml):
    """SIM-07: editing a register value the dispatch reads changes the config hash."""
    request = SimulateAnnualRequest(candidate=c2, day_indices=[14], write_outputs=False)
    calendar = run_annual(request, map_cache_dir=map_cache).calendar
    namespace = cache_namespace("fast")
    before = config_hash(cache_config(request, calendar, config_copy), namespace=namespace)

    def lower_min(data: dict) -> None:
        for entry in data["assumptions"]:
            if entry["id"] == "DSP-ABS-MIN":
                entry["value"] = 0.05

    edit_yaml(config_copy / "assumptions.yaml", lower_min)
    after = config_hash(cache_config(request, calendar, config_copy), namespace=namespace)
    assert after != before


def test_c0_boiler_only(map_cache):
    """C0: no doublets, peak boiler + electric chiller; no brine keys."""
    c0 = make_candidate("C0", 0)
    sim = run_annual(
        SimulateAnnualRequest(candidate=c0, day_indices=[14, 190]), map_cache_dir=map_cache
    )
    for day in sim.annual.days:
        assert day.doublet_flow_m3h == [] and day.brine_heat_drawn_mw == {}
        assert day.heat_by_source_mw["peak_boiler"] == pytest.approx(day.heating_demand_mw)
    assert sim.calendar.outage_start_indices == []


def test_fast_mode_comparison_sample_and_cache(c2, map_cache, runs_root, tmp_path):
    """Fast mode can compare a sample in full mode; the KPIs land in the DesignCache (SIM-07)."""
    cache = DesignCache(tmp_path / "cache.sqlite")
    request = SimulateAnnualRequest(
        candidate=c2, day_indices=[14, 190], compare_day_indices=[190], write_outputs=False
    )
    result = simulate_annual(request, map_cache_dir=map_cache, cache=cache)
    sim = result.data
    assert sim.comparison is not None and len(sim.comparison.days) == 1
    assert sim.outputs == {} and sim.fast_annual is None
    entry = cache.get(sim.config_hash)
    assert entry is not None and entry.namespace == cache_namespace("fast")
    assert entry.kpis == sim.annual.kpis
    again = run_annual(request, map_cache_dir=map_cache)
    assert again.config_hash == sim.config_hash
    cache.close()


# --------------------------------------------------------------------------- WP-08 / WP-09
def test_end_to_end_economics_and_verifier(c2, full_sample):
    """An AnnualResult from TL-12 feeds WP-08 economics/CO₂/exergy and WP-09 evidence + verifier."""
    sim = full_sample.data
    annual = sim.annual
    plant_hex = sim.evidence.hex_points  # type: ignore[union-attr]
    assert plant_hex
    k = annual.kpis
    assert k is not None
    peak_fc = max(d.cool_by_source_mw.get("free_cooling", 0.0) for d in annual.days)
    capex = CapexRequest(
        candidate_id=c2.id,
        doublets=c2.doublets,
        depth_m=c2.depth_m,
        plate_hexes=[
            HexDuty(
                name=p.name,
                mass_flow_kg_s=300.0 * 1030.0 / 3600.0,
                delta_t_k=p.hot_in_degc - p.hot_out_degc,
            )
            for p in plant_hex
        ],
        hthp_heating_mw=14.0,
        chp_thermal_mw=max(d.heat_by_source_mw.get("chp", 0.0) for d in annual.days),
        gas_boiler_mw=6.0,
        electric_chiller_mw=10.0,
        free_cooling_mw=peak_fc,
    )
    eco = economics(
        EconomicsRequest(
            capex=capex, gas_handling=c2.gas_handling, service_by_key=sim.service_by_key
        ),
        annual,
    )
    assert eco.data.kpis.lcoh_per_mwh is not None and eco.data.kpis.lcoh_per_mwh > 0
    assert not any("unknown service" in w for w in eco.warnings)
    co2 = co2_emissions(
        Co2Request(gas_handling=c2.gas_handling, service_by_key=sim.service_by_key), annual
    )
    assert co2.data.total_t == pytest.approx(k.co2_t_per_yr)
    exergy = exergy_kpi(annual, base_kpis=eco.data.kpis)
    assert exergy.data.kpis.exergy_efficiency_frac is not None  # type: ignore[union-attr]
    evidence = verification_evidence(sim, c2)
    assert evidence.annual is annual and evidence.fast_annual is sim.fast_annual
    assert evidence.pumps and evidence.esps and not evidence.stub_evidence
    report = DesignVerifier.from_config().verify(evidence)
    for check_id in ("PD-01", "PD-02", "PD-06", "PH-22"):
        assert report.check(check_id).status == CheckStatus.PASS, report.check(check_id)
    with pytest.raises(ValueError, match="come from the TL-12 result"):
        verification_evidence(sim, c2, kpis=None)
    with pytest.raises(ValueError, match="given for"):
        verification_evidence(sim, make_candidate("C3", 2))
