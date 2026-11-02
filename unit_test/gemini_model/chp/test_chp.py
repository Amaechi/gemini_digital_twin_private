"""Unit tests for CHP model.

Reference values use realistic data for a geothermal doublet surface
network (~10 MW doublet): efficiency_factor=0.8, caloric_value=50 MJ/kg,
gas_emission_factor=56.6 kg/GJ. The gas mass flow rate and water flow
rate below are the already-allocated (post-Splitter) values, derived from
Q_p=Q_s=140 m3/h, gas_water_ratio=0.5 Nm3/m3, gas_density=0.7 kg/Nm3,
matching the co-located Boiler (see test_boiler.py), where the boiler
takes an 80% gas fraction / 90% water fraction and this CHP unit takes
the complementary 20% gas fraction / 10% water fraction.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the
# electrical_fraction parameter test (WP-11).

import unittest

from gemini_model.chp.chp import CHP

CHP_PARAMS = {
    "efficiency_factor": 0.8,
    "caloric_value": 50e6,  # J/kg
    "gas_emission_factor": 56.6,  # kg CO2 / GJ
}

FLOW_RATE = 140.0 / 3600.0  # m3/s, realistic doublet flow of 140 m3/h
GAS_WATER_RATIO = 0.5  # Nm3/m3
GAS_DENSITY = 0.7  # kg/Nm3
CHP_GAS_FRACTION = 0.2  # complementary to boiler's 80% gas fraction
CHP_WATER_FRACTION = 0.1  # complementary to boiler's 90% water fraction

# already-allocated (post-Splitter) gas mass flow and water flow rate
GAS_FLOW_RATE = CHP_GAS_FRACTION * GAS_WATER_RATIO * FLOW_RATE * GAS_DENSITY  # kg/s
WATER_FLOW_RATE = CHP_WATER_FRACTION * FLOW_RATE  # m3/s

CHP_INPUT = {
    "temperature_in": 350.8711856072279,  # K, HeatExchanger secondary outlet
    "water_flow_rate": WATER_FLOW_RATE,
    "gas_flow_rate": GAS_FLOW_RATE,
}


class TestCHP(unittest.TestCase):
    """Test cases for CHP model."""

    def setUp(self):
        """Initialize the configuration and initial parameters."""
        self.chp = CHP()
        self.chp.update_parameters(dict(CHP_PARAMS))

    def test_chp_calculation(self):
        """Test CHP electrical power, heat output, outlet temperature, and emissions."""
        self.chp.calculate_output(dict(CHP_INPUT))
        y = self.chp.get_output()

        self.assertAlmostEqual(y["power_el"], 36296.296296296285, delta=1e-2)
        self.assertAlmostEqual(y["power_th"], 72592.59259259257, delta=1e-2)
        self.assertAlmostEqual(y["temperature_out"], 355.10398984003217, delta=1e-6)
        self.assertAlmostEqual(y["emission"], 0.007703888888888887, delta=1e-8)

    def test_electrical_power_is_half_of_thermal(self):
        """Test the fixed 1/3-2/3 electrical/thermal power split."""
        self.chp.calculate_output(dict(CHP_INPUT))
        y = self.chp.get_output()
        self.assertAlmostEqual(y["power_el"], y["power_th"] / 2)

    def test_electrical_fraction_parameter(self):
        """CHP-EL-FRAC 0.50 sensitivity (WP-11): same useful output, split 50/50."""
        self.chp.calculate_output(dict(CHP_INPUT))
        base = dict(self.chp.get_output())
        self.chp.update_parameters({"electrical_fraction": 0.5})
        self.chp.calculate_output(dict(CHP_INPUT))
        y = self.chp.get_output()
        self.assertAlmostEqual(y["power_el"], y["power_th"])
        total = base["power_el"] + base["power_th"]
        self.assertAlmostEqual(y["power_el"] + y["power_th"], total)
        self.assertAlmostEqual(y["emission"], base["emission"])
        self.chp.update_parameters({"electrical_fraction": 1.0})
        with self.assertRaises(ValueError):
            self.chp.calculate_output(dict(CHP_INPUT))

    def test_zero_water_flow_no_temperature_rise(self):
        """Test zero (already-split) water flow leaves temperature unchanged."""
        u = dict(CHP_INPUT)
        u["water_flow_rate"] = 0.0
        self.chp.calculate_output(u)
        y = self.chp.get_output()
        self.assertAlmostEqual(y["temperature_out"], CHP_INPUT["temperature_in"])

    def test_calculate_output_requires_required_inputs(self):
        """Test required CHP runtime inputs are validated."""
        with self.assertRaises(KeyError):
            self.chp.calculate_output({"temperature_in": 333.0})

    def test_volumetric_gas_flow_rate_matches_mass_flow_rate(self):
        """Test gas_volumetric_flow_rate (via gas_density) matches an equivalent mass flow."""
        u = dict(CHP_INPUT)
        del u["gas_flow_rate"]
        u["gas_volumetric_flow_rate"] = GAS_FLOW_RATE / GAS_DENSITY

        chp_params_with_density = dict(CHP_PARAMS)
        chp_params_with_density["gas_density"] = GAS_DENSITY
        self.chp.update_parameters(chp_params_with_density)

        self.chp.calculate_output(u)
        y = self.chp.get_output()

        self.chp.calculate_output(dict(CHP_INPUT))
        y_reference = self.chp.get_output()

        self.assertAlmostEqual(y["power_el"], y_reference["power_el"], delta=1e-6)
        self.assertAlmostEqual(y["power_th"], y_reference["power_th"], delta=1e-6)
        self.assertAlmostEqual(y["temperature_out"], y_reference["temperature_out"], delta=1e-9)
        self.assertAlmostEqual(y["emission"], y_reference["emission"], delta=1e-9)
