"""GM-08 (WP-03): electric boiler, η 0.99 (spec GM-08; DEA ch. 41, register EB-ETA)."""

from pathlib import Path

import pytest

from gemini_model.electric_boiler import electric_boiler
from gemini_model.electric_boiler.electric_boiler import ElectricBoiler

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "config" / "assumptions.yaml"


def boiler(**parameters):
    """Return a 5 MW electric boiler with optional parameter overrides."""
    model = ElectricBoiler()
    model.update_parameters({"nominal_capacity_w": 5.0e6, **parameters})
    return model


def test_power_from_heat_demand():
    """P_el = Q / 0.99; losses 1 %; no emission."""
    model = boiler()
    model.calculate_output({"heat_demand_w": 2.0e6})
    out = model.get_output()
    assert out["power_th"] == out["heat_output_w"] == 2.0e6
    assert out["power_el"] == pytest.approx(2.0e6 / 0.99, rel=1e-12)
    assert out["losses_w"] == pytest.approx(out["power_el"] - 2.0e6, rel=1e-12)
    assert out["emission"] == 0.0 and out["unmet_heat_w"] == 0.0
    assert out["load_ratio"] == pytest.approx(0.4)


def test_capacity_limit_reports_unmet_heat():
    """Demand above the nominal capacity is capped and the rest reported."""
    model = boiler()
    model.calculate_output({"heat_demand_w": 7.0e6})
    out = model.get_output()
    assert out["heat_output_w"] == 5.0e6 and out["unmet_heat_w"] == 2.0e6
    assert out["load_ratio"] == 1.0


def test_flow_mode_heats_to_set_point():
    """Q = ρ c_p V (100 − T_in); the outlet reaches the 100 °C set point."""
    model = boiler(water_density_kg_m3=1000.0, water_specific_heat_j_kgk=4200.0)
    model.calculate_output({"water_flow_rate_m3s": 0.02, "water_temperature_in_degc": 90.0})
    out = model.get_output()
    assert out["heat_output_w"] == pytest.approx(1000.0 * 4200.0 * 0.02 * 10.0, rel=1e-12)
    assert out["water_temperature_out_degc"] == pytest.approx(100.0, rel=1e-12)


def test_flow_mode_capacity_limit_lowers_outlet():
    """Capped duty: T_out = T_in + Q_nom / (ρ c_p V)."""
    model = boiler(nominal_capacity_w=0.42e6, water_density_kg_m3=1000.0)
    model.update_parameters({"water_specific_heat_j_kgk": 4200.0})
    model.calculate_output({"water_flow_rate_m3s": 0.02, "water_temperature_in_degc": 90.0})
    out = model.get_output()
    assert out["water_temperature_out_degc"] == pytest.approx(95.0, rel=1e-12)
    assert out["unmet_heat_w"] == pytest.approx(0.42e6, rel=1e-12)


def test_inlet_above_set_point_and_zero_flow_give_no_heat():
    """Already hot water passes through; zero flow is idle."""
    model = boiler()
    model.calculate_output({"water_flow_rate_m3s": 0.02, "water_temperature_in_degc": 101.0})
    assert model.get_output()["power_el"] == 0.0
    assert model.get_output()["water_temperature_out_degc"] == 101.0
    model.calculate_output({"water_flow_rate_m3s": 0.0, "water_temperature_in_degc": 60.0})
    assert model.get_output()["power_th"] == 0.0


def test_input_validation():
    """Exactly one of demand or flow; capacity required; 0 < η ≤ 1."""
    with pytest.raises(ValueError, match="exactly one"):
        boiler().calculate_output({})
    with pytest.raises(ValueError, match="exactly one"):
        boiler().calculate_output({"heat_demand_w": 1.0, "water_flow_rate_m3s": 0.01})
    with pytest.raises(ValueError, match="nominal_capacity_w"):
        ElectricBoiler().calculate_output({"heat_demand_w": 1.0})
    with pytest.raises(ValueError, match="efficiency"):
        boiler(efficiency=1.2).calculate_output({"heat_demand_w": 1.0})
    with pytest.raises(ValueError, match="heat_demand_w"):
        boiler().calculate_output({"heat_demand_w": -1.0})


def test_default_matches_the_assumptions_register():
    """η 0.99 is register entry EB-ETA."""
    yaml = pytest.importorskip("yaml")
    with open(ASSUMPTIONS, encoding="utf-8") as file:
        register = {item["id"]: item for item in yaml.safe_load(file)["assumptions"]}
    assert (
        register["EB-ETA"]["value"]
        == electric_boiler.DEFAULT_EFFICIENCY
        == ElectricBoiler().parameters["efficiency"]
    )
