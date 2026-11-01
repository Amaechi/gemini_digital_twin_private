"""HeatRejection model (GM-06): RC-07 cooling tower and dry cooler."""

import pytest

from gemini_model.heat_rejection import heat_rejection

RHO_CP = 1000.0 * 4217.4  # J/(m3 K), GEMINI water PVT defaults


def run(technology="cooling_tower", parameters=None, **inputs):
    """Run a heat-rejection unit once and return its output dict."""
    unit = heat_rejection.HeatRejection()
    unit.update_parameters({"technology": technology, **(parameters or {})})
    unit.calculate_output(inputs)
    return unit.get_output()


@pytest.mark.parametrize("cop, factor", [(0.7, 2.43), (3.0, 1.33)])
def test_chiller_heat_rejection_factor(cop, factor):
    """RC-07 (Guidebook p.262): Q_reject = Q_cool (1 + 1/COP) = 2.43 / 1.33 × Q_cool."""
    assert heat_rejection.chiller_heat_rejection_w(1.0e6, cop) == pytest.approx(
        factor * 1.0e6, abs=0.005e6
    )


def test_cooling_tower_defaults():
    """Tower: T_out = T_wb + 4.5 K, range 5 K, fan + pump 2 %, evaporation Q / h_fg."""
    out = run(heat_load_w=3.0e6, ambient_wet_bulb_degc=18.4)
    assert out["water_temperature_out_degc"] == pytest.approx(22.9)
    assert out["water_temperature_in_degc"] == pytest.approx(27.9)
    assert out["water_flow_rate_m3s"] == pytest.approx(3.0e6 / (RHO_CP * 5.0))
    assert out["power_el"] == pytest.approx(0.02 * 3.0e6)
    assert out["heat_to_ambient_w"] == pytest.approx(3.0e6 + out["power_el"], rel=1e-14)
    assert out["water_evaporation_kg_s"] == pytest.approx(3.0e6 / 2.45e6)
    assert out["power_th"] == 0.0
    assert out["emission"] == 0.0


def test_dry_cooler_has_three_times_the_fan_power_and_no_water_use():
    """Dry cooler: T_out = T_db + 5 K, fan power 3× the wet tower (6 %), no evaporation."""
    out = run("dry_cooler", heat_load_w=3.0e6, ambient_dry_bulb_degc=25.0)
    wet = run(heat_load_w=3.0e6, ambient_wet_bulb_degc=18.4)
    assert out["water_temperature_out_degc"] == pytest.approx(30.0)
    assert out["power_el"] == pytest.approx(3.0 * wet["power_el"])
    assert out["power_el"] == pytest.approx(0.06 * 3.0e6)
    assert out["water_evaporation_kg_s"] == 0.0


def test_given_flow_closes_the_water_balance():
    """With a water flow the range follows from Q = ρ c_p V (T_in - T_out)."""
    out = run(heat_load_w=2.0e6, ambient_wet_bulb_degc=15.0, water_flow_rate_m3s=0.1)
    balance = RHO_CP * 0.1 * (out["water_temperature_in_degc"] - out["water_temperature_out_degc"])
    assert balance == pytest.approx(2.0e6, rel=1e-12)
    assert out["water_flow_rate_m3s"] == 0.1


def test_explicit_approach_and_zero_load():
    """An explicit approach overrides the default; zero load gives zero flow and power."""
    out = run(parameters={"approach_k": 4.0}, heat_load_w=0.0, ambient_wet_bulb_degc=10.0)
    assert out["water_temperature_out_degc"] == pytest.approx(14.0)
    assert out["water_flow_rate_m3s"] == 0.0
    assert out["power_el"] == 0.0
    assert out["range_k"] == 0.0
    assert out["validity_warnings"] == []


def test_freezing_tower_water_is_flagged():
    """Tower leaving water at or below 0 °C (fixed approach) returns a freeze warning."""
    cold = run(heat_load_w=1.0e6, ambient_wet_bulb_degc=-5.0)
    assert cold["water_temperature_out_degc"] == pytest.approx(-0.5)
    assert "freeze" in cold["validity_warnings"][0]
    assert (
        run("dry_cooler", heat_load_w=1.0e6, ambient_dry_bulb_degc=-5.0)["validity_warnings"] == []
    )


@pytest.mark.parametrize(
    "technology, inputs, message",
    [
        ("lake", {"heat_load_w": 1.0, "ambient_wet_bulb_degc": 10.0}, "technology"),
        (
            "cooling_tower",
            {"heat_load_w": 1.0, "ambient_wet_bulb_degc": 10.0, "water_flow_rate_m3s": 0.0},
            "water_flow_rate_m3s",
        ),
        ("cooling_tower", {"heat_load_w": -1.0, "ambient_wet_bulb_degc": 10.0}, ">= 0"),
    ],
)
def test_invalid_inputs_raise(technology, inputs, message):
    """Unknown technology, zero flow with a load, or negative load raise ValueError."""
    with pytest.raises(ValueError, match=message):
        run(technology, **inputs)
