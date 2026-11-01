"""Tests for TL-07 ``thermal_breakthrough`` and TL-08 ``pressure_interference`` (RC-02 checks)."""

import math

import pytest

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.design_basis import breakthrough_time_yr
from gemini_agent.tools.thermal import (
    INTERFERENCE_LIMIT_BAR,
    InterferenceRequest,
    ThermalBreakthroughRequest,
    arrival_time_ratio,
    broken_through_angle_rad,
    min_spacing_for_breakthrough_m,
    pressure_interference,
    production_temperature_degc,
    thermal_breakthrough,
)

UNIT_BAR_300 = (300.0 / 3600.0) * 0.40e-3 / (2.0 * math.pi * 200.0 * 9.869e-16 * 100.0) / 1e5
"""Q μ / (2π k h) at 300 m³/h by hand (DB-06, DB-08, DB-02)."""


# --------------------------------------------------------------------------- TL-07
def test_rc02_breakthrough_validation():
    """RC-02: t_b(h 100, D 1000, Q 300, ρC_a 2.5e6, ρC_w 4.223e6) = 23.6 yr ±2 %; 1.2 km ≈ 34 yr."""
    near = thermal_breakthrough(ThermalBreakthroughRequest(spacing_m=1000.0)).data
    assert near.rho_c_water_j_per_m3_k == pytest.approx(4.223e6, rel=1e-3)
    assert near.breakthrough_time_yr == pytest.approx(23.6, rel=0.02)
    far = thermal_breakthrough().data  # SUB-SPACING 1200 m
    assert far.spacing_m == 1200.0
    assert far.breakthrough_time_yr == pytest.approx(34.0, rel=0.02)


def test_breakthrough_scales_with_spacing_squared_and_inverse_flow():
    """RC-02 property test: t_b ∝ D² and ∝ 1/Q."""
    base = breakthrough_time_yr(100.0, 1000.0, 300.0, 2.5e6, 4.223e6)
    assert breakthrough_time_yr(100.0, 2000.0, 300.0, 2.5e6, 4.223e6) == pytest.approx(4 * base)
    assert breakthrough_time_yr(100.0, 1000.0, 150.0, 2.5e6, 4.223e6) == pytest.approx(2 * base)


def test_arrival_time_ratios_match_rc02():
    """RC-02 eq 2: t_a(π/2) = 3 t_a(0), t_a(3π/4) = 20.13 t_a(0)."""
    assert arrival_time_ratio(0.0) == 1.0
    assert arrival_time_ratio(math.pi / 2) == pytest.approx(3.0)
    assert arrival_time_ratio(3 * math.pi / 4) == pytest.approx(20.13, abs=0.01)
    # small-angle series joins the closed form smoothly
    assert arrival_time_ratio(0.99e-4) == pytest.approx(arrival_time_ratio(1.01e-4), rel=1e-8)
    angles = [0.1 * i for i in range(31)]
    ratios = [arrival_time_ratio(a) for a in angles]
    assert ratios == sorted(ratios)
    with pytest.raises(ValueError):
        arrival_time_ratio(math.pi)


def test_production_temperature_mixing_rule():
    """T_prod = T_res before t_b; (T_res + T_inj)/2 at 3 t_b (θ = π/2); → T_inj late."""
    t_b, t_res, t_inj = 20.0, 87.0, 47.0
    assert production_temperature_degc(19.9, t_b, t_res, t_inj) == t_res
    assert broken_through_angle_rad(3.0) == pytest.approx(math.pi / 2)
    assert production_temperature_degc(3 * t_b, t_b, t_res, t_inj) == pytest.approx(67.0)
    late = production_temperature_degc(1e6 * t_b, t_b, t_res, t_inj)
    assert t_inj < late < t_inj + 0.5


def test_thermal_report_curve_and_horizon():
    """Yearly curve over the analysis period; decline after breakthrough; band ±6 K (RC-02 eq 3)."""
    result = thermal_breakthrough(ThermalBreakthroughRequest(spacing_m=1000.0))
    data = result.data
    assert data.horizon_yr == 30.0  # cost_assumptions analysis_period_yr
    assert [p.time_yr for p in data.curve] == [float(y) for y in range(31)]
    temps = [p.production_temperature_degc for p in data.curve]
    assert temps == sorted(temps, reverse=True)
    assert temps[0] == pytest.approx(87.0)
    assert 85.0 < data.wellhead_temperature_degc < 86.0  # TL-05 wellbore model at 300 m³/h
    assert data.injection_temperature_degc == pytest.approx(data.wellhead_temperature_degc - 40.0)
    assert data.brine_cooling_k == pytest.approx(40.0)  # DB-12
    assert not data.breakthrough_after_horizon
    assert data.decline_at_horizon_k > 0.0
    assert data.heterogeneity_band_k == 6.0
    assert result.warnings  # breakthrough before the horizon
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)


def test_min_spacing_inverts_breakthrough():
    """D_min gives t_b = horizon; the default 1.2 km already exceeds it at 300 m³/h."""
    data = thermal_breakthrough().data
    assert data.breakthrough_after_horizon
    assert data.decline_at_horizon_k == pytest.approx(0.0)
    d_min = min_spacing_for_breakthrough_m(30.0, 100.0, 300.0, 2.5e6, data.rho_c_water_j_per_m3_k)
    assert data.min_spacing_for_horizon_m == pytest.approx(d_min)
    assert breakthrough_time_yr(100.0, d_min, 300.0, 2.5e6, data.rho_c_water_j_per_m3_k) == (
        pytest.approx(30.0)
    )


def test_thermal_rejects_warm_injection():
    """Injection hotter than the reservoir is not a cooling doublet."""
    with pytest.raises(ValueError):
        thermal_breakthrough(ThermalBreakthroughRequest(injection_temperature_degc=95.0))


def test_thermal_enforces_hard_limits():
    """DB-11: flow above 300 m³/h refused; DB-12: cooling above 40 K refused."""
    with pytest.raises(ValueError, match="DB-11"):
        thermal_breakthrough(ThermalBreakthroughRequest(flow_m3h=301.0))
    with pytest.raises(ValueError, match="DB-12"):
        thermal_breakthrough(ThermalBreakthroughRequest(injection_temperature_degc=5.0))
    with pytest.raises(ValueError, match="DB-12"):
        thermal_breakthrough(
            ThermalBreakthroughRequest(
                wellhead_temperature_degc=86.0, injection_temperature_degc=45.0
            )
        )


def test_thermal_accepts_annual_average_flow():
    """Flows below the DB-11 minimum are allowed (annual-average use); t_b grows as 1/Q."""
    low = thermal_breakthrough(ThermalBreakthroughRequest(flow_m3h=30.0)).data
    full = thermal_breakthrough().data
    assert low.breakthrough_time_yr == pytest.approx(10.0 * full.breakthrough_time_yr)
    assert low.wellhead_temperature_degc < full.wellhead_temperature_degc


# --------------------------------------------------------------------------- TL-08
def test_unit_pressure_by_hand():
    """Q μ / (2π k h) ≈ 2.69 bar at 300 m³/h."""
    data = pressure_interference().data
    assert data.unit_pressure_bar == pytest.approx(UNIT_BAR_300, rel=1e-9)
    assert data.unit_pressure_bar == pytest.approx(2.69, abs=0.01)


def test_two_doublets_hand_calculation():
    """Parallel pair at separation s: ΔP at a producer = unit · ln(s / √(s² + D²)) (drawdown)."""
    s, d = 500.0, 1200.0
    data = pressure_interference(InterferenceRequest(doublets=2, separation_m=s)).data
    producer0 = next(w for w in data.wells if w.doublet == 0 and w.kind == "producer")
    injector0 = next(w for w in data.wells if w.doublet == 0 and w.kind == "injector")
    expected = UNIT_BAR_300 * math.log(s / math.hypot(s, d))
    assert producer0.pressure_change_bar == pytest.approx(expected, rel=1e-9)
    assert injector0.pressure_change_bar == pytest.approx(-expected, rel=1e-9)
    assert not data.within_limit


def test_minimum_separation_meets_limit():
    """The computed minimum separation gives exactly the 1 bar limit (closed form for a pair)."""
    data = pressure_interference(InterferenceRequest(doublets=2)).data
    d = data.spacing_m
    closed_form = d / math.sqrt(math.exp(2.0 * INTERFERENCE_LIMIT_BAR / UNIT_BAR_300) - 1.0)
    assert data.min_separation_m == pytest.approx(closed_form, rel=1e-6)
    assert data.separation_m == data.min_separation_m
    assert data.max_abs_pressure_change_bar == pytest.approx(INTERFERENCE_LIMIT_BAR, rel=1e-6)
    assert data.within_limit


def test_three_doublets_need_more_separation():
    """The middle doublet feels both neighbours (superposition)."""
    two = pressure_interference(InterferenceRequest(doublets=2)).data
    three = pressure_interference(InterferenceRequest(doublets=3)).data
    assert three.min_separation_m > two.min_separation_m
    middle = [w for w in three.wells if w.doublet == 1]
    outer = [w for w in three.wells if w.doublet == 0]
    assert abs(middle[0].pressure_change_bar) > abs(outer[0].pressure_change_bar)
    alternating = pressure_interference(InterferenceRequest(doublets=3, layout="alternating"))
    assert alternating.data.wells[2].y_m == alternating.data.spacing_m  # flipped producer


def test_interference_refuses_flow_above_limit():
    """DB-11: 600 m³/h per doublet is refused, not reported as within the 1 bar limit."""
    with pytest.raises(ValueError, match="DB-11"):
        pressure_interference(InterferenceRequest(flow_m3h=600.0))


def test_single_doublet_and_limits():
    """One doublet has no neighbours; DB-10 caps the count at 3."""
    one = pressure_interference(InterferenceRequest(doublets=1))
    assert one.data.max_abs_pressure_change_bar == 0.0 and one.data.within_limit
    assert one.data.min_separation_m is None
    assert len(one.summary) <= SUMMARY_MAX_CHARS
    with pytest.raises(ValueError, match="DB-10"):
        pressure_interference(InterferenceRequest(doublets=4))


def test_interference_tool_envelope():
    """Summary ≤ 300 chars, warning when over the limit, register ids exist."""
    result = pressure_interference(InterferenceRequest(doublets=3, separation_m=800.0))
    assert not result.data.within_limit
    assert result.warnings
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)
