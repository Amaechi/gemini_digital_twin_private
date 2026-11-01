"""Tests for TL-02 ``profile_demand`` (spec §3.2, DB-18/19, WP-04 acceptance)."""

import datetime as dt
import shutil

import pandas as pd
import pytest
from pydantic import ValidationError

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, ToolResult
from gemini_agent.schemas.config import load_config
from gemini_agent.tools.demand import (
    DemandProfile,
    DemandRequest,
    lowest_window,
    profile_demand,
    read_demand_csv,
)

# Spec §3.2 monthly table (acceptance oracle, ±0.1 MW).
SPEC_HEAT_MONTHLY_MW = [26.1, 22.6, 17.2, 11.7, 5.5, 3.8, 3.5, 3.0, 6.0, 12.0, 18.2, 24.0]
SPEC_COOL_MONTHLY_MW = [0.0, 0.0, 0.0, 0.8, 3.5, 6.6, 8.4, 7.0, 4.6, 1.2, 0.1, 0.0]


@pytest.fixture(scope="module")
def result():
    return profile_demand()


@pytest.fixture(scope="module")
def profile(result):
    return result.data


def test_returns_tool_result_with_short_summary(result):
    assert isinstance(result, ToolResult)
    assert isinstance(result.data, DemandProfile)
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    assert "9 Jul–12 Aug" in result.summary
    assert "6 May–9 Jun" in result.summary
    assert result.warnings == []
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)
    assert set(result.assumption_ids) == {"DB-30", "DEM-COP-HD", "DB-33"}


def test_defaults_come_from_the_assumptions_register(profile):
    register = load_config("assumptions")
    assert profile.window_days == register.get("DB-30").value["planned_outage_days_per_doublet"]
    assert profile.heat_driven_cooling_cop == register.get("DEM-COP-HD").value
    assert profile.window_days == 35
    assert profile.heat_driven_cooling_cop == 0.75


def test_series_shape(profile):
    assert profile.year == 2027
    assert len(profile.dates) == 365
    assert profile.dates[0] == dt.date(2027, 1, 1)
    assert profile.dates[-1] == dt.date(2027, 12, 31)
    for stats in (profile.heating, profile.cooling, profile.combined_heat_driven):
        assert len(stats.daily_mw) == 365
        assert stats.load_duration_mw == sorted(stats.daily_mw, reverse=True)
        assert stats.load_duration_mw[0] == stats.peak_mw
        assert sum(stats.monthly_energy_mwh) == pytest.approx(stats.annual_mwh)


def test_peaks_and_totals(profile):
    """DB-18/19: peaks 30.0/10.0 MW on 28 Jan/19 Jul; totals 111.67/23.74 GWh; 219 cooling days."""
    heat, cool = profile.heating, profile.cooling
    assert heat.peak_mw == pytest.approx(30.0, abs=1e-6)
    assert heat.peak_date == dt.date(2027, 1, 28)
    assert cool.peak_mw == pytest.approx(10.0, abs=1e-6)
    assert cool.peak_date == dt.date(2027, 7, 19)
    assert heat.annual_gwh == pytest.approx(111.67, abs=0.005)
    assert cool.annual_gwh == pytest.approx(23.74, abs=0.005)
    assert heat.annual_mwh == pytest.approx(heat.annual_gwh * 1000.0)
    assert heat.nonzero_days == 365
    assert cool.nonzero_days == 219


def test_monthly_table(profile):
    """Spec §3.2 monthly averages reproduced ±0.1 MW."""
    assert profile.heating.monthly_mean_mw == pytest.approx(SPEC_HEAT_MONTHLY_MW, abs=0.1)
    assert profile.cooling.monthly_mean_mw == pytest.approx(SPEC_COOL_MONTHLY_MW, abs=0.1)


def test_load_factor(profile):
    """Spec §3.2: heating load factor 42 %."""
    assert profile.heating.load_factor_frac == pytest.approx(0.42, abs=0.005)


def test_lowest_35_day_windows(profile):
    """Spec §3.2: heat-only 9 Jul–12 Aug (≤ 3.5 MW); heat + cooling/0.75 → 6 May–9 Jun (≤ 12.2)."""
    heat_window = profile.heating.lowest_window
    assert (heat_window.start_date, heat_window.end_date) == (
        dt.date(2027, 7, 9),
        dt.date(2027, 8, 12),
    )
    assert heat_window.length_days == 35
    assert round(heat_window.max_mw, 1) <= 3.5

    combined = profile.combined_heat_driven
    window = combined.lowest_window
    assert (window.start_date, window.end_date) == (dt.date(2027, 5, 6), dt.date(2027, 6, 9))
    assert round(window.max_mw, 1) <= 12.2
    assert window.max_mw == pytest.approx(12.2, abs=0.05)


def test_combined_profile_energy(profile):
    """Spec §3.2: geothermal-side heat rises from 111.7 to ≈ 143 GWh/yr with cooling/0.75."""
    combined = profile.combined_heat_driven
    expected = profile.heating.annual_gwh + profile.cooling.annual_gwh / 0.75
    assert combined.annual_gwh == pytest.approx(expected)
    assert combined.annual_gwh == pytest.approx(143.0, abs=0.5)
    assert combined.daily_mw == pytest.approx(
        [h + c / 0.75 for h, c in zip(profile.heating.daily_mw, profile.cooling.daily_mw)]
    )


def test_days_above_spec_thresholds():
    """Spec §3.2: demand > 28.2 MW on only 4 days; > 15.7 MW on 145 days."""
    result = profile_demand(DemandRequest(thresholds_mw=[28.2, 15.7]))
    counts = {t.threshold_mw: t.days_above for t in result.data.heating.days_above}
    assert counts == {28.2: 4, 15.7: 145}
    assert "DB-33" not in result.assumption_ids


def test_default_thresholds_are_multiples_of_max_heat_per_doublet(profile):
    """Default thresholds k × DB-23 (≈ 14.08, 28.15, 42.23 MW)."""
    thresholds = [t.threshold_mw for t in profile.heating.days_above]
    assert thresholds == pytest.approx([14.0767, 28.1533, 42.23], abs=1e-3)
    assert [t.days_above for t in profile.heating.days_above] == [161, 4, 0]


def test_request_overrides_defaults():
    result = profile_demand(DemandRequest(window_days=7, heat_driven_cooling_cop=0.6))
    assert result.data.window_days == 7
    assert result.data.heat_driven_cooling_cop == 0.6
    assert result.data.heating.lowest_window.length_days == 7
    assert result.assumption_ids == ["DB-33"]


def test_request_validation():
    with pytest.raises(ValidationError):
        DemandRequest(thresholds_mw=[-1.0])
    with pytest.raises(ValidationError):
        DemandRequest(thresholds_mw=[1.0] * 11)
    with pytest.raises(ValidationError):
        DemandRequest(window_days=0)
    with pytest.raises(ValidationError):
        DemandRequest(heat_driven_cooling_cop=0.0)


def test_csvs_resolve_against_data_root(tmp_path, result):
    """``data_root`` relocates the CSVs; a root without them fails loudly."""
    with pytest.raises(FileNotFoundError):
        profile_demand(data_root=tmp_path)
    basis = load_config("design_basis")
    for side in (basis.heating, basis.cooling):
        target = tmp_path / side.demand_csv
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(side.csv_path(), target)
    assert profile_demand(data_root=tmp_path) == result


def test_lowest_window_synthetic():
    dates = [dt.date(2027, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    series = pd.Series([5, 4, 1, 1, 1, 4, 1, 1, 1, 5], index=dates, dtype=float)
    window = lowest_window(series, 3)
    assert (window.start_date, window.end_date) == (dates[2], dates[4])  # earliest of two ties
    assert window.mean_mw == pytest.approx(1.0)
    assert window.max_mw == pytest.approx(1.0)
    assert window.energy_mwh == pytest.approx(72.0)
    with pytest.raises(ValueError):
        lowest_window(series, 11)


def test_warns_when_csv_disagrees_with_design_basis(config_copy, edit_yaml):
    def change(data):
        data["heating"]["peak_mw"] = 31.0
        data["cooling"]["annual_gwh"] = 20.0
        data["cooling"]["nonzero_days"] = 200

    edit_yaml(config_copy / "design_basis.yaml", change)
    warnings = profile_demand(config_dir=config_copy).warnings
    assert len(warnings) == 3
    assert any("heating" in w and "peak" in w for w in warnings)


def _write_csv(path, dates, values, value_column="heating_demand_MWth"):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": dates, value_column: values}).to_csv(path, index=False)


@pytest.mark.parametrize(
    "case",
    ["missing_column", "short_year", "wrong_order", "negative", "non_numeric"],
)
def test_read_demand_csv_rejects_bad_files(tmp_path, case):
    side = load_config("design_basis").heating
    dates = [d.isoformat() for d in pd.date_range("2027-01-01", "2027-12-31").date]
    values: list = [1.0] * 365
    column = side.csv_value_column
    if case == "missing_column":
        column = "other"
    elif case == "short_year":
        dates, values = dates[:-1], values[:-1]
    elif case == "wrong_order":
        dates[0], dates[1] = dates[1], dates[0]
    elif case == "negative":
        values[10] = -0.1
    elif case == "non_numeric":
        values[10] = "n/a"
    _write_csv(tmp_path / side.demand_csv, dates, values, column)
    with pytest.raises(ValueError):
        read_demand_csv(side, 2027, root=tmp_path)


def test_json_round_trip(result):
    restored = ToolResult[DemandProfile].model_validate_json(result.model_dump_json())
    assert restored == result
