"""TL-02b ``climate`` (``profile_climate``): daily dry-bulb, RH and Stull wet-bulb for the design
year, plus the design days (spec §2.3 DB-34, RC-07).

Monthly normals, the interpolation rule and the design points come from ``config/climate_nl.yaml``;
the year comes from ``config/design_basis.yaml``. Nothing is re-typed here.

Source: RC-07 (KNMI De Bilt 1991–2020 normals [W], RH [A], Stull 2011 wet-bulb [D],
NOAA design point [W]).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import Field

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import MONTHS, ClimateConfig, load_config

TOOL_ID = "TL-02b"
ASSUMPTION_IDS = ["DB-34a", "DB-34-RH", "DB-34b", "DB-34c", "DB-34d"]

STULL_RH_RANGE_PCT = (5.0, 99.0)
"""Validity of the Stull (2011) fit. Source: RC-07."""
STULL_T_RANGE_DEGC = (-20.0, 50.0)
"""Validity of the Stull (2011) fit. Source: RC-07."""
WET_BULB_TABLE_TOL_K = 0.1
"""RC-07 validation: the monthly T_wb table is reproduced from T_db and RH to ±0.1 °C."""
ANNUAL_MEAN_TOL_K = 0.1
"""RC-07 validation: the daily series mean ≈ the configured annual mean (DB-34c), quoted to 0.1."""


# --------------------------------------------------------------------------- physics
def stull_wet_bulb_degc(t_degc: ArrayLike, rh_pct: ArrayLike) -> NDArray[np.float64]:
    """Wet-bulb temperature from dry-bulb and relative humidity (Stull 2011).

    ``T_wb = T·atan(0.151977·(RH + 8.313659)^0.5) + atan(T + RH) − atan(RH − 1.676331)
    + 0.00391838·RH^1.5·atan(0.023101·RH) − 4.686035``. Source: RC-07.

    Raises ``ValueError`` outside the fit's validity (RH 5–99 %, T −20…50 °C).
    """
    t = np.asarray(t_degc, dtype=float)
    rh = np.asarray(rh_pct, dtype=float)
    if np.any((rh < STULL_RH_RANGE_PCT[0]) | (rh > STULL_RH_RANGE_PCT[1])):
        raise ValueError(f"Stull wet-bulb is valid for RH {STULL_RH_RANGE_PCT} %")
    if np.any((t < STULL_T_RANGE_DEGC[0]) | (t > STULL_T_RANGE_DEGC[1])):
        raise ValueError(f"Stull wet-bulb is valid for T {STULL_T_RANGE_DEGC} °C")
    return (
        t * np.arctan(0.151977 * np.sqrt(rh + 8.313659))
        + np.arctan(t + rh)
        - np.arctan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh)
        - 4.686035
    )


def interpolate_monthly(
    monthly: list[float], year: int, anchor_day_of_month: int, wrap_year: bool
) -> tuple[list[dt.date], NDArray[np.float64]]:
    """Daily series from 12 monthly values placed at day ``anchor_day_of_month`` (DB-34a).

    Piecewise-linear in calendar days. With ``wrap_year`` the December value is also placed one
    year earlier and the January value one year later, so the series is smooth across New Year;
    without it the series is flat before the first and after the last anchor.
    """
    if len(monthly) != MONTHS:
        raise ValueError("need 12 monthly values")
    anchors = [dt.date(year, m, anchor_day_of_month) for m in range(1, MONTHS + 1)]
    values = list(monthly)
    if wrap_year:
        anchors = [dt.date(year - 1, 12, anchor_day_of_month), *anchors]
        anchors.append(dt.date(year + 1, 1, anchor_day_of_month))
        values = [monthly[-1], *values, monthly[0]]
    days = [dt.date(year, 1, 1) + dt.timedelta(days=i) for i in range(_days_in_year(year))]
    x = np.array([d.toordinal() for d in days], dtype=float)
    xp = np.array([d.toordinal() for d in anchors], dtype=float)
    return days, np.interp(x, xp, np.asarray(values, dtype=float))


def _days_in_year(year: int) -> int:
    return (dt.date(year + 1, 1, 1) - dt.date(year, 1, 1)).days


# --------------------------------------------------------------------------- I/O schemas
class DesignDay(StrictModel):
    """A dry-bulb / wet-bulb design condition (DB-34b)."""

    name: str
    dry_bulb_degc: float
    wet_bulb_degc: float
    basis: str
    assumption_id: str


class ExtremeDay(StrictModel):
    """Warmest or coldest day of the interpolated series."""

    date: dt.date
    dry_bulb_degc: float
    wet_bulb_degc: float


class ClimateProfile(StrictModel):
    """Output of TL-02b."""

    year: int
    station: str
    dates: list[dt.date]
    dry_bulb_degc: list[float]
    relative_humidity_pct: list[float]
    wet_bulb_degc: list[float] = Field(description="Stull (2011) from the daily T_db and RH.")
    annual_mean_dry_bulb_degc: float
    annual_mean_wet_bulb_degc: float
    monthly_wet_bulb_stull_degc: list[float] = Field(
        min_length=MONTHS, max_length=MONTHS, description="Stull from the monthly normals."
    )
    monthly_wet_bulb_max_deviation_k: float = Field(
        description="max |Stull − configured monthly T_wb table| (RC-07 check, ≤ 0.1 K)."
    )
    warmest_day: ExtremeDay
    coldest_day: ExtremeDay
    design_days: list[DesignDay]
    shallow_ground_degc: float = Field(description="DB-34c.")
    exergy_dead_state: str = Field(description="DB-34d.")


# --------------------------------------------------------------------------- tool
def build_climate_profile(climate: ClimateConfig, year: int) -> tuple[ClimateProfile, list[str]]:
    """Build the daily climate series from an already-loaded climate config."""
    m, rule = climate.monthly, climate.interpolation
    dates, t_db = interpolate_monthly(
        m.dry_bulb_degc, year, rule.anchor_day_of_month, rule.wrap_year
    )
    _, rh = interpolate_monthly(
        m.relative_humidity_pct, year, rule.anchor_day_of_month, rule.wrap_year
    )
    t_wb = stull_wet_bulb_degc(t_db, rh)
    monthly_wb = stull_wet_bulb_degc(m.dry_bulb_degc, m.relative_humidity_pct)
    deviation = float(np.max(np.abs(monthly_wb - np.asarray(m.wet_bulb_degc))))
    warm, cold = int(np.argmax(t_db)), int(np.argmin(t_db))

    warnings: list[str] = []
    if deviation > WET_BULB_TABLE_TOL_K:
        warnings.append(
            f"Configured monthly T_wb differs from Stull by {deviation:.2f} K "
            f"(> {WET_BULB_TABLE_TOL_K} K)"
        )
    if abs(float(t_db.mean()) - climate.annual_mean_degc) > ANNUAL_MEAN_TOL_K:
        warnings.append(
            f"Daily T_db mean {float(t_db.mean()):.2f} °C differs from the configured annual mean "
            f"{climate.annual_mean_degc} °C (> {ANNUAL_MEAN_TOL_K} K)"
        )
    profile = ClimateProfile(
        year=year,
        station=climate.station,
        dates=dates,
        dry_bulb_degc=t_db.tolist(),
        relative_humidity_pct=rh.tolist(),
        wet_bulb_degc=t_wb.tolist(),
        annual_mean_dry_bulb_degc=float(t_db.mean()),
        annual_mean_wet_bulb_degc=float(t_wb.mean()),
        monthly_wet_bulb_stull_degc=monthly_wb.tolist(),
        monthly_wet_bulb_max_deviation_k=deviation,
        warmest_day=ExtremeDay(
            date=dates[warm], dry_bulb_degc=float(t_db[warm]), wet_bulb_degc=float(t_wb[warm])
        ),
        coldest_day=ExtremeDay(
            date=dates[cold], dry_bulb_degc=float(t_db[cold]), wet_bulb_degc=float(t_wb[cold])
        ),
        design_days=[
            DesignDay(
                name="summer_design",
                dry_bulb_degc=climate.design_point.dry_bulb_degc,
                wet_bulb_degc=climate.design_point.wet_bulb_degc,
                basis=climate.design_point.basis,
                assumption_id="DB-34b",
            ),
            DesignDay(
                name="warm_case",
                dry_bulb_degc=climate.warm_case.dry_bulb_degc,
                wet_bulb_degc=climate.warm_case.wet_bulb_degc,
                basis=climate.warm_case.basis,
                assumption_id="DB-34b",
            ),
        ],
        shallow_ground_degc=climate.shallow_ground_degc,
        exergy_dead_state=climate.exergy_dead_state,
    )
    return profile, warnings


def _summary(p: ClimateProfile) -> str:
    sd, wc = p.design_days
    return (
        f"{p.station} {p.year}: {len(p.dates)} d, T_db mean {p.annual_mean_dry_bulb_degc:.1f}°C "
        f"({p.coldest_day.dry_bulb_degc:.1f}–{p.warmest_day.dry_bulb_degc:.1f}), T_wb (Stull) "
        f"mean {p.annual_mean_wet_bulb_degc:.1f}°C, max {max(p.wet_bulb_degc):.1f}°C; design "
        f"{sd.dry_bulb_degc:.1f}/{sd.wet_bulb_degc:.1f}°C DB/WB, warm case "
        f"{wc.dry_bulb_degc:.1f}/{wc.wet_bulb_degc:.1f}°C"
    )


def profile_climate(*, config_dir: Path | str | None = None) -> ToolResult[ClimateProfile]:
    """TL-02b ``climate``: daily T_db, RH, T_wb (Stull) and design days (spec §2.3, RC-07).

    Reads ``climate_nl`` and ``design_basis`` (for the year) via ``load_config``.
    """
    climate = load_config("climate_nl", config_dir)
    year = load_config("design_basis", config_dir).year
    profile, warnings = build_climate_profile(climate, year)
    return ToolResult[ClimateProfile](
        data=profile,
        summary=_summary(profile),
        warnings=warnings,
        assumption_ids=list(ASSUMPTION_IDS),
        sources=["RC-07", "spec §2.3"],
    )


__all__ = [
    "TOOL_ID",
    "ClimateProfile",
    "DesignDay",
    "ExtremeDay",
    "build_climate_profile",
    "interpolate_monthly",
    "profile_climate",
    "stull_wet_bulb_degc",
]
