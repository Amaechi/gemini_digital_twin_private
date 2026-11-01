"""ElectricChiller model (GM-05): RC-06 validation, monotonicity, caps and balances."""

import numpy as np
import pytest

from gemini_model.electric_chiller.electric_chiller import (
    ElectricChiller,
    calibrated_carnot_efficiency,
    carnot_cop,
)

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


def test_guidebook_cop_439_on_the_water_basis():
    """RC-06 water basis: COP 4.39 at 7.2 / 32.2 °C water with η = 0.39, no offsets (± 2 %).

    Documented only: the model uses the refrigerant basis (next test), not this one.
    """
    assert carnot_cop(7.2, 32.2, 0.39) == pytest.approx(4.39, rel=0.02)


@pytest.mark.parametrize(
    "inputs",
    [
        {"cooling_water_temperature_in_degc": 32.2 - 5.0},  # condenser water leaves at 32.2 °C
        {"ambient_wet_bulb_degc": 32.2 - 5.0 - 4.5},  # tower chain to the same leaving water
    ],
)
def test_model_reproduces_the_guidebook_point_on_its_refrigerant_basis(inputs):
    """RC-06 validation, model basis: COP(7.2 °C chilled / 32.2 °C condenser water) = 4.39.

    T_evap = 7.2 - 4 = 3.2 °C, T_cond = 32.2 + 3 = 35.2 °C; calibrated η = 0.508 (card: 0.508).
    """
    out = run(
        make_chiller(),
        cooling_load_w=1.0e6,
        chilled_water_temperature_out_degc=7.2,
        chilled_water_temperature_in_degc=12.2,
        **inputs,
    )
    assert out["evaporator_temperature_degc"] == pytest.approx(3.2)
    assert out["condenser_temperature_degc"] == pytest.approx(35.2)
    assert out["cop"] == pytest.approx(4.39, rel=1e-12)
    assert out["carnot_efficiency"] == pytest.approx(0.508, rel=0.02)
    assert out["validity_warnings"] == []


def test_calibrated_eta_on_each_temperature_basis():
    """RC-06 temperature basis of η: 0.51 refrigerant, 0.44 condenser offset only, 0.39 water."""
    assert calibrated_carnot_efficiency() == pytest.approx(4.39 * 32.0 / 276.35, rel=1e-12)
    assert calibrated_carnot_efficiency() == pytest.approx(0.508, abs=0.001)
    assert calibrated_carnot_efficiency(0.0, 3.0) == pytest.approx(0.44, abs=0.005)
    assert calibrated_carnot_efficiency(0.0, 0.0) == pytest.approx(0.39, abs=0.005)
    # Second Guidebook point (4.07 at 45/95 °F) on the model basis: the sensitivity bound.
    assert calibrated_carnot_efficiency(cop=4.07, condenser_water_out_degc=35.0) == (
        pytest.approx(0.513, abs=0.001)
    )


def test_eta_follows_the_model_approaches_and_can_be_overridden():
    """Changing the approaches recalibrates η (point still reproduced); an explicit η wins."""
    inputs = {
        "cooling_load_w": 1.0e6,
        "chilled_water_temperature_out_degc": 7.2,
        "chilled_water_temperature_in_degc": 12.2,
        "cooling_water_temperature_in_degc": 27.2,
    }
    shifted = run(make_chiller(evaporator_approach_k=5.0, condenser_approach_k=2.0), **inputs)
    assert shifted["cop"] == pytest.approx(4.39, rel=1e-12)
    assert shifted["carnot_efficiency"] == pytest.approx(calibrated_carnot_efficiency(5.0, 2.0))
    fixed = run(make_chiller(carnot_efficiency=0.45), **inputs)
    assert fixed["carnot_efficiency"] == 0.45
    assert fixed["cop"] == pytest.approx(0.45 * 276.35 / 32.0)


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
    "option, ambient_key, ambient, t_cond, uncapped",
    [
        ("cooling_tower", "ambient_wet_bulb_degc", 18.4, 30.9, 9.865),  # DB-34b design WB
        ("cooling_tower", "ambient_wet_bulb_degc", 21.0, 33.5, 8.399),  # warm case WB
        ("dry_cooler", "ambient_dry_bulb_degc", 25.0, 33.0, 8.646),  # DB-34b design DB
        ("dry_cooler", "ambient_dry_bulb_degc", 30.0, 38.0, 6.681),  # warm case DB
    ],
)
def test_dutch_summer_cop_is_six_to_eight(option, ambient_key, ambient, t_cond, uncapped):
    """RC-06: at 20 °C chilled supply and Dutch summer conditions COP ≈ 6-8.

    With η 0.508 (refrigerant basis) the cap of 8 binds at the design points (RC-06).
    """
    out = run(make_chiller(heat_rejection=option), cooling_load_w=1e6, **{ambient_key: ambient})
    assert out["evaporator_temperature_degc"] == pytest.approx(16.0)
    assert out["condenser_temperature_degc"] == pytest.approx(t_cond)
    assert out["cop_uncapped"] == pytest.approx(uncapped, abs=0.001)
    assert out["cop"] == pytest.approx(min(uncapped, 8.0), abs=0.001)
    assert 6.0 <= out["cop"] <= 8.0


def test_cop_cap_and_floor():
    """COP is capped at 8 on cold days and floored at 3 in extreme heat [A]."""
    cold = run(make_chiller(), cooling_load_w=1e6, ambient_wet_bulb_degc=2.0)
    assert cold["cop"] == 8.0
    assert cold["cop_uncapped"] > 8.0
    assert cold["condenser_sensitivity_per_k"] == 0.0
    assert "capped" in cold["validity_warnings"][0]
    hot = run(make_chiller(), cooling_load_w=1e6, condenser_temperature_degc=70.0)
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
    assert (direct["cop"], looped["cop"]) == (8.0, pytest.approx(6.68, abs=0.01))
    assert not any("water-cooled" in w for w in direct["validity_warnings"])
    assert "water-cooled formula" in looped["validity_warnings"][0]


def test_cooling_water_input_overrides_the_ambient_estimate():
    """Condenser water from a heat-rejection unit: T_cond = T_cw,in + range + 3 K."""
    out = run(make_chiller(), cooling_load_w=1e6, cooling_water_temperature_in_degc=22.9)
    assert out["condenser_temperature_degc"] == pytest.approx(22.9 + 5.0 + 3.0)
    tower = run(make_chiller(), cooling_load_w=1e6, ambient_wet_bulb_degc=18.4)
    assert tower["cop"] == pytest.approx(out["cop"], rel=1e-12)


def test_condenser_sensitivity_vs_guidebook_data():
    """Documented check: model -3.1 %/K at the Guidebook point vs data ≈ -2.6 %/K (p.257-258).

    Guidebook: COP 4.39 at 90 °F and 4.07 at 95 °F leaving condenser water. On the model's
    refrigerant basis the lift there is 32 K, so d ln COP/dT = -1/32: steeper than the data.
    """
    out = run(
        make_chiller(),
        cooling_load_w=1e6,
        chilled_water_temperature_out_degc=7.2,
        chilled_water_temperature_in_degc=12.2,
        cooling_water_temperature_in_degc=27.2,
    )
    guidebook = (4.07 / 4.39 - 1.0) / (5.0 / 1.8)
    assert guidebook == pytest.approx(-0.026, abs=0.001)
    assert out["condenser_sensitivity_per_k"] == pytest.approx(-1.0 / 32.0, rel=1e-12)
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
