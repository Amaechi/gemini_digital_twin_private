"""Tests for TL-15 ``uncertainty`` (WP-11): ranges, mapping, seeded Monte Carlo, cases.

About 20 Monte Carlo samples on a day subset keep CI fast; the ≥ 200-sample run on the full year
is ``scripts/run_optimisation.py`` (PROGRESS.md).
"""

from __future__ import annotations

import json
import math

import pytest

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.optimise import make_design_candidate, write_scenario_config
from gemini_agent.tools.resource import load_subsurface_basis
from gemini_agent.tools.simulate_annual import year_conditions
from gemini_agent.tools.uncertainty import (
    HP_DATASHEET_LABEL,
    MonteCarloSample,
    UncertaintyRequest,
    analyse,
    driver_ranking,
    load_uncertain_inputs,
    percentile_table,
    sample_inputs,
    scenario_for,
    uncertainty,
)

DAYS = [14, 45, 195, 225, 350]
"""Jan, Feb, Jul, Aug, Dec: the heating and cooling peaks, outside the DB-30 B window."""
DESIGN = make_design_candidate(
    "C2",
    2,
    2500.0,
    300.0,
    {"hthp_heating_mw": 8.0, "peak_boiler_mw": 10.0, "electric_chiller_mw": 9.0},
    heating_return_degc=55.0,
    cooling_return_degc=26.0,
    candidate_id="C2-uq-test",
)


@pytest.fixture(scope="module")
def year():
    """Load the design year once."""
    return year_conditions()


@pytest.fixture(scope="module")
def report(year):
    """Run all discrete cases, the cost tornado and 20 Monte Carlo samples (no OAT)."""
    request = UncertaintyRequest(
        candidate=DESIGN,
        outage_start_indices=[100, 160],
        samples=20,
        one_at_a_time=False,
        day_indices=DAYS,
        write_outputs=False,
    )
    return analyse(request, year=year)


# --------------------------------------------------------------------------- ranges
def test_every_input_traces_to_a_register_entry_or_card():
    """UQ-RANGES: each range names its owner; the base value lies inside it."""
    inputs, _ = load_uncertain_inputs()
    register = load_config("assumptions")
    names = {i.name for i in inputs}
    assert {
        "permeability_md",
        "geothermal_gradient_k_per_km",
        "skin",
        "production_temperature_shift_k",
        "hp_carnot_fraction",
        "hp_approach_k",
        "electric_chiller_carnot_efficiency",
        "esp_intake_margin_factor",
        "injection_pump_suction_barg",
        "injectivity_factor",
        "well_spacing_m",
    } <= names
    owners = {
        "permeability_md": "DB-06",
        "geothermal_gradient_k_per_km": "DB-03",
        "skin": "DB-38",
        "heating_return_degc": "DB-31",
        "warm_climate_frac": "DB-34b",
        "production_temperature_shift_k": "RC-02",
        "hp_carnot_fraction": "VER-HP-ETA",
        "hp_approach_k": "HP-APPROACH",
        "absorption_duhring_slope": "COOL-ABS-B",
        "electric_chiller_carnot_efficiency": "COOL-ELC",
        "esp_intake_margin_factor": "SUB-PBUB",
        "injection_pump_suction_barg": "SUB-PSUC",
        "injectivity_factor": "SUB-II",
        "well_spacing_m": "SUB-SPACING",
        "heating_demand_scale": "UQ-01",
        "cooling_demand_scale": "UQ-01",
    }
    for spec in inputs:
        assert spec.low < spec.high and spec.low <= spec.base <= spec.high, spec.name
        owner = owners[spec.name]
        assert owner in spec.source, spec.name
        if not owner.startswith(("DB-0", "RC-", "UQ-")):  # DB-01…19 / cards / spec live elsewhere
            register.get(owner)
    by_name = {i.name: i for i in inputs}
    assert (by_name["skin"].low, by_name["skin"].high) == (-1.0, 5.0)  # DB-38 sensitivity
    assert (by_name["electric_chiller_carnot_efficiency"].low,) == (0.44,)  # COOL-ELC
    assert by_name["hp_carnot_fraction"].base == register.get("VER-HP-ETA").value
    assert (
        by_name["production_temperature_shift_k"].low,
        by_name["production_temperature_shift_k"].high,
    ) == (
        -6.0,
        6.0,
    )  # RC-02


def test_lognormal_is_fitted_to_p90_p10():
    """Permeability: 10th/90th percentiles = 100/400 mD, median = √(100·400) = 200 = DB-06."""
    inputs, _ = load_uncertain_inputs()
    k = next(i for i in inputs if i.name == "permeability_md")
    assert k.from_unit(0.1) == pytest.approx(100.0, rel=1e-9)
    assert k.from_unit(0.9) == pytest.approx(400.0, rel=1e-9)
    assert k.from_unit(0.5) == pytest.approx(200.0, rel=1e-9) == k.base


def test_scenario_mapping_moves_coupled_entries_together(tmp_path):
    """SUB-PBUB margin = VER-ESP-MARGIN, VER-ESP-PB scaled; gradient carries the ±6 K shift."""
    _, base = load_uncertain_inputs()
    scenario = scenario_for(
        {
            "esp_intake_margin_factor": 1.3,
            "geothermal_gradient_k_per_km": 30.0,
            "production_temperature_shift_k": 6.0,
            "hp_carnot_fraction": 0.5,
            "hp_approach_k": 3.0,
            "injectivity_factor": 0.8,
            "skin": 2.0,
        },
        base,
        depth_m=2500.0,
        name="t",
    )
    assert scenario.assumptions["VER-ESP-MARGIN"] == 1.3
    assert scenario.assumptions["SUB-PBUB.intake_margin_factor"] == 1.3
    assert scenario.assumptions["VER-ESP-PB"] == pytest.approx(60.0 * 1.3 / 1.2)
    gradient = scenario.design_basis["reservoir.geothermal_gradient_k_per_km"]
    assert 12.0 + gradient * 2.5 == pytest.approx(12.0 + 30.0 * 2.5 + 6.0)  # +6 K at depth
    assert scenario.model_overrides["heatpump"] == {
        "max_carnot_fraction": 0.5,
        "datasheet": HP_DATASHEET_LABEL,
    }
    assert scenario.doublet_design == {"injectivity_factor": 0.8}
    cfg = write_scenario_config(scenario, tmp_path / "config")
    sub = load_subsurface_basis(cfg)  # TL-05's margin check accepts the coupled values
    assert sub.bubble_point_margin_factor == 1.3 and sub.skin == 2.0
    assert load_config("assumptions", cfg).get("HP-APPROACH").value == {
        "condenser_approach_k": 3.0,
        "evaporator_approach_k": 3.0,
    }
    with pytest.raises(ValueError, match="unknown uncertain input"):
        scenario_for({"no_such": 1.0}, base, depth_m=2500.0, name="t")


def test_latin_hypercube_is_seeded_and_stratified():
    """Same seed ⇒ same draws; each input's n samples fill n equal-probability strata."""
    inputs, _ = load_uncertain_inputs()
    costs = {"discount_rate_frac": (0.75, 1.25)}
    a = sample_inputs(inputs, costs, None, 20, seed=11)
    b = sample_inputs(inputs, costs, None, 20, seed=11)
    c = sample_inputs(inputs, costs, None, 20, seed=12)
    assert a == b and a != c
    gradient = next(i for i in inputs if i.name == "geothermal_gradient_k_per_km")
    strata = sorted(
        int(
            (v["geothermal_gradient_k_per_km"] - gradient.low) / (gradient.high - gradient.low) * 20
        )
        for v, _ in a
    )
    assert strata == list(range(20))
    assert all(0.75 <= f["discount_rate_frac"] <= 1.25 for _, f in a)
    assert sample_inputs(inputs, costs, None, 0, seed=1) == []


def test_percentiles_and_spearman_drivers_on_synthetic_samples():
    """KPI = 2x + noise-free ⇒ ρ(x) = 1; percentiles are numpy's 10/50/90th."""
    samples = [
        MonteCarloSample(
            index=i,
            values={"x": float(i), "flat": 1.0, "anti": float(-i * i)},
            cost_factors={"c": 1.0 + 0.01 * ((i * 7) % 5)},
            status="ok",
            kpis={"lcoh_per_mwh": 2.0 * i, "co2_t_per_yr": 5.0},
            meets_demand=True,
        )
        for i in range(11)
    ]
    table = percentile_table(samples, {"p10": 10, "p50": 50, "p90": 90})
    assert table["lcoh_per_mwh"] == {"p10": 2.0, "p50": 10.0, "p90": 18.0}
    drivers = driver_ranking(samples, 3)
    top = drivers["lcoh_per_mwh"]
    assert {top[0].input_name, top[1].input_name} == {"x", "anti"}
    assert math.isclose(abs(top[0].spearman_rho), 1.0)
    assert "flat" not in {d.input_name for d in top}
    assert "co2_t_per_yr" not in drivers  # constant KPI: no ranking


# --------------------------------------------------------------------------- analysis
def test_monte_carlo_percentiles_and_kpiset(report):
    """P10 ≤ P50 ≤ P90 in KPISet.percentiles; worst-case margins; probability of meeting demand."""
    assert len(report.samples) == 20 and report.base.status == "ok"
    for kpi in ("lcoh_per_mwh", "lcoc_per_mwh", "co2_t_per_yr", "unmet_heating_days"):
        row = report.kpis.percentiles[kpi]
        assert row["p10"] <= row["p50"] <= row["p90"]
    assert report.kpis.percentiles == report.percentiles
    assert report.kpis.candidate_id == DESIGN.id
    assert report.probability_demand_met is not None and 0.0 <= report.probability_demand_met <= 1.0
    base_margins = report.base.kpis.min_constraint_margins  # type: ignore[union-attr]
    for key, value in report.kpis.min_constraint_margins.items():
        assert value <= base_margins.get(key, value)
    assert report.drivers and all(len(v) <= 8 for v in report.drivers.values())


def test_required_backups_and_robust_nameplates(report):
    """Required = installed + largest daily unmet MW (cap-only units); P90 → robust nameplates."""
    installed = report.installed_backup_mw
    assert installed == {"peak_boiler_mw": 10.0, "electric_chiller_mw": 9.0}  # DESIGN's nameplates
    for sample in report.samples:
        if sample.status != "ok":
            continue
        assert sample.kpis["required_peak_boiler_mw"] >= installed["peak_boiler_mw"]
        if sample.kpis["unmet_heating_mwh"] == 0.0:
            assert sample.kpis["required_peak_boiler_mw"] == installed["peak_boiler_mw"]
        if sample.kpis["unmet_cooling_mwh"] == 0.0:
            assert sample.kpis["required_electric_chiller_mw"] == installed["electric_chiller_mw"]
    for key, value in report.robust_backup_mw.items():
        assert value >= installed[key]
    robust, plain = report.probability_demand_met_robust, report.probability_demand_met
    assert robust is not None and plain is not None and robust >= plain
    if report.robust is not None:
        sizes = report.robust.candidate.equipment_sizes
        assert {k: sizes[k] for k in installed} == report.robust_backup_mw
    assert report.cost_ranges and all(lo < 1.0 < hi for lo, hi in report.cost_ranges.values())


def _chp_ratio(kpis) -> float:
    """CHP electricity generated / CHP heat delivered of a year (both from TL-12 KPIs)."""
    assert kpis is not None and kpis.electricity_generated_mwh
    return kpis.electricity_generated_mwh / kpis.heat_by_source_mwh["chp"]


def test_discrete_cases_include_db31_eco_co2_and_chp(report):
    """UQ-CASES: DB-31 45/65/75 (eval S6), ECO-CO2-GAS 51.53, CHP-EL-FRAC 0.50, DB-30 B."""
    cases = {c.case: c for c in report.cases}
    assert {"DB-31=45", "DB-31=65", "DB-31=75", "ECO-CO2-GAS=51.53", "CHP-EL-FRAC=0.5"} <= set(
        cases
    )
    assert {"SUB-PBUB.basis=pure_water", "DB-34b=warm", "DB-30=B"} <= set(cases)
    for name in ("DB-31=45", "DB-31=65", "DB-31=75"):
        e = cases[name].evaluation
        assert e.candidate.heating_return_degc == float(name.split("=")[1])
        assert cases[name].assumption_id == "DB-31"
    # A colder return lets the plate HEX take more heat: CO₂ and cost fall (the S6 trade-off).
    cold, hot = cases["DB-31=45"].delta_co2_t_per_yr, cases["DB-31=75"].delta_co2_t_per_yr
    assert cold is not None and hot is not None and cold < 0 < hot
    chp = cases["CHP-EL-FRAC=0.5"]
    assert chp.evaluation.status == "ok" and chp.delta_cost_per_mwh is not None
    assert chp.delta_lcoh_per_mwh != 0.0 and chp.delta_co2_t_per_yr != 0.0  # reaches chp.py
    # Post-submission check (FINAL_SPRINT §C): the case reaches chp.py's electrical_fraction.
    # CHP electricity / CHP heat = f / (1 - f): 0.5 at the base 1/3, 1.0 at the 0.50 case.
    assert _chp_ratio(report.base.kpis) == pytest.approx(0.5, rel=1e-6)
    assert _chp_ratio(chp.evaluation.kpis) == pytest.approx(1.0, rel=1e-6)
    base_margins = report.base.kpis.min_constraint_margins  # type: ignore[union-attr]
    pure_case = cases["SUB-PBUB.basis=pure_water"].evaluation
    assert pure_case.kpis is not None
    pure = pure_case.kpis.min_constraint_margins
    assert pure["esp_1_intake_margin_bar"] != base_margins["esp_1_intake_margin_bar"]  # TL-05
    assert cases["DB-30=B"].evaluation.outage_start_indices is None


def test_cost_tornado_top_8_from_wp08(report):
    """ECO-06: WP-08 cost_sensitivity for LCOH and LCOC, at most 8 bars, sorted by swing."""
    assert set(report.cost_tornado) == {"lcoh", "lcoc"}
    for sens in report.cost_tornado.values():
        swings = [b.swing for b in sens.bars]
        assert 0 < len(swings) <= 8 and swings == sorted(swings, reverse=True)


def test_one_at_a_time_bars_and_fixed_capex(year):
    """Low/high per input, sorted by swing; the installed equipment stays the base design's."""
    request = UncertaintyRequest(
        candidate=DESIGN,
        samples=0,
        inputs=["heating_demand_scale", "skin", "electric_chiller_carnot_efficiency"],
        vary_costs=False,
        cases=False,
        cost_tornado=False,
        day_indices=DAYS,
        write_outputs=False,
    )
    report = analyse(request, year=year)
    bars = {b.input_name: b for b in report.one_at_a_time}
    assert set(bars) == set(request.inputs)  # type: ignore[arg-type]
    swings = [b.swing_per_mwh for b in report.one_at_a_time]
    assert swings == sorted(swings, reverse=True)
    demand = bars["heating_demand_scale"]
    assert (demand.low_value, demand.high_value) == (0.9, 1.1) and demand.swing_per_mwh > 0
    assert demand.cost_low_per_mwh is not None and demand.cost_high_per_mwh is not None
    assert demand.swing_per_mwh == pytest.approx(
        abs(demand.cost_high_per_mwh - demand.cost_low_per_mwh)
    )
    assert demand.lcoh_low_per_mwh is not None and demand.lcoc_low_per_mwh is not None
    assert demand.cost_low_per_mwh == pytest.approx(
        demand.lcoh_low_per_mwh + demand.lcoc_low_per_mwh
    )
    assert (
        report.samples == [] and report.percentiles == {} and report.probability_demand_met is None
    )
    # COOL-ELC η is inert while the chiller sits on its COP cap (20 °C chilled water, RC-06).
    assert bars["electric_chiller_carnot_efficiency"].swing_per_mwh == pytest.approx(0.0, abs=1e-9)


def test_same_seed_same_monte_carlo_serial_and_parallel(year):
    """UQ-MC reproducibility: identical samples and KPIs for one seed, also with 2 workers."""
    request = UncertaintyRequest(
        candidate=DESIGN,
        samples=4,
        seed=5,
        inputs=["permeability_md", "heating_demand_scale"],
        one_at_a_time=False,
        cases=False,
        cost_tornado=False,
        day_indices=DAYS,
        write_outputs=False,
    )
    a = analyse(request, year=year)
    b = analyse(request.model_copy(update={"workers": 2}), year=year)
    assert [s.model_dump() for s in a.samples] == [s.model_dump() for s in b.samples]
    assert a.percentiles == b.percentiles


def test_tool_result_summary_and_files(tmp_path, year, monkeypatch):
    """Check the ≤ 300-char summary and the JSON + samples CSV under runs/<id>/."""
    import gemini_agent.tools.uncertainty as module

    monkeypatch.setattr(module, "year_conditions", lambda cfg=None: year)
    result = uncertainty(
        UncertaintyRequest(
            candidate=DESIGN,
            samples=3,
            inputs=["skin"],
            one_at_a_time=False,
            cases=False,
            cost_tornado=False,
            day_indices=DAYS,
            run_id="uq-test",
        ),
        runs_root=tmp_path,
    )
    assert len(result.summary) <= SUMMARY_MAX_CHARS and result.summary.startswith("TL-15")
    assert "UQ-RANGES" in result.assumption_ids
    data = json.loads((tmp_path / "uq-test" / "uncertainty.json").read_text(encoding="utf-8"))
    assert len(data["samples"]) == 3
    rows = (tmp_path / "uq-test" / "uncertainty_samples.csv").read_text().splitlines()
    assert len(rows) == 4 and "skin" in rows[0]
    with pytest.raises(ValueError, match="unknown UQ-RANGES"):
        analyse(UncertaintyRequest(candidate=DESIGN, inputs=["nope"], day_indices=DAYS), year=year)
