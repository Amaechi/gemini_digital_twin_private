"""GM-03 (WP-03): closed-form heat-pump path, HTHP limits and verifier evidence (RC-04)."""

import itertools
import math
import warnings
from pathlib import Path

import pytest

from gemini_model.heatpump import closed_form as cf
from gemini_model.heatpump.heatpump_basic import HeatpumpBasic

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "config" / "assumptions.yaml"
GEMINI_PARAMS = {
    "eta_carnot": 0.5,
    "eta_lorenz": 0.5,
    "COP_0": 3.0,
    "Cp_h": 4181,
    "Cp_s": 4181,
    "rho_h": 1000,
    "rho_s": 1000,
}
# RC-04 "Design use": sink = district water 82 -> 100 °C, source = brine 60 -> 46 °C. The source
# flow makes the uncapped Lorenz point (η_L 0.5, COP 4.79) leave the source at 46 °C:
# V_s / V_h = (1 - 1/4.7935) × 18 / 14.
RC04_PARAMS = {**GEMINI_PARAMS, "mode": "lorenz", "Th_out_target": 100.0, "Ts_in_minimum": 10.0}
RC04_SOURCE_M3H = 100.0 * (1.0 - 1.0 / 4.793508) * 18.0 / 14.0
RC04_INPUTS = {
    "Th_in": 82.0,
    "Ts_in": 60.0,
    "hot_flow_rate": 100.0 / 3600.0,
    "source_flow_rate": RC04_SOURCE_M3H / 3600.0,
}


def run(parameters, inputs):
    """Run HeatpumpBasic once and return its outputs."""
    heatpump = HeatpumpBasic()
    heatpump.update_parameters(parameters)
    heatpump.calculate_output(inputs)
    return heatpump.get_output()


def equation_residuals(output, parameters, inputs):
    """Relative residuals of GEMINI's two equations (COP equation, energy balance)."""
    th_in, ts_in = inputs["Th_in"], inputs["Ts_in"]
    th_out, ts_out, cop = output["Th_out"], output["Ts_out"], output["COP"]
    if parameters["mode"] == "lorenz":
        model = cf.lorenz_cop(th_in, th_out, ts_in, ts_out, parameters["eta_lorenz"])
    else:
        model = cf.carnot_cop(th_out, ts_in, parameters["eta_carnot"])
    q_h = parameters["Cp_h"] * (th_out - th_in) * inputs["hot_flow_rate"] * parameters["rho_h"]
    q_s = parameters["Cp_s"] * (ts_in - ts_out) * inputs["source_flow_rate"] * parameters["rho_s"]
    return abs(cop - model) / abs(cop), abs(cop - q_h / (q_h - q_s)) / abs(cop)


# --- RC-04 validation tests -------------------------------------------------------------------


def test_guidebook_cop_reproduced():
    """RC-04: COP_h 4.41 with η = 0.456 at 15.6 °C evaporator / 48.9 °C condenser (±1 %)."""
    assert cf.carnot_cop(48.9, 15.6, 0.456) == pytest.approx(4.41, rel=0.01)


def test_lorenz_cop_reproduced():
    """RC-04: source 60→46 °C, sink 82→100 °C, η_L 0.5 ⇒ COP 4.79 (±1 %)."""
    assert cf.lorenz_cop(82.0, 100.0, 60.0, 46.0, 0.5) == pytest.approx(4.79, rel=0.01)
    # hand check: T̄_sink 364.08 K, T̄_source 326.10 K
    assert cf.log_mean_temperature_k(355.15, 373.15) == pytest.approx(364.08, abs=0.01)
    assert cf.log_mean_temperature_k(333.15, 319.15) == pytest.approx(326.10, abs=0.01)
    assert cf.mean_lift_k(82.0, 100.0, 60.0, 46.0) == pytest.approx(37.98, abs=0.01)


def test_closed_form_model_reproduces_rc04_design_point():
    """Uncapped closed form: COP 4.79 (±1 %) with the source leaving at 46 °C."""
    out = run({**RC04_PARAMS, "solver": "closed_form", "apply_cop_cap": False}, RC04_INPUTS)
    assert out["COP"] == pytest.approx(4.79, rel=0.01)
    assert out["Ts_out"] == pytest.approx(46.0, abs=0.01)
    assert out["Th_out"] == 100.0
    assert out["operating_branch"] == "target"


def test_energy_balance_closes():
    """RC-04 eq. 3: Q_h = Q_src·COP/(COP−1), P_el = Q_h/COP; the source stream carries Q_src."""
    out = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    q_h, p_el, q_s = out["Thermal_production"], out["electrical_consumption"], out["source_heat_w"]
    assert q_h == pytest.approx(q_s + p_el, rel=1e-12)
    assert q_h == pytest.approx(cf.sink_heat_from_source(q_s, out["COP"]), rel=1e-12)
    assert p_el == pytest.approx(q_h / out["COP"], rel=1e-12)
    capacity_rate = 4181 * 1000 * RC04_INPUTS["source_flow_rate"]
    assert q_s == pytest.approx(capacity_rate * (60.0 - out["Ts_out"]), rel=1e-9)
    assert q_h == pytest.approx(4181 * 1000 * (100.0 / 3600.0) * 18.0, rel=1e-12)


def test_sink_heat_from_source_needs_cop_above_one():
    """Q_h = Q_src·COP/(COP−1) is undefined for COP ≤ 1."""
    assert cf.sink_heat_from_source(3.0e6, 4.0) == pytest.approx(4.0e6)
    with pytest.raises(ValueError, match="COP"):
        cf.sink_heat_from_source(1.0e6, 1.0)


def test_helper_edge_cases():
    """No glide → that temperature; no lift → inf COP."""
    assert cf.log_mean_temperature_k(350.0, 350.0) == 350.0
    assert cf.carnot_cop(50.0, 50.0) == math.inf
    assert cf.lorenz_cop(40.0, 50.0, 70.0, 60.0) == math.inf
    with pytest.raises(ValueError):
        cf.log_mean_temperature_k(0.0, 300.0)


# --- closed form vs GEMINI Nelder-Mead ---------------------------------------------------------


@pytest.mark.parametrize("mode", ["carnot", "lorenz"])
def test_closed_form_matches_gemini_reference_cases(mode):
    """The GEMINI unit-test cases (test_heatpump_basic.py): closed form within 2 % (here 1e-4)."""
    parameters = {**GEMINI_PARAMS, "mode": mode, "Th_out_target": 80, "Ts_in_minimum": 5}
    inputs = {"Th_in": 30, "Ts_in": 40, "hot_flow_rate": 10 / 3600, "source_flow_rate": 20 / 3600}
    reference = {"carnot": (4.4144, 20.6633), "lorenz": (6.5310, 18.8279)}[mode]
    out = run({**parameters, "solver": "closed_form", "apply_cop_cap": False}, inputs)
    assert out["COP"] == pytest.approx(reference[0], rel=1e-4)
    assert out["Ts_out"] == pytest.approx(reference[1], abs=1e-3)
    assert out["Thermal_production"] == pytest.approx(580694.44, abs=0.1)


def _grid():
    return itertools.product(
        ("lorenz", "carnot"), (55.0, 82.0), (90.0, 100.0), (45.0, 60.0, 75.0),
        (50.0, 150.0), (30.0, 250.0), (10.0, 40.0),
    )  # fmt: skip


def test_closed_form_within_2_percent_of_nelder_mead_where_both_converge():
    """RC-04 acceptance: where Nelder-Mead converges to a physical point, they agree within 2 %.

    Converged = both GEMINI equations hold to 0.1 % and the outlets lie between their bounds.
    Both branches (sink at target; source held at Ts_in_minimum) are covered.
    """
    compared, branches = 0, set()
    for mode, sink_in, target, source_in, hot_m3h, source_m3h, t_min in _grid():
        parameters = {**GEMINI_PARAMS, "mode": mode, "Th_out_target": target}
        parameters["Ts_in_minimum"] = t_min
        inputs = {
            "Th_in": sink_in,
            "Ts_in": source_in,
            "hot_flow_rate": hot_m3h / 3600.0,
            "source_flow_rate": source_m3h / 3600.0,
        }
        closed = run({**parameters, "solver": "closed_form", "apply_cop_cap": False}, inputs)
        if not closed["running"]:
            continue
        assert max(equation_residuals(closed, parameters, inputs)) < 1e-9
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # GEMINI's NM explores log(<0)
            nelder = run(parameters, inputs)
            residuals = equation_residuals(nelder, parameters, inputs)
        converged = (
            max(residuals) < 1e-3
            and nelder["COP"] > 1.0
            and sink_in < nelder["Th_out"] <= target + 1e-6
            and t_min - 1e-6 <= nelder["Ts_out"] <= source_in
        )
        if not converged:
            continue
        compared += 1
        branches.add(closed["operating_branch"])
        assert closed["COP"] == pytest.approx(nelder["COP"], rel=0.02)
        assert closed["electrical_consumption"] == pytest.approx(
            nelder["electrical_consumption"], rel=0.02
        )
        assert closed["Thermal_production"] == pytest.approx(nelder["Thermal_production"], rel=0.02)
    assert compared >= 50
    assert branches == {"target", "source_floor"}


def test_closed_form_solves_where_nelder_mead_fails():
    """Robustness: NM returns a negative COP here; the closed form satisfies both equations."""
    parameters = {**GEMINI_PARAMS, "mode": "lorenz", "Th_out_target": 90.0, "Ts_in_minimum": 10.0}
    inputs = {"Th_in": 55.0, "Ts_in": 75.0, "hot_flow_rate": 50 / 3600, "source_flow_rate": 1 / 36}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        nelder = run(parameters, inputs)
    assert not nelder["COP"] > 1.0
    closed = run({**parameters, "solver": "closed_form", "apply_cop_cap": False}, inputs)
    assert closed["running"] and closed["COP"] > 1.0
    assert max(equation_residuals(closed, parameters, inputs)) < 1e-9
    capped = run({**parameters, "solver": "closed_form"}, inputs)  # the RC-04 cap tames it
    assert capped["COP"] == pytest.approx(capped["cop_cap"], rel=1e-12)
    assert capped["COP"] < closed["COP"]


def test_source_floor_branch():
    """Source outlet held at Ts_in_minimum; the sink outlet drops below the target (GEMINI)."""
    parameters = {**RC04_PARAMS, "Ts_in_minimum": 50.0, "solver": "closed_form"}
    out = run({**parameters, "apply_cop_cap": False}, RC04_INPUTS)
    assert out["operating_branch"] == "source_floor"
    assert out["Ts_out"] == 50.0
    assert 82.0 < out["Th_out"] < 100.0
    assert any("Ts_in_minimum" in w for w in out["validity_warnings"])
    assert max(equation_residuals(out, parameters, RC04_INPUTS)) < 1e-9


def test_default_solver_is_gemini_nelder_mead():
    """GEMINI default behaviour is unchanged; the evidence outputs are added on both paths."""
    heatpump = HeatpumpBasic()
    assert heatpump.parameters["solver"] == "nelder_mead"
    out = run(RC04_PARAMS, RC04_INPUTS)
    assert out["solver"] == "nelder_mead" and out["operating_branch"] == "nelder_mead"
    assert out["COP"] == pytest.approx(4.79, rel=0.01)  # NM is never capped
    assert out["COP"] > out["cop_cap"] and not out["within_limits"]
    assert any("above 0.6 x Carnot" in w for w in out["validity_warnings"])
    assert out["source_heat_w"] == pytest.approx(
        out["Thermal_production"] - out["electrical_consumption"]
    )


# --- COP cap, HTHP limits (MISSING until a datasheet) and evidence -----------------------------


def test_cop_capped_at_rc04_default_without_datasheet():
    """COP ≤ 0.6 × Carnot at the refrigerant temperatures (VER-HP-ETA); approaches 4 K."""
    out = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    assert out["condensing_temperature_degc"] == 104.0
    assert out["evaporating_temperature_degc"] == pytest.approx(out["Ts_out"] - 4.0)
    carnot = (104.0 + 273.15) / (104.0 - out["evaporating_temperature_degc"])
    assert out["carnot_cop_refrigerant"] == pytest.approx(carnot, rel=1e-12)
    assert out["COP"] == out["cop_cap"] == pytest.approx(0.6 * carnot, rel=1e-12)
    assert out["cop_uncapped"] > out["COP"]
    # Less heat drawn from the source than uncapped: the brine leaves warmer than 46 °C.
    assert out["Ts_out"] == pytest.approx(47.07, abs=0.01)
    assert out["COP"] == pytest.approx(3.714, abs=0.001)  # RC-04 expects ≈ 3.5–4.5
    assert out["within_limits"]
    assert any("capped" in w for w in out["validity_warnings"])


def test_hthp_limits_flagged_missing_without_datasheet():
    """Lift, max sink temperature and the Carnot fraction are MISSING; defaults are cited."""
    out = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    assert out["missing_inputs"] == [
        "max_lift_k",
        "max_sink_temperature_degc",
        "max_carnot_fraction",
    ]
    assert out["max_sink_temperature_degc"] is None  # no default: not checked
    assert out["max_lift_k"] == 60.0 and out["max_carnot_fraction"] == 0.6
    assert out["limits_source"].startswith("MISSING")
    assert out["assumption_ids"] == ["VER-HP-ETA", "VER-HP-LIFT", "HP-APPROACH", "HP-ETA-L"]
    point = out["heat_pump_point"]
    assert point["max_lift_k"] is None and point["max_carnot_fraction"] is None


def test_datasheet_limits_need_a_citation_and_are_applied():
    """Never invented: limits without a datasheet citation are refused."""
    with pytest.raises(ValueError, match="datasheet"):
        run({**RC04_PARAMS, "solver": "closed_form", "max_lift_k": 70.0}, RC04_INPUTS)
    limits = {
        "datasheet": "Vendor X HTHP datasheet rev. B (test value)",
        "max_lift_k": 50.0,
        "max_sink_temperature_degc": 95.0,
        "max_carnot_fraction": 0.55,
    }
    out = run({**RC04_PARAMS, "solver": "closed_form", **limits}, RC04_INPUTS)
    assert out["missing_inputs"] == [] and out["limits_source"].startswith("datasheet:")
    assert out["COP"] == pytest.approx(0.55 * out["carnot_cop_refrigerant"], rel=1e-12)
    assert not out["within_limits"]  # lift ≈ 53 K > 50 K and 100 °C > 95 °C
    assert any("lift" in w for w in out["validity_warnings"])
    assert any("datasheet maximum" in w for w in out["validity_warnings"])
    assert out["heat_pump_point"]["max_lift_k"] == 50.0
    assert out["heat_pump_point"]["max_carnot_fraction"] == 0.55
    assert "VER-HP-ETA" not in out["assumption_ids"]
    with pytest.raises(ValueError, match="max_carnot_fraction"):
        cf.hthp_limits("cited", max_carnot_fraction=1.5)


def test_lift_above_rc04_default_is_flagged():
    """Sink 100 °C from a 40 °C source (300 m³/h, leaves ≈ 36 °C): lift > 60 K (VER-HP-LIFT)."""
    inputs = {**RC04_INPUTS, "Ts_in": 40.0, "source_flow_rate": 300 / 3600}
    out = run({**RC04_PARAMS, "solver": "closed_form"}, inputs)
    assert out["operating_branch"] == "target"
    assert out["lift_k"] > 60.0
    assert not out["within_limits"]
    assert any("lift" in w and "60" in w for w in out["validity_warnings"])


def test_heat_pump_point_passes_the_verifier_checks():
    """The emitted evidence is a valid HeatPumpPoint and passes PH-13/PH-14 at the cap."""
    from gemini_agent.verify import design_verifier as dv
    from unit_test.gemini_agent.verify import evidence_factory as ef

    verifier = dv.DesignVerifier.from_config()
    capped = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    point = dv.HeatPumpPoint(name="HTHP", **capped["heat_pump_point"])
    report = verifier.verify(ef.good_evidence(heat_pumps=[point]))
    assert report.check("PH-13").status == dv.CheckStatus.PASS
    assert report.check("PH-14").status == dv.CheckStatus.PASS
    assert {"VER-HP-ETA", "VER-HP-LIFT"} <= set(report.assumption_ids)

    uncapped = run(RC04_PARAMS, RC04_INPUTS)  # Nelder-Mead, Lorenz COP 4.79 > 0.6 × Carnot
    point = dv.HeatPumpPoint(name="HTHP", **uncapped["heat_pump_point"])
    report = verifier.verify(ef.good_evidence(heat_pumps=[point]))
    assert report.check("PH-14").status == dv.CheckStatus.FAIL


@pytest.mark.parametrize(
    "inputs, parameters, reason",
    [
        ({"hot_flow_rate": 0.0}, {}, "no sink flow"),
        ({"source_flow_rate": 0.0}, {}, "no source flow"),
        ({"Th_in": 100.0}, {}, "at or above"),
        ({"Ts_in": 9.0}, {}, "Ts_in_minimum"),
        ({"Ts_in": 120.0}, {"apply_cop_cap": False}, "no lift"),
        ({}, {"eta_lorenz": 0.02}, "COP ≤ 1"),
    ],
)
def test_idle_cases_report_reason_without_raising(inputs, parameters, reason):
    """No exception mid-simulation: the heat pump is off and says why."""
    out = run({**RC04_PARAMS, "solver": "closed_form", **parameters}, {**RC04_INPUTS, **inputs})
    assert not out["running"]
    assert out["power_el"] == 0.0 and out["power_th"] == 0.0 and out["source_heat_w"] == 0.0
    assert out["heat_pump_point"] is None and out["within_limits"] is None  # off ≠ violation
    assert any(reason in w for w in out["validity_warnings"])


def test_parameter_validation():
    """Unknown solver and non-positive approaches are refused."""
    with pytest.raises(ValueError, match="solver"):
        run({**RC04_PARAMS, "solver": "newton"}, RC04_INPUTS)
    with pytest.raises(ValueError, match="approach"):
        run({**RC04_PARAMS, "condenser_approach_k": 0.0}, RC04_INPUTS)


def test_dea_reference_cop_is_plausibility_only():
    """RC-10 DEA COP by lift: none below 40 K (our ≈ 15–25 K lift uses Lorenz/Carnot)."""
    assert cf.dea_reference_cop(20.0) is None
    assert cf.dea_reference_cop(40.0) == 4.45
    assert cf.dea_reference_cop(52.5) == pytest.approx(3.6)
    assert cf.dea_reference_cop(100.0) == 2.05
    assert cf.dea_reference_cop(101.0) is None
    out = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    assert out["dea_reference_cop"] == pytest.approx(cf.dea_reference_cop(out["lift_k"]))


def test_small_lift_case_uses_the_rc04_method():
    """A ≈ 20 K outlet-to-outlet lift (brine 90 → 80 °C, sink 82 → 100 °C): capped Lorenz COP."""
    inputs = {**RC04_INPUTS, "Ts_in": 90.0, "source_flow_rate": 200 / 3600}
    out = run({**RC04_PARAMS, "solver": "closed_form"}, inputs)
    assert 15.0 <= out["lift_k"] <= 25.0
    assert out["dea_reference_cop"] is None
    assert out["COP"] == pytest.approx(0.6 * out["carnot_cop_refrigerant"], rel=1e-12)
    assert out["within_limits"]


def test_model_defaults_match_the_assumptions_register():
    """Model constants equal the register entries the verifier also uses (single source)."""
    yaml = pytest.importorskip("yaml")
    with open(ASSUMPTIONS, encoding="utf-8") as file:
        register = {item["id"]: item for item in yaml.safe_load(file)["assumptions"]}
    assert register[cf.CARNOT_FRACTION_ID]["value"] == cf.RC04_DEFAULT_CARNOT_FRACTION
    assert register[cf.MAX_LIFT_ID]["value"] == cf.RC04_DEFAULT_MAX_LIFT_K
    approach = register[cf.APPROACH_ID]["value"]
    parameters = HeatpumpBasic().parameters
    assert approach["condenser_approach_k"] == parameters["condenser_approach_k"]
    assert approach["evaporator_approach_k"] == parameters["evaporator_approach_k"]
    assert register[cf.LORENZ_EFFICIENCY_ID]["value"] == parameters["eta_lorenz"]
    assert cf.DEFAULT_LORENZ_EFFICIENCY == parameters["eta_lorenz"]

    from gemini_agent.verify.design_verifier import DesignVerifier

    verifier = DesignVerifier.from_config()
    assert verifier.hp_carnot_fraction_default == cf.RC04_DEFAULT_CARNOT_FRACTION
    assert verifier.hp_max_lift_default_k == cf.RC04_DEFAULT_MAX_LIFT_K


def test_no_lift_goes_idle_even_with_the_cap():
    """Source hotter than the sink mean: the finite cap must not make the pump run."""
    parameters = {**GEMINI_PARAMS, "mode": "lorenz", "Th_out_target": 60.0, "Ts_in_minimum": 10.0}
    inputs = {"Th_in": 40.0, "Ts_in": 62.0, "hot_flow_rate": 1 / 36, "source_flow_rate": 1 / 12}
    for cap in (True, False):
        out = run({**parameters, "solver": "closed_form", "apply_cop_cap": cap}, inputs)
        assert not out["running"] and out["heat_pump_point"] is None
        assert any("no lift" in w for w in out["validity_warnings"])


def test_non_physical_nelder_mead_point_emits_no_evidence():
    """NM can 'converge' to a sink outlet of ≈ 800 °C here: flagged, no HeatPumpPoint."""
    parameters = {**RC04_PARAMS, "Ts_in_minimum": 46.0}
    inputs = {**RC04_INPUTS, "hot_flow_rate": 300 / 3600, "source_flow_rate": 50 / 3600}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        nelder = run(parameters, inputs)
    assert nelder["Th_out"] > 100.0  # GEMINI's result, unchanged
    assert not nelder["converged"] and not nelder["within_limits"]
    assert nelder["heat_pump_point"] is None
    assert any("did not converge" in w for w in nelder["validity_warnings"])
    closed = run({**parameters, "solver": "closed_form"}, inputs)
    assert closed["converged"] and closed["Th_out"] <= 100.0
    assert closed["operating_branch"] == "source_floor"


def test_approach_outside_rc04_range_warns():
    """RC-04 eq. 4: approach 3–5 K; other values are allowed but flagged."""
    out = run({**RC04_PARAMS, "solver": "closed_form", "condenser_approach_k": 2.0}, RC04_INPUTS)
    assert any("condenser approach 2 K outside" in w for w in out["validity_warnings"])
    assert "HP-APPROACH" not in out["assumption_ids"]


def test_default_path_never_raises_where_gemini_returned():
    """Regression: the evidence code must not raise on a garbage Nelder-Mead point (rule 6)."""
    parameters = {**GEMINI_PARAMS, "mode": "carnot", "Th_out_target": 80, "Ts_in_minimum": 5}
    cases = [
        {"Th_in": 82.0, "Ts_in": 90.0},  # sink inlet above the target (reviewer repro)
        {"Th_in": 95.0, "Ts_in": 20.0},
        {"Th_in": 30.0, "Ts_in": 40.0},
    ]
    for mode in ("carnot", "lorenz"):
        for case in cases:
            inputs = {**case, "hot_flow_rate": 1 / 3600, "source_flow_rate": 1 / 3600}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                out = run({**parameters, "mode": mode}, inputs)
            assert {"COP", "Th_out", "Ts_out", "power_th", "power_el"} <= set(out)
            if not out["converged"]:
                assert out["mean_lift_k"] is None and out["heat_pump_point"] is None


def test_bad_inputs_raise_and_zero_flow_idles():
    """Negative or non-finite inputs are wiring errors (raise); zero flow is just off."""
    with pytest.raises(ValueError, match="negative flow"):
        run({**RC04_PARAMS, "solver": "closed_form"}, {**RC04_INPUTS, "hot_flow_rate": -0.01})
    with pytest.raises(ValueError, match="finite"):
        run({**RC04_PARAMS, "solver": "closed_form"}, {**RC04_INPUTS, "Ts_in": float("nan")})
    out = run({**RC04_PARAMS, "solver": "closed_form"}, {**RC04_INPUTS, "source_flow_rate": 0.0})
    assert not out["running"]


def test_closed_form_in_carnot_mode_warns():
    """Warn: GEMINI's default mode is Carnot (η_C 0.5, unregistered); RC-04 prefers Lorenz."""
    out = run({**RC04_PARAMS, "mode": "carnot", "solver": "closed_form"}, RC04_INPUTS)
    assert any("mode 'carnot'" in w for w in out["validity_warnings"])
    out = run({**RC04_PARAMS, "solver": "closed_form"}, RC04_INPUTS)
    assert not any("mode 'carnot'" in w for w in out["validity_warnings"])
