"""TL-02 ``profile_demand``: peaks, monthly means, load-duration, lowest-load windows, combined
profile of the daily heating and cooling demand (spec §3.2, DB-18/19).

The CSV paths, column names and the published peak/annual/non-zero-day values come from
``config/design_basis.yaml``. The window length (35 days) comes from DB-30, the screening COP of
heat-driven cooling from DEM-COP-HD (``config/assumptions.yaml``). The CSVs hold day-average MW;
energy per day = MW × 24 h.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
from pydantic import Field

from gemini_agent.schemas.common import HOURS_PER_DAY, StrictModel, ToolResult
from gemini_agent.schemas.config import (
    MONTHS,
    REPO_ROOT,
    AssumptionsRegister,
    DemandBasis,
    DesignBasis,
    load_config,
)
from gemini_agent.tools.design_basis import brine_heat_mw

TOOL_ID = "TL-02"
ASSUMPTION_OUTAGE = "DB-30"
ASSUMPTION_BRINE_CP = "DB-33"
ASSUMPTION_COOLING_COP = "DEM-COP-HD"

PEAK_TOL_MW = 0.005
"""Published peaks are quoted to 0.1 MW in the spec but to 0.001 MW in the CSV."""
ANNUAL_TOL_GWH = 0.005
"""Published annual totals carry two decimals (e.g. 111.67 GWh)."""


# --------------------------------------------------------------------------- I/O schemas
class DemandRequest(StrictModel):
    """Inputs of ``profile_demand``. ``None`` means "use the configured default"."""

    window_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Lowest-load window length; default DB-30 planned outage per doublet (35 d).",
    )
    heat_driven_cooling_cop: float | None = Field(
        default=None,
        gt=0,
        description="COP for the combined profile heat + cooling/COP; default DEM-COP-HD (0.75).",
    )
    thresholds_mw: list[Annotated[float, Field(ge=0)]] | None = Field(
        default=None,
        max_length=10,
        description=(
            "Report the number of days with demand above each threshold; default k × DB-23 "
            "(max heat per doublet) for k = 1…count_max."
        ),
    )


class ThresholdCount(StrictModel):
    """Days on which demand is strictly above a threshold."""

    threshold_mw: float
    days_above: int = Field(ge=0)


class LoadWindow(StrictModel):
    """The consecutive-day window with the lowest total demand (inclusive dates)."""

    start_date: dt.date
    end_date: dt.date
    length_days: int = Field(ge=1)
    mean_mw: float
    max_mw: float
    energy_mwh: float


class DemandStats(StrictModel):
    """Statistics of one daily profile (day-average MW)."""

    name: str
    daily_mw: list[float]
    peak_mw: float
    peak_date: dt.date
    mean_mw: float
    annual_mwh: float
    annual_gwh: float
    load_factor_frac: float = Field(description="mean / peak (0 if the peak is 0).")
    nonzero_days: int
    monthly_mean_mw: list[float] = Field(min_length=MONTHS, max_length=MONTHS)
    monthly_energy_mwh: list[float] = Field(min_length=MONTHS, max_length=MONTHS)
    load_duration_mw: list[float] = Field(description="Daily values sorted high → low.")
    days_above: list[ThresholdCount]
    lowest_window: LoadWindow


class DemandProfile(StrictModel):
    """Output of TL-02."""

    year: int
    dates: list[dt.date]
    window_days: int
    heat_driven_cooling_cop: float
    heating: DemandStats
    cooling: DemandStats
    combined_heat_driven: DemandStats = Field(
        description="Heating + cooling / COP: geothermal-side heat if all cooling is heat-driven."
    )


# --------------------------------------------------------------------------- helpers
def read_demand_csv(side: DemandBasis, year: int, root: Path | None = None) -> pd.Series:
    """Read one demand CSV into a daily ``pd.Series`` (MW) indexed by date, with checks.

    Raises ``ValueError`` if dates are not every day of ``year`` in order, or if values are
    missing or negative.
    """
    frame = pd.read_csv(side.csv_path(root or REPO_ROOT))
    for column in (side.csv_date_column, side.csv_value_column):
        if column not in frame.columns:
            raise ValueError(f"{side.demand_csv}: missing column '{column}'")
    dates = pd.to_datetime(frame[side.csv_date_column]).dt.date
    expected = pd.date_range(dt.date(year, 1, 1), dt.date(year, 12, 31), freq="D").date
    if len(dates) != len(expected) or any(a != b for a, b in zip(dates, expected, strict=True)):
        raise ValueError(f"{side.demand_csv}: dates must be every day of {year} in order")
    values = pd.to_numeric(frame[side.csv_value_column], errors="coerce")
    if values.isna().any():
        raise ValueError(f"{side.demand_csv}: missing or non-numeric values")
    if (values < 0).any():
        raise ValueError(f"{side.demand_csv}: negative demand")
    return pd.Series(values.to_numpy(dtype=float), index=list(dates), name=side.csv_value_column)


def lowest_window(series: pd.Series, window_days: int) -> LoadWindow:
    """Consecutive window (within the year, no wrap) with the lowest total demand.

    Ties resolve to the earliest window.
    """
    values = series.to_numpy(dtype=float)
    if window_days > len(values):
        raise ValueError("window longer than the series")
    sums = np.convolve(values, np.ones(window_days), mode="valid")
    start = int(np.argmin(sums))
    chunk = values[start : start + window_days]
    dates = list(series.index)
    return LoadWindow(
        start_date=dates[start],
        end_date=dates[start + window_days - 1],
        length_days=window_days,
        mean_mw=float(chunk.mean()),
        max_mw=float(chunk.max()),
        energy_mwh=float(chunk.sum() * HOURS_PER_DAY),
    )


def demand_stats(
    name: str, series: pd.Series, window_days: int, thresholds_mw: list[float]
) -> DemandStats:
    """Peak, totals, monthly table, load-duration curve, threshold days, lowest window."""
    values = series.to_numpy(dtype=float)
    months = np.array([d.month for d in series.index])
    peak_idx = int(np.argmax(values))
    peak = float(values[peak_idx])
    mean = float(values.mean())
    annual_mwh = float(values.sum() * HOURS_PER_DAY)
    return DemandStats(
        name=name,
        daily_mw=values.tolist(),
        peak_mw=peak,
        peak_date=series.index[peak_idx],
        mean_mw=mean,
        annual_mwh=annual_mwh,
        annual_gwh=annual_mwh / 1000.0,
        load_factor_frac=mean / peak if peak > 0.0 else 0.0,
        nonzero_days=int((values > 0.0).sum()),
        monthly_mean_mw=[float(values[months == m].mean()) for m in range(1, MONTHS + 1)],
        monthly_energy_mwh=[
            float(values[months == m].sum() * HOURS_PER_DAY) for m in range(1, MONTHS + 1)
        ],
        load_duration_mw=sorted(values.tolist(), reverse=True),
        days_above=[
            ThresholdCount(threshold_mw=t, days_above=int((values > t).sum()))
            for t in thresholds_mw
        ],
        lowest_window=lowest_window(series, window_days),
    )


def _check_against_basis(stats: DemandStats, side: DemandBasis) -> list[str]:
    """Warn when the CSV disagrees with the published DB-18/19 values."""
    warnings = []
    if abs(stats.peak_mw - side.peak_mw) > PEAK_TOL_MW or stats.peak_date != side.peak_date:
        warnings.append(
            f"{stats.name}: CSV peak {stats.peak_mw:.3f} MW on {stats.peak_date} differs from "
            f"design basis {side.peak_mw} MW on {side.peak_date}"
        )
    if abs(stats.annual_gwh - side.annual_gwh) > ANNUAL_TOL_GWH:
        warnings.append(
            f"{stats.name}: CSV total {stats.annual_gwh:.2f} GWh differs from design basis "
            f"{side.annual_gwh} GWh"
        )
    if stats.nonzero_days != side.nonzero_days:
        warnings.append(
            f"{stats.name}: CSV has {stats.nonzero_days} non-zero days, design basis "
            f"{side.nonzero_days}"
        )
    return warnings


def _default_thresholds(basis: DesignBasis, assumptions: AssumptionsRegister) -> list[float]:
    cp = assumptions.get(ASSUMPTION_BRINE_CP).value
    if not isinstance(cp, int | float) or isinstance(cp, bool):
        raise TypeError(f"assumption {ASSUMPTION_BRINE_CP} must be a number")
    dbl = basis.doublets
    per_doublet = brine_heat_mw(
        dbl.flow_max_m3h, basis.brine.density_kg_m3, float(cp), dbl.brine_cooling_max_k
    )
    return [k * per_doublet for k in range(1, dbl.count_max + 1)]


def _resolve_request(
    request: DemandRequest, basis: DesignBasis, assumptions: AssumptionsRegister
) -> tuple[int, float, list[float]]:
    window = request.window_days
    if window is None:
        outage = assumptions.get(ASSUMPTION_OUTAGE).value
        if not isinstance(outage, dict):
            raise TypeError(f"assumption {ASSUMPTION_OUTAGE} must be a mapping")
        window = int(outage["planned_outage_days_per_doublet"])
    cop = request.heat_driven_cooling_cop
    if cop is None:
        value = assumptions.get(ASSUMPTION_COOLING_COP).value
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
            raise TypeError(f"assumption {ASSUMPTION_COOLING_COP} must be a positive number")
        cop = float(value)
    thresholds = request.thresholds_mw
    if thresholds is None:
        thresholds = _default_thresholds(basis, assumptions)
    return window, cop, thresholds


def build_demand_profile(
    basis: DesignBasis,
    assumptions: AssumptionsRegister,
    request: DemandRequest | None = None,
    root: Path | None = None,
) -> tuple[DemandProfile, list[str]]:
    """Profile both demand CSVs from an already-loaded design basis; returns (profile, warnings)."""
    window, cop, thresholds = _resolve_request(request or DemandRequest(), basis, assumptions)
    heat = read_demand_csv(basis.heating, basis.year, root)
    cool = read_demand_csv(basis.cooling, basis.year, root)
    combined = heat + cool / cop
    heating = demand_stats("heating", heat, window, thresholds)
    cooling = demand_stats("cooling", cool, window, thresholds)
    profile = DemandProfile(
        year=basis.year,
        dates=list(heat.index),
        window_days=window,
        heat_driven_cooling_cop=cop,
        heating=heating,
        cooling=cooling,
        combined_heat_driven=demand_stats(f"heating+cooling/{cop:g}", combined, window, thresholds),
    )
    warnings = _check_against_basis(heating, basis.heating) + _check_against_basis(
        cooling, basis.cooling
    )
    return profile, warnings


def _short_date(day: dt.date) -> str:
    return f"{day.day} {day:%b}"


def _summary(p: DemandProfile) -> str:
    h, c, x = p.heating, p.cooling, p.combined_heat_driven
    hw, xw = h.lowest_window, x.lowest_window
    return (
        f"Heat peak {h.peak_mw:.1f} MW {_short_date(h.peak_date)}, {h.annual_gwh:.2f} GWh, "
        f"LF {h.load_factor_frac:.0%}; cool peak {c.peak_mw:.1f} MW {_short_date(c.peak_date)}, "
        f"{c.annual_gwh:.2f} GWh, {c.nonzero_days} d>0; lowest {p.window_days}-d heat window "
        f"{_short_date(hw.start_date)}–{_short_date(hw.end_date)} (max {hw.max_mw:.1f} MW); "
        f"heat+cool/{p.heat_driven_cooling_cop:g}: {x.annual_gwh:.1f} GWh, window "
        f"{_short_date(xw.start_date)}–{_short_date(xw.end_date)} (max {xw.max_mw:.1f} MW)"
    )


def profile_demand(
    request: DemandRequest | None = None,
    *,
    config_dir: Path | str | None = None,
    data_root: Path | str | None = None,
) -> ToolResult[DemandProfile]:
    """TL-02: profile the daily heating and cooling demand (spec §3.2, DB-18/19).

    Reads ``design_basis`` and ``assumptions`` via ``load_config``. The CSV paths in the design
    basis are relative to ``data_root`` (default: the repository root), independent of
    ``config_dir``.
    """
    basis = load_config("design_basis", config_dir)
    assumptions = load_config("assumptions", config_dir)
    request = request or DemandRequest()
    root = Path(data_root) if data_root is not None else None
    profile, warnings = build_demand_profile(basis, assumptions, request, root)
    defaults_used = {
        ASSUMPTION_OUTAGE: request.window_days is None,
        ASSUMPTION_COOLING_COP: request.heat_driven_cooling_cop is None,
        ASSUMPTION_BRINE_CP: request.thresholds_mw is None,
    }
    assumption_ids = [aid for aid, used in defaults_used.items() if used]
    return ToolResult[DemandProfile](
        data=profile,
        summary=_summary(profile),
        warnings=warnings,
        assumption_ids=assumption_ids,
        sources=["spec §3.2", "DB-18", "DB-19"],
    )


__all__ = [
    "TOOL_ID",
    "DemandProfile",
    "DemandRequest",
    "DemandStats",
    "LoadWindow",
    "ThresholdCount",
    "build_demand_profile",
    "demand_stats",
    "lowest_window",
    "profile_demand",
    "read_demand_csv",
]
