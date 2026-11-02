"""TL-05 ``doublet_operating_point`` and TL-06 ``doublet_performance_map`` (spec §6; RC-01).

GEMINI's ``ProducerWell`` solves the flow for a given ESP frequency and wellhead pressure (the
producer flow is an OUTPUT). TL-05 inverts it: it solves the ESP frequency (``brentq``, 30–70 Hz)
that delivers a target flow, then runs GEMINI ``ProducerWell`` at that frequency and checks that
GEMINI returns the target flow within 1 %. Per operating point it also gives

* the wellhead temperature, walking GEMINI ``DPDT`` *up* from the reservoir temperature against
  the undisturbed geothermal gradient (RC-01 eq 1 and envelope note, U = 10 W/(m² K) in GEMINI);
* the ESP Δp and power both ways: GEMINI's stage curve and the challenge rule
  ``P_shaft = Q Δp / 0.70`` with ``P_el = P_shaft / (η_motor η_VFD)`` (RC-01 eq 6, DB-13, DB-40);
* the brine bubble point from Henry's law (RC-01 item 9, Sander 2023 constants; sea water basis,
  pure water as second bound), the ESP-intake setpoint 1.2 · p_b, NPSH available (RC-01 item 8),
  the gas-release depths (flash-depth method, DiPippo eq 4.16–4.20) and p_b as explicit evidence
  for the DesignVerifier (``EspEvidence`` = WP-09 ``EspPoint``);
* the injector wellhead pressure and injection-pump power from GEMINI ``InjectorWell`` with the
  brine PVT block and friction as a loss for the downward flow (RC-01 eq 12).

Injection equals production (DB-14). The ESP is sized once per design at the maximum flow
(SUB-ESP); TL-06 evaluates TL-05 on a 50…300 m³/h grid and caches the map under ``runs/``.

Every [A] value comes from ``config/assumptions.yaml`` through
:func:`gemini_agent.tools.resource.load_subsurface_basis`.

Limitations (stated in PROGRESS.md): GEMINI's steady U = 10 W/(m² K) against the undisturbed
gradient overstates the wellbore heat loss at part load (conservative T_wh); the brine PVT has
constant properties (the colder injected brine is not denser or more viscous); the heat the ESP
adds to the brine is neglected; GEMINI's ESP has no flow affinity, so its curve power is only a
comparison. Card status: RC-01 marks the DiPippo equations ``VERIFY``.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
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
    MOLAR_VOLUME_NM3_PER_KMOL,
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
from gemini_simulator.well_flow import CELSIUS_TO_KELVIN, _build_dpdt, _build_esp

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
HENRY_REFERENCE_TEMPERATURE_K = 298.15
"""RC-01 item 9: reference temperature of the van 't Hoff form H(T) = H° exp[B (1/T − 1/T°)]."""
HENRY_CONSTANTS = {
    "sea_water": {"CH4": (1.2e-5, 1100.0), "CO2": (2.8e-4, 2600.0)},
    "pure_water": {"CH4": (1.4e-5, 1600.0), "CO2": (3.4e-4, 2300.0)},
}
"""RC-01 item 9: (H° in mol m⁻³ Pa⁻¹, B in K) per solvent and gas. Source: Sander, Compilation of
Henry's law constants v5.0.0, Atmos. Chem. Phys. 23, 10901–12440 (2023); Burkholder et al.
2019 [L]."""
HENRY_CHECKED_RANGE_DEGC = (25.0, 100.0)
"""RC-01 item 9: range over which CH₄ p_b is stated to rise with T (van 't Hoff extrapolation)."""
LINESHAFT_ECONOMICAL_MAX_M = 730.0
"""RC-01 item 11: lineshaft pumps are economical for setting depths below ≈ 730–800 m [L]."""
MAP_STEP_M3H = 10.0
"""TL-06 default flow step."""
MAP_CACHE_VERSION = "wp05-3"
"""Bump when the operating-point algorithm changes: it is part of the cache key."""
DEFAULT_CACHE_DIR = REPO_ROOT / "runs" / "cache" / "doublet_maps"
"""Performance maps are cached here (``runs/`` is git-ignored)."""
_FLOW_EPS_M3H = 1e-9
_DRY_INTAKE_BARG = 1e-6
"""GEMINI DPDT clamps pressures at 0.001 Pa: an intake at or below this is a dry pump."""
_POWER_FIELDS = frozenset(
    {
        "esp_hydraulic_power_w",
        "esp_shaft_power_w",
        "esp_electric_power_w",
        "esp_curve_shaft_power_w",
        "esp_curve_efficiency_frac",
        "injection_pump_hydraulic_power_w",
        "injection_pump_shaft_power_w",
        "injection_pump_electric_power_w",
        "total_pump_electric_power_w",
    }
)
"""Withheld on infeasible points, so no infeasible power can be dispatched by mistake."""
_CODE_FINGERPRINT_MODULES = (
    "gemini_agent.tools.doublet",
    "gemini_agent.tools.resource",
    "gemini_agent.tools.design_basis",
    "gemini_simulator.well_flow",
    "gemini_simulator.well_components",
    "gemini_simulator.nodal_solver",
    "gemini_model.well.pressure_drop",
    "gemini_model.well.correlation.darcyweisbach",
    "gemini_model.well.correlation.temperaturedrop",
    "gemini_model.pump.esp",
    "gemini_model.reservoir.inflow_performance",
    "gemini_model.fluid.pvt_config",
    "gemini_model.fluid.pvt_water_stp",
)
"""Source files whose bytes are part of the TL-06 cache key (stale maps are never reused)."""


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
    injectivity_factor: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description="II = factor × PI (cold-injection sensitivity, WP-11); None: SUB-II (II = PI).",
    )
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


class GasPartialPressure(StrictModel):
    """Henry's-law partial pressure of one dissolved gas (RC-01 item 9)."""

    gas: Literal["CH4", "CO2"]
    concentration_mol_m3: float = Field(description="c_i = GLR · y_i / 0.022414.")
    henry_mol_m3_pa: float = Field(description="H_i(T) = H° exp[B (1/T − 1/298.15)].")
    partial_pressure_bara: float = Field(description="c_i / H_i(T).")


class BubblePointEstimate(StrictModel):
    """Bubble point for one set of Henry constants (sea water or pure water)."""

    solvent: Literal["sea_water", "pure_water"]
    gases: list[GasPartialPressure]
    bubble_pressure_bara: float = Field(description="p_b = Σ c_i / H_i(T).")


class BubblePoint(StrictModel):
    """Brine bubble point and ESP-intake setpoint (RC-01 item 9; SUB-PBUB)."""

    temperature_degc: float
    gas_liquid_ratio_nm3_per_m3: float
    basis: BubblePointEstimate = Field(description="SUB-PBUB basis (sea water).")
    second_bound: BubblePointEstimate = Field(description="SUB-PBUB second bound (pure water).")
    margin_factor: float = Field(description="SUB-PBUB [A]: setpoint = factor · p_b.")
    atmospheric_pressure_bara: float
    release_pressure_bara: float = Field(description="p_b of the basis.")
    release_pressure_barg: float = Field(description="p_b of the basis, gauge.")
    intake_setpoint_bara: float = Field(description="VER-ESP-PB: intake ≥ factor · p_b.")
    intake_setpoint_barg: float
    notes: list[str] = Field(default_factory=list)


class EspEvidence(StrictModel):
    """ESP evidence for the DesignVerifier: the fields of WP-09 ``EspPoint`` (PH-18, PH-19).

    ``release_pressure_barg`` is the computed bubble point p_b, passed explicitly so the verifier
    does not fall back to its register default.
    """

    name: str
    intake_pressure_barg: float
    release_pressure_barg: float
    npsh_available_m: float | None = None
    npsh_required_m: float | None = Field(default=None, description="Not given (SUB-NPSH).")


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
    bubble_point: BubblePoint
    npsh_margin_m: float
    vapour_pressure_bound_bara: float
    vapour_pressure_bound_valid_to_degc: float
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
    """TL-05 result at one flow.

    ``feasible=False`` lists the reasons; pressures and Δp are kept for diagnosis but every power
    field is ``None`` (gate dispatch on ``feasible``).
    """

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
    esp_curve_efficiency_frac: float | None = Field(
        default=None, description="Q Δp / GEMINI curve power (GEMINI form: ∝ 60/f at fixed Q)."
    )
    esp_flow_to_bep_ratio: float | None = Field(
        default=None, description="Q / (Q_BEP · f/60): flow vs the affinity-scaled BEP."
    )
    release_pressure_barg: float | None = Field(
        default=None, description="Bubble point p_b (Henry's law, SUB-PBUB), gauge."
    )
    intake_setpoint_barg: float | None = Field(
        default=None, description="ESP-intake setpoint 1.2 · p_b (VER-ESP-PB), gauge."
    )
    intake_margin_bar: float | None = Field(
        default=None, description="P_intake − setpoint (≥ 0 required)."
    )
    npsh_available_m: float | None = None
    gas_release_depth_m: float | None = Field(
        default=None, description="Where the unboosted flowing pressure equals p_b."
    )
    setting_depth_margin_m: float | None = Field(
        default=None, description="ESP setting depth − gas-release depth (> 0 required)."
    )
    tubing_gas_release_depth_m: float | None = Field(
        default=None, description="Above this depth gas leaves solution in the tubing."
    )
    esp_evidence: EspEvidence | None = Field(
        default=None, description="For the DesignVerifier (WP-09 EspPoint), p_b explicit."
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
        """Interpolate a numeric point field linearly between the two bracketing grid points.

        Raises ``ValueError`` outside the grid or when a bracketing point is infeasible, so an
        infeasible gap in the map is never bridged.
        """
        flows = [p.flow_m3h for p in self.points]
        if not flows[0] - _FLOW_EPS_M3H <= flow_m3h <= flows[-1] + _FLOW_EPS_M3H:
            raise ValueError(f"flow {flow_m3h} m³/h outside the map range")
        index = int(np.searchsorted(flows, flow_m3h - _FLOW_EPS_M3H))
        if abs(flows[index] - flow_m3h) <= _FLOW_EPS_M3H:
            bracket = [self.points[index]]
        else:
            bracket = [self.points[index - 1], self.points[index]]
        if not all(p.feasible for p in bracket):
            raise ValueError(f"flow {flow_m3h} m³/h is next to an infeasible map point")
        values = [getattr(p, field) for p in bracket]
        if any(not isinstance(v, int | float) or isinstance(v, bool) for v in values):
            raise ValueError(f"field {field!r} is not numeric at {flow_m3h} m³/h")
        if len(bracket) == 1:
            return float(values[0])
        return float(np.interp(flow_m3h, [p.flow_m3h for p in bracket], values))


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
    temperature_k = temperature_degc + CELSIUS_TO_KELVIN
    ambient_k = temperature_k if ambient_degc is None else ambient_degc + CELSIUS_TO_KELVIN
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


def wellhead_temperature_degc(doublet: ResolvedDoublet, flow_m3h: float) -> float:
    """Walk GEMINI DPDT *up* from T_res, one cell per ≤ 100 m, ambient = formation T (RC-01)."""
    n_cells = max(1, math.ceil(doublet.depth_m / WELLBORE_CELL_M))
    edges = np.linspace(doublet.depth_m, 0.0, n_cells + 1)
    temperature = doublet.reservoir_temperature_degc
    pressure = doublet.reservoir_pressure_barg
    for bottom, top in itertools.pairwise(edges):
        ambient = _formation_temperature_degc(doublet, 0.5 * float(bottom + top))
        output = _walk(doublet, float(bottom - top), pressure, temperature, flow_m3h, "up", ambient)
        temperature = output["temperature_output"] - CELSIUS_TO_KELVIN
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


# --------------------------------------------------------------------------- bubble point
def henry_constant_mol_m3_pa(h0_mol_m3_pa: float, b_k: float, temperature_k: float) -> float:
    """Van 't Hoff form ``H(T) = H° exp[B (1/T − 1/298.15)]`` (RC-01 item 9)."""
    return h0_mol_m3_pa * math.exp(
        b_k * (1.0 / temperature_k - 1.0 / HENRY_REFERENCE_TEMPERATURE_K)
    )


def dissolved_gas_mol_m3(gas_liquid_ratio_nm3_per_m3: float, mol_frac: float) -> float:
    """``c_i = GLR · y_i / 0.022414`` mol/m³ (Nm³ at 0 °C, 1.01325 bar; RC-01 item 9, RC-08)."""
    return gas_liquid_ratio_nm3_per_m3 * mol_frac / (MOLAR_VOLUME_NM3_PER_KMOL / 1000.0)


def bubble_point_estimate(
    temperature_degc: float,
    gas_liquid_ratio_nm3_per_m3: float,
    ch4_mol_frac: float,
    co2_mol_frac: float,
    solvent: str,
) -> BubblePointEstimate:
    """``p_b = Σ c_i / H_i(T)`` for one set of Henry constants (RC-01 item 9; ideal gas)."""
    if solvent not in HENRY_CONSTANTS:
        raise ValueError(f"unknown solvent {solvent!r}; expected {sorted(HENRY_CONSTANTS)}")
    temperature_k = temperature_degc + CELSIUS_TO_KELVIN
    gases = []
    for gas, mol_frac in (("CH4", ch4_mol_frac), ("CO2", co2_mol_frac)):
        h0, b_k = HENRY_CONSTANTS[solvent][gas]
        henry = henry_constant_mol_m3_pa(h0, b_k, temperature_k)
        concentration = dissolved_gas_mol_m3(gas_liquid_ratio_nm3_per_m3, mol_frac)
        gases.append(
            GasPartialPressure(
                gas=gas,
                concentration_mol_m3=concentration,
                henry_mol_m3_pa=henry,
                partial_pressure_bara=concentration / henry / PA_PER_BAR,
            )
        )
    return BubblePointEstimate(
        solvent=solvent,
        gases=gases,
        bubble_pressure_bara=sum(g.partial_pressure_bara for g in gases),
    )


def brine_bubble_point(basis: SubsurfaceBasis, temperature_degc: float) -> BubblePoint:
    """Return the bubble point and ESP-intake setpoint of the DB-09 brine (SUB-PBUB)."""
    estimates = [
        bubble_point_estimate(
            temperature_degc,
            basis.gas_liquid_ratio_nm3_per_m3,
            basis.gas_ch4_mol_frac,
            basis.gas_co2_mol_frac,
            solvent,
        )
        for solvent in (basis.bubble_point_basis, basis.bubble_point_second_bound)
    ]
    p_b = estimates[0].bubble_pressure_bara
    setpoint = basis.bubble_point_margin_factor * p_b
    low, high = HENRY_CHECKED_RANGE_DEGC
    notes = []
    if not low <= temperature_degc <= high:
        notes.append(
            f"Bubble point at {temperature_degc:.1f} °C is outside the {low:g}–{high:g} °C range "
            "checked on RC-01 item 9 (single-parameter van 't Hoff extrapolation)."
        )
    atmospheric = basis.atmospheric_pressure_bara
    return BubblePoint(
        temperature_degc=temperature_degc,
        gas_liquid_ratio_nm3_per_m3=basis.gas_liquid_ratio_nm3_per_m3,
        basis=estimates[0],
        second_bound=estimates[1],
        margin_factor=basis.bubble_point_margin_factor,
        atmospheric_pressure_bara=atmospheric,
        release_pressure_bara=p_b,
        release_pressure_barg=p_b - atmospheric,
        intake_setpoint_bara=setpoint,
        intake_setpoint_barg=setpoint - atmospheric,
        notes=notes,
    )


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
    # At the reservoir temperature: CH₄ p_b rises with T over 25–100 °C (RC-01), so the
    # slightly cooler intake makes this the conservative end.
    bubble = brine_bubble_point(basis, conditions.temperature_degc)
    notes.extend(bubble.notes)
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
            # SUB-II: II = PI (RC-01 item 4); a factor < 1 is the cold-injection sensitivity.
            injectivity_index_m3h_per_bar=pi * (design.injectivity_factor or 1.0),
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
            bubble_point=bubble,
            npsh_margin_m=basis.npsh_margin_m,
            vapour_pressure_bound_bara=basis.vapour_pressure_bound_bara,
            vapour_pressure_bound_valid_to_degc=basis.vapour_pressure_bound_valid_to_degc,
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
        setpoint = bubble.intake_setpoint_barg

        def margin(z: float) -> float:
            return _unboosted_pressure_barg(draft, q_max, z, p_wf) - setpoint

        if margin(depth) < 0.0:
            setting_depth = reservoir_top
            notes.append(
                f"No setting depth keeps the intake ≥ {setpoint:.1f} bar(g) (1.2·p_b) at "
                f"{q_max:g} m³/h (P_wf {p_wf:.1f} bar(g)); pump set at the reservoir top "
                f"{reservoir_top:.0f} m."
            )
        elif margin(0.0) >= 0.0:
            setting_depth = basis.esp_setting_depth_step_m
        else:
            z_release = _root_depth(margin, 0.0, depth)
            step = basis.esp_setting_depth_step_m
            setting_depth = min(math.ceil(z_release / step - 1e-9) * step, reservoir_top)
    if setting_depth < LINESHAFT_ECONOMICAL_MAX_M:
        notes.append(
            f"ESP setting depth {setting_depth:.0f} m is in the lineshaft-economical range "
            f"(< ≈{LINESHAFT_ECONOMICAL_MAX_M:.0f} m, RC-01 item 11); the GEMINI ESP is kept."
        )
    return build(stages, float(setting_depth))


# --------------------------------------------------------------------------- TL-05 core
def _infeasible(base: dict, reasons: list[str], notes: list[str]) -> OperatingPoint:
    """Return an infeasible point: pressures and Δp kept for diagnosis, no power numbers."""
    kept = {key: value for key, value in base.items() if key not in _POWER_FIELDS}
    return OperatingPoint(**kept, feasible=False, reasons=reasons, notes=notes)


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
        esp_flow_to_bep_ratio=flow_m3h
        / (esp.best_efficiency_flow_m3h * frequency / esp.frequency_nominal_hz),
    )
    if curve_power > 0 and hydraulic / curve_power > 1.0:
        notes.append(
            f"GEMINI ESP curve power is below Q·Δp at {frequency:.1f} Hz (no flow affinity in "
            "the curve); plant power uses Q·Δp/0.70."
        )

    # Wellhead temperature (DPDT up from T_res) and the GEMINI ProducerWell check.
    t_wellhead = wellhead_temperature_degc(doublet, flow_m3h)
    base.update(
        wellhead_temperature_degc=t_wellhead,
        wellbore_cooling_k=doublet.reservoir_temperature_degc - t_wellhead,
    )
    if t_wellhead > doublet.vapour_pressure_bound_valid_to_degc:
        # Below this temperature any WHP ≥ 0 bar(g) is above saturation (bound = 1 atm at 100 °C).
        notes.append(
            f"Wellhead {t_wellhead:.1f} °C > {doublet.vapour_pressure_bound_valid_to_degc:g} °C: "
            "flashing not checked (no saturation-pressure data); hold the WHP above saturation."
        )
    reasons: list[str] = []

    # ESP setting checks: intake vs 1.2·p_b, NPSH, gas-release depths (RC-01 items 8–9).
    p_bubble = doublet.bubble_point.release_pressure_barg
    setpoint = doublet.bubble_point.intake_setpoint_barg
    base.update(
        release_pressure_barg=p_bubble,
        intake_setpoint_barg=setpoint,
        intake_margin_bar=hyd["p_intake"] - setpoint,
    )
    if hyd["p_intake"] <= _DRY_INTAKE_BARG:
        reasons.append(
            f"ESP set above the dynamic fluid level at {flow_m3h:g} m³/h (GEMINI clamps the intake "
            "pressure at 0): set the pump deeper"
        )
    elif hyd["p_intake"] < setpoint:
        reasons.append(
            f"ESP intake {hyd['p_intake']:.1f} bar(g) is below the setpoint {setpoint:.1f} bar(g) "
            f"= 1.2 × bubble point {p_bubble:.1f} bar(g) (SUB-PBUB, VER-ESP-PB): set the pump "
            "deeper"
        )
    if doublet.reservoir_temperature_degc <= doublet.vapour_pressure_bound_valid_to_degc:
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
        notes.append(
            f"Brine above {doublet.vapour_pressure_bound_valid_to_degc:g} °C: vapour-pressure "
            "bound not valid (SUB-NPSH), NPSH not evaluated."
        )
    base["esp_evidence"] = EspEvidence(
        name=f"producer ESP @ {flow_m3h:g} m3/h",
        intake_pressure_barg=hyd["p_intake"],
        release_pressure_barg=p_bubble,
        npsh_available_m=base.get("npsh_available_m"),
    )
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
            f"Wellhead {upstream_whp:.1f} < p_b {p_bubble:.1f} bar(g): gas leaves solution above "
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
    if t_injection < doublet.surface_temperature_degc:
        notes.append(
            f"Injection at {t_injection:.1f} °C is below the {doublet.surface_temperature_degc:g} "
            "°C surface temperature (DB-04): pass a smaller brine_cooling_k for this design."
        )
    base["brine_heat_mw"] = brine_heat_mw(
        flow_m3h, doublet.density_kg_m3, doublet.cp_j_per_kg_k, doublet.brine_cooling_k
    )
    try:
        injector = _injector(doublet, flow_m3h, t_injection)
    except ValueError as exc:
        reasons.append(f"GEMINI InjectorWell did not converge: {exc}")
    else:
        base.update(injector)
        injector_whp = injector["injector_wellhead_pressure_barg"]
        if injector_whp <= 0.0:
            notes.append(
                f"Injector takes the flow under gravity (required WHP {injector_whp:.2f} bar(g)): "
                "choke the injection."
            )
        if injector_whp < p_bubble:
            notes.append(
                f"Injector wellhead {injector_whp:.1f} < p_b {p_bubble:.1f} bar(g): dissolved gas "
                "can leave solution at the injector top unless the loop is degassed or held "
                "above p_b (C7, SUB-PBUB)."
            )
        if not reasons:
            base["total_pump_electric_power_w"] = (
                base["esp_electric_power_w"] + injector["injection_pump_electric_power_w"]
            )
    if reasons:
        return _infeasible(base, reasons, notes)
    return OperatingPoint(**base, feasible=True, notes=notes)


def producer_well_parameters(
    doublet: ResolvedDoublet, frequency_hz: float, target_m3h: float
) -> dict:
    """GEMINI ``producer_well`` parameters of a resolved doublet at one ESP frequency.

    Brine ``pvt`` (GM-01), explicit SUB-WELL roughness (never GEMINI's 0.01 m default), the
    flow-scaled ESP (SUB-ESP) at its setting depth, the formation temperature at mid-depth as the
    wellbore ambient, and a flow bracket of 0.5–1.5 × target (capped at the curve run-out).
    Shared by TL-05 and the TL-09 config generator so both run identical GEMINI wells.
    """
    esp = doublet.esp
    return {
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


def injector_well_parameters(doublet: ResolvedDoublet) -> dict:
    """GEMINI ``injector_well`` parameters of a resolved doublet (RC-01 eq 12; DB-14, DB-40).

    Same reservoir pressure as the producer, injectivity index (SUB-II), brine ``pvt``, SUB-WELL
    roughness, friction as a loss for the downward flow, and the injection pump at the
    wire-to-water efficiency 0.70 × η_motor × η_VFD. Shared by TL-05 and TL-09.
    """
    return {
        # Same reservoir as the producer (DB-14; spec §7.2: injector at the reservoir pressure).
        "reservoir_pressure_bar": doublet.reservoir_pressure_barg,
        "productivity_index_m3h_per_bar": doublet.injectivity_index_m3h_per_bar,
        "trajectory_table": _trajectory_table(doublet),
        "roughness_m": doublet.well_roughness_m,
        "pvt": doublet.pvt,
        "friction_opposes_flow": True,
        "pressure_bracket_bar": INJECTION_PRESSURE_BRACKET_BAR,
        # Power only; CO₂ of the electricity is WP-08's (GEMINI's emission factor default 0).
        "injector_pump": {"efficiency_factor": doublet.wire_to_water_efficiency_frac},
    }


def _producer_well_flow_m3h(
    doublet: ResolvedDoublet,
    frequency_hz: float,
    wellhead_pressure_barg: float,
    wellhead_temperature_degc: float,
    target_m3h: float,
) -> float:
    """Run GEMINI ``ProducerWell`` (nodal solve) at the solved ESP frequency; return its flow."""
    well = ProducerWell()
    well.update_parameters(producer_well_parameters(doublet, frequency_hz, target_m3h))
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
    well.update_parameters(injector_well_parameters(doublet))
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
    bottomhole = _bottomhole_pressure_barg(doublet, flow_m3h, "injection_reservoir")
    if whp <= INJECTION_PRESSURE_BRACKET_BAR[0]:
        # GEMINI returns its bracket floor when the column alone over-delivers; report the signed
        # requirement instead (≤ 0: the column free-falls, a choke or flow control is needed).
        column = _walk(doublet, doublet.depth_m, 0.0, injection_temperature_degc, -flow_m3h, "down")
        whp = float(bottomhole - column["pressure_output"] / PA_PER_BAR)
    pump_required = bool(whp > suction)
    dp = whp - suction if pump_required else 0.0
    hydraulic = flow_m3h / SECONDS_PER_HOUR * dp * PA_PER_BAR
    friction = _walk(doublet, doublet.depth_m, whp, injection_temperature_degc, -flow_m3h, "down")
    return {
        "injector_bottomhole_pressure_barg": bottomhole,
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
        f"≥ 1.2·p_b {point.intake_setpoint_barg:.1f} bar(g); T_wh "
        f"{point.wellhead_temperature_degc:.1f}°C; inj WHP "
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


@functools.cache
def code_fingerprint() -> str:
    """SHA-256 of the source of every module the map depends on (WP-05 tools and GEMINI)."""
    digest = hashlib.sha256()
    for name in _CODE_FINGERPRINT_MODULES:
        digest.update(name.encode("utf-8"))
        digest.update(Path(importlib.import_module(name).__file__).read_bytes())
    return digest.hexdigest()


def map_cache_key(doublet: ResolvedDoublet, flows: list[float]) -> str:
    """Hash of everything the map depends on (inputs, sizing, grid, version, source code)."""
    payload = {
        "version": MAP_CACHE_VERSION,
        "code": code_fingerprint(),
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
    "HENRY_CONSTANTS",
    "MAP_CACHE_VERSION",
    "TOOL_ID_MAP",
    "TOOL_ID_OPERATING_POINT",
    "BubblePoint",
    "BubblePointEstimate",
    "DoubletDesign",
    "DoubletPerformanceMap",
    "EspDesign",
    "EspEvidence",
    "GasPartialPressure",
    "OperatingPoint",
    "OperatingPointReport",
    "OperatingPointRequest",
    "PerformanceMapRequest",
    "ResolvedDoublet",
    "brine_bubble_point",
    "bubble_point_estimate",
    "build_performance_map",
    "code_fingerprint",
    "curve_runout_and_bep",
    "dissolved_gas_mol_m3",
    "doublet_operating_point",
    "doublet_performance_map",
    "henry_constant_mol_m3_pa",
    "injector_well_parameters",
    "map_cache_key",
    "map_flows",
    "operating_point",
    "producer_well_parameters",
    "resolve_doublet",
    "wellhead_temperature_degc",
]
