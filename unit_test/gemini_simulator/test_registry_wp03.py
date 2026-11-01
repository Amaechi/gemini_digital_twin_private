"""GM-03/08/09 (WP-03): new types are registered and the heat pump + e-boiler run in a project."""

from pathlib import Path

import pytest

from gemini_model.chp.chp import CHP
from gemini_model.electric_boiler.electric_boiler import ElectricBoiler
from gemini_model.heatpump.heatpump_basic import HeatpumpBasic
from gemini_model.thermal_storage.thermal_storage import ThermalStorage
from gemini_simulator.project_loader import build_project
from gemini_simulator.registry import COMPONENT_REGISTRY, create_component

WP03_TYPES = {"electric_boiler": ElectricBoiler, "thermal_storage": ThermalStorage}


@pytest.mark.parametrize("type_name, expected_class", sorted(WP03_TYPES.items()))
def test_wp03_types_are_registered(type_name, expected_class):
    """Each WP-03 type instantiates to its gemini_model class."""
    assert COMPONENT_REGISTRY[type_name] is expected_class
    assert isinstance(create_component(type_name), expected_class)


def test_earlier_types_are_still_registered():
    """WP-03 only appends entries."""
    earlier = {
        "adder", "mixer", "splitter", "separator", "filter", "heat_exchanger", "boiler",
        "chp", "injector_pump", "booster_pump", "well", "reservoir_bottomhole_skin",
        "reservoir_ipr", "esp", "producer_well", "injector_well", "heatpump", "compressor",
        "co2_corrosion", "absorption_chiller", "electric_chiller", "heat_rejection",
        "free_cooling",
    }  # fmt: skip
    assert earlier <= set(COMPONENT_REGISTRY)
    assert COMPONENT_REGISTRY["heatpump"] is HeatpumpBasic


def heat_pump_with_boiler_project(source_min_degc):
    """Brine-sourced HTHP heating district water 82 → 100 °C, electric boiler in series after it."""
    return {
        "components": {
            "hthp": {
                "type": "heatpump",
                "parameters": {
                    "mode": "lorenz",
                    "solver": "closed_form",
                    "Th_out_target": 100.0,
                    "Ts_in_minimum": source_min_degc,
                    "Cp_h": 4217.4,
                    "Cp_s": 4100.0,
                    "rho_h": 1000.0,
                    "rho_s": 1030.0,
                },
            },
            "eboiler": {
                "type": "electric_boiler",
                "parameters": {"nominal_capacity_w": 5.0e6},
            },
        },
        "boundary_inputs": [
            {"component": "hthp", "key": "Th_in", "value": 82.0},
            {"component": "hthp", "key": "Ts_in", "value": 60.0},
            {"component": "hthp", "key": "hot_flow_rate", "value": 100.0 / 3600.0},
            {"component": "hthp", "key": "source_flow_rate", "value": 80.0 / 3600.0},
        ],
        "connections": [
            {
                "predecessor": "hthp",
                "successor": "eboiler",
                "ports": [
                    ["Th_out", "water_temperature_in_degc"],
                    ["hot_flow_rate", "water_flow_rate_m3s"],
                ],
            }
        ],
    }


@pytest.mark.parametrize("source_min_degc, topped_up", [(10.0, False), (50.0, True)])
def test_heat_pump_and_electric_boiler_project(source_min_degc, topped_up):
    """RC-04 peaking practice: the e-boiler tops up to 100 °C when the source floor binds."""
    solver = build_project(heat_pump_with_boiler_project(source_min_degc))
    solver.run()
    hthp = solver.get_output("hthp")
    eboiler = solver.get_output("eboiler")
    assert hthp["running"] and hthp["COP"] == pytest.approx(hthp["cop_cap"], rel=1e-12)
    assert eboiler["water_temperature_out_degc"] == pytest.approx(100.0)
    total = hthp["power_th"] + eboiler["power_th"]
    assert total == pytest.approx(1000.0 * 4217.4 * (100.0 / 3600.0) * 18.0, rel=1e-9)
    assert (eboiler["power_th"] > 0.0) is topped_up
    for output in (hthp, eboiler):
        assert {"power_el", "power_th", "emission"} <= set(output)
        assert output["emission"] == 0.0


def test_chp_register_entry_matches_gemini():
    """CHP-EL-FRAC states GEMINI's fixed 1/3 electrical share (chp.py unchanged)."""
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parents[2] / "config" / "assumptions.yaml"
    with open(path, encoding="utf-8") as file:
        register = {item["id"]: item for item in yaml.safe_load(file)["assumptions"]}
    assert register["CHP-EL-FRAC"]["value"] == pytest.approx(CHP.ELECTRICAL_POWER_FRACTION, 1e-4)
