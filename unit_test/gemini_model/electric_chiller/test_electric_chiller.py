"""ElectricChiller model (GM-05): RC-06 validation, monotonicity, caps and balances."""

import numpy as np
import pytest

from gemini_model.electric_chiller.electric_chiller import ElectricChiller, carnot_cop

Q_NOM = 2.0e6  # W


def make_chiller(**parameters):
    """Return an ElectricChiller with a 2 MW nominal capacity and the given parameters."""
    chiller = ElectricChiller()
    chiller.update_parameters({"nominal_capacity_w": Q_NOM, **parameters})
    return chiller


def run(chiller, **inputs):
    """Run the chiller once and return its output dict."""
    chiller.calculate_output(inputs)
    return chiller.get_output()


def test_guidebook_cop_439_with_eta_039():
    """RC-06: COP 4.39 at 7.2 °C / 32.2 °C with η = 0.39 (± 2 %)."""
    assert carnot_cop(7.2, 32.2, 0.39) == pytest.approx(4.39, rel=0.02)
    out = run(
        make_chiller(carnot_efficiency=0.39),
        cooling_load_w=1.0e6,
        evaporator_temperature_degc=7.2,
        condenser_temperature_degc=32.2,
    )
    assert out["cop"] == pytest.approx(4.39, rel=0.02)


def test_temperature_chain_reaches_the_7_2_32_2_refrigerant_point():
    """T_evap = supply - 4 K and T_cond = T_wb + 4.5 + 5 + 3 K reach 7.2/32.2 °C.

    This checks the temperature chain only: here 32.2 °C is the condensing temperature,
    whereas the Guidebook's 90 °F is condenser leaving water (see the η-bias test).
    """
    out = run(
        make_chiller(carnot_efficiency=0.39),
        cooling_load_w=1.0e6,
        chilled_water_temperature_out_degc=11.2,
        chilled_water_temperature_in_degc=16.0,
        ambient_wet_bulb_degc=19.7,
    )
    assert out["evaporator_temperature_degc"] == pytest.approx(7.2)
    assert out["condenser_temperature_degc"] == pytest.approx(32.2)
    assert out["cop"] == pytest.approx(4.39, rel=0.02)


def test_monotonic_in_condenser_and_evaporator_temperature():
    """RC-06 property: COP falls with T_cond and rises with T_evap (uncapped)."""
    t_cond = np.linspace(20.0, 50.0, 31)
    cops = [carnot_cop(10.0, t, 0.4) for t in t_cond]
    assert np.all(np.diff(cops) < 0.0)
    t_evap = np.linspace(0.0, 18.0, 19)
    cops = [carnot_cop(t, 35.0, 0.4) for t in t_evap]
    assert np.all(np.diff(cops) > 0.0)
    chiller = make_chiller()
    capped = [
        run(chiller, cooling_load_w=1e6, ambient_wet_bulb_degc=t)["cop"]
        for t in np.linspace(0.0, 30.0, 31)
    ]
    assert np.all(np.diff(capped) <= 0.0)


@pytest.mark.parametrize(
    "option, ambient_key, ambient, t_cond, cop",
    [
        ("cooling_tower", "ambient_wet_bulb_degc", 18.4, 30.9, 7.762),  # DB-34b design WB
        ("cooling_tower", "ambient_wet_bulb_degc", 21.0, 33.5, 6.609),  # warm case WB
        ("dry_cooler", "ambient_dry_bulb_degc", 25.0, 33.0, 6.804),  # DB-34b design DB
    ],
)
def test_dutch_summer_cop_is_six_to_eight(option, ambient_key, ambient, t_cond, cop):
    """RC-06: at 20 °C chilled supply and Dutch summer conditions COP ≈ 6-8."""
    out = run(make_chiller(heat_rejection=option), cooling_load_w=1e6, **{ambient_key: ambient})
    assert out["evaporator_temperature_degc"] == pytest.approx(16.0)
    assert out["condenser_temperature_degc"] == pytest.approx(t_cond)
    assert out["cop"] == pytest.approx(cop, abs=0.001)
    assert 6.0 <= out["cop"] <= 8.0


def test_cop_cap_and_floor():
    """COP is capped at 8 on cold days and floored at 3 in extreme heat [A]."""
    cold = run(make_chiller(), cooling_load_w=1e6, ambient_wet_bulb_degc=2.0)
    assert cold["cop"] == 8.0
    assert cold["cop_uncapped"] > 8.0
    assert cold["condenser_sensitivity_per_k"] == 0.0
    assert "capped" in cold["validity_warnings"][0]
    hot = run(
        make_chiller(heat_rejection="dry_cooler"), cooling_load_w=1e6, ambient_dry_bulb_degc=50
    )
    assert hot["cop_uncapped"] < 3.0
    assert hot["cop"] == 3.0
    assert "floored" in hot["validity_warnings"][0]
    reversed_lift = run(
        make_chiller(), cooling_load_w=1e6, evaporator_temperature_degc=16.0,
        condenser_temperature_degc=15.0,
    )  # fmt: skip
    assert reversed_lift["cop"] == 8.0


def test_energy_balance_and_capacity_limit():
    """P_el = Q/COP, Q_reject = Q + P_el exactly; load above nameplate is unmet."""
    out = run(make_chiller(), cooling_load_w=3.0e6, ambient_wet_bulb_degc=18.4)
    assert out["cooling_power_w"] == Q_NOM
    assert out["unmet_cooling_w"] == pytest.approx(1.0e6)
    assert out["power_el"] == pytest.approx(Q_NOM / out["cop"], rel=1e-14)
    assert out["heat_rejected_w"] == pytest.approx(Q_NOM + out["power_el"], rel=1e-14)
    assert out["power_th"] == Q_NOM
    assert out["emission"] == 0.0
    assert out["load_ratio"] == 1.0


def test_chilled_flow_input_closes_the_water_balance():
    """With a chilled-water flow the load is ρ c_p V ΔT and the outlet follows the duty."""
    chiller = make_chiller(nominal_capacity_w=0.5e6)
    out = run(chiller, chilled_water_flow_rate_m3s=0.04, ambient_wet_bulb_degc=15.0)
    rho_cp = 1000.0 * 4217.4
    assert out["cooling_load_w"] == pytest.approx(rho_cp * 0.04 * 6.0)
    assert out["cooling_power_w"] == 0.5e6
    delivered = rho_cp * 0.04 * (26.0 - out["chilled_water_temperature_out_degc"])
    assert delivered == pytest.approx(0.5e6, rel=1e-12)


def test_eta_bias_through_the_card_chain_is_documented():
    """Documented bias (COOL-ELC): with T_cond = 32.2 °C leaving water + 3 K, η 0.39 gives 3.90.

    The η consistent with the -4/+3 K chain at the Guidebook point is 4.39 × 28 / 280.35 ≈ 0.44.
    """
    out = run(
        make_chiller(carnot_efficiency=0.39),
        cooling_load_w=1.0e6,
        evaporator_temperature_degc=7.2,
        cooling_water_temperature_in_degc=32.2 - 5.0,
    )
    assert out["condenser_temperature_degc"] == pytest.approx(35.2)
    assert out["cop"] == pytest.approx(3.90, abs=0.01)
    assert 4.39 * 28.0 / (7.2 + 273.15) == pytest.approx(0.44, abs=0.005)


def test_dry_cooler_with_condenser_water_warns_about_the_formula():
    """dry_cooler + condenser water from a water-loop dry cooler: T_db + 13 K, with a warning."""
    direct = run(
        make_chiller(heat_rejection="dry_cooler"), cooling_load_w=1e6, ambient_dry_bulb_degc=25
    )
    looped = run(
        make_chiller(heat_rejection="dry_cooler"),
        cooling_load_w=1e6,
        cooling_water_temperature_in_degc=25.0 + 5.0,
    )
    assert direct["condenser_temperature_degc"] == pytest.approx(33.0)
    assert looped["condenser_temperature_degc"] == pytest.approx(38.0)
    assert (direct["cop"], looped["cop"]) == (
        pytest.approx(6.80, abs=0.01),
        pytest.approx(5.26, abs=0.01),
    )
    assert direct["validity_warnings"] == []
    assert "water-cooled formula" in looped["validity_warnings"][0]


def test_cooling_water_input_overrides_the_ambient_estimate():
    """Condenser water from a heat-rejection unit: T_cond = T_cw,in + range + 3 K."""
    out = run(make_chiller(), cooling_load_w=1e6, cooling_water_temperature_in_degc=22.9)
    assert out["condenser_temperature_degc"] == pytest.approx(22.9 + 5.0 + 3.0)
    tower = run(make_chiller(), cooling_load_w=1e6, ambient_wet_bulb_degc=18.4)
    assert tower["cop"] == pytest.approx(out["cop"], rel=1e-12)


def test_condenser_sensitivity_vs_guidebook_data():
    """Documented check: model -4.0 %/K at 7.2/32.2 °C vs Guidebook ≈ -2.6 %/K (p.257-258).

    Guidebook: COP 4.39 at 90 °F and 4.07 at 95 °F leaving condenser water. The constant-η
    model is steeper, i.e. conservative in hot weather.
    """
    out = run(
        make_chiller(carnot_efficiency=0.39),
        cooling_load_w=1e6,
        evaporator_temperature_degc=7.2,
        condenser_temperature_degc=32.2,
    )
    guidebook = (4.07 / 4.39 - 1.0) / (5.0 / 1.8)
    assert guidebook == pytest.approx(-0.026, abs=0.001)
    assert out["condenser_sensitivity_per_k"] == pytest.approx(-0.040, abs=0.0005)
    assert out["condenser_sensitivity_per_k"] < guidebook
    # Implied η of the 45/95 °F Guidebook point is ≈ 0.40 (RC-06 table).
    assert 4.07 / carnot_cop(7.22, 35.0, 1.0) == pytest.approx(0.40, abs=0.005)


def test_zero_load_with_return_at_set_point_is_allowed():
    """Downstream of full free cooling the chiller sees zero load at the set point."""
    out = run(
        make_chiller(),
        cooling_load_w=0.0,
        chilled_water_temperature_in_degc=20.0,
        ambient_wet_bulb_degc=10.0,
    )
    assert out["cooling_power_w"] == 0.0
    assert out["power_el"] == 0.0
    assert out["chilled_water_flow_rate_m3s"] == 0.0


@pytest.mark.parametrize(
    "parameters, inputs, message",
    [
        ({}, {"cooling_load_w": 1.0, "chilled_water_flow_rate_m3s": 0.1}, "exactly one"),
        ({"nominal_capacity_w": None}, {"cooling_load_w": 1.0}, "nominal_capacity_w"),
        ({"heat_rejection": "river"}, {"cooling_load_w": 1.0}, "heat_rejection"),
        ({"cop_min": 9.0}, {"cooling_load_w": 1.0}, "cop_min"),
        ({}, {"cooling_load_w": 1.0, "chilled_water_temperature_in_degc": 19.0}, "return"),
    ],
)
def test_invalid_inputs_raise(parameters, inputs, message):
    """Inconsistent or invalid inputs and parameters raise ValueError."""
    with pytest.raises(ValueError, match=message):
        make_chiller(**parameters).calculate_output({"ambient_wet_bulb_degc": 15.0, **inputs})
