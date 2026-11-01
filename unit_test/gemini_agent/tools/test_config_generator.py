"""Tests for TL-09 ``generate_gemini_config`` (WP-06; spec §3.4, §3.5; RC-03, RC-08)."""

from __future__ import annotations

import json
import math
import shutil
from typing import Any

import pytest

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, GasHandling
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.tools.config_generator import (
    DISTRICT_FLUID,
    GENERATOR_ASSUMPTION_IDS,
    ConfigError,
    ConfigRequest,
    DistrictSpec,
    DoubletTrainSpec,
    PlantDispatch,
    PlantSpec,
    ProjectBuilder,
    build_plant_project,
    counterflow_ntu,
    gas_burner_values,
    generate_gemini_config,
    generate_plant,
    hex_ua_for_outlet,
    load_generator_basis,
)
from gemini_model.fluid.pvt_config import heat_exchanger_fluid_parameters
from gemini_model.heat_exchanger.heat_exchanger import HeatExchanger
from gemini_simulator.project_loader import build_project
from unit_test.gemini_agent.tools.plant_factory import (
    CONCEPT_CASES,
    basis,
    generated,
    make_candidate,
)

EXAMPLE = REPO_ROOT / "simulator_examples" / "projects" / "doublet_plant.json"


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"duplicate JSON keys {duplicates}"
    return dict(pairs)


def assert_topological(project: dict[str, Any]) -> None:
    """Every connection into a component precedes all connections out of it, grouped."""
    successors = [c["successor"] for c in project["connections"]]
    for k, entry in enumerate(project["connections"]):
        pred = entry["predecessor"]
        later_inputs = [s for s in successors[k:] if s == pred]
        assert not later_inputs, f"'{pred}' gets an input after it is read (connection {k})"
    seen: list[str] = []
    for succ in successors:
        if seen and seen[-1] == succ:
            continue
        assert succ not in seen, f"connections into '{succ}' are not consecutive"
        seen.append(succ)


# --------------------------------------------------------------------------- builder
def test_builder_rejects_duplicate_names_and_unknown_types():
    """Builder refuses duplicate names, unregistered types and unknown components."""
    builder = ProjectBuilder()
    builder.add("a", "filter", {}, "filter")
    with pytest.raises(ConfigError, match="duplicate component name 'a'"):
        builder.add("a", "filter", {}, "filter")
    with pytest.raises(ConfigError, match="not in COMPONENT_REGISTRY"):
        builder.add("b", "magic_box", {}, "filter")
    with pytest.raises(ConfigError, match="unknown component"):
        builder.connect("a", "nope", [("x", "y")])


def test_builder_orders_connections_topologically_and_detects_cycles():
    """Connections come out in topological order (runnable) and a cycle is refused."""
    builder = ProjectBuilder()
    for name in ("c", "b", "a"):
        builder.add(name, "adder", {"num_inputs": 1}, "gas_adder")
    builder.connect("b", "c", [("output", "input_0")])  # declared before a → b
    builder.connect("a", "b", [("output", "input_0")])
    builder.boundary_input("a", "input_0", 2.0)
    project = builder.project()
    assert [c["successor"] for c in project["connections"]] == ["b", "c"]
    solver = build_project(project)
    solver.run()
    assert solver.get_output("c", "output") == 2.0
    builder.connect("c", "a", [("output", "input_0")])
    with pytest.raises(ConfigError, match="cycle"):
        builder.project()


# --------------------------------------------------------------------------- all concepts
@pytest.mark.parametrize(("concept", "doublets", "depth", "parent"), CONCEPT_CASES)
def test_every_concept_builds_runs_and_is_well_formed(concept, doublets, depth, parent):
    """C0–C7 with 0–3 doublets: unique names, ordered connections, wells only via TL-05; runs."""
    _, config = generated(concept, doublets, depth, parent)
    project = config.project
    assert set(project) == {"_description", "components", "boundary_inputs", "connections"}
    json.loads(json.dumps(project), object_pairs_hook=_pairs_hook)  # unique names in the text
    assert_topological(project)
    types = {spec["type"] for spec in project["components"].values()}
    assert "well" not in types
    producers = [n for n, s in project["components"].items() if s["type"] == "producer_well"]
    injectors = [n for n, s in project["components"].items() if s["type"] == "injector_well"]
    assert len(producers) == len(injectors) == doublets
    solver = build_project(project)
    solver.run()
    for name in producers:
        assert solver.get_output(name, "flow_rate") == pytest.approx(300.0, rel=0.01)
    assert config.config_hash and len(config.doublets) == doublets
    assert set(config.assumption_ids) == set(GENERATOR_ASSUMPTION_IDS)


@pytest.mark.parametrize(("concept", "doublets", "depth", "parent"), CONCEPT_CASES[1:])
def test_wells_come_from_resolve_doublet(concept, doublets, depth, parent):
    """Wells use brine pvt, SUB-WELL roughness, injector friction as a loss and the TL-05 point."""
    _, config = generated(concept, doublets, depth, parent)
    _, sub = basis()
    components = config.project["components"]
    producer = components["producer_well_1"]["parameters"]
    injector = components["injector_well_1"]["parameters"]
    pvt = {
        "liquid_density_kg_m3": 1030.0,
        "liquid_viscosity_pa_s": 0.0004,
        "liquid_specific_heat_j_kgk": 4100.0,
    }
    for params in (producer, injector):
        assert params["pvt"] == pvt
        assert params["roughness_m"] == pytest.approx(sub.well_roughness_m)
        assert params["roughness_m"] != 0.01
    assert injector["friction_opposes_flow"] is True
    assert injector["injector_pump"]["efficiency_factor"] == pytest.approx(0.70 * 0.93 * 0.97)
    assert set(producer["esp"]) == {"no_stages", "head_coeff", "power_coeff"}
    assert injector["reservoir_pressure_bar"] == producer["reservoir_pressure_bar"]
    boundary = {(b["component"], b["key"]): b["value"] for b in config.project["boundary_inputs"]}
    plan = config.doublets[0]
    assert boundary[("producer_well_1", "wellhead_temperature")] == plan.wellhead_temperature_degc
    assert (
        boundary[("producer_well_1", "wellhead_pressure")] == plan.producer_wellhead_pressure_barg
    )
    assert producer["esp_freq_hz"] == plan.esp_frequency_hz


def test_brine_and_district_fluid_properties_on_hex_boiler_chp():
    """HEX primary = brine, secondary/boiler/CHP/HP sink = district water (GM-01, WP-02)."""
    _, config = generated("C2", 2, 2500.0, None)
    components = config.project["components"]
    hex_params = components["hex_1"]["parameters"]
    brine = heat_exchanger_fluid_parameters(components["producer_well_1"]["parameters"]["pvt"])
    assert hex_params["fluid_density"] == brine["fluid_density"] == 1030.0
    assert hex_params["specific_heat"] == brine["specific_heat"] == 4100.0
    assert hex_params["secondary_fluid_density"] == DISTRICT_FLUID["fluid_density"] == 1000.0
    assert hex_params["secondary_specific_heat"] == DISTRICT_FLUID["specific_heat"] == 4217.4
    for burner in ("chp", "peak_boiler"):
        params = components[burner]["parameters"]
        assert params["fluid_density"] == 1000.0 and params["specific_heat"] == 4217.4
    hp = components["hthp_1"]["parameters"]
    assert (hp["rho_s"], hp["Cp_s"], hp["rho_h"], hp["Cp_h"]) == (1030.0, 4100.0, 1000.0, 4217.4)
    assert hp["Th_out_target"] == 100.0
    assert hp["Ts_in_minimum"] == pytest.approx(config.doublets[0].wellhead_temperature_degc - 40)


def test_concept_equipment():
    """Each concept family gets the equipment of spec §3.4 (C6/C7 from their parent)."""

    def roles(concept, doublets, depth=2500.0, parent=None):
        return set(generated(concept, doublets, depth, parent)[1].roles.values())

    assert roles("C0", 0) == {"boiler", "electric_chiller", "heat_rejection"}
    assert {"direct_hex", "chp", "separator"} <= roles("C1", 3)
    assert "heat_pump" not in roles("C1", 3) and "free_cooling" not in roles("C1", 3)
    assert {"heat_pump", "free_cooling", "electric_chiller"} <= roles("C2", 2)
    assert {"absorption_chiller", "brine_splitter", "brine_mixer"} <= roles("C3", 2)
    assert "heat_pump" not in roles("C4", 2, 3200.0)
    assert "absorption_chiller" in roles("C4", 2, 3200.0)
    assert "direct_hex" not in roles("C5", 3, 1500.0) and "heat_pump" in roles("C5", 3, 1500.0)
    c7 = roles("C7", 2, parent="C2-2d")
    assert "separator" not in c7 and "chp" not in c7 and "heat_pump" in c7
    _, c6 = generated("C6", 2, 2500.0, "C3-2d")
    assert c6.layout_concept == "C3" and any("HT-ATES" in note for note in c6.notes)


# --------------------------------------------------------------------------- RC-08 burners
def test_rc08_associated_gas_values():
    """RC-08 validation: 0.965 kg/Nm³, 29.7 MJ/kg, 68.6 kg/GJ; methane 50.03 MJ/kg."""
    lhv, factor, density = gas_burner_values(0.8, 0.2)
    assert density == pytest.approx(0.965, abs=0.002)
    assert lhv / 1e6 == pytest.approx(29.7, abs=0.2)
    assert factor == pytest.approx(68.6, abs=0.5)
    assert factor == pytest.approx(68.56, abs=0.01)
    grid_lhv, _, _ = gas_burner_values(1.0, 0.0)
    assert grid_lhv / 1e6 == pytest.approx(50.03, abs=0.01)


def test_chp_burns_associated_gas_at_separator_lhv():
    """The CHP burns all separated gas at the separator LHV and 68.56 kg/GJ (RC-08: 1.91 MW)."""
    _, config = generated("C1", 1, 2500.0, None)
    solver = build_project(config.project)
    solver.run()
    chp = config.project["components"]["chp"]["parameters"]
    assert chp["caloric_value"] == pytest.approx(solver.get_output("separator_1", "fuel_lhv_j_kg"))
    assert chp["gas_emission_factor"] == pytest.approx(68.56, abs=0.01)
    # RC-08 validation: fuel power at 300 m³/h = 1.91 ± 0.02 MW (LHV).
    assert solver.get_output("separator_1", "fuel_power_w") / 1e6 == pytest.approx(1.91, abs=0.02)
    fuel_w = solver.inputs["chp"]["gas_flow_rate"] * chp["caloric_value"]
    assert fuel_w == pytest.approx(solver.get_output("separator_1", "fuel_power_w"))
    co2_kg_s = solver.get_output("chp", "emission")
    assert co2_kg_s == pytest.approx(solver.get_output("separator_1", "co2_if_burned_kg_s"), 1e-3)
    boiler = config.project["components"]["peak_boiler"]["parameters"]
    assert boiler["gas_emission_factor"] == 56.6 and boiler["efficiency_factor"] == 0.90
    assert config.burner_fuels == {"chp": "associated_gas", "peak_boiler": "grid_gas"}


def test_separator_letdown_brings_brine_to_pump_suction():
    """CFG-SURFACE: the degasser letdown brings the brine to SUB-PSUC; surface is adiabatic."""
    _, config = generated("C2", 2, 2500.0, None)
    _, sub = basis()
    solver = build_project(config.project)
    solver.run()
    suction = solver.get_output("filter_b_1", "pressure_out")
    assert suction == pytest.approx(sub.injection_pump_suction_barg, abs=0.05)
    assert solver.get_output("separator_1", "temperature_out") == solver.get_output(
        "producer_well_1", "temperature_out"
    )


# --------------------------------------------------------------------------- RC-03 HEX
def test_rc03_gns_example_area_and_lmtd_consistency():
    """GNS p.57–58: 300 kW, 90→60 °C vs 45→70 °C: LMTD 17.4 K, A = 6.8 m² at U 3, C_f 0.85."""
    fluid = {"fluid_density": 1000.0, "specific_heat": 4200.0}
    hot = 300e3 / (30.0 * 4.2e6)
    cold = 300e3 / (25.0 * 4.2e6)
    ua = hex_ua_for_outlet(
        primary_flow_m3s=hot,
        primary_in_degc=90.0,
        secondary_flow_m3s=cold,
        secondary_in_degc=45.0,
        secondary_out_degc=70.0,
        primary_fluid=fluid,
        secondary_fluid=fluid,
    )
    lmtd = (20.0 - 15.0) / math.log(20.0 / 15.0)
    assert lmtd == pytest.approx(17.4, abs=0.05)
    assert ua == pytest.approx(300e3 / lmtd, rel=0.02)  # ε-NTU vs LMTD within ±2 %
    assert ua / (3000.0 * 0.85) == pytest.approx(6.8, rel=0.03)
    model = HeatExchanger()  # RC-03 eq 3: GEMINI's model hits the target at this UA
    model.update_parameters(
        {"flow_resistance": 0.0, "flow_configuration": "counter", "heat_transfer_coefficient": ua}
        | fluid
        | {"secondary_fluid_density": 1000.0, "secondary_specific_heat": 4200.0}
    )
    model.calculate_output(
        {
            "pressure_in": 0.0,
            "primary_flow_rate": hot,
            "secondary_flow_rate": cold,
            "primary_temperature_in": 90.0,
            "secondary_temperature_in": 45.0,
        }
    )
    assert model.output["secondary_temperature_out"] == pytest.approx(70.0, abs=1e-6)
    assert model.output["primary_temperature_out"] == pytest.approx(60.0, abs=1e-6)


def test_counterflow_ntu_inverts_rc03_eq1_including_balanced_flows():
    """RC-03 eq 1 inverted round-trips for C_r 0–1, including the balanced limit."""
    for c_r in (0.0, 0.3, 0.8, 0.999999, 1.0):
        for ntu in (0.5, 2.0, 9.0):
            if c_r == 1.0:
                eps = ntu / (1 + ntu)
            else:
                x = math.exp(-ntu * (1 - c_r))
                eps = (1 - x) / (1 - c_r * x)
            assert counterflow_ntu(eps, c_r) == pytest.approx(ntu, rel=1e-6)
    with pytest.raises(ValueError):
        counterflow_ntu(1.0, 0.5)


def test_rc03_guidebook_example_ntu_and_area():
    """Guidebook p.222–223: 160→130 / 110→150 °F, U 900 Btu/h·ft²·°F, F 0.9: NTU 3.1, 259 ft²."""
    fluid = {"fluid_density": 1000.0, "specific_heat": 4186.0}
    c_hot, c_cold = 40.0, 30.0  # W/K ratio for ΔT 30 °F hot, 40 °F cold
    ua = hex_ua_for_outlet(
        primary_flow_m3s=c_hot / 4.186e6,
        primary_in_degc=(160 - 32) / 1.8,
        secondary_flow_m3s=c_cold / 4.186e6,
        secondary_in_degc=(110 - 32) / 1.8,
        secondary_out_degc=(150 - 32) / 1.8,
        primary_fluid=fluid,
        secondary_fluid=fluid,
    )
    ntu_book = ua / c_cold / 0.9
    assert ntu_book == pytest.approx(3.1, rel=0.05)
    q_btu_h = 200 * 500 * 30  # 200 gpm × 500 × ΔT
    lmtd_f = 10.0 / math.log(2.0)
    assert q_btu_h / (900 * 0.9 * lmtd_f) == pytest.approx(259, rel=0.05)


def test_hex_design_rule_and_evidence():
    """CFG-HEX: 3 K approaches, UA = Q/LMTD, NTU ≤ 4 per pass, 24 kPa/NTU; C4 limited by DB-12."""
    _, config = generated("C2", 2, 2500.0, None)
    h = config.hex_design
    t_wh = config.doublets[0].wellhead_temperature_degc
    assert h.district_out_degc == pytest.approx(t_wh - 3.0)
    assert h.brine_out_degc == pytest.approx(58.0)
    assert h.duty_mw == pytest.approx(300 / 3600 * 1030 * 4100 * (t_wh - 58.0) / 1e6)
    lmtd = 3.0  # balanced flows: both ends at the 3 K approach
    assert h.ua_w_per_k == pytest.approx(h.duty_mw * 1e6 / lmtd, rel=0.02)
    assert h.ntu_per_pass <= 4.0 and h.passes * h.ntu_per_pass == pytest.approx(h.ntu)
    assert h.pressure_drop_per_pass_pa == pytest.approx(3.5 * 6894.757 * h.ntu_per_pass)
    points = config.hex_points()
    assert [p.name for p in points] == ["hex_1", "hex_2"]
    assert points[0].plate_material == "titanium" and points[0].design_pressure_barg <= 20.0
    c4 = generated("C4", 2, 3200.0, None)[1].hex_design
    assert c4.district_out_degc == 100.0
    assert c4.brine_in_degc - c4.brine_out_degc == pytest.approx(40.0)


def test_c7_pressurised_loop_is_flagged_against_plate_hex_rating():
    """C7: WHP at 1.2 p_b(a), no degasser/CHP, flagged above the plate-HEX pressure rating."""
    _, config = generated("C7", 2, 2500.0, "C2-2d")
    assert config.flags and "plate-HEX rating" in config.flags[0] and "PD-10" in config.flags[0]
    assert config.hex_design.design_pressure_barg > 20.68
    boundary = {(b["component"], b["key"]): b["value"] for b in config.project["boundary_inputs"]}
    whp = boundary[("producer_well_1", "wellhead_pressure")]
    p_b = config.release_pressure_barg
    assert whp == pytest.approx(1.2 * (p_b + 1.01325) - 1.01325, rel=1e-6)
    assert "chp" not in config.roles and "separator_1" not in config.roles
    result = generate_gemini_config(
        ConfigRequest(candidate=make_candidate("C7", 2, parent_id="C2-2d"))
    )
    assert any("plate-HEX rating" in w for w in result.warnings)


# --------------------------------------------------------------------------- dispatch
def test_doublet_off_is_omitted_and_flows_are_validated():
    """A doublet at 0 m³/h is left out; wrong-length or infeasible dispatch flows are refused."""
    gb, sub = basis()
    candidate = make_candidate("C2", 3)
    config = generate_plant(
        candidate, dispatch=PlantDispatch(doublet_flows_m3h=[300.0, 0.0, 150.0]), gb=gb, sub=sub
    )
    assert [p.active for p in config.doublets] == [True, False, True]
    assert "producer_well_2" not in config.project["components"]
    fractions = config.project["components"]["district_split_hex"]["parameters"]
    assert fractions["split_fractions"] == pytest.approx([2 / 3, 1 / 3])
    with pytest.raises(ConfigError, match="2 doublet flows for 3"):
        generate_plant(candidate, dispatch=PlantDispatch(doublet_flows_m3h=[300.0, 300.0]))
    with pytest.raises(ConfigError, match="infeasible"):
        generate_plant(candidate, dispatch=PlantDispatch(doublet_flows_m3h=[300.0, 40.0, 300.0]))


def test_tool_result_and_register_ids():
    """TL-09 result: short summary; every assumption id exists (CHP share = CHP-EL-FRAC 1/3)."""
    result = generate_gemini_config(ConfigRequest(candidate=make_candidate("C3", 2)))
    assert len(result.summary) <= SUMMARY_MAX_CHARS and result.summary.startswith("TL-09")
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)
    assert "CHP-EL-FRAC" in result.assumption_ids
    assert register.get("CHP-EL-FRAC").value == pytest.approx(1 / 3, abs=5e-5)
    assert set(register.get("CFG-CHP").value) == {"total_efficiency_frac"}  # no duplicate share
    assert load_generator_basis().chp_electrical_fraction == 0.3333


# --------------------------------------------------------------------------- GEMINI example
def example_spec(example: dict[str, Any]) -> PlantSpec:
    """Return the GEMINI doublet example as a 1-doublet C1-like ``PlantSpec`` (own parameters)."""
    components = example["components"]
    boundary = {(b["component"], b["key"]): b["value"] for b in example["boundary_inputs"]}

    def params(name: str) -> dict[str, Any]:
        return components[name]["parameters"]

    doublet = DoubletTrainSpec(
        index=1,
        producer=params("producer_well"),
        injector=params("injector_well"),
        wellhead_pressure_barg=boundary[("producer_well", "wellhead_pressure")],
        wellhead_temperature_degc=boundary[("producer_well", "wellhead_temperature")],
        separator=params("separator"),
        filter_a=params("filter_a"),
        filter_b=params("filter_b"),
        hex=params("heat_exchanger"),
    )
    district = DistrictSpec(
        flow_m3s=boundary[("secondary_splitter", "input")],
        return_temperature_degc=boundary[("heat_exchanger", "secondary_temperature_in")],
        supply_temperature_degc=100.0,
        chp=params("chp"),
        chp_fuel="associated_gas",
        chp_layout="parallel",
        chp_water_frac=params("secondary_splitter")["split_fractions"][1],
        boiler=params("boiler"),
        boiler_fuel="associated_gas",
        associated_gas_to_boiler_frac=params("gas_splitter")["split_fractions"][0],
    )
    return PlantSpec(name="GEMINI doublet example (C1-like)", doublets=[doublet], district=district)


def test_doublet_example_reproduced_within_one_percent():
    """WP-06 acceptance: the GEMINI doublet example rebuilt by the generator, outputs within 1 %."""
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    reference = build_project(example)
    reference.run()
    built = build_plant_project(example_spec(example))
    assert_topological(built.project)
    solver = build_project(built.project)
    solver.run()
    pairs = [
        (("producer_well", "flow_rate"), ("producer_well_1", "flow_rate")),
        (("producer_well", "esp_power_el"), ("producer_well_1", "esp_power_el")),
        (("producer_well", "esp_intake_pressure"), ("producer_well_1", "esp_intake_pressure")),
        (("separator", "temperature_out"), ("separator_1", "temperature_out")),
        (("separator", "gas_mass_flow_rate"), ("separator_1", "gas_mass_flow_rate")),
        (("heat_exchanger", "heat_duty"), ("hex_1", "heat_duty")),
        (("heat_exchanger", "primary_temperature_out"), ("hex_1", "primary_temperature_out")),
        (("heat_exchanger", "secondary_temperature_out"), ("hex_1", "secondary_temperature_out")),
        (("boiler", "power_th"), ("peak_boiler", "power_th")),
        (("boiler", "temperature_out"), ("peak_boiler", "temperature_out")),
        (("chp", "power_el"), ("chp", "power_el")),
        (("chp", "power_th"), ("chp", "power_th")),
        (("water_mixer", "temperature"), ("district_mix_out", "temperature")),
        (("filter_b", "pressure_out"), ("filter_b_1", "pressure_out")),
        (("injector_well", "wellhead_pressure"), ("injector_well_1", "wellhead_pressure")),
        (("injector_well", "power_el"), ("injector_well_1", "power_el")),
    ]
    for (ref_name, key), (name, key2) in pairs:
        expected = float(reference.get_output(ref_name, key))
        assert float(solver.get_output(name, key2)) == pytest.approx(expected, rel=0.01), key
    assert solver.get_output("producer_well_1", "flow_rate") == pytest.approx(129.02, abs=0.01)


def test_register_gas_and_chp_values_match_the_code(tmp_path):
    """CFG-ASSOC-GAS equals the computed values; CHP-EL-FRAC ≠ 1/3 reaches chp.py (WP-11)."""
    register = load_config("assumptions")
    assoc = register.get("CFG-ASSOC-GAS").value
    lhv, factor, density = gas_burner_values(0.8, 0.2)
    assert assoc["lhv_j_kg"] == pytest.approx(lhv, rel=1e-3)
    assert assoc["emission_kg_per_gj"] == pytest.approx(factor, abs=0.005)
    assert assoc["density_kg_nm3"] == pytest.approx(density, abs=0.0005)
    config_dir = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", config_dir)
    path = config_dir / "assumptions.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("  - id: CHP-EL-FRAC\n")
    block = text[start:].replace("    value: 0.3333\n", "    value: 0.5\n", 1)
    path.write_text(text[:start] + block, encoding="utf-8")
    gb = load_generator_basis(config_dir)
    assert gb.chp_electrical_fraction == 0.5
    config = generate_plant(make_candidate("C2", 2), gb=gb, sub=basis()[1])
    assert config.project["components"]["chp"]["parameters"]["electrical_fraction"] == 0.5
    _, base = generated("C2", 2, 2500.0, None)
    assert "electrical_fraction" not in base.project["components"]["chp"]["parameters"]
    path.write_text(text[:start] + block.replace("value: 0.5\n", "value: 1.0\n", 1), "utf-8")
    with pytest.raises(ConfigError, match="CHP-EL-FRAC"):
        load_generator_basis(config_dir)


def test_gas_handling_drives_degassing_and_hp_lift_floor():
    """Gas handling (DB-36) sets degassing; heat-pump floors respect DB-12 and VER-HP-LIFT."""
    gb, sub = basis()
    reinjecting = make_candidate("C2", 2).model_copy(
        update={"gas_handling": GasHandling.PRESSURISED_REINJECT}
    )
    config = generate_plant(reinjecting, gb=gb, sub=sub)
    assert "separator_1" not in config.roles and "chp" not in config.roles
    assert any("overrides" in note for note in config.notes)
    _, c5 = generated("C5", 3, 1500.0, None)
    floor = c5.project["components"]["hthp_1"]["parameters"]["Ts_in_minimum"]
    assert floor == pytest.approx(100.0 - 60.0)  # lift limit binds over T_wh − 40 K = 16.4 °C
    _, c4 = generated("C4", 2, 3200.0, None)
    assert any("flashing is not checked" in flag for flag in c4.flags)
    assert "district_split_hex_bypass" in c4.roles


def test_associated_gas_factor_agrees_between_code_paths():
    """CFG-ASSOC-GAS (separator path, 68.56) = ECO-CO2-AG / DB-28 (design-basis path, WP-08)."""
    from gemini_agent.tools.design_basis import gas_mix_properties

    _, factor, density = gas_burner_values(0.8, 0.2)
    mix = gas_mix_properties(0.8, 0.2)
    assert mix.density_kg_per_nm3 == pytest.approx(density, rel=1e-9)
    per_gj = mix.co2_when_burned_kg_per_nm3 / mix.lhv_mj_per_nm3 * 1e3
    assert per_gj == pytest.approx(factor, rel=1e-9)
    eco = load_config("assumptions").get("ECO-CO2-AG").value
    assert eco["factor_kg_per_gj_lhv"] == pytest.approx(factor, abs=0.05)
