"""TL-07 ``thermal_breakthrough`` and TL-08 ``pressure_interference`` (spec §6; RC-02).

TL-07: Gringarten–Sauty breakthrough time ``t_b = π h D² (ρC)_a / (3 (ρC)_w Q)`` (RC-02 eq 1,
helper reused from ``tools/design_basis.py``) and the burden-free production-temperature curve
of RC-02 eq 2: the streamline leaving the injector at angle θ (from the well axis) arrives after

    t_a(θ) = t_b · 3 (1 − θ cot θ) / sin² θ

(the standard doublet streamline travel time; it reproduces RC-02's t_a(π/2) = 3 t_a(0) and
t_a(3π/4) = 20.13 t_a(0)), and a fraction θ/π of the flow travels on streamlines faster than θ,
so after breakthrough ``T_prod = (1 − θ/π) T_res + (θ/π) T_inj`` with ``t_a(θ) = t``. No heat
recharge from the cap/base rock is included (conservative). RC-02 eq 3 gives the ±6 K
heterogeneity band on the 30-year temperature. The default injection temperature is the TL-05
wellhead temperature at this flow minus the DB-12 maximum brine cooling.

TL-08: steady line-source superposition of every other doublet's producer (sink) and injector
(source), ``ΔP = Q μ / (2π k h) · ln(r_prod / r_inj)`` at a well, against the ≤ 1 bar regulator
rule (RC-02, Dogger). Sign: RC-02 eq 4 prints ``ln(r_inj / r_prod)``, i.e. *drawdown* positive;
here a pressure *rise* is positive (source = injector), so the sign is deliberately reversed. The
magnitude and the ≤ 1 bar check (on |ΔP|) are the same.

Flows above the DB-11 maximum are refused (hard limit); lower flows are allowed because TL-07 and
TL-08 are also used with annual-average flows. Card status: RC-02 marks the closed-form t_b as
``VERIFY`` (Gringarten–Sauty 1975 not printed in the source paper).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import Field
from scipy.optimize import brentq

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.tools import doublet as doublet_tools
from gemini_agent.tools.design_basis import (
    M2_PER_MILLIDARCY,
    PA_PER_BAR,
    SECONDS_PER_HOUR,
    SECONDS_PER_YEAR,
    breakthrough_time_yr,
)
from gemini_agent.tools.resource import (
    ASSUMPTION_AQUIFER_RHOC,
    ASSUMPTION_BRINE_CP,
    ASSUMPTION_SPACING,
    SubsurfaceBasis,
    formation_temperature_degc,
    load_subsurface_basis,
)

TOOL_ID_BREAKTHROUGH = "TL-07"
TOOL_ID_INTERFERENCE = "TL-08"

HETEROGENEITY_BAND_K = 6.0
"""RC-02 eq 3: ±6 °C on the 30-yr production temperature (Boolean lens case) [L]."""
INTERFERENCE_LIMIT_BAR = 1.0
"""RC-02 eq 4 / Defaults: interference ≤ 1 bar at neighbouring wells (Dogger regulator rule) [L]."""
MAX_HORIZON_YR = 100.0
"""Upper bound of the production-temperature curve horizon."""
_THETA_SMALL = 1e-4


def _check_flow_limit(flow_m3h: float, basis: SubsurfaceBasis) -> None:
    """Refuse flows above the DB-11 maximum per doublet (golden rule 4)."""
    if flow_m3h > basis.flow_max_m3h:
        raise ValueError(
            f"flow {flow_m3h:g} m³/h exceeds the DB-11 hard limit {basis.flow_max_m3h:g} m³/h "
            "per doublet"
        )


# --------------------------------------------------------------------------- RC-02 eq 2 helpers
def arrival_time_ratio(theta_rad: float) -> float:
    """``t_a(θ) / t_a(0) = 3 (1 − θ cot θ) / sin² θ`` for a doublet streamline (RC-02 eq 2)."""
    if not 0.0 <= theta_rad < math.pi:
        raise ValueError("streamline angle must be in [0, π)")
    if theta_rad < _THETA_SMALL:
        # Series: 1 − θ cot θ = θ²/3 + θ⁴/45 + …, sin² θ = θ² − θ⁴/3 + … ⇒ ratio = 1 + 2θ²/5 + …
        return 1.0 + 0.4 * theta_rad**2
    return 3.0 * (1.0 - theta_rad / math.tan(theta_rad)) / math.sin(theta_rad) ** 2


def broken_through_angle_rad(time_over_breakthrough: float) -> float:
    """Streamline angle θ whose arrival time is ``t`` (``t / t_b`` given); 0 before breakthrough."""
    if time_over_breakthrough <= 1.0:
        return 0.0
    upper = math.pi * (1.0 - 1e-12)
    if arrival_time_ratio(upper) <= time_over_breakthrough:
        return upper
    return float(
        brentq(
            lambda theta: arrival_time_ratio(theta) - time_over_breakthrough,
            _THETA_SMALL,
            upper,
            xtol=1e-12,
        )
    )


def production_temperature_degc(
    time_yr: float,
    breakthrough_yr: float,
    reservoir_temperature_degc: float,
    injection_temperature_degc: float,
) -> float:
    """Burden-free production temperature (RC-02 eq 2): ``(1 − θ/π) T_res + (θ/π) T_inj``."""
    if time_yr < 0.0:
        raise ValueError("time must be non-negative")
    theta = broken_through_angle_rad(time_yr / breakthrough_yr)
    fraction = theta / math.pi
    return (1.0 - fraction) * reservoir_temperature_degc + fraction * injection_temperature_degc


def min_spacing_for_breakthrough_m(
    target_yr: float,
    thickness_m: float,
    flow_m3h: float,
    rho_c_aquifer_j_per_m3_k: float,
    rho_c_water_j_per_m3_k: float,
) -> float:
    """Spacing D with ``t_b(D) = target`` (RC-02 eq 1 solved for D)."""
    flow_m3s = flow_m3h / SECONDS_PER_HOUR
    return math.sqrt(
        3.0
        * rho_c_water_j_per_m3_k
        * flow_m3s
        * target_yr
        * SECONDS_PER_YEAR
        / (math.pi * thickness_m * rho_c_aquifer_j_per_m3_k)
    )


# --------------------------------------------------------------------------- TL-07
class ThermalBreakthroughRequest(StrictModel):
    """TL-07 input; ``None`` fields take the design basis / register values."""

    spacing_m: float | None = Field(default=None, gt=0, description="None: SUB-SPACING.")
    flow_m3h: float | None = Field(default=None, gt=0, description="None: DB-11 maximum.")
    depth_m: float | None = Field(default=None, gt=0, description="None: DB-01 (sets T_res).")
    wellhead_temperature_degc: float | None = Field(
        default=None, description="Producer wellhead T; None: TL-05 wellbore model at this flow."
    )
    injection_temperature_degc: float | None = Field(
        default=None, description="None: wellhead T − DB-12 maximum brine cooling."
    )
    horizon_yr: float | None = Field(
        default=None, gt=0, le=MAX_HORIZON_YR, description="None: analysis period (ECO-01)."
    )


class TemperaturePoint(StrictModel):
    """One point of the production-temperature curve."""

    time_yr: float
    production_temperature_degc: float


class ThermalBreakthroughReport(StrictModel):
    """TL-07 output."""

    spacing_m: float
    flow_m3h: float
    thickness_m: float
    reservoir_temperature_degc: float
    wellhead_temperature_degc: float = Field(description="Producer wellhead (TL-05 model).")
    injection_temperature_degc: float
    brine_cooling_k: float = Field(description="Wellhead − injection temperature (≤ DB-12).")
    rho_c_aquifer_j_per_m3_k: float
    rho_c_water_j_per_m3_k: float
    breakthrough_time_yr: float = Field(description="RC-02 eq 1 (Gringarten & Sauty 1975).")
    horizon_yr: float
    breakthrough_after_horizon: bool = Field(description="t_b ≥ horizon (ENG-06 target).")
    min_spacing_for_horizon_m: float = Field(description="D with t_b = horizon at this flow.")
    temperature_at_horizon_degc: float
    decline_at_horizon_k: float
    heterogeneity_band_k: float = Field(description="RC-02 eq 3: ±6 K on the 30-yr value.")
    curve: list[TemperaturePoint] = Field(description="Yearly, burden-free (RC-02 eq 2).")


def thermal_breakthrough_report(
    basis: SubsurfaceBasis, request: ThermalBreakthroughRequest | None = None
) -> ThermalBreakthroughReport:
    """TL-07 core (RC-02 eq 1–3)."""
    request = request or ThermalBreakthroughRequest()
    spacing = request.spacing_m or basis.well_spacing_m
    flow = request.flow_m3h or basis.flow_max_m3h
    _check_flow_limit(flow, basis)
    depth = request.depth_m or basis.design_depth_m
    t_res = formation_temperature_degc(depth, basis)
    t_wh = request.wellhead_temperature_degc
    if t_wh is None:
        design = doublet_tools.DoubletDesign(depth_m=depth)
        t_wh = doublet_tools.wellhead_temperature_degc(
            doublet_tools.resolve_doublet(design, basis), flow
        )
    if t_wh > t_res:
        raise ValueError("wellhead temperature must not exceed the reservoir temperature")
    t_inj = (
        t_wh - basis.brine_cooling_max_k
        if request.injection_temperature_degc is None
        else request.injection_temperature_degc
    )
    if t_inj > t_res:
        raise ValueError("injection temperature must not exceed the reservoir temperature")
    if t_wh - t_inj > basis.brine_cooling_max_k + 1e-9:
        raise ValueError(
            f"brine cooling {t_wh - t_inj:.1f} K exceeds the hard limit "
            f"{basis.brine_cooling_max_k:g} K (DB-12)"
        )
    horizon = request.horizon_yr or basis.analysis_period_yr
    rho_c_water = basis.density_kg_m3 * basis.cp_j_per_kg_k
    rho_c_aquifer = basis.aquifer_rho_c_j_per_m3_k
    t_b = breakthrough_time_yr(basis.thickness_m, spacing, flow, rho_c_aquifer, rho_c_water)
    years = [float(y) for y in range(math.floor(horizon) + 1)]
    if years[-1] < horizon:
        years.append(horizon)
    curve = [
        TemperaturePoint(
            time_yr=y, production_temperature_degc=production_temperature_degc(y, t_b, t_res, t_inj)
        )
        for y in years
    ]
    t_end = curve[-1].production_temperature_degc
    return ThermalBreakthroughReport(
        spacing_m=spacing,
        flow_m3h=flow,
        thickness_m=basis.thickness_m,
        reservoir_temperature_degc=t_res,
        wellhead_temperature_degc=t_wh,
        injection_temperature_degc=t_inj,
        brine_cooling_k=t_wh - t_inj,
        rho_c_aquifer_j_per_m3_k=rho_c_aquifer,
        rho_c_water_j_per_m3_k=rho_c_water,
        breakthrough_time_yr=t_b,
        horizon_yr=horizon,
        breakthrough_after_horizon=t_b >= horizon,
        min_spacing_for_horizon_m=min_spacing_for_breakthrough_m(
            horizon, basis.thickness_m, flow, rho_c_aquifer, rho_c_water
        ),
        temperature_at_horizon_degc=t_end,
        decline_at_horizon_k=t_res - t_end,
        heterogeneity_band_k=HETEROGENEITY_BAND_K,
        curve=curve,
    )


def thermal_breakthrough(
    request: ThermalBreakthroughRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[ThermalBreakthroughReport]:
    """TL-07: breakthrough time and production-temperature curve (RC-02 eq 1–3)."""
    basis = load_subsurface_basis(config_dir)
    data = thermal_breakthrough_report(basis, request)
    warnings: list[str] = []
    if not data.breakthrough_after_horizon:
        warnings.append(
            f"Breakthrough after {data.breakthrough_time_yr:.1f} yr, before the "
            f"{data.horizon_yr:g}-yr horizon: increase spacing to ≥ "
            f"{data.min_spacing_for_horizon_m:.0f} m or reduce the average flow."
        )
    summary = (
        f"t_b {data.breakthrough_time_yr:.1f} yr (D {data.spacing_m:.0f} m, Q {data.flow_m3h:.0f} "
        f"m³/h, h {data.thickness_m:.0f} m); T_prod {data.temperature_at_horizon_degc:.1f}°C at "
        f"{data.horizon_yr:g} yr (−{data.decline_at_horizon_k:.1f} K, ±"
        f"{data.heterogeneity_band_k:g} K); D for t_b ≥ {data.horizon_yr:g} yr: "
        f"{data.min_spacing_for_horizon_m:.0f} m"
    )
    return ToolResult[ThermalBreakthroughReport](
        data=data,
        summary=summary,
        warnings=warnings,
        assumption_ids=[ASSUMPTION_AQUIFER_RHOC, ASSUMPTION_BRINE_CP, ASSUMPTION_SPACING],
        sources=["RC-02", "cost_assumptions analysis_period_yr (ECO-01)"],
    )


# --------------------------------------------------------------------------- TL-08
class InterferenceRequest(StrictModel):
    """TL-08 input."""

    doublets: int = Field(default=2, ge=1, description="Number of doublets (DB-10 checked).")
    flow_m3h: float | None = Field(default=None, gt=0, description="None: DB-11 maximum.")
    spacing_m: float | None = Field(default=None, gt=0, description="None: SUB-SPACING.")
    separation_m: float | None = Field(
        default=None, gt=0, description="Distance between doublet axes; None: the minimum."
    )
    layout: Literal["parallel", "alternating"] = Field(
        default="parallel",
        description="parallel: producers side by side; alternating: every other doublet flipped.",
    )


class WellInterference(StrictModel):
    """Pressure change at one well caused by all other doublets."""

    doublet: int
    kind: Literal["producer", "injector"]
    x_m: float
    y_m: float
    pressure_change_bar: float = Field(description="Positive = rise (RC-02 eq 4 superposition).")


class InterferenceReport(StrictModel):
    """TL-08 output."""

    doublets: int
    flow_m3h: float
    spacing_m: float
    layout: str
    unit_pressure_bar: float = Field(description="Q μ / (2π k h) in bar.")
    limit_bar: float
    min_separation_m: float | None = Field(description="Smallest separation with max |ΔP| ≤ limit.")
    separation_m: float | None = Field(description="Separation evaluated (None for one doublet).")
    wells: list[WellInterference]
    max_abs_pressure_change_bar: float
    within_limit: bool


def _layout(
    doublets: int, spacing_m: float, separation_m: float, layout: str
) -> list[tuple[int, str, float, float]]:
    wells = []
    for i in range(doublets):
        flipped = layout == "alternating" and i % 2 == 1
        y_producer, y_injector = (spacing_m, 0.0) if flipped else (0.0, spacing_m)
        x = i * separation_m
        wells.append((i, "producer", x, y_producer))
        wells.append((i, "injector", x, y_injector))
    return wells


def _pressure_changes_bar(
    unit_bar: float, wells: list[tuple[int, str, float, float]]
) -> list[float]:
    """RC-02 eq 4 at each well from the other doublets (own doublet excluded)."""
    by_doublet: dict[int, dict[str, tuple[float, float]]] = {}
    for doublet, kind, x, y in wells:
        by_doublet.setdefault(doublet, {})[kind] = (x, y)
    changes = []
    for doublet, _, x, y in wells:
        total = 0.0
        for other, positions in by_doublet.items():
            if other == doublet:
                continue
            r_prod = math.dist((x, y), positions["producer"])
            r_inj = math.dist((x, y), positions["injector"])
            total += unit_bar * math.log(r_prod / r_inj)
        changes.append(total)
    return changes


def interference_report(
    basis: SubsurfaceBasis, request: InterferenceRequest | None = None
) -> InterferenceReport:
    """TL-08 core: line-source superposition, steady state (RC-02 eq 4)."""
    request = request or InterferenceRequest()
    if not basis.doublet_count_min <= request.doublets <= basis.doublet_count_max:
        raise ValueError(
            f"{request.doublets} doublets outside the DB-10 hard limit "
            f"{basis.doublet_count_min}–{basis.doublet_count_max}"
        )
    flow = request.flow_m3h or basis.flow_max_m3h
    _check_flow_limit(flow, basis)
    spacing = request.spacing_m or basis.well_spacing_m
    k_m2 = basis.permeability_md * M2_PER_MILLIDARCY
    unit_bar = (
        flow
        / SECONDS_PER_HOUR
        * basis.viscosity_pa_s
        / (2.0 * math.pi * k_m2 * basis.thickness_m)
        / PA_PER_BAR
    )

    def worst(separation: float) -> float:
        changes = _pressure_changes_bar(
            unit_bar, _layout(request.doublets, spacing, separation, request.layout)
        )
        return max(abs(c) for c in changes)

    min_separation = None
    if request.doublets > 1:
        low, high = 1e-3 * spacing, 1e3 * spacing
        if worst(low) <= INTERFERENCE_LIMIT_BAR:
            min_separation = low
        else:
            min_separation = float(
                brentq(lambda s: worst(s) - INTERFERENCE_LIMIT_BAR, low, high, xtol=1e-6)
            )
    separation = request.separation_m if request.separation_m is not None else min_separation
    positions = _layout(request.doublets, spacing, separation or 0.0, request.layout)
    changes = _pressure_changes_bar(unit_bar, positions) if request.doublets > 1 else []
    wells = [
        WellInterference(
            doublet=doublet,
            kind=kind,
            x_m=x,
            y_m=y,
            pressure_change_bar=changes[i] if changes else 0.0,
        )
        for i, (doublet, kind, x, y) in enumerate(positions)
    ]
    worst_change = max((abs(w.pressure_change_bar) for w in wells), default=0.0)
    return InterferenceReport(
        doublets=request.doublets,
        flow_m3h=flow,
        spacing_m=spacing,
        layout=request.layout,
        unit_pressure_bar=unit_bar,
        limit_bar=INTERFERENCE_LIMIT_BAR,
        min_separation_m=min_separation,
        separation_m=separation if request.doublets > 1 else None,
        wells=wells,
        max_abs_pressure_change_bar=worst_change,
        within_limit=worst_change <= INTERFERENCE_LIMIT_BAR + 1e-9,
    )


def pressure_interference(
    request: InterferenceRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[InterferenceReport]:
    """TL-08: inter-doublet pressure interference vs the ≤ 1 bar rule (RC-02 eq 4)."""
    basis = load_subsurface_basis(config_dir)
    data = interference_report(basis, request)
    warnings: list[str] = []
    if not data.within_limit:
        warnings.append(
            f"Interference {data.max_abs_pressure_change_bar:.2f} bar exceeds "
            f"{data.limit_bar:g} bar: separate the doublets by ≥ {data.min_separation_m:.0f} m."
        )
    if data.doublets == 1:
        summary = (
            f"1 doublet: no neighbouring doublet, interference 0 bar (limit {data.limit_bar:g})"
        )
    else:
        summary = (
            f"{data.doublets} doublets ({data.layout}, D {data.spacing_m:.0f} m, Q "
            f"{data.flow_m3h:.0f} m³/h): max |ΔP| {data.max_abs_pressure_change_bar:.2f} bar at "
            f"separation {data.separation_m:.0f} m (limit {data.limit_bar:g} bar ⇒ min "
            f"{data.min_separation_m:.0f} m); within limit: {data.within_limit}"
        )
    return ToolResult[InterferenceReport](
        data=data,
        summary=summary,
        warnings=warnings,
        assumption_ids=[ASSUMPTION_SPACING],
        sources=["RC-02"],
    )


__all__ = [
    "HETEROGENEITY_BAND_K",
    "INTERFERENCE_LIMIT_BAR",
    "TOOL_ID_BREAKTHROUGH",
    "TOOL_ID_INTERFERENCE",
    "InterferenceReport",
    "InterferenceRequest",
    "TemperaturePoint",
    "ThermalBreakthroughReport",
    "ThermalBreakthroughRequest",
    "WellInterference",
    "arrival_time_ratio",
    "broken_through_angle_rad",
    "interference_report",
    "min_spacing_for_breakthrough_m",
    "pressure_interference",
    "production_temperature_degc",
    "thermal_breakthrough",
    "thermal_breakthrough_report",
]
