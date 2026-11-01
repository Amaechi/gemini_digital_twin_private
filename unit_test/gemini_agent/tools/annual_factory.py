"""Build synthetic ``AnnualResult`` fixtures with constant daily values (WP-08 tests)."""

from __future__ import annotations

import datetime as dt

from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet

YEAR = 2027


def make_day(day_index: int, **overrides: object) -> DayResult:
    """One day with no flows; ``overrides`` set any ``DayResult`` field."""
    data: dict[str, object] = {
        "day_index": day_index,
        "date": dt.date(YEAR, 1, 1) + dt.timedelta(days=day_index),
        "ambient_dry_bulb_degc": 10.0,
        "heating_demand_mw": 0.0,
        "cooling_demand_mw": 0.0,
    }
    data.update(overrides)
    return DayResult.model_validate(data)


def make_annual(
    candidate_id: str = "C2-test",
    *,
    days: int = 365,
    kpis: KPISet | None = None,
    **day_fields: object,
) -> AnnualResult:
    """``days`` identical days (day_index 0…days−1) with ``day_fields`` on each."""
    return AnnualResult(
        candidate_id=candidate_id,
        year=YEAR,
        interpretation="A",
        days=[make_day(i, **day_fields) for i in range(days)],
        kpis=kpis,
    )
