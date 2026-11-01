"""TL-03 ``reservoir_conditions`` and TL-04 ``productivity_index`` (spec §6; RC-01).

Also home of :class:`SubsurfaceBasis`, the inputs every subsurface tool (TL-03…TL-08) shares,
resolved once from ``config/`` (design basis, hard limits, assumptions register, cost
assumptions). Values are never re-typed here; the physics helpers come from
``tools/design_basis.py`` (WP-04).

Sources: RC-01 eq 1–4 (temperature and pressure at depth, radial-Darcy PI, drawdown);
GEMINI ``gemini_model.reservoir.reservoir_pressuredrop.bottomhole_skin_dp`` (PI cross-check).
Card status: RC-01 marks the DiPippo equations ``VERIFY``.

Pressure at depth follows DB-07/RC-01 eq 2 literally: the given 250 bar(g) at DB-01 and ``ρ g z``
elsewhere, so a depth sweep sees a 2.6 bar step at 2500 m (``ρ g z`` = 252.6 bar there).
"""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import Field

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import AssumptionsRegister, load_config
from gemini_agent.tools.design_basis import (
    M2_PER_MILLIDARCY,
    PA_PER_BAR,
    SECONDS_PER_HOUR,
    hydrostatic_pressure_barg,
    productivity_index_m3h_per_bar,
    temperature_at_depth_degc,
)
from gemini_model.reservoir.reservoir_pressuredrop import bottomhole_skin_dp

TOOL_ID_CONDITIONS = "TL-03"
TOOL_ID_PRODUCTIVITY = "TL-04"

ASSUMPTION_BRINE_CP = "DB-33"
ASSUMPTION_WELL_GEOMETRY = "DB-38"
ASSUMPTION_PUMP_EFFICIENCY = "DB-40"
ASSUMPTION_AQUIFER_RHOC = "SUB-RHOC"
ASSUMPTION_INJECTIVITY = "SUB-II"
ASSUMPTION_WELL = "SUB-WELL"
ASSUMPTION_PRODUCER_WHP = "SUB-PWH"
ASSUMPTION_BUBBLE_PRESSURE = "SUB-PBUB"
ASSUMPTION_NPSH = "SUB-NPSH"
ASSUMPTION_ESP = "SUB-ESP"
ASSUMPTION_SUCTION = "SUB-PSUC"
ASSUMPTION_SPACING = "SUB-SPACING"
ASSUMPTION_VERIFIER_ESP_MARGIN = "VER-ESP-MARGIN"
"""WP-09's margin on p_b; must equal SUB-PBUB's (TL-05 sizes, the verifier checks, same 1.2)."""

BUBBLE_POINT_SOLVENTS = frozenset({"sea_water", "pure_water"})
"""Henry's-law constant sets on RC-01 item 9."""
DEPTH_TOLERANCE_M = 1e-6
"""A depth within this of DB-01 uses the given reservoir pressure DB-05 (else hydrostatic)."""
MAX_REPORT_FLOWS = 12
"""Upper bound on the number of flows a TL-04 request may tabulate."""


# --------------------------------------------------------------------------- shared basis
class SubsurfaceBasis(StrictModel):
    """Inputs shared by TL-03…TL-08, read from ``config/`` (each field cites its DB/register id)."""

    design_depth_m: float = Field(description="DB-01 reservoir mid-depth TVD.")
    thickness_m: float = Field(description="DB-02 gross thickness h.")
    gradient_k_per_km: float = Field(description="DB-03.")
    surface_temperature_degc: float = Field(description="DB-04.")
    design_pressure_barg: float = Field(description="DB-05 reservoir pressure at DB-01.")
    permeability_md: float = Field(description="DB-06.")
    density_kg_m3: float = Field(description="DB-08 brine density.")
    viscosity_pa_s: float = Field(description="DB-08 brine viscosity.")
    cp_j_per_kg_k: float = Field(description="DB-33 brine c_p [A].")
    skin: float = Field(description="DB-38 [A].")
    wellbore_radius_m: float = Field(description="DB-38 [A].")
    drainage_radius_m: float = Field(description="DB-38 [A].")
    injectivity_rule: str = Field(description="SUB-II [A] ('equal_to_pi').")
    heating_supply_degc: float = Field(description="DB-16.")
    doublet_count_min: int = Field(description="DB-10 (limits.yaml).")
    doublet_count_max: int = Field(description="DB-10 (limits.yaml).")
    flow_min_m3h: float = Field(description="DB-11 minimum per active doublet (limits.yaml).")
    flow_max_m3h: float = Field(description="DB-11 maximum per doublet (limits.yaml).")
    brine_cooling_max_k: float = Field(description="DB-12 (limits.yaml).")
    pump_hydraulic_efficiency_frac: float = Field(description="DB-13/DB-40.")
    motor_efficiency_frac: float = Field(description="DB-40 [A].")
    vfd_efficiency_frac: float = Field(description="DB-40 [A].")
    aquifer_rho_c_j_per_m3_k: float = Field(description="SUB-RHOC [A].")
    well_inner_diameter_m: float = Field(description="SUB-WELL [A].")
    well_roughness_m: float = Field(description="SUB-WELL [A] (RC-01 eq 5 steel).")
    producer_wellhead_pressure_barg: float = Field(description="SUB-PWH [A].")
    gas_liquid_ratio_nm3_per_m3: float = Field(description="DB-09 GLR.")
    gas_ch4_mol_frac: float = Field(description="DB-09.")
    gas_co2_mol_frac: float = Field(description="DB-09.")
    bubble_point_basis: str = Field(description="SUB-PBUB: Henry constants used for p_b.")
    bubble_point_second_bound: str = Field(description="SUB-PBUB: Henry constants of the bound.")
    bubble_point_margin_factor: float = Field(description="SUB-PBUB [A]: intake ≥ factor · p_b.")
    npsh_margin_m: float = Field(description="SUB-NPSH [A] (RC-01 item 8).")
    vapour_pressure_bound_bara: float = Field(description="SUB-NPSH [A].")
    vapour_pressure_bound_valid_to_degc: float = Field(description="SUB-NPSH [A].")
    atmospheric_pressure_bara: float = Field(description="SUB-NPSH.")
    esp_stage_head_coeff: list[float] = Field(description="SUB-ESP (GEMINI ESP polynomial).")
    esp_stage_power_coeff: list[float] = Field(description="SUB-ESP (GEMINI ESP polynomial).")
    esp_frequency_min_hz: float = Field(description="SUB-ESP (RC-01 item 7).")
    esp_frequency_max_hz: float = Field(description="SUB-ESP (RC-01 item 7).")
    esp_frequency_nominal_hz: float = Field(description="SUB-ESP (GEMINI curve reference).")
    esp_setting_depth_step_m: float = Field(description="SUB-ESP.")
    injection_pump_suction_barg: float = Field(description="SUB-PSUC [A].")
    well_spacing_m: float = Field(description="SUB-SPACING [A] (RC-02 default).")
    analysis_period_yr: float = Field(description="cost_assumptions analysis_period_yr (ECO-01).")

    @property
    def wire_to_water_efficiency_frac(self) -> float:
        """Pump × motor × VFD efficiency (DB-40): electrical = hydraulic / this."""
        return (
            self.pump_hydraulic_efficiency_frac
            * self.motor_efficiency_frac
            * self.vfd_efficiency_frac
        )


SUBSURFACE_ASSUMPTION_IDS = [
    ASSUMPTION_BRINE_CP,
    ASSUMPTION_WELL_GEOMETRY,
    ASSUMPTION_PUMP_EFFICIENCY,
    ASSUMPTION_AQUIFER_RHOC,
    ASSUMPTION_INJECTIVITY,
    ASSUMPTION_WELL,
    ASSUMPTION_PRODUCER_WHP,
    ASSUMPTION_BUBBLE_PRESSURE,
    ASSUMPTION_NPSH,
    ASSUMPTION_ESP,
    ASSUMPTION_SUCTION,
    ASSUMPTION_SPACING,
]
"""Every register id :func:`load_subsurface_basis` reads."""


def _number(register: AssumptionsRegister, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"assumption {assumption_id} must be a number, got {value!r}")
    return float(value)


def _mapping(register: AssumptionsRegister, assumption_id: str) -> dict:
    value = register.get(assumption_id).value
    if not isinstance(value, dict):
        raise TypeError(f"assumption {assumption_id} must be a mapping")
    return value


def load_subsurface_basis(config_dir: Path | str | None = None) -> SubsurfaceBasis:
    """Read every input of the subsurface tools from ``config/`` (no values in code)."""
    basis = load_config("design_basis", config_dir)
    limits = load_config("limits", config_dir)
    register = load_config("assumptions", config_dir)
    costs = load_config("cost_assumptions", config_dir)
    res, brine = basis.reservoir, basis.brine
    geometry = _mapping(register, ASSUMPTION_WELL_GEOMETRY)
    pumps = _mapping(register, ASSUMPTION_PUMP_EFFICIENCY)
    well = _mapping(register, ASSUMPTION_WELL)
    npsh = _mapping(register, ASSUMPTION_NPSH)
    esp = _mapping(register, ASSUMPTION_ESP)
    bubble = _mapping(register, ASSUMPTION_BUBBLE_PRESSURE)
    if bubble.get("method") != "henry_law":
        raise ValueError(f"{ASSUMPTION_BUBBLE_PRESSURE}: only method 'henry_law' is supported")
    for key in ("basis", "second_bound"):
        if bubble.get(key) not in BUBBLE_POINT_SOLVENTS:
            raise ValueError(
                f"{ASSUMPTION_BUBBLE_PRESSURE} {key} must be one of {sorted(BUBBLE_POINT_SOLVENTS)}"
            )
    margin = float(bubble["intake_margin_factor"])
    try:
        verifier_margin = _number(register, ASSUMPTION_VERIFIER_ESP_MARGIN)
    except KeyError:
        verifier_margin = margin
    if verifier_margin != margin:
        raise ValueError(
            f"{ASSUMPTION_BUBBLE_PRESSURE} margin {margin:g} differs from "
            f"{ASSUMPTION_VERIFIER_ESP_MARGIN} {verifier_margin:g}: TL-05 and the verifier must "
            "agree"
        )
    if well.get("trajectory") != "vertical":
        raise ValueError(f"{ASSUMPTION_WELL}: only a vertical trajectory is supported")
    injectivity_rule = register.get(ASSUMPTION_INJECTIVITY).value
    if injectivity_rule != "equal_to_pi":
        raise ValueError(f"unsupported {ASSUMPTION_INJECTIVITY} value {injectivity_rule!r}")
    if float(pumps["pump_hydraulic_efficiency_frac"]) != limits.pump_hydraulic_efficiency_frac:
        raise ValueError(f"{ASSUMPTION_PUMP_EFFICIENCY} pump efficiency disagrees with limits.yaml")
    return SubsurfaceBasis(
        design_depth_m=res.mid_depth_m,
        thickness_m=res.gross_thickness_m,
        gradient_k_per_km=res.geothermal_gradient_k_per_km,
        surface_temperature_degc=res.surface_temperature_degc,
        design_pressure_barg=res.pressure_barg,
        permeability_md=res.permeability_md,
        density_kg_m3=brine.density_kg_m3,
        viscosity_pa_s=brine.viscosity_pa_s,
        cp_j_per_kg_k=_number(register, ASSUMPTION_BRINE_CP),
        skin=float(geometry["skin"]),
        wellbore_radius_m=float(geometry["wellbore_radius_m"]),
        drainage_radius_m=float(geometry["drainage_radius_m"]),
        injectivity_rule=str(injectivity_rule),
        heating_supply_degc=basis.heating.supply_temperature_degc,
        doublet_count_min=limits.doublet_count_min,
        doublet_count_max=limits.doublet_count_max,
        flow_min_m3h=limits.flow_min_active_m3h,
        flow_max_m3h=limits.flow_max_m3h,
        brine_cooling_max_k=limits.brine_cooling_max_k,
        pump_hydraulic_efficiency_frac=limits.pump_hydraulic_efficiency_frac,
        motor_efficiency_frac=float(pumps["motor_efficiency_frac"]),
        vfd_efficiency_frac=float(pumps["vfd_efficiency_frac"]),
        aquifer_rho_c_j_per_m3_k=_number(register, ASSUMPTION_AQUIFER_RHOC),
        well_inner_diameter_m=float(well["inner_diameter_m"]),
        well_roughness_m=float(well["roughness_m"]),
        producer_wellhead_pressure_barg=_number(register, ASSUMPTION_PRODUCER_WHP),
        gas_liquid_ratio_nm3_per_m3=brine.gas_liquid_ratio_nm3_per_m3,
        gas_ch4_mol_frac=brine.gas_ch4_mol_frac,
        gas_co2_mol_frac=brine.gas_co2_mol_frac,
        bubble_point_basis=str(bubble["basis"]),
        bubble_point_second_bound=str(bubble["second_bound"]),
        bubble_point_margin_factor=margin,
        npsh_margin_m=float(npsh["npsh_margin_m"]),
        vapour_pressure_bound_bara=float(npsh["vapour_pressure_bound_bara"]),
        vapour_pressure_bound_valid_to_degc=float(npsh["vapour_pressure_bound_valid_to_degc"]),
        atmospheric_pressure_bara=float(npsh["atmospheric_pressure_bara"]),
        esp_stage_head_coeff=[float(c) for c in esp["stage_head_coeff"]],
        esp_stage_power_coeff=[float(c) for c in esp["stage_power_coeff"]],
        esp_frequency_min_hz=float(esp["frequency_min_hz"]),
        esp_frequency_max_hz=float(esp["frequency_max_hz"]),
        esp_frequency_nominal_hz=float(esp["frequency_nominal_hz"]),
        esp_setting_depth_step_m=float(esp["setting_depth_step_m"]),
        injection_pump_suction_barg=_number(register, ASSUMPTION_SUCTION),
        well_spacing_m=_number(register, ASSUMPTION_SPACING),
        analysis_period_yr=float(costs.items["analysis_period_yr"].value),
    )


def reservoir_pressure_at_depth_barg(depth_m: float, basis: SubsurfaceBasis) -> tuple[float, str]:
    """Reservoir pressure (bar(g)) and its model at ``depth_m``.

    DB-05 (given) at the design depth DB-01; hydrostatic ``ρ g z`` elsewhere (DB-07, RC-01 eq 2).
    """
    if abs(depth_m - basis.design_depth_m) <= DEPTH_TOLERANCE_M:
        return basis.design_pressure_barg, "given"
    return hydrostatic_pressure_barg(depth_m, basis.density_kg_m3), "hydrostatic"


def formation_temperature_degc(depth_m: float, basis: SubsurfaceBasis) -> float:
    """Undisturbed formation temperature at ``depth_m`` (RC-01 eq 1, DB-03/DB-04)."""
    return temperature_at_depth_degc(
        depth_m, basis.surface_temperature_degc, basis.gradient_k_per_km
    )


# --------------------------------------------------------------------------- TL-03
class ReservoirConditionsRequest(StrictModel):
    """TL-03 input."""

    depth_m: float | None = Field(default=None, gt=0, description="TVD; None: DB-01.")


class ReservoirConditions(StrictModel):
    """TL-03 output: reservoir state at one depth."""

    depth_m: float
    temperature_degc: float = Field(description="RC-01 eq 1: T = T_surface + gradient·z.")
    pressure_barg: float = Field(description="DB-05 at DB-01, else hydrostatic (DB-07).")
    pressure_model: str = Field(description="'given' (DB-05) or 'hydrostatic' (ρ g z).")
    hydrostatic_pressure_barg: float = Field(description="ρ g z of a static brine column.")
    static_wellhead_pressure_barg: float = Field(
        description="P_res − ρ g z; ≤ 0 means the producer needs artificial lift (ESP)."
    )
    esp_required: bool
    heating_supply_gap_k: float = Field(description="DB-16 supply − T_res; > 0 needs a top-up.")
    depth_for_heating_supply_m: float = Field(
        description="Depth where T_res reaches the heating supply temperature (RC-01 eq 1)."
    )


def reservoir_conditions_at(depth_m: float, basis: SubsurfaceBasis) -> ReservoirConditions:
    """Reservoir temperature and pressure at ``depth_m`` (TL-03 core; RC-01 eq 1–2)."""
    if depth_m <= 0.0:
        raise ValueError("depth must be positive")
    pressure, model = reservoir_pressure_at_depth_barg(depth_m, basis)
    hydro = hydrostatic_pressure_barg(depth_m, basis.density_kg_m3)
    temperature = formation_temperature_degc(depth_m, basis)
    return ReservoirConditions(
        depth_m=depth_m,
        temperature_degc=temperature,
        pressure_barg=pressure,
        pressure_model=model,
        hydrostatic_pressure_barg=hydro,
        static_wellhead_pressure_barg=pressure - hydro,
        esp_required=pressure - hydro <= 0.0,
        heating_supply_gap_k=basis.heating_supply_degc - temperature,
        depth_for_heating_supply_m=(basis.heating_supply_degc - basis.surface_temperature_degc)
        / basis.gradient_k_per_km
        * 1000.0,
    )


def reservoir_conditions(
    request: ReservoirConditionsRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[ReservoirConditions]:
    """TL-03: reservoir T and P at a depth (hydrostatic if z ≠ DB-01). Source: RC-01 eq 1–2."""
    request = request or ReservoirConditionsRequest()
    basis = load_subsurface_basis(config_dir)
    depth = request.depth_m if request.depth_m is not None else basis.design_depth_m
    data = reservoir_conditions_at(depth, basis)
    warnings: list[str] = []
    if data.pressure_model == "hydrostatic":
        warnings.append(
            f"Pressure is hydrostatic ρgz (DB-07): at {basis.design_depth_m:.0f} m this gives "
            f"{hydrostatic_pressure_barg(basis.design_depth_m, basis.density_kg_m3):.1f} bar(g), "
            f"not the given {basis.design_pressure_barg:g} bar(g)."
        )
    if data.heating_supply_gap_k > 0.0:
        warnings.append(
            f"Reservoir temperature is below the {basis.heating_supply_degc:.0f} °C heating "
            "supply: geothermal heat needs a top-up (heat pump, boiler or CHP)."
        )
    summary = (
        f"z {depth:.0f} m: T_res {data.temperature_degc:.1f}°C, P_res {data.pressure_barg:.1f} "
        f"bar(g) ({data.pressure_model}); static WHP {data.static_wellhead_pressure_barg:.1f} "
        f"bar(g) ⇒ ESP {'yes' if data.esp_required else 'no'}; supply gap "
        f"{data.heating_supply_gap_k:.1f} K ({basis.heating_supply_degc:.0f}°C reached at "
        f"{data.depth_for_heating_supply_m:.0f} m)"
    )
    return ToolResult[ReservoirConditions](
        data=data,
        summary=summary,
        warnings=warnings,
        assumption_ids=[],
        sources=["RC-01", "spec §2.1 DB-01…07"],
    )


# --------------------------------------------------------------------------- TL-04
class ProductivityIndexRequest(StrictModel):
    """TL-04 input; ``None`` fields take the design basis / DB-38 values."""

    skin: float | None = Field(default=None, description="None: DB-38.")
    wellbore_radius_m: float | None = Field(default=None, gt=0, description="None: DB-38.")
    drainage_radius_m: float | None = Field(default=None, gt=0, description="None: DB-38.")
    permeability_md: float | None = Field(default=None, gt=0, description="None: DB-06.")
    thickness_m: float | None = Field(default=None, gt=0, description="None: DB-02.")
    flows_m3h: list[float] | None = Field(
        default=None,
        max_length=MAX_REPORT_FLOWS,
        description="Flows to tabulate; None: DB-11 minimum and maximum.",
    )


class DrawdownPoint(StrictModel):
    """Bottom-hole pressure change at one flow (RC-01 eq 4)."""

    flow_m3h: float
    drawdown_bar: float = Field(description="Producer P_res − P_wf = Q / PI.")
    injection_overpressure_bar: float = Field(description="Injector P_bh − P_res = Q / II.")


class ProductivityIndexReport(StrictModel):
    """TL-04 output: radial-Darcy PI and II with skin."""

    permeability_md: float
    thickness_m: float
    viscosity_pa_s: float
    skin: float
    wellbore_radius_m: float
    drainage_radius_m: float
    log_radius_ratio: float = Field(description="ln(r_e / r_w).")
    productivity_index_m3h_per_bar: float = Field(description="PI = 2π k h / (μ [ln(r_e/r_w)+S]).")
    productivity_index_m3s_per_pa: float = Field(description="Same PI in GEMINI IPR units.")
    injectivity_index_m3h_per_bar: float = Field(description="II (SUB-II: II = PI).")
    skin_share_frac: float = Field(description="S / (ln(r_e/r_w) + S): skin part of drawdown.")
    gemini_cross_check_rel_diff: float = Field(
        description="|PI(GEMINI bottomhole_skin_dp) − PI| / PI at the first tabulated flow."
    )
    drawdown: list[DrawdownPoint]


def _gemini_productivity_index_m3h_per_bar(
    flow_m3h: float,
    permeability_md: float,
    thickness_m: float,
    viscosity_pa_s: float,
    wellbore_radius_m: float,
    drainage_radius_m: float,
    skin: float,
) -> float:
    """PI from GEMINI ``bottomhole_skin_dp`` (Darcy + skin Δp at one flow), in m³/h/bar."""
    model = bottomhole_skin_dp()
    model.update_parameters(
        {
            "reservoir_pressure": 0.0,
            "reservoir_radius": drainage_radius_m,
            "reservoir_permeability": permeability_md * M2_PER_MILLIDARCY,
            "reservoir_thickness": thickness_m,
            "reservoir_top": 0.0,
        }
    )
    flow_m3s = flow_m3h / SECONDS_PER_HOUR
    model.calculate_output(
        {
            "flow": flow_m3s,
            "viscosity": viscosity_pa_s,
            "density": 0.0,
            "well_radius": wellbore_radius_m,
            "skin_factor": skin,
        },
        None,
    )
    output = model.get_output()
    dp_bar = (output["bottomhole_dp"] + output["skin_dp"]) / PA_PER_BAR
    return flow_m3h / dp_bar


def productivity_index_report(
    basis: SubsurfaceBasis, request: ProductivityIndexRequest | None = None
) -> ProductivityIndexReport:
    """TL-04 core: PI/II with skin (RC-01 eq 3–4) plus a GEMINI cross-check."""
    request = request or ProductivityIndexRequest()
    skin = basis.skin if request.skin is None else request.skin
    r_w = request.wellbore_radius_m or basis.wellbore_radius_m
    r_e = request.drainage_radius_m or basis.drainage_radius_m
    k_md = request.permeability_md or basis.permeability_md
    h = request.thickness_m or basis.thickness_m
    if r_e <= r_w:
        raise ValueError("drainage radius must exceed the wellbore radius")
    pi = productivity_index_m3h_per_bar(k_md, h, basis.viscosity_pa_s, r_e, r_w, skin)
    injectivity = pi  # SUB-II: II = PI (RC-01 item 4)
    flows = request.flows_m3h or [basis.flow_min_m3h, basis.flow_max_m3h]
    if any(q <= 0.0 for q in flows):
        raise ValueError("flows must be positive")
    log_ratio = math.log(r_e / r_w)
    gemini_pi = _gemini_productivity_index_m3h_per_bar(
        flows[0], k_md, h, basis.viscosity_pa_s, r_w, r_e, skin
    )
    return ProductivityIndexReport(
        permeability_md=k_md,
        thickness_m=h,
        viscosity_pa_s=basis.viscosity_pa_s,
        skin=skin,
        wellbore_radius_m=r_w,
        drainage_radius_m=r_e,
        log_radius_ratio=log_ratio,
        productivity_index_m3h_per_bar=pi,
        productivity_index_m3s_per_pa=pi / (SECONDS_PER_HOUR * PA_PER_BAR),
        injectivity_index_m3h_per_bar=injectivity,
        skin_share_frac=skin / (log_ratio + skin),
        gemini_cross_check_rel_diff=abs(gemini_pi - pi) / pi,
        drawdown=[
            DrawdownPoint(
                flow_m3h=q, drawdown_bar=q / pi, injection_overpressure_bar=q / injectivity
            )
            for q in flows
        ],
    )


def productivity_index(
    request: ProductivityIndexRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[ProductivityIndexReport]:
    """TL-04: radial-Darcy PI/II with skin (RC-01 eq 3–4; GEMINI ``bottomhole_skin_dp``)."""
    basis = load_subsurface_basis(config_dir)
    data = productivity_index_report(basis, request)
    warnings: list[str] = []
    if data.skin < 0.0:
        warnings.append("Negative skin (stimulated well): PI is above the undamaged value.")
    largest = max(data.drawdown, key=lambda p: p.flow_m3h)
    summary = (
        f"PI {data.productivity_index_m3h_per_bar:.2f} m³/h/bar (k {data.permeability_md:.0f} mD, "
        f"h {data.thickness_m:.0f} m, S {data.skin:g}, ln(re/rw) {data.log_radius_ratio:.2f}); "
        f"II = PI; drawdown {largest.drawdown_bar:.1f} bar and injection overpressure "
        f"{largest.injection_overpressure_bar:.1f} bar at {largest.flow_m3h:.0f} m³/h"
    )
    return ToolResult[ProductivityIndexReport](
        data=data,
        summary=summary,
        warnings=warnings,
        assumption_ids=[ASSUMPTION_WELL_GEOMETRY, ASSUMPTION_INJECTIVITY],
        sources=["RC-01", "GEMINI bottomhole_skin_dp"],
    )


__all__ = [
    "SUBSURFACE_ASSUMPTION_IDS",
    "TOOL_ID_CONDITIONS",
    "TOOL_ID_PRODUCTIVITY",
    "DrawdownPoint",
    "ProductivityIndexReport",
    "ProductivityIndexRequest",
    "ReservoirConditions",
    "ReservoirConditionsRequest",
    "SubsurfaceBasis",
    "formation_temperature_degc",
    "load_subsurface_basis",
    "productivity_index",
    "productivity_index_report",
    "reservoir_conditions",
    "reservoir_conditions_at",
    "reservoir_pressure_at_depth_barg",
]
