"""Tests for TL-13 ``optimise_design`` (WP-11): tiny budgets on a day subset keep CI fast.

The full-size C2 optimisation runs once via ``scripts/run_optimisation.py`` (PROGRESS.md).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
import yaml

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS
from gemini_agent.schemas.config import default_config_dir, load_config
from gemini_agent.tools.dispatch import availability_calendar, db30_rule
from gemini_agent.tools.optimise import (
    DesignEvaluation,
    EvaluationPool,
    Evaluator,
    OptimiseRequest,
    Scenario,
    SearchSpace,
    Variable,
    capex_request,
    load_optimiser_settings,
    make_design_candidate,
    optimise,
    optimise_design,
    pareto_points,
    scenario_year,
    write_scenario_config,
)
from gemini_agent.tools.simulate_annual import year_conditions

DAYS = [14, 45, 195, 225, 350]
"""Jan, Feb, Jul, Aug, Dec: the heating peak, the cooling peak, outside the DB-30 B window."""


def candidate(n: int = 2, flow: float = 300.0, concept: str = "C2", **sizes: float):
    """Return a search-point candidate at 2.5 km (DB-31/DB-32 returns)."""
    return make_design_candidate(
        concept,  # type: ignore[arg-type]
        n,
        2500.0,
        flow,
        sizes,
        heating_return_degc=55.0,
        cooling_return_degc=26.0,
    )


@pytest.fixture(scope="module")
def year():
    """Load the design year once."""
    return year_conditions()


@pytest.fixture(scope="module")
def evaluator(year):
    """Build the base evaluator on the day subset."""
    return Evaluator.build(year=year, day_indices=DAYS)


def tiny_request(**update) -> OptimiseRequest:
    """Return a tiny request: two doublet counts at 2.5 km, one generation per DE stage."""
    data = {
        "concept": "C2",
        "doublet_options": [1, 2],
        "depth_options_m": [2500.0],
        "top_k": 1,
        "popsize": 1,
        "maxiter": 1,
        "maxiter_outage": 1,
        "day_indices": DAYS,
        "write_outputs": False,
    }
    data.update(update)
    return OptimiseRequest(**data)


@pytest.fixture(scope="module")
def tiny_run(year):
    """Run one tiny optimisation with every post-search stage on."""
    return optimise(tiny_request(), year=year)


# --------------------------------------------------------------------------- settings
def test_settings_come_from_the_register():
    """OPT-SPACE/OPT-OBJ/OPT-DE: OPT-01 depths, seeded DE, the barrier above any penalty."""
    s = load_optimiser_settings()
    register = load_config("assumptions")
    assert s.depth_options_m == [1500.0, 2000.0, 2500.0, 3000.0, 3200.0, 3500.0]
    assert s.doublet_options == [1, 2, 3]
    assert s.seed == register.get("OPT-DE").value["seed"]  # type: ignore[index]
    assert s.rejected_objective > s.unmet_penalty_base_per_mwh > 0


def test_scenario_config_copy_edits_only_named_values(tmp_path):
    """Dotted design-basis paths and register keys are edited in a copy; typos raise."""
    scenario = Scenario(
        design_basis={"reservoir.permeability_md": 300.0},
        assumptions={"SUB-PSUC": 2.0, "HP-APPROACH.condenser_approach_k": 3.0},
    )
    cfg = write_scenario_config(scenario, tmp_path / "config")
    assert load_config("design_basis", cfg).reservoir.permeability_md == 300.0
    register = load_config("assumptions", cfg)
    assert register.get("SUB-PSUC").value == 2.0
    assert register.get("HP-APPROACH").value["condenser_approach_k"] == 3.0  # type: ignore[index]
    assert register.get("HP-APPROACH").value["evaporator_approach_k"] == 4.0  # type: ignore[index]
    assert load_config("design_basis").reservoir.permeability_md == 200.0  # original untouched
    for bad in (
        Scenario(design_basis={"reservoir.permeability": 1.0}),
        Scenario(assumptions={"NO-SUCH": 1.0}),
        Scenario(assumptions={"SUB-PSUC.key": 1.0}),
    ):
        with pytest.raises(KeyError):
            write_scenario_config(bad, tmp_path / "bad")


def test_scenario_year_scales_demand_and_shifts_ambient(year):
    """UQ-01 demand ±10 % and the DB-34b warm offset act on every day."""
    shifted = scenario_year(
        year,
        Scenario(
            heating_demand_scale=1.1,
            cooling_demand_scale=0.9,
            dry_bulb_shift_k=5.0,
            wet_bulb_shift_k=2.6,
        ),
    )
    for a, b in zip(year.days, shifted.days, strict=True):
        assert b.heating_demand_mw == pytest.approx(1.1 * a.heating_demand_mw)
        assert b.cooling_demand_mw == pytest.approx(0.9 * a.cooling_demand_mw)
    assert scenario_year(year, Scenario()) is year
    for a, b in zip(year.days, shifted.days, strict=True):
        expected = 5.0 if a.cooling_demand_mw > 0 else 0.0  # DB-34b is a summer design point
        assert b.ambient_dry_bulb_degc == pytest.approx(a.ambient_dry_bulb_degc + expected)


# --------------------------------------------------------------------------- search space
def test_outage_transform_always_gives_a_valid_db30_schedule(year):
    """Any DE vector decodes to non-overlapping outages inside the year (DB-30 A, SIM-OUTAGE)."""
    settings = load_optimiser_settings()
    rule = db30_rule()
    for n in (1, 2, 3):
        span = 365 - n * rule.planned_outage_days_per_doublet
        space = SearchSpace(
            concept="C2",
            doublets=n,
            depth_m=2500.0,
            variables=[Variable(name="design_flow_m3h", low=50.0, high=300.0)]
            + [Variable(name=f"outage_{i}", low=0, high=span, integer=True) for i in range(n)],
            outage_days=rule.planned_outage_days_per_doublet,
        )
        rng = np.random.default_rng(n)
        for _ in range(200):
            x = [rng.uniform(50, 300)] + list(rng.integers(0, span + 1, size=n))
            flow, _, starts = space.decode(x, settings)
            assert flow == round(flow) and 50.0 <= flow <= 300.0
            calendar = availability_calendar(
                n, year.year, [1.0] * 365, rule=rule, outage_start_indices=starts
            )
            assert calendar.outage_start_indices == starts
            assert space.encode_outages(starts) == sorted(int(v) for v in x[1:])


# --------------------------------------------------------------------------- evaluation
def test_hard_limit_violation_is_rejected_with_the_guard_reason_never_priced(evaluator):
    """Golden rule 4: DB-10/DB-11 violations get the guard's reason and the barrier, no KPIs."""
    for bad, code in ((candidate(n=4), "DB-10"), (candidate(flow=320.0), "DB-11")):
        e = evaluator.evaluate(bad)
        assert e.status == "rejected" and code in e.violations
        assert e.reason.startswith("TL-10") and code in e.reason
        assert e.objective_per_mwh == evaluator.settings.rejected_objective
        assert e.kpis is None and e.lcoh_per_mwh is None and not e.meets_demand


def test_objective_is_lcoh_plus_lcoc_and_unmet_is_penalised(evaluator):
    """OPT-OBJ: meets demand ⇒ LCOH + LCOC; a too-small boiler ⇒ base + slope × unmet MWh."""
    good = evaluator.evaluate(candidate())
    assert good.status == "ok" and good.meets_demand and good.penalty_per_mwh == 0.0
    assert good.objective_per_mwh == pytest.approx(good.lcoh_per_mwh + good.lcoc_per_mwh)
    short = evaluator.evaluate(
        candidate(hthp_heating_mw=1.0, peak_boiler_mw=0.0, electric_chiller_mw=0.5), None
    )
    assert short.status == "ok", short.reason  # supply shortfall = unmet demand (penalised)
    assert short.limit_breaches  # DB-16/DB-17 margins < 0 come with the unmet demand
    unmet = short.unmet_heating_mwh + short.unmet_cooling_mwh  # type: ignore[operator]
    s = evaluator.settings
    assert unmet > 0 and not short.meets_demand
    assert short.penalty_per_mwh == pytest.approx(
        s.unmet_penalty_base_per_mwh + s.unmet_penalty_per_unmet_mwh * unmet
    )
    assert short.objective_per_mwh == pytest.approx(
        short.lcoh_per_mwh + short.lcoc_per_mwh + short.penalty_per_mwh
    )
    # (On a full year LCOH + LCOC ≈ 90 USD/MWh, so the 1000 USD/MWh base always dominates; the
    # day subset inflates LCOH, hence no ordering assertion here.)
    assert short.penalty_per_mwh > s.unmet_penalty_base_per_mwh


def test_capex_request_maps_the_generated_plant(evaluator):
    """Plate HEX per doublet at the design ṁ/ΔT, nameplates, absorption F on brine (WP-08)."""
    run = evaluator.run(
        candidate(concept="C3", absorption_chiller_mw=4.0, hthp_heating_mw=7.0), keep=True
    )
    assert run.plant is not None and run.simulation is not None
    capex = capex_request(run.plant, run.simulation.annual)
    h = run.plant.meta.hex_design
    assert h is not None and len(capex.plate_hexes) == 2
    assert capex.plate_hexes[0].delta_t_k == pytest.approx(h.brine_in_degc - h.brine_out_degc)
    assert capex.plate_hexes[0].mass_flow_kg_s == pytest.approx(300.0 * 1030.0 / 3600.0)
    assert capex.hthp_heating_mw == 7.0 and capex.absorption_chiller_mw == 4.0
    assert 0.0 < capex.absorption_capacity_fraction <= 1.0  # type: ignore[operator]
    assert capex.absorption_titanium_tubes
    assert capex.gas_boiler_mw == 30.0 and capex.electric_chiller_mw == 10.0  # CFG-SIZING peaks


def test_scenario_model_override_reaches_the_gemini_model(year):
    """VER-HP-ETA override (with its register label) changes the HTHP COP on the fast plant."""
    base = Evaluator.build(year=year, day_indices=[14]).run(candidate(), keep=True)
    low = Evaluator.build(
        scenario=Scenario(
            name="hp-0.5",
            model_overrides={"heatpump": {"max_carnot_fraction": 0.5, "datasheet": "test"}},
        ),
        year=year,
        day_indices=[14],
    ).run(candidate(), keep=True)
    e_base = base.simulation.annual.days[0].electricity_by_consumer_mw  # type: ignore[union-attr]
    e_low = low.simulation.annual.days[0].electricity_by_consumer_mw  # type: ignore[union-attr]
    hp = [k for k in e_base if k.startswith("hthp")]
    assert hp and sum(e_low[k] for k in hp) > sum(e_base[k] for k in hp)


def test_operational_hard_limit_breach_is_rejected_not_penalised(year, monkeypatch):
    """A WP-07 margin below zero on DB-11/12/14/16/17 rejects the year (golden rule 4)."""
    import gemini_agent.tools.optimise as module

    real = module.economics

    def breached(request, annual, **kwargs):
        result = real(request, annual, **kwargs)
        margins = result.data.kpis.min_constraint_margins | {"heating_supply_margin_k": -7.4}
        kpis = result.data.kpis.model_copy(update={"min_constraint_margins": margins})
        return result.model_copy(update={"data": result.data.model_copy(update={"kpis": kpis})})

    monkeypatch.setattr(module, "economics", breached)
    e = Evaluator.build(year=year, day_indices=DAYS).evaluate(candidate())
    assert e.status == "rejected" and e.violations == ["DB-16"]
    assert "heating_supply_margin_k = -7.4 (DB-16)" in e.reason and e.kpis is None
    kept = Evaluator.build(year=year, day_indices=DAYS, reject_limit_breaches=False)
    e = kept.evaluate(candidate())
    assert e.status == "ok" and e.limit_breaches == ["heating_supply_margin_k = -7.4 (DB-16)"]
    assert not e.meets_demand  # TL-15 counts it as demand not met
    assert (
        module.limit_breaches({"hthp_capacity_margin_mw": -1.0, "doublet_2_flow_margin_m3h": -1e-9})
        == []
    )


def test_electric_chiller_covers_the_db34b_design_day(year):
    """Right-sized chiller ≥ its duty on the DB-19 peak day at the DB-34b design ambient."""
    evaluator = Evaluator.build(year=year, day_indices=DAYS, right_size_backups=True)
    run = evaluator.run(candidate(hthp_heating_mw=6.0), keep=True)
    design_duty = evaluator.design_day_chiller_mw(run.plant)  # type: ignore[arg-type]
    days = run.simulation.annual.days  # type: ignore[union-attr]
    largest = max(d.cool_by_source_mw.get("electric_chiller", 0.0) for d in days)
    assert design_duty > largest  # smoothed normals never reach 25/18.4 °C
    sized = run.evaluation.right_sized_mw["electric_chiller_mw"]
    assert design_duty <= sized < design_duty + 0.05 + 1e-9


def test_free_cooling_tower_is_priced(evaluator):
    """The free-cooling rejection beyond the chiller plant's own tower is a CapexRequest tower."""
    run = evaluator.run(candidate(electric_chiller_mw=2.0), keep=True)
    capex = capex_request(run.plant, run.simulation.annual)  # type: ignore[arg-type,union-attr]
    days = run.simulation.annual.days  # type: ignore[union-attr]
    rejection = max(
        d.cool_by_source_mw.get("free_cooling", 0.0)
        + d.cool_by_source_mw.get("electric_chiller", 0.0)
        + d.electricity_by_consumer_mw.get("electric_chiller", 0.0)
        for d in days
    )
    assert 0.0 < capex.cooling_tower_mw < rejection
    assert capex.free_cooling_mw > 0.0


def test_pool_caches_identical_designs(evaluator):
    """Same design twice ⇒ one evaluation (key excludes the candidate id)."""
    a = candidate()
    b = a.model_copy(update={"id": "renamed"})
    with EvaluationPool(evaluator) as pool:
        first, second = pool.evaluate([(a, None), (b, None)])
    assert first is second and len(pool.history) == 1


def test_pareto_points_are_non_dominated():
    """Lowest cost, lowest CO₂ and the knee; dominated and unmet designs are dropped."""

    def ev(key: str, objective: float, co2: float, unmet: float = 0.0) -> DesignEvaluation:
        return DesignEvaluation(
            key=key,
            candidate=candidate(),
            status="ok",
            objective_per_mwh=objective,
            co2_t_per_yr=co2,
            unmet_heating_mwh=unmet,
            unmet_cooling_mwh=0.0,
        )

    points = [
        ev("a", 100, 900),
        ev("b", 110, 500),
        ev("c", 150, 450),
        ev("d", 200, 100),
        ev("dominated", 160, 800),
        ev("unmet", 50, 50, unmet=1.0),
    ]
    chosen = pareto_points(points, 3)
    assert [p.key for p in chosen] == ["a", "b", "d"]
    assert pareto_points(points, 2)[0].key == "a"
    assert pareto_points([], 3) == []


# --------------------------------------------------------------------------- optimise
def test_tiny_optimisation_finds_a_feasible_guarded_design(tiny_run):
    """Integers enumerated; DE on sizes, then on outages; best meets demand, guard-approved."""
    result, history = tiny_run
    assert {(e.candidate.doublets, e.candidate.depth_m) for e in result.screening} == {
        (1, 2500.0),
        (2, 2500.0),
    }
    assert [run.stage for run in result.runs] == ["size", "outage"]
    size, outage = result.runs
    assert [v.name for v in size.variables] == ["design_flow_m3h", "hthp_heating_mw"]
    assert [v.name for v in outage.variables] == [f"outage_{i + 1}" for i in range(size.doublets)]
    assert size.nfev >= 5 and outage.nfev >= 5
    best = result.best
    assert best is not None and best.meets_demand and best.status == "ok"
    assert best.candidate.id.endswith("-opt") and best.stage == "final"
    searched = [e for e in history if e.stage.startswith(("screen", "de-")) and e.meets_demand]
    assert best.objective_per_mwh == pytest.approx(
        min(e.objective_per_mwh for e in searched), rel=1e-9
    )
    assert best.outage_start_indices is not None
    assert result.default_outage is not None and result.default_outage.outage_start_indices is None
    # The outage stage is seeded with the SIM-OUTAGE schedule, so it can only improve on it.
    assert best.objective_per_mwh <= result.default_outage.objective_per_mwh + 1e-9
    assert len(result.pareto) <= 3
    assert result.pareto[0].objective_per_mwh <= result.pareto[-1].objective_per_mwh
    for point in result.pareto:  # the priced design: right-sized nameplates written in
        assert {"peak_boiler_mw", "electric_chiller_mw"} <= set(point.candidate.equipment_sizes)
    assert result.n_evaluations == len(history)
    assert not [w for w in result.warnings if "right-sized" in w]


def test_backups_are_right_sized_and_confirmed(tiny_run):
    """OPT-SPACE: boiler/chiller = largest daily output (rounded up), re-run with zero unmet."""
    result, history = tiny_run
    best = result.best
    sizes = best.candidate.equipment_sizes  # type: ignore[union-attr]
    assert {"peak_boiler_mw", "electric_chiller_mw"} <= set(sizes)
    searched = min(
        (e for e in history if e.stage.startswith(("screen", "de-")) and e.meets_demand),
        key=lambda e: e.objective_per_mwh,
    )
    assert searched.right_sized_mw == {
        k: sizes[k] for k in ("peak_boiler_mw", "electric_chiller_mw")
    }
    assert best.right_sized_mw == {}  # explicit nameplates are never re-sized
    peak = best.boiler_peak_mw  # type: ignore[union-attr]
    assert peak is not None and sizes["electric_chiller_mw"] >= 0.5
    assert peak <= sizes["peak_boiler_mw"] < peak + 0.05 + 1e-9  # rounded up to 0.05 MW


def test_outage_stage_reuses_the_plant_and_memoises(year):
    """Same sizes, another schedule: the reused plant recomputes only the changed days."""
    evaluator = Evaluator.build(year=year, day_indices=DAYS)
    design = candidate(hthp_heating_mw=6.0)
    first = evaluator.run(design, [10, 190], keep=True)
    plant = first.plant
    n_run = len(plant._memo_run)  # type: ignore[union-attr]
    second = evaluator.run(design, [50, 200], keep=True)
    assert second.plant is plant
    assert len(plant._memo_run) < 2 * n_run  # type: ignore[union-attr]
    fresh = Evaluator.build(year=year, day_indices=DAYS).evaluate(design, [50, 200])
    assert (
        fresh.objective_per_mwh == second.evaluation.objective_per_mwh
    )  # memo gives identical results


def test_dispatch_levers_are_priced_from_register_bounds(tiny_run):
    """DSP-HP-MIN at 0 and 0.25 (register sensitivity) evaluated on the optimum."""
    result, _ = tiny_run
    levers = {(d.lever, d.value) for d in result.dispatch_variants}
    assert all(d.evaluation.stage == "dispatch" for d in result.dispatch_variants)
    assert ("DSP-HP-MIN.min_part_load_frac", 0.0) in levers
    assert ("DSP-HP-MIN.max_extra_flow_frac", 0.25) in levers
    for variant in result.dispatch_variants:
        assert variant.evaluation.scenario == f"{variant.lever}={variant.value:g}"
        assert variant.delta_objective_per_mwh is not None


def test_storage_scan_is_unpriced_c6(tiny_run):
    """C6 children of the optimum at derived sizes, flagged unpriced (no RC-10 ATES cost)."""
    result, _ = tiny_run
    for e in result.storage_scan:
        assert e.candidate.concept == "C6" and "not priced" in e.reason
        assert e.candidate.parent_id == result.best.candidate.id  # type: ignore[union-attr]
    assert any("HT-ATES" in w or "no boiler gas" in w for w in result.warnings)


def test_same_seed_same_result_serial_and_parallel(tiny_run, year):
    """OPT-03 reproducibility: identical history for the same seed, also with a process pool."""
    first, history = tiny_run
    quick = {"dispatch_search": False, "storage_sizes": []}
    again, history_again = optimise(tiny_request(**quick), year=year)
    parallel, history_parallel = optimise(tiny_request(workers=2, **quick), year=year)
    reference = [
        (e.key, e.objective_per_mwh) for e in history if e.stage.startswith(("screen", "de-"))
    ]
    for other in (history_again, history_parallel):
        assert [
            (e.key, e.objective_per_mwh) for e in other if e.stage.startswith(("screen", "de-"))
        ] == (reference)
    for other in (again, parallel):
        assert other.best is not None and first.best is not None
        assert other.best.key == first.best.key
        assert other.best.objective_per_mwh == first.best.objective_per_mwh
    other_seed, _ = optimise(tiny_request(seed=7, **quick), year=year)
    assert other_seed.seed == 7


def test_failed_final_rerun_reports_no_optimum(year, monkeypatch):
    """If the optimum fails its re-run with the right-sized nameplates: best = None, a warning."""
    real = Evaluator.evaluate

    def evaluate(self, candidate, outages=None, *, stage=""):
        e = real(self, candidate, outages, stage=stage)
        return e.model_copy(update={"unmet_heating_mwh": 1.0}) if stage == "final" else e

    monkeypatch.setattr(Evaluator, "evaluate", evaluate)
    request = tiny_request(doublet_options=[2], maxiter=0, optimise_outages=False)
    result, history = optimise(request.model_copy(update={"dispatch_search": False}), year=year)
    assert result.best is None and result.storage_scan == []
    assert any("no optimum reported" in w for w in result.warnings)
    assert history[-1].stage == "final" and not history[-1].meets_demand


def test_tool_result_summary_and_files(tmp_path, year):
    """ToolResult: ≤ 300-char summary with the optimum; JSON + CSV written to runs/<id>/."""
    result = optimise_design(
        tiny_request(
            doublet_options=[2],
            write_outputs=True,
            run_id="opt-test",
            dispatch_search=False,
            storage_sizes=[],
            maxiter=0,
            optimise_outages=False,
        ),
        runs_root=tmp_path,
    )
    assert len(result.summary) <= SUMMARY_MAX_CHARS and result.summary.startswith("TL-13 C2")
    assert "OPT-OBJ" in result.assumption_ids
    data = json.loads((tmp_path / "opt-test" / "optimisation.json").read_text(encoding="utf-8"))
    assert data["best"]["status"] == "ok"
    rows = (tmp_path / "opt-test" / "optimisation_evaluations.csv").read_text().splitlines()
    assert rows[0].startswith("key,stage") and len(rows) == result.data.n_evaluations + 1


def test_register_entries_are_complete():
    """Every OPT-*/UQ-* entry states a source and justification (golden rule 2)."""
    path = default_config_dir() / "assumptions.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for entry in raw["assumptions"]:
        if entry["id"].startswith(("OPT-", "UQ-")):
            # OPT-ROBUST-BACKUP was added before L-2 (DR-11); the rest by WP-11.
            assert entry["added_by"] in {"WP-11", "Pre-L-2"}
            assert entry["source"] and entry["justification"]
    assert math.isfinite(load_optimiser_settings().tol)


def test_pool_is_terminated_when_the_block_fails():
    """WP-14: an error (or SIGTERM → SystemExit) inside ``with EvaluationPool`` stops the
    workers at once, so a timed-out run leaves no orphan processes."""
    from types import SimpleNamespace

    pool = EvaluationPool(SimpleNamespace(scenario=SimpleNamespace(name="base")), workers=2)
    if pool._pool is None:  # no fork start method on this platform: nothing to leak
        return
    workers = list(pool._pool._pool)
    with pytest.raises(SystemExit), pool:
        raise SystemExit(143)
    assert pool._pool is None
    for process in workers:
        process.join(timeout=10)
        assert not process.is_alive()
