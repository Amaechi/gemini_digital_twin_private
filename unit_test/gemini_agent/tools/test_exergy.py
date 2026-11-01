"""Tests for TL-14 exergy (RC-11 validation tests; DB-34d dead state)."""

from __future__ import annotations

import math

import pytest

from gemini_agent.schemas.config import load_config
from gemini_agent.tools import exergy as ex
from gemini_agent.tools.climate import build_climate_profile
from unit_test.gemini_agent.tools.annual_factory import YEAR, make_annual

K = 273.15


def test_rc11_liquid_exergy_example() -> None:
    """RC-11: c 4.1 kJ/kgK, T 360.15 K, T0 283.15 K ⇒ e ≈ 36.4 kJ/kg (±0.5)."""
    e = ex.liquid_specific_exergy_j_per_kg(360.15, 283.15, 4100.0)
    assert e / 1000 == pytest.approx(36.4, abs=0.5)


def test_rc11_limits() -> None:
    """RC-11: e(T0) = 0; 0 ≤ E_heat < Q; E_Q(T → T0) → 0."""
    assert ex.liquid_specific_exergy_j_per_kg(283.15, 283.15, 4100.0) == 0.0
    assert ex.liquid_specific_exergy_j_per_kg(275.0, 283.15, 4100.0) > 0.0  # below T0 too
    q = 10.0 * 4100.0 * 45.0
    e_heat = ex.stream_heat_exergy_w(10.0, 4100.0, 373.15, 328.15, 283.15)
    assert 0.0 <= e_heat < q
    assert ex.heat_exergy_w(1e6, 283.15 + 1e-6, 283.15) == pytest.approx(0.0, abs=1e-2)
    with pytest.raises(ValueError):
        ex.heat_exergy_w(1.0, -5.0, 283.15)


def test_delivered_factor_equals_stream_form() -> None:
    """E_heat/Q = 1 − T0 ln(Ts/Tr)/(Ts − Tr) equals the stream form with ṁc = Q/ΔT."""
    t_s, t_r, t0 = 373.15, 328.15, 283.15
    m_c = 1.0e6 / (t_s - t_r)
    stream = ex.stream_heat_exergy_w(m_c, 1.0, t_s, t_r, t0)
    assert ex.delivered_heat_exergy_factor(t_s, t_r, t0) * 1.0e6 == pytest.approx(stream)
    assert ex.delivered_heat_exergy_factor(t_s, t_r, t0) == pytest.approx(0.1912, abs=1e-3)


def test_cold_exergy_zero_above_dead_state() -> None:
    """20/26 °C cooling: zero when T_lm ≥ T0, Q (T0/T_lm − 1) when T0 is warmer."""
    t_s, t_r = 20 + K, 26 + K
    assert ex.cold_exergy_w(1e6, t_s, t_r, 15 + K) == 0.0
    t_lm = ex.log_mean_temperature_k(t_s, t_r)
    assert t_s < t_lm < t_r
    assert ex.cold_exergy_w(1e6, t_s, t_r, 30 + K) == pytest.approx(1e6 * ((30 + K) / t_lm - 1))
    assert ex.log_mean_temperature_k(300.0, 300.0) == 300.0


@pytest.fixture(scope="module")
def climate():
    """Fixture: climate."""
    profile, _ = build_climate_profile(load_config("climate_nl"), YEAR)
    return profile


def test_exergy_kpi_uses_daily_dry_bulb(climate) -> None:
    """DB-34d: T0 = daily dry-bulb of tools/climate.py, per day_index."""
    annual = make_annual(
        days=365,
        heat_by_source_mw={"hex": 10.0},
        cool_by_source_mw={"chiller": 2.0},
        electricity_by_consumer_mw={"esp": 0.5},
        doublet_flow_m3h=[300.0],
        injection_flow_m3h=[300.0],
        production_temperature_degc=[85.0],
        injection_temperature_degc=[45.0],
    )
    result = ex.exergy_kpi(annual, climate=climate)
    report = result.data
    heat_exergy = sum(
        240.0 * ex.delivered_heat_exergy_factor(100 + K, 55 + K, t0 + K)
        for t0 in climate.dry_bulb_degc
    )
    assert report.heat_exergy_mwh == pytest.approx(heat_exergy)
    m_dot = 300.0 * 1030.0 / 3600.0
    brine = sum(
        m_dot * ex.liquid_specific_exergy_j_per_kg(85 + K, t0 + K, 4100.0) * 86400 / 3.6e9
        for t0 in climate.dry_bulb_degc
    )
    assert report.brine_exergy_mwh == pytest.approx(brine)
    assert report.cold_exergy_days == sum(
        1 for t0 in climate.dry_bulb_degc if t0 + K > ex.log_mean_temperature_k(20 + K, 26 + K)
    )
    spent = brine + 365 * 12.0
    assert report.functional_efficiency_frac == pytest.approx(
        (report.heat_exergy_mwh + report.cold_exergy_mwh) / spent
    )
    assert report.utilisation_frac == pytest.approx(
        (report.heat_exergy_mwh + report.cold_exergy_mwh - 365 * 12.0) / brine
    )
    assert 0 < report.functional_efficiency_frac < 1
    assert report.t0_mean_degc == pytest.approx(climate.annual_mean_dry_bulb_degc)
    kpis = report.kpis
    assert kpis.exergy_efficiency_frac == report.functional_efficiency_frac
    assert kpis.exergy_utilisation_frac == report.utilisation_frac
    assert "DB-34d" in kpis.exergy_dead_state and "daily dry-bulb" in kpis.exergy_dead_state
    assert len(result.summary) <= 300


def test_c0_has_no_utilisation_but_a_functional_efficiency(climate) -> None:
    """Check c0 has no utilisation but a functional efficiency."""
    annual = make_annual(
        "C0", days=3, heat_by_source_mw={"boiler": 10.0}, gas_fuel_by_consumer_mw={"boiler": 11.0}
    )
    report = ex.exergy_kpi(annual, climate=climate).data
    assert report.utilisation_frac is None
    fuel = 3 * 24 * 11.0 * 1.04
    assert report.fuel_exergy_mwh == pytest.approx(fuel)
    assert report.functional_efficiency_frac == pytest.approx(report.heat_exergy_mwh / fuel)


def test_network_temperatures_from_day_results(climate) -> None:
    """Check network temperatures from day results."""
    annual = make_annual(
        days=1, heat_by_source_mw={"hex": 1.0}, heating_supply_degc=90.0, heating_return_degc=50.0
    )
    report = ex.exergy_kpi(annual, climate=climate).data
    t0 = climate.dry_bulb_degc[0] + K
    assert report.heat_exergy_mwh == pytest.approx(
        24.0 * ex.delivered_heat_exergy_factor(90 + K, 50 + K, t0)
    )


def test_climate_year_must_match(climate) -> None:
    """Check climate year must match."""
    other, _ = build_climate_profile(load_config("climate_nl"), YEAR + 1)
    with pytest.raises(ValueError, match="year"):
        ex.exergy_kpi(make_annual(days=1), climate=other)
    report = ex.exergy_kpi(make_annual(days=1)).data  # default climate from config
    assert math.isclose(report.t0_mean_degc, climate.dry_bulb_degc[0])


def test_exergy_merges_into_given_kpis_and_flags_ambient_mismatch(climate) -> None:
    """Exergy fields join the economics KPIs; a simulated ambient ≠ T0 is warned about."""
    from gemini_agent.schemas.results import KPISet

    base = KPISet(candidate_id="C2-test", currency="USD", lcoh_per_mwh=50.0)
    annual = make_annual(
        days=2,
        heat_by_source_mw={"hex": 1.0},
        electricity_by_consumer_mw={"esp": 0.3},
        ambient_dry_bulb_degc=30.0,
    )
    result = ex.exergy_kpi(annual, climate=climate, base_kpis=base)
    kpis = result.data.kpis
    assert kpis.lcoh_per_mwh == 50.0 and kpis.currency == "USD"
    assert kpis.exergy_efficiency_frac is not None
    assert result.data.ambient_mismatch_days == 2
    assert any("ambient" in w for w in result.warnings)
    with pytest.raises(ValueError):
        ex.exergy_kpi(annual, climate=climate, base_kpis=KPISet(candidate_id="other"))
