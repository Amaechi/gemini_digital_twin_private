"""Tests for TL-02b ``climate`` / ``profile_climate`` (spec §2.3 DB-34, RC-07 validation tests)."""

import datetime as dt

import numpy as np
import pytest

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.climate import (
    ClimateProfile,
    interpolate_monthly,
    profile_climate,
    stull_wet_bulb_degc,
)


@pytest.fixture(scope="module")
def result():
    return profile_climate()


@pytest.fixture(scope="module")
def profile(result):
    return result.data


@pytest.fixture(scope="module")
def climate():
    return load_config("climate_nl")


def test_returns_tool_result_with_short_summary(result):
    assert isinstance(result, ToolResult)
    assert isinstance(result.data, ClimateProfile)
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    assert result.warnings == []
    assert "RC-07" in result.sources
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)


def test_stull_case():
    """RC-07 validation: T = 20 °C, RH = 50 % ⇒ T_wb = 13.7 °C (±0.1)."""
    assert float(stull_wet_bulb_degc(20.0, 50.0)) == pytest.approx(13.7, abs=0.1)


def test_stull_is_vectorised_and_bounded():
    t_wb = stull_wet_bulb_degc([0.0, 10.0, 30.0], [50.0, 80.0, 99.0])
    assert t_wb.shape == (3,)
    assert np.all(t_wb <= np.array([0.0, 10.0, 30.0]) + 0.05)
    with pytest.raises(ValueError):
        stull_wet_bulb_degc(20.0, 2.0)
    with pytest.raises(ValueError):
        stull_wet_bulb_degc(55.0, 50.0)


def test_monthly_wet_bulb_table_reproduced(profile, climate):
    """RC-07 validation: monthly T_wb table reproduced from T_db and RH (±0.1 °C)."""
    assert profile.monthly_wet_bulb_stull_degc == pytest.approx(
        climate.monthly.wet_bulb_degc, abs=0.1
    )
    assert profile.monthly_wet_bulb_max_deviation_k <= 0.1


def test_daily_series_365_values_mean(profile):
    """RC-07 validation: interpolated daily series has 365 values, mean ≈ 10.5 °C."""
    assert profile.year == 2027
    assert len(profile.dates) == 365
    for series in (profile.dry_bulb_degc, profile.relative_humidity_pct, profile.wet_bulb_degc):
        assert len(series) == 365
    assert profile.dates[0] == dt.date(2027, 1, 1)
    assert profile.dates[-1] == dt.date(2027, 12, 31)
    assert profile.annual_mean_dry_bulb_degc == pytest.approx(10.5, abs=0.1)


def test_series_hits_normals_on_anchor_days(profile, climate):
    """DB-34a: the daily series equals the monthly normal on day 15 of each month."""
    for month in range(1, 13):
        i = profile.dates.index(dt.date(2027, month, 15))
        assert profile.dry_bulb_degc[i] == pytest.approx(climate.monthly.dry_bulb_degc[month - 1])
        assert profile.relative_humidity_pct[i] == pytest.approx(
            climate.monthly.relative_humidity_pct[month - 1]
        )


def test_series_wraps_smoothly_over_new_year(profile, climate):
    """Dec → Jan wrap: 1 Jan lies on the line from 15 Dec (4.2 °C) to 15 Jan (3.6 °C)."""
    dec, jan = climate.monthly.dry_bulb_degc[11], climate.monthly.dry_bulb_degc[0]
    assert profile.dry_bulb_degc[0] == pytest.approx(dec + (jan - dec) * 17 / 31)
    assert abs(profile.dry_bulb_degc[-1] - profile.dry_bulb_degc[0]) < 0.05


def test_wet_bulb_is_stull_of_daily_values_and_below_dry_bulb(profile):
    expected = stull_wet_bulb_degc(profile.dry_bulb_degc, profile.relative_humidity_pct)
    assert profile.wet_bulb_degc == pytest.approx(expected.tolist())
    assert all(wb < db for wb, db in zip(profile.wet_bulb_degc, profile.dry_bulb_degc))


def test_extremes(profile):
    assert profile.warmest_day.date == dt.date(2027, 7, 15)
    assert profile.warmest_day.dry_bulb_degc == pytest.approx(18.2)
    assert profile.coldest_day.date == dt.date(2027, 1, 15)
    assert profile.coldest_day.dry_bulb_degc == pytest.approx(3.6)


def test_design_days(profile):
    """DB-34b: design point 25.0/18.4 °C DB/WB; warm case 30/21 °C."""
    by_name = {d.name: d for d in profile.design_days}
    assert (by_name["summer_design"].dry_bulb_degc, by_name["summer_design"].wet_bulb_degc) == (
        25.0,
        18.4,
    )
    assert (by_name["warm_case"].dry_bulb_degc, by_name["warm_case"].wet_bulb_degc) == (30.0, 21.0)
    assert all(d.assumption_id == "DB-34b" for d in profile.design_days)
    assert profile.shallow_ground_degc == 12.5
    assert profile.exergy_dead_state == "daily_dry_bulb"


def test_relative_humidity_matches_register(climate):
    """DB-34-RH in the register and the RH row in climate_nl.yaml must agree."""
    register = load_config("assumptions")
    assert register.get("DB-34-RH").value == climate.monthly.relative_humidity_pct


def test_interpolation_without_wrap_and_leap_year():
    monthly = [float(m) for m in range(1, 13)]
    days, values = interpolate_monthly(monthly, 2028, 15, wrap_year=False)
    assert len(days) == 366
    assert values[0] == pytest.approx(1.0)  # flat before the first anchor
    assert values[-1] == pytest.approx(12.0)  # flat after the last anchor
    with pytest.raises(ValueError):
        interpolate_monthly(monthly[:11], 2027, 15, wrap_year=True)


def test_warns_when_wet_bulb_table_is_inconsistent(config_copy, edit_yaml):
    def change(data):
        data["monthly"]["wet_bulb_degc"][0] = 3.0  # Stull gives 2.36 °C for January

    edit_yaml(config_copy / "climate_nl.yaml", change)
    result = profile_climate(config_dir=config_copy)
    assert len(result.warnings) == 1
    assert result.data.monthly_wet_bulb_max_deviation_k > 0.1


def test_warns_when_annual_mean_disagrees(config_copy, edit_yaml):
    def change(data):
        data["annual_mean_degc"] = 11.0

    edit_yaml(config_copy / "climate_nl.yaml", change)
    warnings = profile_climate(config_dir=config_copy).warnings
    assert len(warnings) == 1
    assert "annual mean" in warnings[0]


def test_year_follows_design_basis(config_copy, edit_yaml):
    def change(data):
        data["year"] = 2028

    edit_yaml(config_copy / "design_basis.yaml", change)
    profile = profile_climate(config_dir=config_copy).data
    assert profile.year == 2028
    assert len(profile.dates) == 366


def test_json_round_trip(result):
    restored = ToolResult[ClimateProfile].model_validate_json(result.model_dump_json())
    assert restored == result
