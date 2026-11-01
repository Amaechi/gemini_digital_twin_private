"""Unit tests for gemini_agent.schemas.results."""

import datetime as dt

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet


def _day(index, **overrides):
    fields = {
        "day_index": index,
        "date": dt.date(2027, 1, 1) + dt.timedelta(days=index),
        "ambient_dry_bulb_degc": 3.6,
        "heating_demand_mw": 24.0,
        "cooling_demand_mw": 0.0,
        "doublet_available": [True, True],
        "doublet_flow_m3h": [300.0, 250.0],
        "injection_flow_m3h": [300.0, 250.0],
        "production_temperature_degc": [86.0, 86.0],
        "injection_temperature_degc": [46.0, 46.0],
        "heat_by_source_mw": {"geothermal_hex": 15.0, "hthp": 6.0, "gas_boiler": 3.0},
        "electricity_by_consumer_mw": {"esp": 0.5, "hthp": 2.0},
        "co2_by_source_kg": {"gas_boiler": 1000.0, "grid": 500.0},
    }
    fields.update(overrides)
    return DayResult(**fields)


def test_day_result_totals():
    """Day totals are the sums of the per-source dictionaries."""
    day = _day(0)
    assert day.heat_supplied_mw == pytest.approx(24.0)
    assert day.cool_supplied_mw == 0.0
    assert day.electricity_consumed_mw == pytest.approx(2.5)
    assert day.co2_kg == pytest.approx(1500.0)


def test_day_result_round_trip_is_strict():
    """Dump → validate works although the model forbids extra fields."""
    day = _day(3, unmet_heating_mwh=1.5)
    assert DayResult.model_validate(day.model_dump()) == day
    assert DayResult.model_validate_json(day.model_dump_json()) == day


def test_day_result_rejects_inconsistent_per_doublet_lists_and_negative_flow():
    """Per-doublet lists must share one length; flows are non-negative."""
    with pytest.raises(ValidationError, match="inconsistent"):
        _day(0, production_temperature_degc=[86.0])
    with pytest.raises(ValidationError):
        _day(0, doublet_flow_m3h=[-1.0, 250.0])
    with pytest.raises(ValidationError):
        _day(0, unmet_cooling_mwh=-0.1)


def test_day_result_without_doublets():
    """C0-style days have empty per-doublet lists."""
    day = _day(
        0,
        doublet_available=[],
        doublet_flow_m3h=[],
        injection_flow_m3h=[],
        production_temperature_degc=[],
        injection_temperature_degc=[],
    )
    assert day.doublet_flow_m3h == []


def test_annual_result_aggregates_day_average_mw_to_mwh():
    """Annual energy = Σ day-average MW × 24 h; unmet days are counted."""
    days = [_day(0), _day(1, unmet_heating_mwh=12.0), _day(2, unmet_cooling_mwh=0.5)]
    annual = AnnualResult(candidate_id="C2-2x300", year=2027, interpretation="A", days=days)
    assert annual.heat_delivered_mwh == pytest.approx(3 * 24.0 * 24.0)
    assert annual.electricity_consumed_mwh == pytest.approx(3 * 2.5 * 24.0)
    assert annual.unmet_heating_mwh == pytest.approx(12.0)
    assert annual.unmet_heating_days == 1
    assert annual.unmet_cooling_days == 1
    assert annual.co2_t == pytest.approx(3 * 1.5)
    assert AnnualResult.model_validate_json(annual.model_dump_json()) == annual


def test_annual_result_rejects_duplicate_or_unsorted_days_and_mismatched_kpis():
    """Days must be unique and increasing; KPIs must belong to the same candidate."""
    with pytest.raises(ValidationError, match="day_index"):
        AnnualResult(candidate_id="X", year=2027, interpretation="A", days=[_day(1), _day(1)])
    with pytest.raises(ValidationError, match="day_index"):
        AnnualResult(candidate_id="X", year=2027, interpretation="B", days=[_day(2), _day(1)])
    with pytest.raises(ValidationError, match="candidate_id"):
        AnnualResult(
            candidate_id="X", year=2027, interpretation="A", days=[], kpis=KPISet(candidate_id="Y")
        )
    with pytest.raises(ValidationError):
        AnnualResult(candidate_id="X", year=2027, interpretation="C", days=[])


def test_kpi_set_defaults_to_not_computed():
    """All KPIs are None/empty until a tool fills them; percentiles hold P10/P50/P90."""
    kpis = KPISet(
        candidate_id="C2", percentiles={"lcoh_per_mwh": {"p10": 40.0, "p50": 48.0, "p90": 60.0}}
    )
    assert kpis.lcoh_per_mwh is None
    assert kpis.percentiles["lcoh_per_mwh"]["p50"] == 48.0
    assert KPISet.model_validate_json(kpis.model_dump_json()) == kpis


def test_nan_and_inf_are_rejected():
    """NaN/inf would serialise to JSON null and break round-trips, so they are rejected."""
    with pytest.raises(ValidationError):
        _day(0, ambient_dry_bulb_degc=float("nan"))
    with pytest.raises(ValidationError):
        _day(0, margins={"brine_cooling_max_k": float("nan")})
    with pytest.raises(ValidationError):
        _day(0, doublet_flow_m3h=[float("inf"), 250.0])
    with pytest.raises(ValidationError):
        KPISet(candidate_id="C2", lcoh_per_mwh=float("inf"))


def test_annual_result_rejects_date_not_matching_day_index():
    """The date must equal 1 Jan of `year` + day_index."""
    with pytest.raises(ValidationError, match="does not match"):
        AnnualResult(
            candidate_id="X",
            year=2027,
            interpretation="A",
            days=[_day(0, date=dt.date(2027, 3, 1))],
        )
    with pytest.raises(ValidationError, match="does not match"):
        AnnualResult(candidate_id="X", year=2026, interpretation="A", days=[_day(0)])
