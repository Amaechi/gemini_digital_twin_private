"""Tests for TL-01 ``load_design_basis`` (spec §2.2 DB-20…29; RC-01, RC-02, RC-08 checks)."""

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.design_basis import (
    DesignBasisReport,
    DesignBasisRequest,
    breakthrough_time_yr,
    brine_heat_mw,
    gas_mix_properties,
    hydrostatic_pressure_barg,
    load_design_basis,
    productivity_index_m3h_per_bar,
)


@pytest.fixture(scope="module")
def result():
    return load_design_basis()


@pytest.fixture(scope="module")
def derived(result):
    return result.data.derived


def test_returns_tool_result_with_short_summary(result):
    assert isinstance(result, ToolResult)
    assert isinstance(result.data, DesignBasisReport)
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    assert "87.0" in result.summary
    assert {"RC-01", "RC-02", "RC-08"} <= set(result.sources)


def test_assumption_ids_exist_in_register(result):
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)
    assert set(result.assumption_ids) == {"DB-33", "DB-38", "SUB-RHOC", "SUB-II"}


def test_fixed_inputs_come_from_config(result):
    assert result.data.fixed == load_config("design_basis")
    assert result.data.brine_cp_j_per_kg_k == load_config("assumptions").get("DB-33").value


def test_db20_reservoir_temperature(derived):
    """DB-20: 12 + 0.03 × 2500 = 87 °C; supply 100 °C is 13 K above it (spec §3.1 fact 1)."""
    assert derived.reservoir_temperature_degc == pytest.approx(87.0, abs=1e-9)
    assert derived.heating_supply_above_reservoir_k == pytest.approx(13.0, abs=1e-9)


def test_db21_hydrostatic_pressure_and_pumps(result, derived):
    """DB-21 / RC-01 validation: 2500 m, 1030 kg/m³ → 252.6 bar ±0.1 ⇒ ESP + injection pump."""
    assert derived.hydrostatic_pressure_barg == pytest.approx(252.6, abs=0.1)
    assert derived.static_wellhead_pressure_barg == pytest.approx(250.0 - 252.6075, abs=1e-6)
    assert derived.production_wellhead_pressure_at_max_flow_barg == pytest.approx(
        derived.static_wellhead_pressure_barg - derived.drawdown_at_max_flow_bar
    )
    assert derived.esp_required is True
    assert derived.injection_pump_required is True
    assert derived.injection_wellhead_pressure_at_max_flow_barg > (
        derived.injection_wellhead_pressure_at_min_flow_barg
    )
    assert any("top-up" in w for w in result.warnings)


def test_rc01_brine_vs_water_column():
    """RC-01 validation: brine vs water (1000 kg/m³) difference over 2500 m → 7.36 bar."""
    diff = hydrostatic_pressure_barg(2500.0, 1030.0) - hydrostatic_pressure_barg(2500.0, 1000.0)
    assert diff == pytest.approx(7.36, abs=0.01)


def test_db22_to_db24_flow_and_heat(derived):
    """DB-22 85.8 kg/s; DB-23 ≈ 14.1 MW; DB-24 ≈ 2.35 MW (c_p 4.1 kJ/kg K, ΔT 40 K)."""
    assert derived.mass_flow_at_max_flow_kg_s == pytest.approx(85.8, abs=0.05)
    assert derived.max_heat_per_doublet_mw == pytest.approx(14.1, abs=0.05)
    assert derived.min_heat_per_active_doublet_mw == pytest.approx(2.35, abs=0.01)
    assert derived.max_geothermal_heat_all_doublets_mw == pytest.approx(3 * 14.0767, abs=0.01)
    assert derived.min_doublets_for_peak_brine_heat == 3


def test_db25_productivity_index(derived):
    """RC-01 validation: S = 0, r_e/r_w = 1000/0.108 → PI 12.2 m³/h/bar ±2 % (spec 12–16).

    Drawdown at 300 m³/h is in the RC-01 envelope 20–25 bar (spec quotes ≈ 22 bar).
    """
    assert derived.productivity_index_m3h_per_bar == pytest.approx(12.2, rel=0.02)
    assert 12.0 <= derived.productivity_index_m3h_per_bar <= 16.0
    assert 20.0 <= derived.drawdown_at_max_flow_bar <= 25.0
    assert derived.drawdown_at_max_flow_bar == pytest.approx(
        300.0 / derived.productivity_index_m3h_per_bar
    )


def test_productivity_index_skin_and_errors():
    base = productivity_index_m3h_per_bar(200.0, 100.0, 4e-4, 1000.0, 0.108, 0.0)
    assert productivity_index_m3h_per_bar(200.0, 100.0, 4e-4, 1000.0, 0.108, 5.0) < base
    assert productivity_index_m3h_per_bar(200.0, 100.0, 4e-4, 1000.0, 0.108, -1.0) > base
    with pytest.raises(ValueError):
        productivity_index_m3h_per_bar(200.0, 100.0, 4e-4, 1000.0, 0.108, -20.0)


def test_db26_breakthrough(derived):
    """RC-02 validation: 23.6 yr ±2 % at D = 1.0 km; spec DB-26: 34 yr at 1.2 km."""
    times = {p.spacing_m: p.breakthrough_time_yr for p in derived.breakthrough}
    assert times[1000.0] == pytest.approx(23.6, rel=0.02)
    assert times[1200.0] == pytest.approx(34.0, rel=0.02)
    assert all(p.flow_m3h == 300.0 for p in derived.breakthrough)


def test_breakthrough_scales_with_spacing_squared_and_inverse_flow():
    """RC-02 property test: t_b ∝ D² and ∝ 1/Q."""
    base = breakthrough_time_yr(100.0, 1000.0, 300.0, 2.5e6, 4.223e6)
    assert breakthrough_time_yr(100.0, 2000.0, 300.0, 2.5e6, 4.223e6) == pytest.approx(4 * base)
    assert breakthrough_time_yr(100.0, 1000.0, 150.0, 2.5e6, 4.223e6) == pytest.approx(2 * base)
    assert base == pytest.approx(23.6, rel=0.02)
    with pytest.raises(ValueError):
        breakthrough_time_yr(100.0, 1000.0, 0.0, 2.5e6, 4.223e6)


def test_db27_associated_gas(derived):
    """DB-27 / RC-08: 240 Nm³/h ⇒ ≈ 1.91 MW fuel; ≈ 0.46 kg CH₄ + 0.32 kg CO₂ per m³ brine."""
    assert derived.associated_gas_at_max_flow_nm3h == pytest.approx(240.0)
    assert derived.associated_gas_fuel_at_max_flow_mw == pytest.approx(1.91, abs=0.01)
    assert derived.ch4_kg_per_m3_brine == pytest.approx(0.46, abs=0.005)
    assert derived.co2_kg_per_m3_brine == pytest.approx(0.32, abs=0.01)


def test_db28_gas_mix(derived):
    """RC-08 validation: 0.965 ±0.002 kg/Nm³; LHV 29.7 ±0.2 MJ/kg; 68.6 ±0.5 kg CO₂/GJ."""
    gas = derived.gas_mix
    assert gas.density_kg_per_nm3 == pytest.approx(0.965, abs=0.002)
    assert gas.lhv_mj_per_kg == pytest.approx(29.7, abs=0.2)
    assert gas.lhv_mj_per_nm3 == pytest.approx(28.6, abs=0.05)
    assert gas.co2_when_burned_kg_per_gj == pytest.approx(68.6, abs=0.5)
    assert gas.co2_when_burned_kg_per_nm3 == pytest.approx(1.964, abs=0.001)


def test_pure_methane_gas_properties():
    """RC-08: pure CH₄ → 16.04/22.414 kg/Nm³, 35.8 MJ/Nm³ (≈ 50 MJ/kg as in the GEMINI example)."""
    gas = gas_mix_properties(1.0, 0.0)
    assert gas.density_kg_per_nm3 == pytest.approx(16.04 / 22.414)
    assert gas.lhv_mj_per_kg == pytest.approx(50.0, abs=0.1)


def test_db29_temperature_at_depth(derived):
    expected = {1500.0: 57.0, 2000.0: 72.0, 3000.0: 102.0, 3200.0: 108.0, 3500.0: 117.0}
    got = {p.depth_m: p.temperature_degc for p in derived.temperature_at_depth}
    assert got == pytest.approx(expected, abs=1e-9)


def test_request_controls_reporting_points():
    request = DesignBasisRequest(breakthrough_spacings_m=[1500.0], report_depths_m=[3100.0])
    derived = load_design_basis(request).data.derived
    assert [p.spacing_m for p in derived.breakthrough] == [1500.0]
    assert derived.temperature_at_depth[0].temperature_degc == pytest.approx(105.0)


def test_brine_heat_is_linear():
    assert brine_heat_mw(600.0, 1030.0, 4100.0, 20.0) == pytest.approx(
        brine_heat_mw(300.0, 1030.0, 4100.0, 40.0)
    )


def test_values_follow_the_config_files(config_copy, edit_yaml):
    """Nothing is re-typed: changing the YAML changes the derived values."""

    def deeper(data):
        data["reservoir"]["mid_depth_m"] = 3000.0

    def cp_fresh_water(data):
        next(a for a in data["assumptions"] if a["id"] == "DB-33")["value"] = 4190.0

    edit_yaml(config_copy / "design_basis.yaml", deeper)
    edit_yaml(config_copy / "assumptions.yaml", cp_fresh_water)
    derived = load_design_basis(config_dir=config_copy).data.derived
    assert derived.reservoir_temperature_degc == pytest.approx(102.0)
    assert derived.heating_supply_above_reservoir_k == pytest.approx(-2.0)
    assert derived.max_heat_per_doublet_mw == pytest.approx(14.0767 * 4190 / 4100, rel=1e-4)


def test_esp_required_when_drawdown_exceeds_static_head(config_copy, edit_yaml):
    """Mild overpressure: static WHP > 0, but drawdown at Q_max still needs an ESP."""

    def overpressure(data):
        data["reservoir"]["pressure_barg"] = 255.0

    edit_yaml(config_copy / "design_basis.yaml", overpressure)
    derived = load_design_basis(config_dir=config_copy).data.derived
    assert derived.static_wellhead_pressure_barg > 0.0
    assert derived.production_wellhead_pressure_at_max_flow_barg < 0.0
    assert derived.esp_required is True


def test_esp_not_required_for_strong_overpressure(config_copy, edit_yaml):
    def overpressure(data):
        data["reservoir"]["pressure_barg"] = 300.0

    edit_yaml(config_copy / "design_basis.yaml", overpressure)
    derived = load_design_basis(config_dir=config_copy).data.derived
    assert derived.production_wellhead_pressure_at_max_flow_barg > 0.0
    assert derived.esp_required is False


def test_summary_stays_short_for_largest_request():
    """Any valid request keeps the summary ≤ 300 chars (t_b reported as a range)."""
    request = DesignBasisRequest(
        breakthrough_spacings_m=[123.456 * (i + 1) ** 3 for i in range(10)],
        report_depths_m=[500.0 * (i + 1) for i in range(10)],
    )
    result = load_design_basis(request)
    assert len(result.summary) <= SUMMARY_MAX_CHARS
    assert len(result.data.derived.breakthrough) == 10


@pytest.mark.parametrize(
    "kwargs",
    [
        {"breakthrough_spacings_m": [1000.0] * 11},
        {"report_depths_m": [1000.0] * 11},
        {"breakthrough_spacings_m": [-1000.0]},
        {"report_depths_m": [0.0]},
        {"breakthrough_spacings_m": []},
    ],
)
def test_request_rejects_invalid_points(kwargs):
    with pytest.raises(ValidationError):
        DesignBasisRequest(**kwargs)


def test_unsupported_injectivity_rule_raises(config_copy, edit_yaml):
    def change(data):
        next(a for a in data["assumptions"] if a["id"] == "SUB-II")["value"] = "half_of_pi"

    edit_yaml(config_copy / "assumptions.yaml", change)
    with pytest.raises(ValueError, match="SUB-II"):
        load_design_basis(config_dir=config_copy)


def test_json_round_trip(result):
    restored = ToolResult[DesignBasisReport].model_validate_json(result.model_dump_json())
    assert restored == result
