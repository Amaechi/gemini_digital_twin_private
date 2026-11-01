"""GM-02: heatpump, compressor and co2_corrosion are usable from project JSON."""

import numpy as np
import pytest

from gemini_model.compressor.compressor import Compressor
from gemini_model.corrosion.co2_corrosion import CO2Corrosion
from gemini_model.fluid.pvt_water_stp import PVTConstantSTP
from gemini_model.heatpump.heatpump_basic import HeatpumpBasic
from gemini_simulator.project_loader import build_project
from gemini_simulator.registry import COMPONENT_REGISTRY, create_component

COMPRESSOR_PARAMETERS = {
    "mechanical_efficiency": 0.95,
    "compressor_efficiency": 0.75,
    "specific_heat_ratio": 1.3,
    "gas_constant": 384.0,  # J/kg.K for the 80/20 CH4/CO2 mix: 8314.5 / 21.64
    "number_of_stages": 2,
}


@pytest.mark.parametrize(
    "type_name, expected_class",
    [("heatpump", HeatpumpBasic), ("compressor", Compressor), ("co2_corrosion", CO2Corrosion)],
)
def test_new_types_are_registered_and_instantiable(type_name, expected_class):
    """The GM-02 types instantiate to the expected GEMINI classes."""
    assert COMPONENT_REGISTRY[type_name] is expected_class
    assert isinstance(create_component(type_name), expected_class)


def test_existing_types_are_still_registered():
    """GM-02 only adds entries; the original types are untouched."""
    original = {
        "adder", "mixer", "splitter", "separator", "filter", "heat_exchanger", "boiler",
        "chp", "injector_pump", "booster_pump", "well", "reservoir_bottomhole_skin",
        "reservoir_ipr", "esp", "producer_well", "injector_well",
    }  # fmt: skip
    assert original <= set(COMPONENT_REGISTRY)
    assert isinstance(create_component("well").PVT, PVTConstantSTP)


def test_heatpump_and_compressor_run_inside_a_project():
    """A project JSON with the new types builds, runs and reports the standard outputs."""
    project = {
        "components": {
            "heat_pump": {
                "type": "heatpump",
                "parameters": {"mode": "carnot", "eta_carnot": 0.5, "Th_out_target": 80},
            },
            "gas_compressor": {"type": "compressor", "parameters": COMPRESSOR_PARAMETERS},
        },
        "boundary_inputs": [
            {"component": "heat_pump", "key": "Th_in", "value": 30.0},
            {"component": "heat_pump", "key": "Ts_in", "value": 40.0},
            {"component": "heat_pump", "key": "hot_flow_rate", "value": 10.0 / 3600.0},
            {"component": "heat_pump", "key": "source_flow_rate", "value": 20.0 / 3600.0},
            {"component": "gas_compressor", "key": "mass_flow", "value": 0.064},
            {"component": "gas_compressor", "key": "pressure_in", "value": 3.0},
            {"component": "gas_compressor", "key": "pressure_out", "value": 10.0},
            {"component": "gas_compressor", "key": "temperature_in", "value": 60.0},
        ],
        "connections": [],
    }
    solver = build_project(project)
    solver.run()

    heat_pump = solver.get_output("heat_pump")
    assert heat_pump["power_th"] > 0.0
    assert heat_pump["power_el"] > 0.0
    assert heat_pump["power_th"] / heat_pump["power_el"] == pytest.approx(heat_pump["COP"])

    compressor = solver.get_output("gas_compressor")
    assert compressor["power_el"] == compressor["compressor_power"] > 0.0
    assert compressor["power_th"] == 0.0
    assert compressor["emission"] == 0.0


def test_compressor_power_el_equals_the_gemini_formula():
    """power_el mirrors GEMINI's formula: n m R T1/(k-1) (r^((k-1)/k) - 1) / (eta_m eta_c).

    This pins the existing implementation, r = (p2/p1)^(1/n); it is not an
    independent check (the formula lacks the factor k of the steady-flow
    isentropic work -- see the Compressor docstring and PROGRESS.md).
    """
    compressor = create_component("compressor")
    compressor.update_parameters(COMPRESSOR_PARAMETERS)
    compressor.calculate_output(
        {"mass_flow": 0.064, "pressure_in": 3.0, "pressure_out": 10.0, "temperature_in": 60.0}
    )
    n, k, r_gas = 2, 1.3, 384.0
    stage_ratio = (10.0 / 3.0) ** (1 / n)
    expected = n * 0.064 * r_gas * 333.15 / (k - 1) * (stage_ratio ** ((k - 1) / k) - 1)
    expected /= 0.95 * 0.75
    assert compressor.get_output()["power_el"] == pytest.approx(expected, rel=1e-12)


def test_well_type_from_project_json_uses_its_pvt_block():
    """GM-01: a 'well' (DPDT) built from JSON keeps its brine PVT (hook runs before params)."""
    project = {
        "components": {
            "brine_well": {
                "type": "well",
                "parameters": {
                    "diameter": [0.2],
                    "length": [2500.0],
                    "angle": [1.5707963267948966],
                    "roughness": [4.57e-5],
                    "friction_correlation": "darcy_weisbach",
                    "friction_correlation_2p": "BeggsBrill",
                    "correction_factors": [1, 0],
                    "pvt": {"liquid_density_kg_m3": 1030.0},
                },
            }
        },
        "boundary_inputs": [
            {"component": "brine_well", "key": "direction", "value": "down"},
            {"component": "brine_well", "key": "pressure", "value": 1e5},
            {"component": "brine_well", "key": "temperature", "value": 300.0},
            {"component": "brine_well", "key": "temperature_ambient", "value": 300.0},
            {"component": "brine_well", "key": "flowrate", "value": 1e-6},
        ],
        "connections": [],
    }
    solver = build_project(project)
    well = solver.components["brine_well"]
    assert well.PVT.parameters["RHOL"] == 1030.0
    # GEMINI's DPDT needs numpy arrays for its geometry (plain JSON lists fail in
    # calculate_output -- existing behaviour); a later update without "pvt" keeps the PVT.
    geometry = ("diameter", "length", "angle", "roughness")
    well.update_parameters({key: np.asarray(well.parameters[key]) for key in geometry})
    assert well.PVT.parameters["RHOL"] == 1030.0
    solver.run()
    head_bar = solver.get_output("brine_well", "pressuredrop_grav_output") / 1e5
    assert head_bar == pytest.approx(252.6, abs=0.1)  # RC-01: 1030 kg/m3 over 2500 m
