"""Tests for TL-23 pipe_heat_loss (RC-09 validation tests)."""

from __future__ import annotations

import math

import pytest

from gemini_agent.tools import network as net
from gemini_agent.tools.network import PipeGeometry, PipeHeatLossRequest

GPM_TO_M3S = 3.785411784e-3 / 60.0


def test_rc09_example_relation_and_documented_discrepancy() -> None:
    """RC-09 "0.55 K/km" example: ΔT relation check plus a documented card discrepancy.

    This is NOT a reproduction (the card states only the result): the implied loss q' ≈ 43 W/m
    (water 980 kg/m³, 4.19 kJ/kgK at 150 °F) is put back through ΔT = q'L/(ṁc_p). Discrepancy
    for WP-15: with the card's own PUR k = 0.026 W/mK and 25–50 mm thickness the insulation alone
    gives R_ins ≥ 1.59 m·K/W, while 0.55 K/km over 53 K (150 °F − 12.5 °C) needs
    R_total ≈ 1.24 m·K/W (1.30 at a 10 °C ground) — the Guidebook case uses other conditions.
    """
    m_dot = 300.0 * GPM_TO_M3S * 980.0
    implied_q = 0.55e-3 * m_dot * 4190.0
    assert implied_q == pytest.approx(42.7, abs=0.5)
    assert net.pipe_temperature_drop_k(implied_q, 1000.0, m_dot, 4190.0) == pytest.approx(
        0.55, rel=0.10
    )
    r_ins_25mm = net.insulation_resistance_m_k_per_w(0.0842, 0.025, 0.026)
    assert r_ins_25mm > (65.6 - 12.5) / implied_q


def test_rc09_p213_example_bracketed_by_plausible_inputs() -> None:
    """RC-09 p.213–214: 8″ pre-insulated, 66 °C water, −7 °C surface ⇒ ≈ 32 W/m (range check).

    Insulation thickness, cover and soil conductivity are not on the card: the test inputs
    (PUR 25–50 mm, centre depth 1.0 m, k_soil 1–2 W/mK) are assumptions of this test only.
    """
    r_pipe = 0.2191 / 2  # 8-inch nominal pipe OD 219.1 mm

    def q(thickness: float, k_soil: float) -> float:
        return net.buried_pipe_heat_loss_w_per_m(
            66.0,
            -7.0,
            pipe_outer_radius_m=r_pipe,
            insulation_thickness_m=thickness,
            burial_depth_m=1.0,
            k_soil_w_per_m_k=k_soil,
        )

    assert q(0.05, 1.0) < 32.0 < q(0.025, 2.0)


def test_layered_resistance_hand_calculation() -> None:
    """Check layered resistance hand calculation."""
    r_pipe, thickness, depth, k_soil = 0.0842, 0.05, 1.0, 1.5
    r_ins = math.log((r_pipe + thickness) / r_pipe) / (2 * math.pi * 0.026)
    r_soil = math.acosh(depth / (r_pipe + thickness)) / (2 * math.pi * k_soil)
    q = net.buried_pipe_heat_loss_w_per_m(
        100.0,
        12.5,
        pipe_outer_radius_m=r_pipe,
        insulation_thickness_m=thickness,
        burial_depth_m=depth,
        k_soil_w_per_m_k=k_soil,
    )
    assert q == pytest.approx(87.5 / (r_ins + r_soil))
    thicker = net.buried_pipe_heat_loss_w_per_m(
        100.0,
        12.5,
        pipe_outer_radius_m=r_pipe,
        insulation_thickness_m=0.1,
        burial_depth_m=depth,
        k_soil_w_per_m_k=k_soil,
    )
    assert thicker < q
    with pytest.raises(ValueError):
        net.soil_resistance_m_k_per_w(0.2, 0.1, 1.0)


def test_pipe_heat_loss_layered_reports_min_flow_drop() -> None:
    """Check pipe heat loss layered reports min flow drop."""
    request = PipeHeatLossRequest(
        fluid_temperature_degc=100.0,
        annual_heat_demand_mwh=111_670.0,
        design_mass_flow_kg_s=160.0,
        min_mass_flow_kg_s=20.0,
        length_m=5000.0,
        geometry=PipeGeometry(
            pipe_outer_radius_m=0.16,
            insulation_thickness_m=0.08,
            burial_depth_m=1.2,
            k_soil_w_per_m_k=1.5,
        ),
    )
    result = net.pipe_heat_loss(request)
    report = result.data
    assert report.method == "layered_resistance"
    assert report.ground_temperature_degc == 10.5  # DB-34c annual mean air (surface)
    assert report.temperature_drop_min_flow_k == pytest.approx(
        8.0 * report.temperature_drop_design_k
    )
    assert report.annual_heat_loss_mwh == pytest.approx(
        report.heat_loss_w_per_m * 5000.0 * 8760 / 1e6
    )
    assert report.missing_inputs == []
    assert len(result.summary) <= 300


def test_pipe_heat_loss_default_fraction_when_network_missing() -> None:
    """Check pipe heat loss default fraction when network missing."""
    result = net.pipe_heat_loss(
        PipeHeatLossRequest(fluid_temperature_degc=100.0, annual_heat_demand_mwh=111_670.0)
    )
    report = result.data
    assert report.method == "default_fraction"
    assert report.loss_fraction_of_demand == 0.10  # NET-LOSS-FRAC
    assert report.annual_heat_loss_mwh == pytest.approx(11_167.0)
    assert report.length_m == 5000.0  # NET-TRUNK-M
    assert len(report.missing_inputs) == 2
    assert "NET-LOSS-FRAC" in result.assumption_ids
    assert result.warnings and result.warnings[0].startswith("MISSING INPUT")
    with pytest.raises(ValueError):
        PipeHeatLossRequest(
            fluid_temperature_degc=100.0,
            annual_heat_demand_mwh=1.0,
            design_mass_flow_kg_s=1.0,
            min_mass_flow_kg_s=2.0,
        )
