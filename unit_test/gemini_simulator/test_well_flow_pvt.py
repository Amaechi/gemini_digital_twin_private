"""GM-01: the "pvt" block of producer/injector well configs reaches the well model.

Reference values: RC-01 validation tests (brine 1030 kg/m3 vs water over
2500 m -> 7.36 bar more hydrostatic head).
"""

import copy
import json
from pathlib import Path

import pytest

from gemini_simulator.project_loader import build_project
from gemini_simulator.well_components import InjectorWell, ProducerWell
from gemini_simulator.well_flow import (
    producer_esp_power_w,
    producer_esp_pressures_bar,
    solve_injector_wellhead_pressure_bar,
    solve_primary_flow_m3h,
)

PROJECT_PATH = (
    Path(__file__).resolve().parents[2] / "simulator_examples" / "projects" / "doublet_plant.json"
)
BRINE_DENSITY_ONLY = {"liquid_density_kg_m3": 1030.0}
BRINE_PVT = {
    "liquid_density_kg_m3": 1030.0,  # DB-08 [P]
    "liquid_viscosity_pa_s": 0.40e-3,  # DB-08 [P]
    "liquid_specific_heat_j_kgk": 4100.0,  # DB-33 [A]
}
VERTICAL_2500M = [{"MD": 0.0, "TVD": 0.0, "ID": 0.2}, {"MD": 2500.0, "TVD": 2500.0, "ID": 0.2}]


def _project():
    with open(PROJECT_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def _injector_config(pvt=None):
    config = {
        "reservoir_pressure_bar": 260.0,
        "productivity_index_m3h_per_bar": 10.0,
        "trajectory_table": VERTICAL_2500M,
    }
    if pvt is not None:
        config["pvt"] = pvt
    return config


def test_injector_wellhead_pressure_drops_by_brine_head():
    """At near-zero flow, brine needs ~7.36 bar less wellhead pressure than water."""
    kwargs = {"flow_m3h": 1.0, "wellhead_temperature_c": 60.0}
    water_bar = solve_injector_wellhead_pressure_bar(_injector_config(), **kwargs)
    brine_bar = solve_injector_wellhead_pressure_bar(_injector_config(BRINE_DENSITY_ONLY), **kwargs)
    assert water_bar - brine_bar == pytest.approx(7.36, abs=0.05)


def test_esp_discharge_pressure_uses_brine_density():
    """The wellhead-to-ESP pressure rise scales with density (1030/1000) for brine.

    Both terms scale with rho: gravity (rho g z) and, for this fully rough
    pipe (roughness 0.01 m), friction (f L/D rho v^2/2 with f ~ constant).
    """
    water = _project()["components"]["producer_well"]["parameters"]
    brine = {**water, "pvt": BRINE_DENSITY_ONLY}
    wellhead_bar = 3.0
    _, water_discharge = producer_esp_pressures_bar(water, 130.0, wellhead_bar)
    _, brine_discharge = producer_esp_pressures_bar(brine, 130.0, wellhead_bar)
    ratio = (brine_discharge - wellhead_bar) / (water_discharge - wellhead_bar)
    assert ratio == pytest.approx(1030.0 / 1000.0, abs=1e-3)
    # The gravity part alone is (1030 - 1000) g 650 m = 1.91 bar.
    assert brine_discharge - water_discharge > 30.0 * 9.81 * 650.0 / 1e5


def test_producer_flow_with_brine_differs_from_water():
    """Heavier brine lowers the flow the ESP well delivers at the same wellhead pressure."""
    water = _project()["components"]["producer_well"]["parameters"]
    brine = {**water, "pvt": BRINE_PVT}
    kwargs = {"wellhead_pressure_bar": 3.0, "wellhead_temperature_c": 80.0}
    water_flow = solve_primary_flow_m3h(water, **kwargs)
    brine_flow = solve_primary_flow_m3h(brine, **kwargs)
    assert water_flow == pytest.approx(129.02, abs=0.01)  # GEMINI example, default unchanged
    assert brine_flow < water_flow - 1.0


def test_esp_pressure_rise_and_power_scale_with_brine_specific_gravity():
    """The ESP pumps the same brine as the column: dP = SG rho_w g H and P = SG P_water.

    The GEMINI ESP curves are for water (head in ft, brake power at SG 1); with a 1030 kg/m3
    'pvt' block both scale by SG = 1030 / 1000 (RC-01 items 6-8). Before the WP-01 review the
    ESP ignored the block (28.44 bar and 175.1 kW for both fluids at 130 m3/h).
    """
    water = _project()["components"]["producer_well"]["parameters"]
    brine = {**water, "pvt": BRINE_PVT}
    flow_m3h, wellhead_bar = 130.0, 3.0
    water_intake, water_discharge = producer_esp_pressures_bar(water, flow_m3h, wellhead_bar)
    brine_intake, brine_discharge = producer_esp_pressures_bar(brine, flow_m3h, wellhead_bar)
    assert water_intake > 0.0 and brine_intake > 0.0  # not clipped at 0 bar
    water_rise_bar = water_discharge - water_intake
    brine_rise_bar = brine_discharge - brine_intake
    assert water_rise_bar == pytest.approx(28.44, abs=0.01)  # GEMINI water value, unchanged
    assert brine_rise_bar / water_rise_bar == pytest.approx(1030.0 / 1000.0, rel=1e-12)
    water_power_w = producer_esp_power_w(water, flow_m3h)
    assert producer_esp_power_w(brine, flow_m3h) / water_power_w == pytest.approx(
        1030.0 / 1000.0, rel=1e-12
    )


def test_pvt_block_without_density_keeps_the_gemini_esp():
    """SG is exactly 1 unless the block sets the liquid density, so the ESP is unchanged."""
    water = _project()["components"]["producer_well"]["parameters"]
    no_density = {**water, "pvt": {"liquid_specific_heat_j_kgk": 4100.0}}
    assert producer_esp_power_w(no_density, 130.0) == producer_esp_power_w(water, 130.0)
    kwargs = {"wellhead_pressure_bar": 3.0, "wellhead_temperature_c": 80.0}
    assert solve_primary_flow_m3h(no_density, **kwargs) == solve_primary_flow_m3h(water, **kwargs)


def test_producer_flow_with_brine_uses_the_brine_esp():
    """Regression: the brine producer delivers 114.80 m3/h (112.95 with a water ESP).

    The stronger ESP (x 1.03) recovers part of the flow lost to the heavier column.
    """
    water = _project()["components"]["producer_well"]["parameters"]
    brine = {**water, "pvt": BRINE_PVT}
    flow_m3h = solve_primary_flow_m3h(brine, wellhead_pressure_bar=3.0, wellhead_temperature_c=80.0)
    assert flow_m3h == pytest.approx(114.80, abs=0.01)


def test_project_json_pvt_reaches_both_wells():
    """A 'pvt' block in project JSON changes the solved plant; the default is unchanged."""
    default_solver = build_project(_project())
    default_solver.run()
    assert default_solver.get_output("producer_well", "flow_rate") == pytest.approx(
        129.02, abs=0.01
    )

    # The example injector (3100 m TVD, 240 bar reservoir) floors at ~0 bar wellhead pressure; raise
    # the injection reservoir pressure in both runs so the wellhead pressure is positive.
    water = copy.deepcopy(_project())
    water["components"]["injector_well"]["parameters"]["reservoir_pressure_bar"] = 340.0
    water_solver = build_project(water)
    water_solver.run()

    project = copy.deepcopy(water)
    project["components"]["producer_well"]["parameters"]["pvt"] = BRINE_PVT
    project["components"]["injector_well"]["parameters"]["pvt"] = BRINE_PVT
    brine_solver = build_project(project)
    brine_solver.run()
    assert brine_solver.get_output("producer_well", "flow_rate") < 128.0
    # The injector side gains ~7 bar of column head, so it needs a lower wellhead pressure.
    # The brine column (and its lower flow) changes the injector wellhead pressure. The
    # flow-independent 7.36 bar check is test_injector_wellhead_pressure_drops_by_brine_head.
    water_whp_bar = water_solver.get_output("injector_well", "wellhead_pressure")
    brine_whp_bar = brine_solver.get_output("injector_well", "wellhead_pressure")
    assert water_whp_bar > 10.0
    assert brine_whp_bar < water_whp_bar - 1.0


@pytest.mark.parametrize("component_class", [ProducerWell, InjectorWell])
def test_well_components_reject_a_bad_pvt_block(component_class):
    """A misspelt 'pvt' key fails when the project is built, not mid-run."""
    with pytest.raises(ValueError):
        component_class().update_parameters({"pvt": {"density": 1030.0}})
