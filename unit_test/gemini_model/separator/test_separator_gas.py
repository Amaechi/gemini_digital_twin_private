"""Separator gas-composition tests (GM-12).

Reference values: RC-08 "Associated gas" and its validation tests
(GLR 0.8 Nm3/m3; 80 mol % CH4, 20 mol % CO2; density 0.965 +/-0.002 kg/Nm3;
LHV 29.7 +/-0.2 MJ/kg; 68.6 +/-0.5 kg CO2/GJ when burned; fuel power at
300 m3/h = 1.91 +/-0.02 MW).
"""

import pytest

from gemini_model.separator.separator import Separator, associated_gas_properties

FLOW_300_M3S = 300.0 / 3600.0  # design maximum per doublet (DB-11)
DESIGN_GAS = {"gas_water_ratio": 0.8, "ch4_fraction": 0.8, "co2_fraction": 0.2}
BASE = {"flow_resistance": 0.0, "temperature_drop": 0.0}


def _run(parameters, flow_m3s=FLOW_300_M3S):
    separator = Separator()
    separator.update_parameters({**BASE, **parameters})
    separator.calculate_output({"pressure": 10e5, "temperature": 353.15, "flow_rate": flow_m3s})
    return separator.get_output()


def test_associated_gas_properties_match_rc08():
    """80/20 CH4/CO2: 0.965 kg/Nm3, 29.7 MJ/kg, 68.6 kg CO2/GJ when burned."""
    gas = associated_gas_properties(0.8, 0.2)
    assert gas["density_kg_nm3"] == pytest.approx(0.965, abs=0.002)
    assert gas["lhv_j_nm3"] == pytest.approx(28.64e6, rel=1e-9)
    assert gas["lhv_j_kg"] / 1e6 == pytest.approx(29.7, abs=0.2)
    assert gas["co2_if_burned_kg_per_nm3"] == pytest.approx(1.964, abs=0.001)
    kg_per_gj = gas["co2_if_burned_kg_per_nm3"] / gas["lhv_j_nm3"] * 1e9
    assert kg_per_gj == pytest.approx(68.6, abs=0.5)


def test_fuel_power_at_300_m3h_matches_rc08():
    """RC-08: 300 m3/h x GLR 0.8 = 240 Nm3/h -> fuel 1.91 MW (LHV)."""
    y = _run(DESIGN_GAS)
    assert y["gas_flow_rate"] * 3600.0 == pytest.approx(240.0)
    assert y["fuel_power_w"] / 1e6 == pytest.approx(1.91, abs=0.02)
    assert y["gas_density_kg_nm3"] == pytest.approx(0.965, abs=0.002)
    assert y["gas_mass_flow_rate"] == pytest.approx(y["gas_flow_rate"] * y["gas_density_kg_nm3"])


def test_species_mass_flows_match_rc08_per_m3_brine():
    """RC-08: per m3 brine 0.46 kg CH4 and 0.32 kg CO2 (inherent).

    The card's 0.32 kg uses the real-gas CO2 density (1.977 kg/Nm3); the ideal-gas
    basis the card uses for 0.965 kg/Nm3 and 68.6 kg/GJ gives 0.314 kg, which is
    what the model reproduces.
    """
    y = _run(DESIGN_GAS, flow_m3s=1.0)  # 1 m3/s -> values are per m3 of brine
    assert y["ch4_mass_flow_kg_s"] == pytest.approx(0.46, abs=0.005)
    assert y["inherent_co2_kg_s"] == pytest.approx(0.314, abs=0.005)
    # Mass balance: species add up to the total gas mass flow.
    assert y["ch4_mass_flow_kg_s"] + y["inherent_co2_kg_s"] == pytest.approx(
        y["gas_mass_flow_rate"]
    )
    # Burning adds 44.01/16.04 kg CO2 per kg CH4 to the inherent CO2.
    assert y["co2_if_burned_kg_s"] == pytest.approx(
        y["inherent_co2_kg_s"] + y["ch4_mass_flow_kg_s"] * 44.01 / 16.04
    )


def test_separator_itself_does_not_emit():
    """The inherent CO2 is reported, but counted where the gas is burned or vented."""
    y = _run(DESIGN_GAS)
    assert y["inherent_co2_kg_s"] > 0.0
    assert y["emission"] == 0.0
    assert y["power_el"] == 0.0
    assert y["power_th"] == 0.0


def test_explicit_density_and_lhv_override_the_derived_values():
    """Explicit gas_density/fuel_lhv_j_kg win over the composition-derived values."""
    y = _run({**DESIGN_GAS, "gas_density": 0.9, "fuel_lhv_j_kg": 30e6})
    assert y["gas_density_kg_nm3"] == 0.9
    assert y["fuel_lhv_j_kg"] == 30e6
    assert y["fuel_power_w"] == pytest.approx(240.0 / 3600.0 * 0.9 * 30e6)
    # Species flows keep the composition's mass fractions and still add up to the total.
    assert y["ch4_mass_flow_kg_s"] + y["inherent_co2_kg_s"] == pytest.approx(
        y["gas_mass_flow_rate"]
    )
    derived = _run(DESIGN_GAS)
    assert y["inherent_co2_kg_s"] / y["gas_mass_flow_rate"] == pytest.approx(
        derived["inherent_co2_kg_s"] / derived["gas_mass_flow_rate"]
    )


def test_explicit_lhv_without_composition_gives_fuel_power():
    """fuel_lhv_j_kg alone (legacy gas_density) still yields a fuel power; no species flows."""
    y = _run({"gas_water_ratio": 0.8, "gas_density": 0.965, "fuel_lhv_j_kg": 29.7e6})
    assert y["fuel_power_w"] == pytest.approx(240.0 / 3600.0 * 0.965 * 29.7e6)
    assert y["inherent_co2_kg_s"] == 0.0


def test_explicit_lhv_without_any_gas_density_raises():
    """An LHV with gas flow but no density (no gas_density, no composition) is an error.

    Otherwise the gas mass flow is 0 and fuel_power_w silently reads 0 W instead of the
    RC-08 1.91 MW (WP-01 review).
    """
    with pytest.raises(ValueError, match="gas density is <= 0"):
        _run({"gas_water_ratio": 0.8, "fuel_lhv_j_kg": 29.7e6})
    # Without gas flow there is no fuel to lose, so the same parameters are accepted.
    assert _run({"gas_water_ratio": 0.0, "fuel_lhv_j_kg": 29.7e6})["fuel_power_w"] == 0.0


def test_legacy_configuration_is_unchanged():
    """Without a composition the old outputs are unchanged and the new ones are 0."""
    y = _run({"gas_water_ratio": 0.5, "gas_density": 0.7})
    assert y["gas_flow_rate"] == pytest.approx(0.5 * FLOW_300_M3S)
    assert y["gas_mass_flow_rate"] == pytest.approx(0.5 * FLOW_300_M3S * 0.7)
    for key in ("fuel_power_w", "ch4_mass_flow_kg_s", "inherent_co2_kg_s", "co2_if_burned_kg_s"):
        assert y[key] == 0.0
    assert _run({})["gas_mass_flow_rate"] == 0.0


def test_no_gas_flow_gives_zero_fuel_power():
    """GLR 0 with a composition gives zero gas, fuel and CO2."""
    y = _run({"ch4_fraction": 0.8, "co2_fraction": 0.2})
    assert y["gas_flow_rate"] == 0.0
    assert y["fuel_power_w"] == 0.0
    assert y["inherent_co2_kg_s"] == 0.0


@pytest.mark.parametrize(
    "ch4, co2",
    [(0.8, 0.1), (0.8, 0.3), (1.2, -0.2), (-0.1, 1.1)],
)
def test_invalid_composition_raises(ch4, co2):
    """Fractions must lie in [0, 1] and add up to 1."""
    with pytest.raises(ValueError):
        associated_gas_properties(ch4, co2)
    with pytest.raises(ValueError):
        _run({"gas_water_ratio": 0.8, "ch4_fraction": ch4, "co2_fraction": co2})


def test_pure_methane():
    """Pure CH4: density 16.04/22.414 kg/Nm3, LHV 35.8 MJ/Nm3, no inherent CO2."""
    gas = associated_gas_properties(1.0, 0.0)
    assert gas["density_kg_nm3"] == pytest.approx(16.04 / 22.414)
    assert gas["lhv_j_nm3"] == pytest.approx(35.8e6)
    assert gas["co2_kg_per_nm3"] == 0.0
