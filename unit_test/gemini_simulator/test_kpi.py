"""Unit tests for the generalised gemini_simulator.kpi helpers (GM-10, GM-01)."""

import copy
import json
import warnings
from pathlib import Path

import pytest

from gemini_model.fluid.pvt_config import heat_exchanger_fluid_parameters
from gemini_simulator import kpi
from gemini_simulator.project_loader import build_project

PROJECT_PATH = (
    Path(__file__).resolve().parents[2] / "simulator_examples" / "projects" / "doublet_plant.json"
)
BRINE_PVT = {"liquid_density_kg_m3": 1030.0, "liquid_specific_heat_j_kgk": 4100.0}


class _Component:
    def __init__(self, parameters=None):
        self.parameters = parameters or {}


class _FakeSolver:
    """Minimal stand-in for a solved Solver: fixed outputs, optional parameters."""

    def __init__(self, outputs, parameters=None):
        self._outputs = outputs
        parameters = parameters or {}
        self.components = {name: _Component(parameters.get(name)) for name in outputs}

    def get_output(self, component, key=None, timestep=-1):
        snapshot = self._outputs[component]
        return snapshot if key is None else snapshot[key]


def _plant_solver(pvt=None, hex_gets_pvt=False):
    with open(PROJECT_PATH, "r", encoding="utf-8") as file:
        project = json.load(file)
    if pvt is not None:
        project = copy.deepcopy(project)
        project["components"]["producer_well"]["parameters"]["pvt"] = pvt
        if hex_gets_pvt:
            project["components"]["heat_exchanger"]["parameters"].update(
                heat_exchanger_fluid_parameters(pvt)
            )
    solver = build_project(project)
    solver.run()
    return solver


def test_defaults_reproduce_the_legacy_formulas():
    """With default arguments every helper gives the original hard-coded result."""
    solver = _plant_solver()
    flow_m3s = solver.get_output("producer_well", "flow_rate_m3s")
    t_in = solver.get_output("filter_a", "temperature_out")
    t_out = solver.get_output("heat_exchanger", "primary_temperature_out")
    assert kpi.doublet_thermal_power_w(solver, flow_m3s) == (
        flow_m3s * 1050.0 * 4200.0 * (t_in - t_out)
    )
    assert kpi.secondary_outlet_temperature(solver) == solver.get_output(
        "water_mixer", "temperature"
    )
    assert kpi.plant_thermal_power_w(solver, 0.04, 20.0) == (
        0.04 * 1050.0 * 4200.0 * (solver.get_output("water_mixer", "temperature") - 20.0)
    )
    chp_el = solver.get_output("chp", "power_el")
    assert kpi.electricity_surplus_w(solver, 1000.0, 500.0) == chp_el - 1000.0 - 500.0
    assert kpi.power_total_w(5e6, solver, 1000.0, 500.0) == 5e6 + chp_el - 1500.0
    assert kpi.total_emission_kg_s(solver, 0.1, 0.2) == (
        solver.get_output("boiler", "emission") + solver.get_output("chp", "emission") + 0.1 + 0.2
    )
    assert (kpi.FLUID_DENSITY, kpi.SPECIFIC_HEAT) == (1050.0, 4200.0)
    assert kpi.net_electrical_power_w is kpi.electricity_surplus_w


def test_primary_fluid_properties_come_from_the_producer_pvt():
    """GM-01: the producer well's 'pvt' block is the single source of rho and c_p."""
    solver = _plant_solver(BRINE_PVT, hex_gets_pvt=True)
    assert kpi.fluid_properties(solver) == (1030.0, 4100.0)
    assert kpi.fluid_consistency_issues(solver) == []
    flow_m3s = solver.get_output("producer_well", "flow_rate_m3s")
    t_in = solver.get_output("filter_a", "temperature_out")
    t_out = solver.get_output("heat_exchanger", "primary_temperature_out")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # consistent PVT -> no warning
        power_w = kpi.doublet_thermal_power_w(solver, flow_m3s)
    assert power_w == pytest.approx(flow_m3s * 1030.0 * 4100.0 * (t_in - t_out))
    # Energy balance closes: the KPI equals the duty the heat exchanger computed.
    assert power_w == pytest.approx(solver.get_output("heat_exchanger", "heat_duty"), rel=1e-9)
    # Explicit values still win.
    assert kpi.doublet_thermal_power_w(
        solver, flow_m3s, fluid_density=1000.0, specific_heat=4000.0
    ) == pytest.approx(flow_m3s * 1000.0 * 4000.0 * (t_in - t_out))


def test_mismatched_heat_exchanger_fluid_is_flagged():
    """Brine PVT on the well but default HEX rho/c_p -> reported, and the KPI warns."""
    solver = _plant_solver(BRINE_PVT)  # HEX keeps its 1050 kg/m3, 4200 J/kg.K defaults
    issues = kpi.fluid_consistency_issues(solver)
    assert len(issues) == 1 and "heat_exchanger" in issues[0]
    flow_m3s = solver.get_output("producer_well", "flow_rate_m3s")
    with pytest.warns(UserWarning, match="energy balance will not close"):
        power_w = kpi.doublet_thermal_power_w(solver, flow_m3s)
    hex_duty_w = solver.get_output("heat_exchanger", "heat_duty")
    assert power_w / hex_duty_w == pytest.approx(1030.0 * 4100.0 / (1050.0 * 4200.0), rel=1e-9)
    # Several components and a missing name are reported too.
    issues = kpi.fluid_consistency_issues(solver, components=("heat_exchanger", "boiler", "nope"))
    assert len(issues) == 3


def test_no_consistency_issues_for_legacy_projects():
    """Without a 'pvt' block nothing is compared, so the defaults never warn."""
    solver = _plant_solver()
    assert kpi.fluid_consistency_issues(solver) == []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        kpi.doublet_thermal_power_w(solver, solver.get_output("producer_well", "flow_rate_m3s"))


def test_fluid_properties_fall_back_to_legacy_defaults():
    """No 'pvt' block, or no such component -> the legacy KPI constants."""
    solver = _FakeSolver({"producer_well": {}})
    assert kpi.fluid_properties(solver) == (1050.0, 4200.0)
    # A non-default component name that does not exist must not fall back silently.
    with pytest.raises(KeyError):
        kpi.fluid_properties(solver, component="producer_2")
    with pytest.raises(KeyError):
        kpi.fluid_consistency_issues(solver, pvt_component="producer_2")
    assert kpi.fluid_properties(solver, default_density_kg_m3=1000.0) == (1000.0, 4200.0)
    assert kpi.fluid_properties(object()) == (1050.0, 4200.0)  # no components at all
    # A partial block keeps the PVT defaults for the keys it leaves out (as the well does).
    partial = _FakeSolver({"well": {}}, parameters={"well": {"pvt": {"RHOL": 1030.0}}})
    assert kpi.fluid_properties(partial, component="well") == (1030.0, 4217.4)


def test_component_names_are_parameters():
    """Any topology works by passing its own component names (no hard-coded names)."""
    solver = _FakeSolver(
        {
            "hex_mixer": {"temperature": 60.0},
            "hex_1": {"primary_temperature_in": 85.0, "primary_temperature_out": 45.0},
            "gas_engine": {"power_el": 2.0e6, "emission": 0.05},
            "aux_boiler": {"power_el": 0.0, "emission": 0.02},
            "heat_pump": {"power_el": 0.5e6, "emission": 0.0},
        }
    )
    assert kpi.secondary_outlet_temperature(solver, mixer="hex_mixer") == 60.0
    assert kpi.plant_thermal_power_w(
        solver, 0.1, 40.0, mixer="hex_mixer", fluid_density=1000.0, specific_heat=4000.0
    ) == pytest.approx(0.1 * 1000.0 * 4000.0 * 20.0)
    assert kpi.doublet_thermal_power_w(
        solver,
        0.05,
        inlet=("hex_1", "primary_temperature_in"),
        outlet=("hex_1", "primary_temperature_out"),
    ) == pytest.approx(0.05 * 1050.0 * 4200.0 * 40.0)
    surplus = kpi.electricity_surplus_w(
        solver, 0.3e6, 0.1e6, generators=("gas_engine",), consumers=("heat_pump",)
    )
    assert surplus == pytest.approx(2.0e6 - 0.3e6 - 0.1e6 - 0.5e6)
    assert kpi.power_total_w(
        10e6, solver, generators=("gas_engine",), consumers=("heat_pump",)
    ) == pytest.approx(10e6 + 1.5e6)
    assert kpi.total_emission_kg_s(solver, 0.001, emitters=("gas_engine", "aux_boiler")) == (
        pytest.approx(0.071)
    )
    assert kpi.total_emission_kg_s(solver, 0.001, 0.002, emitters=()) == pytest.approx(0.003)


def test_sum_component_outputs():
    """Sums named outputs; misspelt names raise; None counts as 0."""
    solver = _FakeSolver({"a": {"power_el": 1.0, "p": None}, "b": {"power_el": 2.5, "p": 3.0}})
    assert kpi.sum_component_outputs(solver, "power_el", ("a", "b")) == 3.5
    assert kpi.sum_component_outputs(solver, "p", ("a", "b")) == 3.0
    assert kpi.sum_component_outputs(solver, "power_el", ()) == 0.0
    with pytest.raises(KeyError):
        kpi.sum_component_outputs(solver, "power_el", ("a", "c"))
    with pytest.raises(KeyError):
        kpi.sum_component_outputs(solver, "power_th", ("a",))


def test_module_defaults_are_read_at_call_time(monkeypatch):
    """Patching kpi.FLUID_DENSITY/SPECIFIC_HEAT still changes the defaults, as before."""
    solver = _FakeSolver({"water_mixer": {"temperature": 30.0}})
    monkeypatch.setattr(kpi, "FLUID_DENSITY", 1000.0)
    monkeypatch.setattr(kpi, "SPECIFIC_HEAT", 4000.0)
    assert kpi.plant_thermal_power_w(solver, 0.01, 20.0) == pytest.approx(0.01 * 1000 * 4000 * 10)
    assert kpi.fluid_properties(solver) == (1000.0, 4000.0)
