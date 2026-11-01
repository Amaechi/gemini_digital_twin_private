"""Tests for TL-10 DesignGuard (WP-06; limits.yaml hard limits, duplicate configurations)."""

from __future__ import annotations

import copy
import functools
import json
import shutil
from pathlib import Path

import pytest
import yaml

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS
from gemini_agent.schemas.config import REPO_ROOT
from gemini_agent.tools.config_generator import (
    GeneratedConfig,
    PlantDispatch,
    generate_plant,
)
from gemini_agent.verify.design_guard import DesignGuard, GuardRequest, design_guard
from unit_test.gemini_agent.tools.plant_factory import (
    CONCEPT_CASES,
    basis,
    generated,
    make_candidate,
)

GUARD_FILE = REPO_ROOT / "src" / "gemini_agent" / "verify" / "design_guard.py"


@functools.cache
def guard() -> DesignGuard:
    """Return a guard on the repository config (signatures are cleared by ``check``)."""
    return DesignGuard.from_config()


def check(candidate, config=None, project=None):
    """Validate with a fresh signature table."""
    g = guard()
    g.signatures.clear()
    return g.validate(candidate, config, project=project)


def mutated(config: GeneratedConfig, edit) -> GeneratedConfig:
    """Copy of ``config`` whose project was changed by ``edit(project)``."""
    project = copy.deepcopy(config.project)
    edit(project)
    return config.model_copy(update={"project": project})


def codes(result) -> list[str]:
    """Return the violation codes of a guard result."""
    return [v.code for v in result.violations]


def test_header_names_plant_debugger_action_guard():
    """The adapted file carries the Plant Debugger attribution header."""
    head = GUARD_FILE.read_text(encoding="utf-8").splitlines()[:2]
    assert head[0].startswith("# Adapted from marcosanyo/PlantDebugger (Apache-2.0)")
    assert "safety/action_guard.py @ d69507a" in head[1]


@pytest.mark.parametrize(("concept", "doublets", "depth", "parent"), CONCEPT_CASES)
def test_generated_configs_are_approved(concept, doublets, depth, parent):
    """Every generated C0–C7 config passes the hard limits."""
    candidate, config = generated(concept, doublets, depth, parent)
    result = check(candidate, config)
    assert result.approved, result.reason
    assert result.status == "APPROVED" and result.signature


def test_c7_rating_flag_is_a_warning_not_a_violation():
    """The plate-HEX rating flag is a warning (not a limits.yaml hard limit)."""
    candidate, config = generated("C7", 2, 2500.0, "C2-2d")
    result = check(candidate, config)
    assert result.approved
    assert any("plate-HEX rating" in w for w in result.warnings)


# --------------------------------------------------------------------------- hard limits
def test_db10_doublet_count():
    """DB-10: four doublets are rejected with the count in the reason."""
    candidate = make_candidate("C1", 4)
    result = check(candidate)
    assert codes(result) == ["DB-10"]
    assert "4 doublets outside 1–3" in result.reason
    assert any("structural checks" in w for w in result.warnings)


@pytest.mark.parametrize(
    ("flow", "limit"), [(400.0, "flow_max_m3h"), (30.0, "flow_min_active_m3h")]
)
def test_db11_design_flow(flow, limit):
    """DB-11: a design flow outside 50–300 m³/h is rejected."""
    result = check(make_candidate("C1", 1, flow_m3h=flow))
    assert codes(result) == ["DB-11"] and result.violations[0].limit == limit
    assert f"design flow {flow:g} m³/h outside 50–300" in result.reason


def test_db11_dispatch_flow_of_an_active_doublet():
    """DB-11: an active doublet dispatched below 50 m³/h is rejected."""
    candidate, config = generated("C2", 2, 2500.0, None)
    plans = [
        p.model_copy(update={"flow_m3h": 40.0}) if p.index == 2 else p for p in config.doublets
    ]
    result = check(candidate, config.model_copy(update={"doublets": plans}))
    assert codes(result) == ["DB-11"]
    assert "doublet 2 flow 40 m³/h" in result.reason


def test_db12_brine_cooling():
    """DB-12: HP source floor, plate-HEX design ΔT and absorption generator ΔT within 40 K."""
    candidate, config = generated("C2", 2, 2500.0, None)

    def lower_floor(project):
        project["components"]["hthp_1"]["parameters"]["Ts_in_minimum"] -= 5.0

    result = check(candidate, mutated(config, lower_floor))
    assert codes(result) == ["DB-12"] and "hthp_1 may cool the brine" in result.reason
    deep = config.hex_design.model_copy(update={"brine_out_degc": 40.0})
    result = check(candidate, config.model_copy(update={"hex_design": deep}))
    assert codes(result) == ["DB-12"] and "plate-HEX design brine cooling" in result.reason
    c3, c3_config = generated("C3", 2, 2500.0, None)

    def big_drive_dt(project):
        project["components"]["absorption_chiller"]["parameters"]["generator_delta_t_k"] = 45.0

    result = check(c3, mutated(c3_config, big_drive_dt))
    assert codes(result) == ["DB-12"] and "generator ΔT 45 K" in result.reason


def test_db13_injection_pump_efficiency():
    """DB-13/DB-40: the injection pump must run at 0.70 × motor × VFD."""
    candidate, config = generated("C1", 1, 2500.0, None)

    def gemini_example_pump(project):
        project["components"]["injector_well_1"]["parameters"]["injector_pump"] = {
            "efficiency_factor": 0.8
        }

    result = check(candidate, mutated(config, gemini_example_pump))
    assert codes(result) == ["DB-13"]
    assert "0.8 ≠ 0.7 × motor × VFD = 0.6315" in result.reason


def test_db14_injection_equals_production():
    """DB-14: brine leaks, cross-wired injectors and missing injectors are rejected."""
    candidate, config = generated("C3", 2, 2500.0, None)

    def leak(project):  # 10 % of the brine split off and never recombined
        project["components"]["brine_split_1"]["parameters"]["split_fractions"] = [0.5, 0.4]

    result = check(candidate, mutated(config, leak))
    assert "DB-14" in codes(result)
    assert "injector_well_1 injects producer_well_1 × 0.9" in result.reason

    c2, c2_config = generated("C2", 2, 2500.0, None)

    def cross_wire(project):
        for entry in project["connections"]:
            if entry["successor"] == "injector_well_2":
                entry["predecessor"] = "filter_b_1"

    result = check(c2, mutated(c2_config, cross_wire))
    assert codes(result) == ["DB-14"] and "injector_well_2" in result.reason

    def drop_injector(project):
        del project["components"]["injector_well_2"]
        project["connections"] = [
            c for c in project["connections"] if c["successor"] != "injector_well_2"
        ]

    result = check(c2, mutated(c2_config, drop_injector))
    assert "2 producer(s) but 1 injector(s)" in result.reason


def test_db16_db17_set_points():
    """DB-16/DB-17: heat-pump target 100 °C and chiller supply 20 °C."""
    candidate, config = generated("C2", 2, 2500.0, None)

    def edit(project):
        project["components"]["hthp_2"]["parameters"]["Th_out_target"] = 90.0
        project["components"]["electric_chiller"]["parameters"][
            "chilled_water_temperature_out_degc"
        ] = 18.0

    result = check(candidate, mutated(config, edit))
    assert codes(result) == ["DB-16", "DB-17"]
    assert "hthp_2 sink target 90.0 °C ≠ 100 °C" in result.reason
    assert "electric_chiller supply 18.0 °C ≠ 20 °C" in result.reason


def test_db36_no_venting():
    """DB-36: unburned separated gas and degassing under pressurised reinjection are rejected."""
    candidate, config = generated("C1", 3, 2500.0, None)

    def vent_one(project):
        project["connections"] = [
            c
            for c in project["connections"]
            if not (c["predecessor"] == "separator_2" and c["successor"] == "gas_adder")
        ]

    result = check(candidate, mutated(config, vent_one))
    assert codes(result) == ["DB-36"]
    assert "gas from separator_2 is not burned" in result.reason

    reinject = make_candidate("C7", 2, parent_id="C2-2d")
    _, degassed = generated("C2", 2, 2500.0, None)
    result = check(reinject, degassed)
    assert "DB-36" in codes(result) and "pressurised reinjection" in result.reason


def test_gemini_example_fails_only_on_pump_efficiency():
    """The GEMINI example burns its gas and balances Q_inj; only its 0.8 pump fails."""
    example = json.loads(
        (REPO_ROOT / "simulator_examples" / "projects" / "doublet_plant.json").read_text("utf-8")
    )
    result = check(make_candidate("C1", 1), project=example)
    assert codes(result) == ["DB-13"]  # gas is burned in boiler + CHP; Q_inj traced to Q_prod


# --------------------------------------------------------------------------- duplicates
def test_duplicate_configurations_by_hash():
    """Renamed copies are duplicates; other flows and C6 vs its parent are not."""
    gb, sub = basis()
    original = make_candidate("C2", 2)
    copy_ = make_candidate("C2", 2, candidate_id="C2-renamed", description="same design")
    other_flow = make_candidate("C2", 2, candidate_id="C2-250", flow_m3h=250.0)
    configs = {c.id: generate_plant(c, gb=gb, sub=sub) for c in (original, copy_, other_flow)}
    g = DesignGuard.from_config()
    results = g.validate_batch((c, configs[c.id]) for c in (original, copy_, other_flow))
    assert [r.approved for r in results] == [True, False, True]
    assert results[1].duplicate_of == "C2-2d" and "same GEMINI project" in results[1].reason
    again = g.validate(original, configs[original.id])
    assert again.approved and any("already approved" in w for w in again.warnings)
    fresh = DesignGuard.from_config()
    prior = fresh.validate(copy_, configs[copy_.id], prior_signatures={results[0].signature: "x"})
    assert prior.duplicate_of == "x"
    # C6 builds the same GEMINI plant as its parent but is a different design (storage).
    c3, c3_config = generated("C3", 2, 2500.0, None)
    c6, c6_config = generated("C6", 2, 2500.0, "C3-2d")
    assert c6_config.config_hash == c3_config.config_hash
    pair = DesignGuard.from_config().validate_batch([(c3, c3_config), (c6, c6_config)])
    assert all(r.approved for r in pair)


def test_signature_ignores_comment_keys():
    """Comment keys ('_…') do not change the guard signature."""
    candidate, config = generated("C1", 1, 2500.0, None)
    project = copy.deepcopy(config.project)
    project["_description"] = "edited comment"
    assert DesignGuard.signature(candidate, project) == DesignGuard.signature(
        candidate, config.project
    )


# --------------------------------------------------------------------------- config + tool
def test_guard_reads_limits_yaml(tmp_path: Path):
    """The guard reads config/limits.yaml (a lower doublet maximum rejects 3 doublets)."""
    config_copy = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", config_copy)
    limits = config_copy / "limits.yaml"
    data = yaml.safe_load(limits.read_text(encoding="utf-8"))
    data["doublet_count_max"] = 2
    limits.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    g = DesignGuard.from_config(config_copy)
    result = g.validate(make_candidate("C1", 3))
    assert codes(result) == ["DB-10"] and "outside 1–2" in result.reason


def test_design_guard_tool():
    """TL-10 tool: batch result, failed ids and a short summary."""
    gb, sub = basis()
    ok = make_candidate("C1", 1)
    bad = make_candidate("C1", 1, candidate_id="C1-fast", flow_m3h=350.0)
    config = generate_plant(ok, dispatch=PlantDispatch(), gb=gb, sub=sub)
    result = design_guard(GuardRequest(candidates=[ok, bad], configs={ok.id: config}))
    assert result.data.failed_ids == ["C1-fast"]
    assert result.summary.startswith("TL-10: 1/2 approved; rejected C1-fast")
    assert len(result.summary) <= SUMMARY_MAX_CHARS
