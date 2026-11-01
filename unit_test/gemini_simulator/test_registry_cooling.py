"""GM-04...GM-07 (WP-02): the cooling models are registered and run inside a JSON project."""

from pathlib import Path

import pytest

from gemini_model.absorption_chiller import characteristic
from gemini_model.absorption_chiller.absorption_chiller import AbsorptionChiller
from gemini_model.electric_chiller.electric_chiller import (
    ElectricChiller,
    calibrated_carnot_efficiency,
)
from gemini_model.free_cooling.free_cooling import FreeCooling
from gemini_model.heat_rejection.heat_rejection import DEFAULT_APPROACH_K, HeatRejection
from gemini_simulator.project_loader import build_project
from gemini_simulator.registry import COMPONENT_REGISTRY, create_component

COOLING_TYPES = {
    "absorption_chiller": AbsorptionChiller,
    "electric_chiller": ElectricChiller,
    "heat_rejection": HeatRejection,
    "free_cooling": FreeCooling,
}
ASSUMPTIONS = Path(__file__).resolve().parents[2] / "config" / "assumptions.yaml"


@pytest.mark.parametrize("type_name, expected_class", sorted(COOLING_TYPES.items()))
def test_cooling_types_are_registered(type_name, expected_class):
    """Each WP-02 type instantiates to its gemini_model class."""
    assert COMPONENT_REGISTRY[type_name] is expected_class
    assert isinstance(create_component(type_name), expected_class)


def test_earlier_types_are_still_registered():
    """WP-02 only appends entries."""
    earlier = {
        "adder", "mixer", "splitter", "separator", "filter", "heat_exchanger", "boiler",
        "chp", "injector_pump", "booster_pump", "well", "reservoir_bottomhole_skin",
        "reservoir_ipr", "esp", "producer_well", "injector_well", "heatpump", "compressor",
        "co2_corrosion",
    }  # fmt: skip
    assert earlier <= set(COMPONENT_REGISTRY)


def cooling_plant_project(t_wb_degc, load_w):
    """Free cooling + electric chiller + tower, and a brine-driven absorption chiller + tower."""
    return {
        "components": {
            "economiser": {"type": "free_cooling", "parameters": {}},
            "chiller": {"type": "electric_chiller", "parameters": {"nominal_capacity_w": 5e6}},
            "chiller_tower": {"type": "heat_rejection", "parameters": {}},
            "absorber": {
                "type": "absorption_chiller",
                "parameters": {
                    "nominal_capacity_w": 2e6,
                    # the tower below counts fan + pump power (RC-05/RC-06 rule)
                    "include_heat_rejection_auxiliaries": False,
                    "drive_density_kg_m3": 1030.0,
                    "drive_specific_heat_j_kgk": 4100.0,
                },
            },
            "absorber_tower": {"type": "heat_rejection", "parameters": {}},
        },
        "boundary_inputs": [
            {"component": "economiser", "key": "ambient_wet_bulb_degc", "value": t_wb_degc},
            {"component": "economiser", "key": "cooling_load_w", "value": load_w},
            {"component": "chiller_tower", "key": "ambient_wet_bulb_degc", "value": t_wb_degc},
            {"component": "chiller_tower", "key": "heat_load_w", "value": 0.0},
            {"component": "absorber_tower", "key": "ambient_wet_bulb_degc", "value": t_wb_degc},
            {"component": "absorber_tower", "key": "heat_load_w", "value": 0.0},
            {"component": "absorber", "key": "drive_temperature_in_degc", "value": 85.0},
            {"component": "absorber", "key": "drive_flow_rate_m3s", "value": 100.0 / 3600.0},
            {"component": "absorber", "key": "cooling_load_w", "value": 1.0e6},
        ],
        "connections": [
            {
                "predecessor": "chiller_tower",
                "successor": "chiller",
                "predecessor_output": "water_temperature_out_degc",
                "successor_input": "cooling_water_temperature_in_degc",
            },
            {
                "predecessor": "economiser",
                "successor": "chiller",
                "ports": [
                    ["residual_cooling_w", "cooling_load_w"],
                    ["chilled_water_temperature_out_degc", "chilled_water_temperature_in_degc"],
                ],
            },
            {
                "predecessor": "chiller",
                "successor": "chiller_tower",
                "predecessor_output": "heat_rejected_w",
                "successor_input": "heat_load_w",
            },
            {
                "predecessor": "absorber_tower",
                "successor": "absorber",
                "predecessor_output": "water_temperature_out_degc",
                "successor_input": "cooling_water_temperature_in_degc",
            },
            {
                "predecessor": "absorber",
                "successor": "absorber_tower",
                "predecessor_output": "heat_rejected_w",
                "successor_input": "heat_load_w",
            },
        ],
    }


@pytest.mark.parametrize("t_wb_degc, mode", [(10.0, "full"), (15.4, "partial"), (20.0, "off")])
def test_cooling_plant_project_runs_and_balances(t_wb_degc, mode):
    """A JSON project with every new type builds, runs, and passes duties along consistently."""
    load = 4.0e6
    solver = build_project(cooling_plant_project(t_wb_degc, load))
    solver.run()
    economiser = solver.get_output("economiser")
    chiller = solver.get_output("chiller")
    chiller_tower = solver.get_output("chiller_tower")
    absorber = solver.get_output("absorber")
    absorber_tower = solver.get_output("absorber_tower")

    assert economiser["mode"] == mode
    # Free cooling + chiller meet the load; the chiller sees the pre-cooled return.
    total = economiser["free_cooling_w"] + chiller["cooling_power_w"]
    assert total == pytest.approx(load, rel=1e-12)
    assert chiller["unmet_cooling_w"] == 0.0
    if mode == "partial":
        assert chiller["chilled_water_flow_rate_m3s"] == pytest.approx(
            economiser["chilled_water_flow_rate_m3s"], rel=1e-12
        )
    # The towers reject what the chillers reject, at T_wb + 4.5 K.
    assert chiller_tower["heat_rejected_w"] == pytest.approx(chiller["heat_rejected_w"])
    assert absorber_tower["heat_rejected_w"] == pytest.approx(absorber["heat_rejected_w"])
    # Switch off: the tower counts fan + pumps, so the chiller adds no aux power.
    assert absorber["power_el"] == 0.0
    assert absorber["heat_rejected_w"] == absorber["absorber_condenser_heat_w"]
    assert absorber["cooling_water_temperature_in_degc"] == pytest.approx(t_wb_degc + 4.5)
    if chiller["cooling_power_w"] > 0.0:
        assert chiller["condenser_temperature_degc"] == pytest.approx(t_wb_degc + 4.5 + 5.0 + 3.0)
    # The absorption chiller: drive-side balance with brine properties.
    drive = 1030.0 * 4100.0 * (100.0 / 3600.0)
    drive_heat = drive * (85.0 - absorber["drive_temperature_out_degc"])
    assert drive_heat == pytest.approx(absorber["drive_heat_w"], rel=1e-12)
    for name in ("economiser", "chiller", "chiller_tower", "absorber", "absorber_tower"):
        output = solver.get_output(name)
        assert {"power_el", "power_th", "emission"} <= set(output), name
        assert output["emission"] == 0.0


def test_model_defaults_match_the_assumptions_register():
    """Model defaults equal the WP-02 entries of config/assumptions.yaml (single source)."""
    yaml = pytest.importorskip("yaml")
    with open(ASSUMPTIONS, encoding="utf-8") as file:
        register = {item["id"]: item for item in yaml.safe_load(file)["assumptions"]}

    absorber = AbsorptionChiller().parameters
    assert register["COOL-ABS-B"]["value"] == absorber["duhring_slope"]
    assert register["COOL-ABS-CAP"]["value"] == absorber["max_capacity_fraction"]
    assert absorber["include_heat_rejection_auxiliaries"] is True
    assert register["COOL-ABS-AUX"]["value"] == pytest.approx(
        absorber["aux_power_fraction"], rel=1e-4
    )
    calibration = register["COOL-ABS-CAL"]["value"]
    assert calibration["generator_delta_t_k"] == absorber["generator_delta_t_k"]
    assert calibration["cooling_water_range_k"] == absorber["cooling_water_range_k"]
    assert calibration["chilled_water_delta_t_k"] == absorber["calibration_chilled_water_delta_t_k"]
    for key, value in characteristic.calibrate_to_ghc1989().items():
        assert calibration[key] == pytest.approx(value, rel=1e-4), key

    electric = ElectricChiller().parameters
    elc = register["COOL-ELC"]
    assert elc["tag"] == "D"
    assert electric["carnot_efficiency"] is None  # calibrated from the approaches below
    assert elc["value"]["carnot_efficiency"] == pytest.approx(
        calibrated_carnot_efficiency(
            electric["evaporator_approach_k"], electric["condenser_approach_k"]
        ),
        rel=1e-5,
    )
    for key, value in register["COOL-ELC-APPROACH"]["value"].items():
        assert electric[key] == value, key

    rejection = HeatRejection().parameters
    hr = register["COOL-HR"]["value"]
    assert hr["tower_approach_k"] == DEFAULT_APPROACH_K["cooling_tower"]
    assert hr["dry_cooler_approach_k"] == DEFAULT_APPROACH_K["dry_cooler"]
    for key in ("range_k", "aux_power_fraction", "dry_cooler_power_factor", "latent_heat_j_kg"):
        assert rejection[key] == hr[key], key

    free = FreeCooling().parameters
    for key, value in register["COOL-FC"]["value"].items():
        assert free[key] == value, key
    for parameters in (absorber, electric, free):
        assert parameters["chilled_water_temperature_in_degc"] == register["DB-32"]["value"]
