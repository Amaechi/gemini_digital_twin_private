"""HeatExchanger per-side fluid properties (WP-02 decision on the WP-01 follow-up)."""

import pytest

from gemini_model.heat_exchanger.heat_exchanger import HeatExchanger

PARAMETERS = {
    "flow_resistance": 0.0,
    "heat_transfer_coefficient": 250e3,  # W/K
    "flow_configuration": "counter",
}
INPUTS = {
    "pressure_in": 3e5,
    "primary_temperature_in": 85.0,
    "secondary_temperature_in": 40.0,
    "primary_flow_rate": 200.0 / 3600.0,
    "secondary_flow_rate": 180.0 / 3600.0,
}
BRINE = {"fluid_density": 1030.0, "specific_heat": 4100.0}
WATER = {"secondary_fluid_density": 1000.0, "secondary_specific_heat": 4217.4}


def run(parameters):
    """Run the heat exchanger once with INPUTS and return its output."""
    hex_model = HeatExchanger()
    hex_model.update_parameters({**PARAMETERS, **parameters})
    hex_model.calculate_output(dict(INPUTS))
    return hex_model.get_output()


def test_default_secondary_properties_are_the_primary_ones():
    """Without secondary parameters the output equals the original single-fluid model."""
    single = run(BRINE)
    explicit = run({**BRINE, "secondary_fluid_density": 1030.0, "secondary_specific_heat": 4100.0})
    assert explicit == single
    legacy = run({})
    assert legacy == run({"secondary_fluid_density": 1050.0, "secondary_specific_heat": 4200.0})


def test_both_sides_close_their_energy_balance_with_their_own_properties():
    """Brine primary, district-water secondary: Q = (ρ c_p V ΔT) on each side exactly."""
    out = run({**BRINE, **WATER})
    primary = 1030.0 * 4100.0 * INPUTS["primary_flow_rate"]
    secondary = 1000.0 * 4217.4 * INPUTS["secondary_flow_rate"]
    q_primary = primary * (INPUTS["primary_temperature_in"] - out["primary_temperature_out"])
    q_secondary = secondary * (
        out["secondary_temperature_out"] - INPUTS["secondary_temperature_in"]
    )
    assert q_primary == pytest.approx(out["heat_duty"], rel=1e-12)
    assert q_secondary == pytest.approx(out["heat_duty"], rel=1e-12)
    # With one ρ/c_p for both sides the secondary temperature rise would be off by the ratio.
    single = run(BRINE)
    rise_single = single["secondary_temperature_out"] - INPUTS["secondary_temperature_in"]
    rise_per_side = out["secondary_temperature_out"] - INPUTS["secondary_temperature_in"]
    assert rise_per_side / rise_single == pytest.approx(
        (1030.0 * 4100.0) / (1000.0 * 4217.4) * out["heat_duty"] / single["heat_duty"], rel=1e-12
    )
