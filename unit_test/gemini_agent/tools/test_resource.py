"""Tests for TL-03 ``reservoir_conditions`` and TL-04 ``productivity_index`` (RC-01 checks)."""

import math

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.resource import (
    SUBSURFACE_ASSUMPTION_IDS,
    ProductivityIndexRequest,
    ReservoirConditionsRequest,
    load_subsurface_basis,
    productivity_index,
    reservoir_conditions,
)


@pytest.fixture(scope="module")
def basis():
    """Subsurface inputs from the repository config."""
    return load_subsurface_basis()


def test_basis_reads_every_register_entry(basis):
    """All subsurface [A] values exist in the register (golden rule 2) and are loaded."""
    register = load_config("assumptions")
    for assumption_id in SUBSURFACE_ASSUMPTION_IDS:
        register.get(assumption_id)
    assert basis.well_roughness_m == pytest.approx(0.0457e-3)  # RC-01 eq 5 steel, not 0.01 m
    assert basis.well_inner_diameter_m == pytest.approx(2 * basis.wellbore_radius_m)
    assert basis.bubble_point_basis == "sea_water"  # RC-01 item 9 (Henry's law)
    assert basis.bubble_point_second_bound == "pure_water"
    assert basis.bubble_point_margin_factor == 1.2
    assert basis.gas_liquid_ratio_nm3_per_m3 == 0.8  # DB-09
    assert basis.esp_frequency_min_hz == 30.0 and basis.esp_frequency_max_hz == 70.0
    assert basis.analysis_period_yr == 30.0
    assert basis.wire_to_water_efficiency_frac == pytest.approx(0.70 * 0.93 * 0.97)


def test_basis_follows_config_edits(config_copy, edit_yaml):
    """Register values are read from config, never re-typed in code."""

    def mutate(data):
        for item in data["assumptions"]:
            if item["id"] == "SUB-PWH":
                item["value"] = 12.5

    edit_yaml(config_copy / "assumptions.yaml", mutate)
    assert load_subsurface_basis(config_copy).producer_wellhead_pressure_barg == 12.5


@pytest.mark.parametrize(
    ("assumption_id", "key", "value", "message"),
    [
        ("SUB-WELL", "trajectory", "deviated", "vertical"),
        ("DB-40", "pump_hydraulic_efficiency_frac", 0.65, "limits"),
        ("SUB-PBUB", "method", "default_20_barg", "henry_law"),
        ("SUB-PBUB", "basis", "brine", "basis"),
        ("SUB-PBUB", "intake_margin_factor", 1.3, "VER-ESP-MARGIN"),
    ],
)
def test_basis_rejects_inconsistent_register(
    config_copy, edit_yaml, assumption_id, key, value, message
):
    """Unsupported or contradictory register values fail loudly."""

    def mutate(data):
        for item in data["assumptions"]:
            if item["id"] == assumption_id:
                item["value"][key] = value

    edit_yaml(config_copy / "assumptions.yaml", mutate)
    with pytest.raises(ValueError, match=message):
        load_subsurface_basis(config_copy)


def test_reservoir_conditions_at_design_depth():
    """RC-01 eq 1–2: 87 °C, given 250 bar(g) vs 252.6 bar hydrostatic ⇒ ESP needed."""
    result = reservoir_conditions()
    assert isinstance(result, ToolResult)
    data = result.data
    assert data.temperature_degc == pytest.approx(87.0)
    assert data.pressure_barg == 250.0 and data.pressure_model == "given"
    assert data.hydrostatic_pressure_barg == pytest.approx(252.6, abs=0.1)
    assert data.static_wellhead_pressure_barg == pytest.approx(-2.6, abs=0.1)
    assert data.esp_required
    assert data.heating_supply_gap_k == pytest.approx(13.0)
    assert data.depth_for_heating_supply_m == pytest.approx(88.0 / 0.030)
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    assert "RC-01" in result.sources
    assert result.warnings  # 87 °C < 100 °C supply


def test_reservoir_conditions_other_depth_is_hydrostatic():
    """DB-07: other depths use ρ g z and the 30 K/km gradient."""
    data = reservoir_conditions(ReservoirConditionsRequest(depth_m=3000.0)).data
    assert data.pressure_model == "hydrostatic"
    assert data.pressure_barg == pytest.approx(1030.0 * 9.81 * 3000.0 / 1e5)
    assert data.static_wellhead_pressure_barg == pytest.approx(0.0, abs=1e-9)
    assert data.temperature_degc == pytest.approx(102.0)
    assert data.heating_supply_gap_k < 0.0


def test_reservoir_conditions_rejects_bad_depth():
    """Depth must be positive."""
    with pytest.raises(ValidationError):
        ReservoirConditionsRequest(depth_m=0.0)


def test_productivity_index_matches_rc01():
    """RC-01 validation: PI 12.2 m³/h/bar ±2 %; II = PI; GEMINI skin model agrees."""
    result = productivity_index()
    data = result.data
    assert data.productivity_index_m3h_per_bar == pytest.approx(12.2, rel=0.02)
    assert data.injectivity_index_m3h_per_bar == data.productivity_index_m3h_per_bar
    assert data.productivity_index_m3s_per_pa == pytest.approx(
        data.productivity_index_m3h_per_bar / 3.6e8
    )
    assert data.log_radius_ratio == pytest.approx(math.log(1000.0 / 0.108))
    assert data.gemini_cross_check_rel_diff < 1e-9
    by_flow = {p.flow_m3h: p for p in data.drawdown}
    assert by_flow[300.0].drawdown_bar == pytest.approx(24.5, abs=0.1)  # RC-01: 20–25 bar
    assert by_flow[50.0].injection_overpressure_bar == pytest.approx(50.0 / 12.22, rel=0.01)
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    assert set(result.assumption_ids) <= set(SUBSURFACE_ASSUMPTION_IDS)


def test_productivity_index_skin_reduces_pi():
    """Skin S adds to ln(r_e/r_w) in the denominator (RC-01 eq 3)."""
    base = productivity_index().data
    damaged = productivity_index(ProductivityIndexRequest(skin=5.0, flows_m3h=[100.0])).data
    expected = (
        base.productivity_index_m3h_per_bar * base.log_radius_ratio / (base.log_radius_ratio + 5.0)
    )
    assert damaged.productivity_index_m3h_per_bar == pytest.approx(expected)
    assert damaged.skin_share_frac == pytest.approx(5.0 / (base.log_radius_ratio + 5.0))
    assert damaged.gemini_cross_check_rel_diff < 1e-9
    stimulated = productivity_index(ProductivityIndexRequest(skin=-1.0))
    assert stimulated.data.productivity_index_m3h_per_bar > base.productivity_index_m3h_per_bar
    assert stimulated.warnings


def test_productivity_index_rejects_bad_geometry():
    """r_e must exceed r_w."""
    with pytest.raises(ValueError, match="drainage radius"):
        productivity_index(ProductivityIndexRequest(drainage_radius_m=0.05))
