"""TL-05 ``doublet_operating_point`` and TL-06 ``doublet_performance_map`` (spec §6; RC-01).

GEMINI's ``ProducerWell`` solves the flow for a given ESP frequency and wellhead pressure (the
producer flow is an OUTPUT). TL-05 inverts it: it solves the ESP frequency (``brentq``, 30–70 Hz)
that delivers a target flow, then runs GEMINI ``ProducerWell`` at that frequency and checks that
GEMINI returns the target flow within 1 %. Per operating point it also gives

* the wellhead temperature, walking GEMINI ``DPDT`` *up* from the reservoir temperature against
  the undisturbed geothermal gradient (RC-01 eq 1 and envelope note, U = 10 W/(m² K) in GEMINI);
* the ESP Δp and power both ways: GEMINI's stage curve and the challenge rule
  ``P_shaft = Q Δp / 0.70`` with ``P_el = P_shaft / (η_motor η_VFD)`` (RC-01 eq 6, DB-13, DB-40);
* the ESP intake-pressure check against the bubble/CO₂-release pressure (RC-01 item 9), NPSH
  available (RC-01 item 8) and the gas-release depths (flash-depth method, DiPippo eq 4.16–4.20);
* the injector wellhead pressure and injection-pump power from GEMINI ``InjectorWell`` with the
  brine PVT block and friction as a loss for the downward flow (RC-01 eq 12).

Injection equals production (DB-14). The ESP is sized once per design at the maximum flow
(SUB-ESP); TL-06 evaluates TL-05 on a 50…300 m³/h grid and caches the map under ``runs/``.

Every [A] value comes from ``config/assumptions.yaml`` through
:func:`gemini_agent.tools.resource.load_subsurface_basis`.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, ValidationError
from scipy.optimize import brentq, minimize_scalar

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import REPO_ROOT
from gemini_agent.tools.design_basis import (
    G_M_S2,
    PA_PER_BAR,
    SECONDS_PER_HOUR,
    brine_heat_mw,
    productivity_index_m3h_per_bar,
    temperature_at_depth_degc,
)
from gemini_agent.tools.resource import (
    SUBSURFACE_ASSUMPTION_IDS,
    SubsurfaceBasis,
    load_subsurface_basis,
    reservoir_conditions_at,
)
from gemini_model.pump.esp import ESP
from gemini_model.reservoir.inflow_performance import IPR
from gemini_simulator.well_components import InjectorWell, ProducerWell

# The same DPDT/ESP builders ProducerWell uses, so the inverse solve and the GEMINI check run on
# identical well and pump objects (brine PVT, explicit roughness, specific-gravity-scaled ESP).
from gemini_simulator.well_flow import _build_dpdt, _build_esp

TOOL_ID_OPERATING_POINT = "TL-05"
TOOL_ID_MAP = "TL-06"

FLOW_TOLERANCE_FRAC = 0.01
"""WP-05 acceptance: GEMINI ``ProducerWell`` must return the target flow within 1 %."""
WELLBORE_CELL_M = 100.0
"""Numerical: DPDT cell length for the wellbore temperature walk (one ambient per cell)."""
RUNOUT_SEARCH_MAX_M3H = 2000.0
"""Numerical: upper end of the scan for the ESP curve's zero-head (run-out) flow."""
INJECTION_PRESSURE_BRACKET_BAR = (0.001, 500.0)
"""GEMINI ``InjectorWell`` default bracket; the floor means 'no pump needed'."""
MAP_STEP_M3H = 10.0
"""TL-06 default flow step."""
MAP_CACHE_VERSION = "wp05-1"
"""Bump when the operating-point algorithm changes: it is part of the cache key."""
DEFAULT_CACHE_DIR = REPO_ROOT / "runs" / "cache" / "doublet_maps"
"""Performance maps are cached here (``runs/`` is git-ignored)."""
_FLOW_EPS_M3H = 1e-9
_DRY_INTAKE_BARG = 1e-6
"""GEMINI DPDT clamps pressures at 0.001 Pa: an intake at or below this is a dry pump."""


# --------------------------------------------------------------------------- models
class DoubletDesign(StrictModel):
    """Doublet design inputs for TL-05/TL-06; ``None`` takes the register value or sizes it."""

    depth_m: float | None = Field(default=None, gt=0, description="Reservoir TVD; None: DB-01.")
    producer_wellhead_pressure_barg: float | None = Field(
        default=None, ge=0, description="None: SUB-PWH. C7 (no degassing) keeps it ≥ SUB-PBUB."
    )
    injection_pump_suction_barg: float | None = Field(
        default=None, ge=0, description="None: SUB-PSUC."
    )
    brine_cooling_k: float | None = Field(
        default=None, gt=0, description="Producer → injector ΔT; None: DB-12 maximum."
    )
    well_inner_diameter_m: float | None = Field(default=None, gt=0, description="None: SUB-WELL.")
    well_roughness_m: float | None = Field(default=None, ge=0, description="None: SUB-WELL.")
    skin: float | None = Field(default=None, description="None: DB-38.")
    esp_flow_scale: float | None = Field(
        default=None, gt=0, description="None: best-efficiency point at the maximum flow."
    )
    esp_stages: int | None = Field(default=None, ge=1, description="None: sized (SUB-ESP).")
    esp_setting_depth_m: float | None = Field(
        default=None, gt=0, description="None: sized (SUB-ESP)."
    )


class EspDesign(StrictModel):
    """ESP selected for the doublet (SUB-ESP rules)."""

    flow_scale: float = Field(description="k: Q × k, head per stage × 1, power × k.")
    stages: int
    setting_depth_m: float = Field(description="Pump intake depth TVD.")
    head_coeff: list[float] = Field(description="GEMINI ESP head polynomial after flow scaling.")
    power_coeff: list[float] = Field(description="GEMINI ESP power polynomial after scaling.")
    best_efficiency_flow_m3h: float = Field(description="BEP of the scaled curve at 60 Hz.")
    best_efficiency_frac: float = Field(description="Curve efficiency at the BEP.")
    runout_flow_m3h: float = Field(description="Zero-head flow of the scaled curve.")
    frequency_min_hz: float
    frequency_max_hz: float
    frequency_nominal_hz: float
    sized: list[str] = Field(description="Which of flow_scale/stages/setting_depth were sized.")
    notes: list[str] = Field(default_factory=list)


class ResolvedDoublet(StrictModel):
    """All inputs of one doublet after defaults and ESP sizing (the cache key of TL-06)."""

    depth_m: float
    reservoir_temperature_degc: float
    reservoir_pressure_barg: float
    pressure_model: str
    thickness_m: float
    productivity_index_m3h_per_bar: float
    injectivity_index_m3h_per_bar: float
    density_kg_m3: float
    viscosity_pa_s: float
    cp_j_per_kg_k: float
    well_inner_diameter_m: float
    well_roughness_m: float
    producer_wellhead_pressure_barg: float
    injection_pump_suction_barg: float
    brine_cooling_k: float
    bubble_pressure_barg: float
    npsh_margin_m: float
    vapour_pressure_bound_bara: float
    atmospheric_pressure_bara: float
    surface_temperature_degc: float
    gradient_k_per_km: float
    pump_hydraulic_efficiency_frac: float
    motor_efficiency_frac: float
    vfd_efficiency_frac: float
    flow_min_m3h: float
    flow_max_m3h: float
    esp: EspDesign

    @property
    def wire_to_water_efficiency_frac(self) -> float:
        """Pump × motor × VFD efficiency (DB-40)."""
        return (
            self.pump_hydraulic_efficiency_frac
            * self.motor_efficiency_frac
            * self.vfd_efficiency_frac
        )

    @property
    def pvt(self) -> dict[str, float]:
        """Brine ``"pvt"`` block for GEMINI wells (GM-01; DB-08, DB-33)."""
        return {
            "liquid_density_kg_m3": self.density_kg_m3,
            "liquid_viscosity_pa_s": self.viscosity_pa_s,
            "liquid_specific_heat_j_kgk": self.cp_j_per_kg_k,
        }


class OperatingPoint(StrictModel):
    """TL-05 result at one flow; ``feasible=False`` lists the reasons, numbers may be missing."""

    flow_m3h: float
    feasible: bool
    reasons: list[str] = Field(default_factory=list, description="Why the point is infeasible.")
    notes: list[str] = Field(default_factory=list, description="Non-blocking flags.")
    injection_flow_m3h: float = Field(description="Q_inj = Q_prod (DB-14).")
    bottomhole_pressure_barg: float | None = Field(default=None, description="P_wf (RC-01 eq 4).")
    drawdown_bar: float | None = None
    esp_mode: Literal["variable_frequency", "min_frequency_choked"] | None = None
    esp_frequency_hz: float | None = None
    producer_wellhead_pressure_barg: float | None = Field(
        default=None, description="Upstream of the choke (= design WHP unless choked)."
    )
    wellhead_choke_dp_bar: float | None = None
    producer_flow_check_m3h: float | None = Field(
        default=None, description="Flow GEMINI ProducerWell returns at the solved frequency."
    )
    flow_error_frac: float | None = None
    esp_intake_pressure_barg: float | None = None
    esp_discharge_pressure_barg: float | None = None
    esp_dp_bar: float | None = None
    producer_friction_bar: float | None = None
    esp_hydraulic_power_w: float | None = Field(default=None, description="Q Δp.")
    esp_shaft_power_w: float | None = Field(default=None, description="Q Δp / 0.70 (DB-13).")
    esp_electric_power_w: float | None = Field(default=None, description="Shaft / (0.93·0.97).")
    esp_curve_shaft_power_w: float | None = Field(default=None, description="GEMINI ESP curve.")
    esp_curve_efficiency_frac: float | None = Field(default=None, description="Q Δp / curve.")
    intake_margin_bar: float | None = Field(default=None, description="P_intake − P_bubble.")
    npsh_available_m: float | None = None
    gas_release_depth_m: float | None = Field(
        default=None, description="Where the unboosted flowing pressure equals P_bubble."
    )
    setting_depth_margin_m: float | None = Field(
        default=None, description="ESP setting depth − gas-release depth (> 0 required)."
    )
    tubing_gas_release_depth_m: float | None = Field(
        default=None, description="Above this depth gas leaves solution in the tubing."
    )
    wellhead_temperature_degc: float | None = None
    wellbore_cooling_k: float | None = None
    injection_temperature_degc: float | None = None
    injector_bottomhole_pressure_barg: float | None = None
    injector_friction_bar: float | None = None
    injector_wellhead_pressure_barg: float | None = None
    injection_pump_required: bool | None = None
    injection_pump_dp_bar: float | None = None
    injection_pump_hydraulic_power_w: float | None = None
    injection_pump_shaft_power_w: float | None = None
    injection_pump_electric_power_w: float | None = None
    total_pump_electric_power_w: float | None = None
    brine_heat_mw: float | None = Field(default=None, description="ṁ c_p ΔT at brine_cooling_k.")


class OperatingPointRequest(StrictModel):
    """TL-05 input."""

    flow_m3h: float | None = Field(default=None, gt=0, description="None: DB-11 maximum.")
    design: DoubletDesign = Field(default_factory=DoubletDesign)


class OperatingPointReport(StrictModel):
    """TL-05 output."""

    doublet: ResolvedDoublet
    point: OperatingPoint


class PerformanceMapRequest(StrictModel):
    """TL-06 input."""

    design: DoubletDesign = Field(default_factory=DoubletDesign)
    step_m3h: float = Field(default=MAP_STEP_M3H, ge=1.0, le=250.0)


class DoubletPerformanceMap(StrictModel):
    """TL-06 output: TL-05 over the DB-11 range for one resolved doublet."""

    doublet: ResolvedDoublet
    points: list[OperatingPoint]
    feasible_flow_min_m3h: float | None
    feasible_flow_max_m3h: float | None
    infeasible_flows_m3h: list[float]
    cache_key: str
    from_cache: bool = Field(default=False, description="Set on load; not stored in the file.")

    def interpolate(self, field: str, flow_m3h: float) -> float:
        """Linear interpolation of a numeric point field over the feasible points."""
        points = [p for p in self.points if p.feasible]
        flows = [p.flow_m3h for p in points]
        if not points or not flows[0] - _FLOW_EPS_M3H <= flow_m3h <= flows[-1] + _FLOW_EPS_M3H:
            raise ValueError(f"flow {flow_m3h} m³/h outside the feasible map range")
        values = [getattr(p, field) for p in points]
        if any(not isinstance(v, int | float) or isinstance(v, bool) for v in values):
            raise ValueError(f"field {field!r} is not numeric on every feasible point")
        return float(np.interp(flow_m3h, flows, values))


# --------------------------------------------------------------------------- ESP curve helpers
def _scaled_coefficients(
    head_coeff: list[float], power_coeff: list[float], flow_scale: float
) -> tuple[list[float], list[float]]:
    """Coefficients of ``h(Q/k)`` and ``k·P(Q/k)`` (pump family scaling, SUB-ESP)."""
    head = [c / flow_scale**i for i, c in enumerate(head_coeff)]
    power = [flow_scale * c / flow_scale**i for i, c in enumerate(power_coeff)]
    return head, power


def _stage_curve(head_coeff: list[float], power_coeff: list[float]) -> ESP:
    """One GEMINI ESP stage (water, SG 1) for curve analysis."""
    esp = ESP()
    esp.update_parameters({"no_stages": 1, "head_coeff": head_coeff, "power_coeff": power_coeff})
    return esp


def _curve_point(esp: ESP, flow_m3h: float, frequency_hz: float) -> tuple[float, float]:
    """Head (Pa) and brake power (W) of a GEMINI ESP at a flow and frequency."""
    esp.calculate_output({"pump_freq": frequency_hz, "pump_flow": flow_m3h / SECONDS_PER_HOUR})
    output = esp.get_output()
    return output["pump_head"], output["pump_power"]


def curve_runout_and_bep(
    head_coeff: list[float], power_coeff: list[float], frequency_hz: float
) -> tuple[float, float, float]:
    """Run-out flow, best-efficiency flow (m³/h) and BEP efficiency of an ESP stage curve.

    Efficiency is ``Q·head / power`` from GEMINI's head (Pa) and brake power (W); the run-out is
    the first zero of the head (GEMINI's head scales with f² at a fixed flow, so it does not
    depend on frequency).
    """
    esp = _stage_curve(head_coeff, power_coeff)

    def head(q: float) -> float:
        return _curve_point(esp, q, frequency_hz)[0]

    previous = 1.0
    if head(previous) <= 0.0:
        raise ValueError("ESP curve has no positive head at 1 m³/h")
    runout = None
    for q in np.arange(2.0, RUNOUT_SEARCH_MAX_M3H + 1.0, 1.0):
        if head(float(q)) <= 0.0:
            runout = float(brentq(head, previous, float(q)))
            break
        previous = float(q)
    if runout is None:
        raise ValueError(f"ESP curve has no run-out below {RUNOUT_SEARCH_MAX_M3H:.0f} m³/h")

    def negative_efficiency(q: float) -> float:
        h, p = _curve_point(esp, q, frequency_hz)
        return -(q / SECONDS_PER_HOUR) * h / p

    best = minimize_scalar(
        negative_efficiency, bounds=(1.0, runout), method="bounded", options={"xatol": 1e-6}
    )
    return runout, float(best.x), float(-best.fun)


# --------------------------------------------------------------------------- well helpers
def _segments(length_m: float, diameter_m: float) -> list[dict[str, float]]:
    """Return one vertical well section for ``well_flow._build_dpdt`` (SUB-WELL)."""
    return [{"angle": 90.0, "depth": length_m, "diameter": diameter_m}]


def _trajectory_table(doublet: ResolvedDoublet) -> list[dict[str, float]]:
    """Vertical MD/TVD/ID survey for GEMINI ``ProducerWell``/``InjectorWell``."""
    d = doublet.well_inner_diameter_m
    return [
        {"MD": 0.0, "TVD": 0.0, "ID": d},
        {"MD": doublet.depth_m, "TVD": doublet.depth_m, "ID": d},
    ]


def _walk(
    doublet: ResolvedDoublet,
    length_m: float,
    pressure_barg: float,
    temperature_degc: float,
    flow_m3h: float,
    direction: str,
    ambient_degc: float | None = None,
) -> dict:
    """Run GEMINI ``DPDT`` over one vertical section (positive flow = upward)."""
    dpdt = _build_dpdt(
        _segments(length_m, doublet.well_inner_diameter_m),
        roughness=doublet.well_roughness_m,
        pvt=doublet.pvt,
    )
    temperature_k = temperature_degc + 273.15
    ambient_k = temperature_k if ambient_degc is None else ambient_degc + 273.15
    dpdt.calculate_output(
        {
            "pressure": pressure_barg * PA_PER_BAR,
            "temperature": temperature_k,
            "temperature_ambient": ambient_k,
            "flowrate": flow_m3h / SECONDS_PER_HOUR,
            "direction": direction,
        },
        None,
    )
    return dpdt.get_output()


def _bottomhole_pressure_barg(doublet: ResolvedDoublet, flow_m3h: float, kind: str) -> float:
    """GEMINI ``IPR``: production ``P_res − Q/PI`` or injection ``P_res + Q/II`` (RC-01 eq 4)."""
    ipr = IPR()
    if kind == "production_reservoir":
        index = {"productivity_index": doublet.productivity_index_m3h_per_bar / 3.6e8}
    else:
        index = {"injectivity_index": doublet.injectivity_index_m3h_per_bar / 3.6e8}
    ipr.update_parameters(
        {
            "type": kind,
            "reservoir_pressure": doublet.reservoir_pressure_barg * PA_PER_BAR,
            **index,
        }
    )
    ipr.calculate_output({"flow": flow_m3h / SECONDS_PER_HOUR}, None)
    return ipr.get_output()["bottomhole_pressure"] / PA_PER_BAR


def _unboosted_pressure_barg(
    doublet: ResolvedDoublet, flow_m3h: float, depth_m: float, p_wf_barg: float
) -> float:
    """Flowing pressure at ``depth_m`` walking DPDT up from the bottomhole (no pump)."""
    if depth_m >= doublet.depth_m:
        return p_wf_barg
    output = _walk(
        doublet,
        doublet.depth_m - depth_m,
        p_wf_barg,
        doublet.reservoir_temperature_degc,
        flow_m3h,
        "up",
    )
    return output["pressure_output"] / PA_PER_BAR


def _producer_hydraulics(
    doublet: ResolvedDoublet, flow_m3h: float, wellhead_pressure_barg: float
) -> dict[str, float]:
    """Return the required ESP Δp at a flow (RC-01 item 7) with GEMINI IPR + DPDT.

    ``P_intake`` walks the section below the pump up from ``P_wf``; ``P_discharge`` walks the
    section above it down from the wellhead (production flow, friction adds). Pressure walks are
    isothermal: the constant-property brine PVT makes them temperature-independent.
    """
    z_pump = doublet.esp.setting_depth_m
    p_wf = _bottomhole_pressure_barg(doublet, flow_m3h, "production_reservoir")
    below = _walk(
        doublet,
        doublet.depth_m - z_pump,
        p_wf,
        doublet.reservoir_temperature_degc,
        flow_m3h,
        "up",
    )
    above = _walk(
        doublet,
        z_pump,
        wellhead_pressure_barg,
        doublet.reservoir_temperature_degc,
        flow_m3h,
        "down",
    )
    p_intake = below["pressure_output"] / PA_PER_BAR
    p_discharge = above["pressure_output"] / PA_PER_BAR
    return {
        "p_wf": p_wf,
        "p_intake": p_intake,
        "p_discharge": p_discharge,
        "dp_required": p_discharge - p_intake,
        "friction": (below["pressuredrop_fric_output"] + above["pressuredrop_fric_output"])
        / PA_PER_BAR,
    }


def _wellhead_temperature_degc(doublet: ResolvedDoublet, flow_m3h: float) -> float:
    """Walk GEMINI DPDT *up* from T_res, one cell per ≤ 100 m, ambient = formation T (RC-01)."""
    n_cells = max(1, math.ceil(doublet.depth_m / WELLBORE_CELL_M))
    edges = np.linspace(doublet.depth_m, 0.0, n_cells + 1)
    temperature = doublet.reservoir_temperature_degc
    pressure = doublet.reservoir_pressure_barg
    for bottom, top in itertools.pairwise(edges):
        ambient = _formation_temperature_degc(doublet, 0.5 * float(bottom + top))
        output = _walk(doublet, float(bottom - top), pressure, temperature, flow_m3h, "up", ambient)
        temperature = output["temperature_output"] - 273.15
        pressure = output["pressure_output"] / PA_PER_BAR
    return float(temperature)


def _formation_temperature_degc(doublet: ResolvedDoublet, depth_m: float) -> float:
    """Undisturbed formation temperature (RC-01 eq 1; DB-03/DB-04)."""
    return temperature_at_depth_degc(
        depth_m, doublet.surface_temperature_degc, doublet.gradient_k_per_km
    )


def _root_depth(func: Callable[[float], float], low: float, high: float) -> float:
    """``brentq`` on a depth interval (``func(low)`` and ``func(high)`` differ in sign)."""
    return float(brentq(func, low, high, xtol=1e-6))


# --------------------------------------------------------------------------- resolution
def _esp_head_pa(
    doublet: ResolvedDoublet, stages: int, flow_m3h: float, frequency_hz: float
) -> tuple[float, float]:
    """Brine ESP head (Pa) and brake power (W): GEMINI ESP via ``well_flow._build_esp``."""
    esp = _build_esp(
        {
            "no_stages": stages,
            "head_coeff": doublet.esp.head_coeff,
            "power_coeff": doublet.esp.power_coeff,
        },
        doublet.pvt,
    )
    esp.calculate_output({"pump_freq": frequency_hz, "pump_flow": flow_m3h / SECONDS_PER_HOUR})
    output = esp.get_output()
    return output["pump_head"], output["pump_power"]


def resolve_doublet(design: DoubletDesign, basis: SubsurfaceBasis) -> ResolvedDoublet:
    """Apply register defaults and size the ESP (SUB-ESP rules) for one doublet design.

    Raises ``ValueError`` for a design that breaks a hard limit (DB-12) or cannot be sized.
    """
    depth = design.depth_m if design.depth_m is not None else basis.design_depth_m
    conditions = reservoir_conditions_at(depth, basis)
    cooling = design.brine_cooling_k or basis.brine_cooling_max_k
    if cooling > basis.brine_cooling_max_k:
        raise ValueError(
            f"brine cooling {cooling:g} K exceeds the hard limit "
            f"{basis.brine_cooling_max_k:g} K (DB-12)"
        )
    skin = basis.skin if design.skin is None else design.skin
    pi = productivity_index_m3h_per_bar(
        basis.permeability_md,
        basis.thickness_m,
        basis.viscosity_pa_s,
        basis.drainage_radius_m,
        basis.wellbore_radius_m,
        skin,
    )
    head0, power0 = basis.esp_stage_head_coeff, basis.esp_stage_power_coeff
    f_nom = basis.esp_frequency_nominal_hz
    sized: list[str] = []
    notes: list[str] = []
    _, base_bep, _ = curve_runout_and_bep(head0, power0, f_nom)
    flow_scale = design.esp_flow_scale
    if flow_scale is None:
        flow_scale = basis.flow_max_m3h / base_bep
        sized.append("flow_scale")
    head_coeff, power_coeff = _scaled_coefficients(head0, power0, flow_scale)
    runout, bep, bep_eff = curve_runout_and_bep(head_coeff, power_coeff, f_nom)
    reservoir_top = depth - 0.5 * basis.thickness_m
    provisional_depth = design.esp_setting_depth_m or 0.5 * depth
    if provisional_depth >= depth:
        raise ValueError(f"ESP setting depth {provisional_depth:g} m is not above {depth:g} m")

    def build(stages: int, setting_depth: float) -> ResolvedDoublet:
        return ResolvedDoublet(
            depth_m=depth,
            reservoir_temperature_degc=conditions.temperature_degc,
            reservoir_pressure_barg=conditions.pressure_barg,
            pressure_model=conditions.pressure_model,
            thickness_m=basis.thickness_m,
            productivity_index_m3h_per_bar=pi,
            injectivity_index_m3h_per_bar=pi,  # SUB-II: II = PI (RC-01 item 4)
            density_kg_m3=basis.density_kg_m3,
            viscosity_pa_s=basis.viscosity_pa_s,
            cp_j_per_kg_k=basis.cp_j_per_kg_k,
            well_inner_diameter_m=design.well_inner_diameter_m or basis.well_inner_diameter_m,
            well_roughness_m=(
                basis.well_roughness_m
                if design.well_roughness_m is None
                else design.well_roughness_m
            ),
            producer_wellhead_pressure_barg=(
                basis.producer_wellhead_pressure_barg
                if design.producer_wellhead_pressure_barg is None
                else design.producer_wellhead_pressure_barg
            ),
            injection_pump_suction_barg=(
                basis.injection_pump_suction_barg
                if design.injection_pump_suction_barg is None
                else design.injection_pump_suction_barg
            ),
            brine_cooling_k=cooling,
            bubble_pressure_barg=basis.bubble_pressure_barg,
            npsh_margin_m=basis.npsh_margin_m,
            vapour_pressure_bound_bara=basis.vapour_pressure_bound_bara,
            atmospheric_pressure_bara=basis.atmospheric_pressure_bara,
            surface_temperature_degc=basis.surface_temperature_degc,
            gradient_k_per_km=basis.gradient_k_per_km,
            pump_hydraulic_efficiency_frac=basis.pump_hydraulic_efficiency_frac,
            motor_efficiency_frac=basis.motor_efficiency_frac,
            vfd_efficiency_frac=basis.vfd_efficiency_frac,
            flow_min_m3h=basis.flow_min_m3h,
            flow_max_m3h=basis.flow_max_m3h,
            esp=EspDesign(
                flow_scale=flow_scale,
                stages=stages,
                setting_depth_m=setting_depth,
                head_coeff=head_coeff,
                power_coeff=power_coeff,
                best_efficiency_flow_m3h=bep,
                best_efficiency_frac=bep_eff,
                runout_flow_m3h=runout,
                frequency_min_hz=basis.esp_frequency_min_hz,
                frequency_max_hz=basis.esp_frequency_max_hz,
                frequency_nominal_hz=f_nom,
                sized=list(sized),
                notes=list(notes),
            ),
        )

    q_max = basis.flow_max_m3h
    draft = build(design.esp_stages or 1, provisional_depth)
    stages = design.esp_stages
    if stages is None:
        # Δp_required does not depend on the split depth (uniform vertical well, constant PVT).
        dp_required = _producer_hydraulics(draft, q_max, draft.producer_wellhead_pressure_barg)[
            "dp_required"
        ]
        stage_head_bar = _esp_head_pa(draft, 1, q_max, f_nom)[0] / PA_PER_BAR
        if q_max >= runout or stage_head_bar <= 0.0:
            # Beyond run-out the polynomial is extrapolated (its head turns positive again).
            raise ValueError(
                f"ESP curve gives no head at the maximum flow {q_max:g} m³/h "
                f"(run-out {runout:.0f} m³/h); increase esp_flow_scale"
            )
        stages = max(1, math.ceil(dp_required / stage_head_bar - 1e-9))
        sized.append("stages")
    setting_depth = design.esp_setting_depth_m
    if setting_depth is None:
        sized.append("setting_depth")
        p_wf = _bottomhole_pressure_barg(draft, q_max, "production_reservoir")
        p_bubble = basis.bubble_pressure_barg

        def margin(z: float) -> float:
            return _unboosted_pressure_barg(draft, q_max, z, p_wf) - p_bubble

        if margin(depth) < 0.0:
            setting_depth = reservoir_top
            notes.append(
                f"No setting depth keeps the intake ≥ {p_bubble:g} bar(g) at {q_max:g} m³/h "
                f"(P_wf {p_wf:.1f} bar(g)); pump set at the reservoir top {reservoir_top:.0f} m."
            )
        elif margin(0.0) >= 0.0:
            setting_depth = basis.esp_setting_depth_step_m
        else:
            z_release = _root_depth(margin, 0.0, depth)
            step = basis.esp_setting_depth_step_m
            setting_depth = min(math.ceil(z_release / step - 1e-9) * step, reservoir_top)
    return build(stages, float(setting_depth))


# --------------------------------------------------------------------------- TL-05 core
def _infeasible(base: dict, reasons: list[str], notes: list[str]) -> OperatingPoint:
    return OperatingPoint(**base, feasible=False, reasons=reasons, notes=notes)


def operating_point(doublet: ResolvedDoublet, flow_m3h: float) -> OperatingPoint:
    """TL-05 core: operating point of a resolved doublet at ``flow_m3h`` (see module doc)."""
    base: dict = {"flow_m3h": flow_m3h, "injection_flow_m3h": flow_m3h}
    notes: list[str] = []
    if not (
        doublet.flow_min_m3h - _FLOW_EPS_M3H <= flow_m3h <= doublet.flow_max_m3h + _FLOW_EPS_M3H
    ):
        reason = (
            f"flow {flow_m3h:g} m³/h is outside the DB-11 hard limit "
            f"{doublet.flow_min_m3h:g}–{doublet.flow_max_m3h:g} m³/h per active doublet"
        )
        return _infeasible(base, [reason], notes)
    esp = doublet.esp
    whp = doublet.producer_wellhead_pressure_barg
    hyd = _producer_hydraulics(doublet, flow_m3h, whp)
    base.update(
        bottomhole_pressure_barg=hyd["p_wf"],
        drawdown_bar=doublet.reservoir_pressure_barg - hyd["p_wf"],
        producer_friction_bar=hyd["friction"],
    )
    if hyd["p_wf"] <= 0.0:
        return _infeasible(base, ["bottomhole pressure would fall below 0 bar(g)"], notes)
    if flow_m3h >= esp.runout_flow_m3h:
        return _infeasible(
            base,
            [f"flow beyond the ESP curve run-out ({esp.runout_flow_m3h:.0f} m³/h)"],
            notes,
        )

    def head_bar(frequency_hz: float) -> float:
        return _esp_head_pa(doublet, esp.stages, flow_m3h, frequency_hz)[0] / PA_PER_BAR

    def excess(frequency_hz: float) -> float:
        return head_bar(frequency_hz) - hyd["dp_required"]

    if excess(esp.frequency_max_hz) < 0.0:
        reason = (
            f"ESP Δp {hyd['dp_required']:.1f} bar needs more than {esp.frequency_max_hz:g} Hz "
            f"({esp.stages} stages give {head_bar(esp.frequency_max_hz):.1f} bar)"
        )
        return _infeasible(base, [reason], notes)
    upstream_whp = whp
    if excess(esp.frequency_min_hz) >= 0.0:
        frequency = esp.frequency_min_hz
        mode = "min_frequency_choked"
        head_min = head_bar(frequency)

        def choke(wellhead_barg: float) -> float:
            return head_min - _producer_hydraulics(doublet, flow_m3h, wellhead_barg)["dp_required"]

        upper = whp + max(head_min - hyd["dp_required"], 0.0) + 1.0
        upstream_whp = float(brentq(choke, whp, upper, xtol=1e-9)) if choke(whp) > 0 else whp
        hyd = _producer_hydraulics(doublet, flow_m3h, upstream_whp)
        notes.append(
            f"Below {esp.frequency_min_hz:g} Hz: ESP held at the minimum frequency and the "
            f"wellhead choked by {upstream_whp - whp:.1f} bar."
        )
    else:
        frequency = float(brentq(excess, esp.frequency_min_hz, esp.frequency_max_hz, xtol=1e-10))
        mode = "variable_frequency"
    dp = hyd["dp_required"]
    flow_m3s = flow_m3h / SECONDS_PER_HOUR
    curve_power = _esp_head_pa(doublet, esp.stages, flow_m3h, frequency)[1]
    hydraulic = flow_m3s * dp * PA_PER_BAR
    shaft = hydraulic / doublet.pump_hydraulic_efficiency_frac
    base.update(
        esp_mode=mode,
        esp_frequency_hz=frequency,
        producer_wellhead_pressure_barg=upstream_whp,
        wellhead_choke_dp_bar=upstream_whp - whp,
        esp_intake_pressure_barg=hyd["p_intake"],
        esp_discharge_pressure_barg=hyd["p_discharge"],
        esp_dp_bar=dp,
        esp_hydraulic_power_w=hydraulic,
        esp_shaft_power_w=shaft,
        esp_electric_power_w=shaft / (doublet.motor_efficiency_frac * doublet.vfd_efficiency_frac),
        esp_curve_shaft_power_w=curve_power,
        esp_curve_efficiency_frac=hydraulic / curve_power if curve_power > 0 else None,
    )
    if curve_power > 0 and hydraulic / curve_power > 1.0:
        notes.append(
            f"GEMINI ESP curve power is below Q·Δp at {frequency:.1f} Hz (no flow affinity in "
            "the curve); plant power uses Q·Δp/0.70."
        )

    # Wellhead temperature (DPDT up from T_res) and the GEMINI ProducerWell check.
    t_wellhead = _wellhead_temperature_degc(doublet, flow_m3h)
    base.update(
        wellhead_temperature_degc=t_wellhead,
        wellbore_cooling_k=doublet.reservoir_temperature_degc - t_wellhead,
    )
    reasons: list[str] = []

    # ESP setting checks: intake vs bubble point, NPSH, gas-release depths (RC-01 items 8–9).
    p_bubble = doublet.bubble_pressure_barg
    base["intake_margin_bar"] = hyd["p_intake"] - p_bubble
    if hyd["p_intake"] <= _DRY_INTAKE_BARG:
        reasons.append(
            f"ESP set above the dynamic fluid level at {flow_m3h:g} m³/h (GEMINI clamps the intake "
            "pressure at 0): set the pump deeper"
        )
    elif hyd["p_intake"] < p_bubble:
        reasons.append(
            f"ESP intake {hyd['p_intake']:.1f} bar(g) is below the bubble/CO₂-release pressure "
            f"{p_bubble:g} bar(g) (SUB-PBUB): set the pump deeper"
        )
    if doublet.reservoir_temperature_degc <= 100.0:
        npsh = (
            (
                hyd["p_intake"]
                + doublet.atmospheric_pressure_bara
                - doublet.vapour_pressure_bound_bara
            )
            * PA_PER_BAR
            / (doublet.density_kg_m3 * G_M_S2)
        )
        base["npsh_available_m"] = npsh
        if npsh < doublet.npsh_margin_m:
            reasons.append(f"NPSH available {npsh:.1f} m < {doublet.npsh_margin_m:g} m (SUB-NPSH)")
    else:
        notes.append("Brine above 100 °C: vapour-pressure bound not valid, NPSH not evaluated.")
    p_wf = hyd["p_wf"]

    def unboosted_margin(z: float) -> float:
        return _unboosted_pressure_barg(doublet, flow_m3h, z, p_wf) - p_bubble

    if unboosted_margin(doublet.depth_m) >= 0.0:
        release = (
            None
            if unboosted_margin(0.0) >= 0.0
            else _root_depth(unboosted_margin, 0.0, doublet.depth_m)
        )
        base["gas_release_depth_m"] = release
        base["setting_depth_margin_m"] = None if release is None else esp.setting_depth_m - release
    if upstream_whp < p_bubble:

        def tubing_margin(z: float) -> float:
            if z <= 0.0:
                return upstream_whp - p_bubble
            output = _walk(doublet, z, upstream_whp, t_wellhead, flow_m3h, "down")
            return output["pressure_output"] / PA_PER_BAR - p_bubble

        z_tubing = (
            esp.setting_depth_m
            if tubing_margin(esp.setting_depth_m) < 0.0
            else _root_depth(tubing_margin, 0.0, esp.setting_depth_m)
        )
        base["tubing_gas_release_depth_m"] = z_tubing
        notes.append(
            f"Wellhead {upstream_whp:.1f} < {p_bubble:g} bar(g): gas leaves solution above "
            f"≈ {z_tubing:.0f} m in the tubing; dose inhibitor below it (RC-12) or hold pressure."
        )

    # GEMINI ProducerWell must return the target flow at the solved frequency (≤ 1 %).
    try:
        check = _producer_well_flow_m3h(doublet, frequency, upstream_whp, t_wellhead, flow_m3h)
    except ValueError as exc:
        reasons.append(f"GEMINI ProducerWell did not converge at {frequency:.1f} Hz: {exc}")
    else:
        error = abs(check - flow_m3h) / flow_m3h
        base.update(producer_flow_check_m3h=check, flow_error_frac=error)
        if error > FLOW_TOLERANCE_FRAC:
            reasons.append(f"GEMINI ProducerWell gives {check:.1f} m³/h (target {flow_m3h:g})")

    # Injector: GEMINI InjectorWell with brine PVT and friction as a loss (RC-01 eq 12).
    t_injection = t_wellhead - doublet.brine_cooling_k
    base["injection_temperature_degc"] = t_injection
    base["brine_heat_mw"] = brine_heat_mw(
        flow_m3h, doublet.density_kg_m3, doublet.cp_j_per_kg_k, doublet.brine_cooling_k
    )
    try:
        injector = _injector(doublet, flow_m3h, t_injection)
    except ValueError as exc:
        reasons.append(f"GEMINI InjectorWell did not converge: {exc}")
    else:
        base.update(injector)
        if not injector["injection_pump_required"]:
            notes.append(
                "Injector takes the flow under gravity (required WHP ≤ 0): choke the injection."
            )
        if not reasons:
            base["total_pump_electric_power_w"] = (
                base["esp_electric_power_w"] + injector["injection_pump_electric_power_w"]
            )
    if reasons:
        return _infeasible(base, reasons, notes)
    return OperatingPoint(**base, feasible=True, notes=notes)


def _producer_well_flow_m3h(
    doublet: ResolvedDoublet,
    frequency_hz: float,
    wellhead_pressure_barg: float,
    wellhead_temperature_degc: float,
    target_m3h: float,
) -> float:
    """Run GEMINI ``ProducerWell`` (nodal solve) at the solved ESP frequency; return its flow."""
    esp = doublet.esp
    well = ProducerWell()
    well.update_parameters(
        {
            "reservoir_pressure_bar": doublet.reservoir_pressure_barg,
            "productivity_index_m3h_per_bar": doublet.productivity_index_m3h_per_bar,
            "trajectory_table": _trajectory_table(doublet),
            "roughness_m": doublet.well_roughness_m,
            "esp_depth_m": esp.setting_depth_m,
            "esp": {
                "no_stages": esp.stages,
                "head_coeff": esp.head_coeff,
                "power_coeff": esp.power_coeff,
            },
            "esp_freq_hz": frequency_hz,
            "pvt": doublet.pvt,
            "ambient_temperature_c": _formation_temperature_degc(doublet, 0.5 * doublet.depth_m),
            "flow_bracket_m3h": (0.5 * target_m3h, min(1.5 * target_m3h, esp.runout_flow_m3h)),
        }
    )
    well.calculate_output(
        {
            "wellhead_pressure": wellhead_pressure_barg,
            "wellhead_temperature": wellhead_temperature_degc,
        }
    )
    return float(well.get_output()["flow_rate"])


def _injector(doublet: ResolvedDoublet, flow_m3h: float, injection_temperature_degc: float) -> dict:
    """Injector WHP and pump power from GEMINI ``InjectorWell`` (brine PVT, RC-01 eq 12)."""
    well = InjectorWell()
    well.update_parameters(
        {
            # Same reservoir as the producer (DB-14; spec §7.2: injector at the reservoir pressure).
            "reservoir_pressure_bar": doublet.reservoir_pressure_barg,
            "productivity_index_m3h_per_bar": doublet.injectivity_index_m3h_per_bar,
            "trajectory_table": _trajectory_table(doublet),
            "roughness_m": doublet.well_roughness_m,
            "pvt": doublet.pvt,
            "friction_opposes_flow": True,
            "pressure_bracket_bar": INJECTION_PRESSURE_BRACKET_BAR,
            "injector_pump": {
                "efficiency_factor": doublet.wire_to_water_efficiency_frac,
                "electricity_emission_factor": 0.0,
            },
        }
    )
    suction = doublet.injection_pump_suction_barg
    well.calculate_output(
        {
            "flow_rate": flow_m3h,
            "wellhead_temperature": injection_temperature_degc,
            "pressure_in": suction * PA_PER_BAR,
        }
    )
    output = well.get_output()
    whp = float(output["wellhead_pressure"])
    pump_required = whp > INJECTION_PRESSURE_BRACKET_BAR[0]
    dp = max(0.0, whp - suction) if pump_required else 0.0
    hydraulic = flow_m3h / SECONDS_PER_HOUR * dp * PA_PER_BAR
    friction = _walk(doublet, doublet.depth_m, whp, injection_temperature_degc, -flow_m3h, "down")
    return {
        "injector_bottomhole_pressure_barg": _bottomhole_pressure_barg(
            doublet, flow_m3h, "injection_reservoir"
        ),
        "injector_friction_bar": abs(friction["pressuredrop_fric_output"]) / PA_PER_BAR,
        "injector_wellhead_pressure_barg": whp,
        "injection_pump_required": pump_required,
        "injection_pump_dp_bar": dp,
        "injection_pump_hydraulic_power_w": hydraulic,
        "injection_pump_shaft_power_w": hydraulic / doublet.pump_hydraulic_efficiency_frac,
        "injection_pump_electric_power_w": float(output["power_el"]) if pump_required else 0.0,
    }


# --------------------------------------------------------------------------- TL-05 tool
def _point_summary(doublet: ResolvedDoublet, point: OperatingPoint) -> str:
    head = f"Q {point.flow_m3h:.0f} m³/h @{doublet.depth_m:.0f} m"
    if not point.feasible:
        return f"{head}: INFEASIBLE — {'; '.join(point.reasons)}"[:300]
    mode = " choked" if point.esp_mode == "min_frequency_choked" else ""
    return (
        f"{head}: ESP {point.esp_frequency_hz:.1f} Hz{mode}, Δp {point.esp_dp_bar:.1f} bar, "
        f"shaft {point.esp_shaft_power_w / 1e6:.2f} MW (curve "
        f"{point.esp_curve_shaft_power_w / 1e6:.2f}), intake {point.esp_intake_pressure_barg:.1f} "
        f"bar(g); T_wh {point.wellhead_temperature_degc:.1f}°C; inj WHP "
        f"{point.injector_wellhead_pressure_barg:.1f} bar(g), pump "
        f"{point.injection_pump_shaft_power_w / 1e6:.2f} MW shaft; total "
        f"{point.total_pump_electric_power_w / 1e6:.2f} MW_e"
    )


def doublet_operating_point(
    request: OperatingPointRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[OperatingPointReport]:
    """TL-05: ESP frequency, powers, checks and injection pump at one flow (RC-01; GEMINI)."""
    request = request or OperatingPointRequest()
    basis = load_subsurface_basis(config_dir)
    doublet = resolve_doublet(request.design, basis)
    flow = request.flow_m3h if request.flow_m3h is not None else basis.flow_max_m3h
    point = operating_point(doublet, flow)
    warnings = [f"Infeasible: {reason}" for reason in point.reasons]
    warnings += doublet.esp.notes
    return ToolResult[OperatingPointReport](
        data=OperatingPointReport(doublet=doublet, point=point),
        summary=_point_summary(doublet, point),
        warnings=warnings,
        assumption_ids=list(SUBSURFACE_ASSUMPTION_IDS),
        sources=["RC-01", "GEMINI ProducerWell/InjectorWell/DPDT/ESP/IPR"],
    )


# --------------------------------------------------------------------------- TL-06
def map_flows(basis_min: float, basis_max: float, step: float) -> list[float]:
    """Flow grid from the DB-11 minimum to the maximum (both included)."""
    count = max(1, math.floor((basis_max - basis_min) / step + 1e-9))
    flows = [basis_min + i * step for i in range(count + 1)]
    flows = [q for q in flows if q <= basis_max + _FLOW_EPS_M3H]
    if abs(flows[-1] - basis_max) > _FLOW_EPS_M3H:
        flows.append(basis_max)
    return [round(q, 9) for q in flows]


def map_cache_key(doublet: ResolvedDoublet, flows: list[float]) -> str:
    """Hash of everything the map depends on (inputs, sizing, grid, algorithm version)."""
    payload = {
        "version": MAP_CACHE_VERSION,
        "doublet": doublet.model_dump(mode="json"),
        "flows_m3h": flows,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def build_performance_map(doublet: ResolvedDoublet, flows: list[float]) -> DoubletPerformanceMap:
    """Evaluate TL-05 at every flow of the grid (no caching)."""
    points = [operating_point(doublet, q) for q in flows]
    feasible = [p.flow_m3h for p in points if p.feasible]
    return DoubletPerformanceMap(
        doublet=doublet,
        points=points,
        feasible_flow_min_m3h=min(feasible) if feasible else None,
        feasible_flow_max_m3h=max(feasible) if feasible else None,
        infeasible_flows_m3h=[p.flow_m3h for p in points if not p.feasible],
        cache_key=map_cache_key(doublet, flows),
    )


def _load_cached(path: Path, key: str) -> DoubletPerformanceMap | None:
    if not path.is_file():
        return None
    try:
        cached = DoubletPerformanceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError, ValueError):
        return None
    if cached.cache_key != key:
        return None
    return cached.model_copy(update={"from_cache": True})


def _write_cache(path: Path, performance_map: DoubletPerformanceMap) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        performance_map.model_dump_json(exclude={"from_cache"}, indent=1), encoding="utf-8"
    )
    tmp.replace(path)


def _map_summary(perf: DoubletPerformanceMap) -> str:
    d = perf.doublet
    feasible = [p for p in perf.points if p.feasible]
    head = (
        f"Map {perf.points[0].flow_m3h:.0f}–{perf.points[-1].flow_m3h:.0f} m³/h "
        f"({len(perf.points)} pts) @{d.depth_m:.0f} m, ESP {d.esp.stages} stg @"
        f"{d.esp.setting_depth_m:.0f} m"
    )
    if not feasible:
        return f"{head}: no feasible point; first reason: {perf.points[0].reasons[0]}"[:300]

    def span(values: list[float], fmt: str) -> str:
        return f"{format(min(values), fmt)}–{format(max(values), fmt)}"

    return (
        f"{head}: feasible {perf.feasible_flow_min_m3h:.0f}–{perf.feasible_flow_max_m3h:.0f}; "
        f"f {span([p.esp_frequency_hz for p in feasible], '.1f')} Hz; pumps "
        f"{span([p.total_pump_electric_power_w / 1e6 for p in feasible], '.2f')} MW_e; T_wh "
        f"{span([p.wellhead_temperature_degc for p in feasible], '.1f')}°C; inj WHP "
        f"{span([p.injector_wellhead_pressure_barg for p in feasible], '.1f')} bar(g)"
        f"{'; cached' if perf.from_cache else ''}"
    )


def doublet_performance_map(
    request: PerformanceMapRequest | None = None,
    *,
    config_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    use_cache: bool = True,
) -> ToolResult[DoubletPerformanceMap]:
    """TL-06: TL-05 over the DB-11 range, cached as JSON under ``runs/cache/doublet_maps``."""
    request = request or PerformanceMapRequest()
    basis = load_subsurface_basis(config_dir)
    doublet = resolve_doublet(request.design, basis)
    flows = map_flows(basis.flow_min_m3h, basis.flow_max_m3h, request.step_m3h)
    key = map_cache_key(doublet, flows)
    path = Path(cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR) / f"{key}.json"
    perf = _load_cached(path, key) if use_cache else None
    if perf is None:
        perf = build_performance_map(doublet, flows)
        if use_cache:
            _write_cache(path, perf)
    warnings = list(doublet.esp.notes)
    if perf.infeasible_flows_m3h:
        flows_text = ", ".join(f"{q:g}" for q in perf.infeasible_flows_m3h)
        warnings.append(f"Infeasible flows (m³/h): {flows_text}")
    return ToolResult[DoubletPerformanceMap](
        data=perf,
        summary=_map_summary(perf),
        warnings=warnings,
        assumption_ids=list(SUBSURFACE_ASSUMPTION_IDS),
        sources=["RC-01", "GEMINI ProducerWell/InjectorWell/DPDT/ESP/IPR"],
    )


__all__ = [
    "DEFAULT_CACHE_DIR",
    "FLOW_TOLERANCE_FRAC",
    "MAP_CACHE_VERSION",
    "TOOL_ID_MAP",
    "TOOL_ID_OPERATING_POINT",
    "DoubletDesign",
    "DoubletPerformanceMap",
    "EspDesign",
    "OperatingPoint",
    "OperatingPointReport",
    "OperatingPointRequest",
    "PerformanceMapRequest",
    "ResolvedDoublet",
    "build_performance_map",
    "curve_runout_and_bep",
    "doublet_operating_point",
    "doublet_performance_map",
    "map_cache_key",
    "map_flows",
    "operating_point",
    "resolve_doublet",
]
