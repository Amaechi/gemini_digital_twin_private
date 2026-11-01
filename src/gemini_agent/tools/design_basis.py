"""TL-01 ``load_design_basis``: fixed inputs DB-01…19 plus derived quantities DB-20…29.

The fixed inputs come from ``config/design_basis.yaml`` (never re-typed here); [A] defaults come
from ``config/assumptions.yaml``. The derived quantities are the sanity envelope the verifier
checks (spec §2.2). The small physics helpers below are public so that the subsurface tools
(WP-05) can reuse them instead of duplicating the equations.

Sources: spec §2.1–2.2; RC-01 (hydrostatic pressure, radial-Darcy PI, drawdown);
RC-02 (Gringarten–Sauty breakthrough time); RC-08 (associated-gas properties).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated

from pydantic import Field

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import AssumptionsRegister, DesignBasis, load_config

TOOL_ID = "TL-01"

# --------------------------------------------------------------------------- constants
G_M_S2 = 9.81
"""Gravity. Source: RC-01 item 2 (``P = ρ g z``, g = 9.81) [P]."""
PA_PER_BAR = 1.0e5
SECONDS_PER_HOUR = 3600.0
SECONDS_PER_YEAR = 365.25 * 86400.0
"""Julian year, used to express breakthrough times in years."""
M2_PER_MILLIDARCY = 9.869e-16
"""Source: RC-01 inputs line (1 mD = 9.869e-16 m²)."""
M3S_PER_PA_TO_M3H_PER_BAR = SECONDS_PER_HOUR * PA_PER_BAR
"""PI[m³/h/bar] = PI[m³/s/Pa] × 3.6e8 (RC-01 item 3, GEMINI ``well_flow.py``)."""

MOLAR_VOLUME_NM3_PER_KMOL = 22.414
"""Ideal-gas molar volume at 0 °C, 1.01325 bar. Source: RC-08 associated gas."""
MOLAR_MASS_CH4_KG_PER_KMOL = 16.04
"""Source: RC-08 associated gas."""
MOLAR_MASS_CO2_KG_PER_KMOL = 44.01
"""Source: RC-08 associated gas."""
LHV_CH4_MJ_PER_NM3 = 35.8
"""Lower heating value of methane. Source: RC-08 associated gas."""

ASSUMPTION_BRINE_CP = "DB-33"
ASSUMPTION_WELL_GEOMETRY = "DB-38"
ASSUMPTION_AQUIFER_RHOC = "SUB-RHOC"
ASSUMPTION_INJECTIVITY = "SUB-II"

MAX_REPORT_POINTS = 10
"""Upper bound on the number of spacings / depths a request may ask for."""
PositiveFloat = Annotated[float, Field(gt=0)]


# --------------------------------------------------------------------------- physics helpers
def temperature_at_depth_degc(
    depth_m: float, surface_temperature_degc: float, gradient_k_per_km: float
) -> float:
    """Linear geothermal gradient ``T(z) = T_surface + gradient·z`` (DB-20, DB-29; RC-01)."""
    return surface_temperature_degc + gradient_k_per_km * depth_m / 1000.0


def hydrostatic_pressure_barg(depth_m: float, density_kg_m3: float) -> float:
    """Gauge pressure of a static brine column ``P = ρ g z`` (DB-21). Source: RC-01 item 2."""
    return density_kg_m3 * G_M_S2 * depth_m / PA_PER_BAR


def mass_flow_kg_s(flow_m3h: float, density_kg_m3: float) -> float:
    """Volumetric to mass flow (DB-22)."""
    return flow_m3h / SECONDS_PER_HOUR * density_kg_m3


def brine_heat_mw(
    flow_m3h: float, density_kg_m3: float, cp_j_per_kg_k: float, delta_t_k: float
) -> float:
    """Heat extracted from the brine ``Q = ṁ c_p ΔT`` (DB-23, DB-24)."""
    return mass_flow_kg_s(flow_m3h, density_kg_m3) * cp_j_per_kg_k * delta_t_k / 1.0e6


def productivity_index_m3h_per_bar(
    permeability_md: float,
    thickness_m: float,
    viscosity_pa_s: float,
    drainage_radius_m: float,
    wellbore_radius_m: float,
    skin: float = 0.0,
) -> float:
    """Steady radial-Darcy PI (DB-25).

    ``PI = 2π k h / (μ [ln(r_e/r_w) + S])``. Source: RC-01 item 3 (DiPippo eq 4.2–4.4).
    """
    denominator = math.log(drainage_radius_m / wellbore_radius_m) + skin
    if denominator <= 0.0:
        raise ValueError("ln(r_e/r_w) + S must be positive")
    k_m2 = permeability_md * M2_PER_MILLIDARCY
    pi_m3s_per_pa = 2.0 * math.pi * k_m2 * thickness_m / (viscosity_pa_s * denominator)
    return pi_m3s_per_pa * M3S_PER_PA_TO_M3H_PER_BAR


def breakthrough_time_yr(
    thickness_m: float,
    spacing_m: float,
    flow_m3h: float,
    rho_c_aquifer_j_per_m3_k: float,
    rho_c_water_j_per_m3_k: float,
) -> float:
    """Doublet thermal-breakthrough time (DB-26).

    ``t_b = π h D² (ρC)_a / (3 (ρC)_w Q)``. Source: RC-02 eq 1 (Gringarten & Sauty 1975).
    """
    if flow_m3h <= 0.0:
        raise ValueError("flow must be positive")
    flow_m3s = flow_m3h / SECONDS_PER_HOUR
    t_b_s = (
        math.pi
        * thickness_m
        * spacing_m**2
        * rho_c_aquifer_j_per_m3_k
        / (3.0 * rho_c_water_j_per_m3_k * flow_m3s)
    )
    return t_b_s / SECONDS_PER_YEAR


class GasMixProperties(StrictModel):
    """Associated-gas mixture properties (DB-28). Source: RC-08 associated gas."""

    density_kg_per_nm3: float
    lhv_mj_per_nm3: float
    lhv_mj_per_kg: float
    co2_when_burned_kg_per_nm3: float = Field(description="Combustion CO₂ + inherent CO₂.")
    co2_when_burned_kg_per_gj: float = Field(description="Per GJ LHV, incl. inherent CO₂.")


def gas_mix_properties(ch4_mol_frac: float, co2_mol_frac: float) -> GasMixProperties:
    """CH₄/CO₂ mix: density, LHV and CO₂ when burned (each CH₄ → one CO₂). Source: RC-08."""
    molar_mass = ch4_mol_frac * MOLAR_MASS_CH4_KG_PER_KMOL + co2_mol_frac * (
        MOLAR_MASS_CO2_KG_PER_KMOL
    )
    density = molar_mass / MOLAR_VOLUME_NM3_PER_KMOL
    lhv_nm3 = ch4_mol_frac * LHV_CH4_MJ_PER_NM3
    co2_nm3 = (ch4_mol_frac + co2_mol_frac) * MOLAR_MASS_CO2_KG_PER_KMOL / MOLAR_VOLUME_NM3_PER_KMOL
    return GasMixProperties(
        density_kg_per_nm3=density,
        lhv_mj_per_nm3=lhv_nm3,
        lhv_mj_per_kg=lhv_nm3 / density,
        co2_when_burned_kg_per_nm3=co2_nm3,
        co2_when_burned_kg_per_gj=co2_nm3 / (lhv_nm3 / 1000.0),
    )


# --------------------------------------------------------------------------- I/O schemas
class DesignBasisRequest(StrictModel):
    """Inputs of ``load_design_basis``: the points at which DB-26 and DB-29 are reported."""

    breakthrough_spacings_m: list[PositiveFloat] = Field(
        default=[1000.0, 1200.0],
        min_length=1,
        max_length=MAX_REPORT_POINTS,
        description="Well spacings at reservoir depth for DB-26 (spec: 1.0 and 1.2 km).",
    )
    report_depths_m: list[PositiveFloat] = Field(
        default=[1500.0, 2000.0, 3000.0, 3200.0, 3500.0],
        min_length=1,
        max_length=MAX_REPORT_POINTS,
        description="Depths for the DB-29 temperature table.",
    )


class BreakthroughPoint(StrictModel):
    """Breakthrough time at one well spacing (DB-26), at maximum flow per doublet."""

    spacing_m: float = Field(gt=0)
    flow_m3h: float = Field(gt=0)
    breakthrough_time_yr: float


class DepthTemperature(StrictModel):
    """Temperature at one depth (DB-29)."""

    depth_m: float = Field(gt=0)
    temperature_degc: float


class DerivedDesignQuantities(StrictModel):
    """Derived quantities DB-20…29 (spec §2.2), computed from the design basis."""

    reservoir_temperature_degc: float = Field(description="DB-20 at mid-depth.")
    heating_supply_above_reservoir_k: float = Field(
        description="DB-16 supply − DB-20; > 0 ⇒ every MWh needs a top-up (spec §3.1 fact 1)."
    )
    hydrostatic_pressure_barg: float = Field(description="DB-21 ρgz at mid-depth.")
    static_wellhead_pressure_barg: float = Field(
        description="P_res − ρgz: shut-in wellhead pressure of a full brine column."
    )
    production_wellhead_pressure_at_max_flow_barg: float = Field(
        description="P_res − Q_max/PI − ρgz: flowing wellhead pressure (friction neglected)."
    )
    esp_required: bool = Field(
        description=(
            "Flowing wellhead pressure at Q_max ≤ 0 ⇒ the producer cannot deliver max flow "
            "unaided ⇒ ESP needed (DB-21)."
        )
    )
    injection_wellhead_pressure_at_min_flow_barg: float = Field(
        description="P_res + Q_min/II − ρgz (friction neglected; II = PI, SUB-II)."
    )
    injection_wellhead_pressure_at_max_flow_barg: float = Field(
        description="P_res + Q_max/II − ρgz (friction neglected; II = PI, SUB-II)."
    )
    injection_pump_required: bool = Field(
        description="Injection wellhead pressure > 0 at every active flow (DB-21)."
    )
    mass_flow_at_max_flow_kg_s: float = Field(description="DB-22.")
    max_heat_per_doublet_mw: float = Field(description="DB-23 at max flow and max brine ΔT.")
    min_heat_per_active_doublet_mw: float = Field(description="DB-24 at min flow, max ΔT.")
    max_geothermal_heat_all_doublets_mw: float = Field(description="count_max × DB-23.")
    min_doublets_for_peak_brine_heat: int = Field(
        description=(
            "ceil(DB-18 peak / DB-23): doublets whose brine-side heat alone covers the configured "
            "heating peak (spec §3.1 fact 2); a temperature top-up is still needed (fact 1)."
        )
    )
    productivity_index_m3h_per_bar: float = Field(description="DB-25 (DB-38 skin, r_w, r_e).")
    drawdown_at_max_flow_bar: float = Field(description="DB-25: Q_max / PI.")
    breakthrough: list[BreakthroughPoint] = Field(description="DB-26 at max flow.")
    associated_gas_at_max_flow_nm3h: float = Field(description="DB-27 GLR × Q_max.")
    associated_gas_fuel_at_max_flow_mw: float = Field(description="DB-27 LHV fuel power.")
    ch4_kg_per_m3_brine: float = Field(description="DB-27.")
    co2_kg_per_m3_brine: float = Field(description="DB-27 (dissolved CO₂).")
    gas_mix: GasMixProperties = Field(description="DB-28.")
    temperature_at_depth: list[DepthTemperature] = Field(description="DB-29.")


class DesignBasisReport(StrictModel):
    """Output of TL-01: the fixed design basis, the [A] inputs used and the derived envelope."""

    fixed: DesignBasis
    brine_cp_j_per_kg_k: float = Field(description="DB-33 [A].")
    skin: float = Field(description="DB-38 [A].")
    wellbore_radius_m: float = Field(description="DB-38 [A].")
    drainage_radius_m: float = Field(description="DB-38 [A].")
    aquifer_rho_c_j_per_m3_k: float = Field(description="SUB-RHOC [A].")
    derived: DerivedDesignQuantities


# --------------------------------------------------------------------------- tool
def _assumption_float(register: AssumptionsRegister, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"assumption {assumption_id} must be a number, got {value!r}")
    return float(value)


def derive_quantities(
    basis: DesignBasis,
    assumptions: AssumptionsRegister,
    request: DesignBasisRequest | None = None,
) -> DesignBasisReport:
    """Compute DB-20…29 from an already-loaded design basis and assumptions register."""
    request = request or DesignBasisRequest()
    res, brine, dbl = basis.reservoir, basis.brine, basis.doublets

    cp = _assumption_float(assumptions, ASSUMPTION_BRINE_CP)
    geometry = assumptions.get(ASSUMPTION_WELL_GEOMETRY).value
    if not isinstance(geometry, dict):
        raise TypeError(f"assumption {ASSUMPTION_WELL_GEOMETRY} must be a mapping")
    skin = float(geometry["skin"])
    r_w = float(geometry["wellbore_radius_m"])
    r_e = float(geometry["drainage_radius_m"])
    rho_c_aquifer = _assumption_float(assumptions, ASSUMPTION_AQUIFER_RHOC)
    injectivity_rule = assumptions.get(ASSUMPTION_INJECTIVITY).value
    if injectivity_rule != "equal_to_pi":
        raise ValueError(f"unsupported {ASSUMPTION_INJECTIVITY} value {injectivity_rule!r}")

    t_res = temperature_at_depth_degc(
        res.mid_depth_m, res.surface_temperature_degc, res.geothermal_gradient_k_per_km
    )
    p_hydro = hydrostatic_pressure_barg(res.mid_depth_m, brine.density_kg_m3)
    p_static_wh = res.pressure_barg - p_hydro
    pi = productivity_index_m3h_per_bar(
        res.permeability_md, res.gross_thickness_m, brine.viscosity_pa_s, r_e, r_w, skin
    )
    injectivity = pi  # SUB-II: II = PI (RC-01 item 4)
    p_prod_max = p_static_wh - dbl.flow_max_m3h / pi
    p_inj_min = p_static_wh + dbl.flow_min_active_m3h / injectivity
    p_inj_max = p_static_wh + dbl.flow_max_m3h / injectivity

    q_max = brine_heat_mw(dbl.flow_max_m3h, brine.density_kg_m3, cp, dbl.brine_cooling_max_k)
    q_min = brine_heat_mw(dbl.flow_min_active_m3h, brine.density_kg_m3, cp, dbl.brine_cooling_max_k)
    rho_c_water = brine.density_kg_m3 * cp
    gas = gas_mix_properties(brine.gas_ch4_mol_frac, brine.gas_co2_mol_frac)
    glr = brine.gas_liquid_ratio_nm3_per_m3
    gas_nm3h = glr * dbl.flow_max_m3h

    derived = DerivedDesignQuantities(
        reservoir_temperature_degc=t_res,
        heating_supply_above_reservoir_k=basis.heating.supply_temperature_degc - t_res,
        hydrostatic_pressure_barg=p_hydro,
        static_wellhead_pressure_barg=p_static_wh,
        production_wellhead_pressure_at_max_flow_barg=p_prod_max,
        esp_required=p_prod_max <= 0.0,
        injection_wellhead_pressure_at_min_flow_barg=p_inj_min,
        injection_wellhead_pressure_at_max_flow_barg=p_inj_max,
        injection_pump_required=p_inj_min > 0.0,
        mass_flow_at_max_flow_kg_s=mass_flow_kg_s(dbl.flow_max_m3h, brine.density_kg_m3),
        max_heat_per_doublet_mw=q_max,
        min_heat_per_active_doublet_mw=q_min,
        max_geothermal_heat_all_doublets_mw=dbl.count_max * q_max,
        min_doublets_for_peak_brine_heat=math.ceil(basis.heating.peak_mw / q_max),
        productivity_index_m3h_per_bar=pi,
        drawdown_at_max_flow_bar=dbl.flow_max_m3h / pi,
        breakthrough=[
            BreakthroughPoint(
                spacing_m=spacing,
                flow_m3h=dbl.flow_max_m3h,
                breakthrough_time_yr=breakthrough_time_yr(
                    res.gross_thickness_m, spacing, dbl.flow_max_m3h, rho_c_aquifer, rho_c_water
                ),
            )
            for spacing in request.breakthrough_spacings_m
        ],
        associated_gas_at_max_flow_nm3h=gas_nm3h,
        associated_gas_fuel_at_max_flow_mw=gas_nm3h / SECONDS_PER_HOUR * gas.lhv_mj_per_nm3,
        ch4_kg_per_m3_brine=glr
        * brine.gas_ch4_mol_frac
        * MOLAR_MASS_CH4_KG_PER_KMOL
        / MOLAR_VOLUME_NM3_PER_KMOL,
        co2_kg_per_m3_brine=glr
        * brine.gas_co2_mol_frac
        * MOLAR_MASS_CO2_KG_PER_KMOL
        / MOLAR_VOLUME_NM3_PER_KMOL,
        gas_mix=gas,
        temperature_at_depth=[
            DepthTemperature(
                depth_m=z,
                temperature_degc=temperature_at_depth_degc(
                    z, res.surface_temperature_degc, res.geothermal_gradient_k_per_km
                ),
            )
            for z in request.report_depths_m
        ],
    )
    return DesignBasisReport(
        fixed=basis,
        brine_cp_j_per_kg_k=cp,
        skin=skin,
        wellbore_radius_m=r_w,
        drainage_radius_m=r_e,
        aquifer_rho_c_j_per_m3_k=rho_c_aquifer,
        derived=derived,
    )


def _summary(report: DesignBasisReport) -> str:
    d, dbl = report.derived, report.fixed.doublets
    # Fixed-size t_b part (range over the requested spacings) keeps the summary ≤ 300 chars.
    shortest = min(d.breakthrough, key=lambda b: b.spacing_m)
    longest = max(d.breakthrough, key=lambda b: b.spacing_m)
    tb = f"{shortest.breakthrough_time_yr:.3g} yr@{shortest.spacing_m / 1000:.3g} km"
    if longest is not shortest:
        tb += f"…{longest.breakthrough_time_yr:.3g} yr@{longest.spacing_m / 1000:.3g} km"
    return (
        f"T_res {d.reservoir_temperature_degc:.1f}°C (supply "
        f"{report.fixed.heating.supply_temperature_degc:.0f}°C); P_hyd "
        f"{d.hydrostatic_pressure_barg:.1f} vs P_res {report.fixed.reservoir.pressure_barg:.0f} "
        f"bar(g) ⇒ ESP {'yes' if d.esp_required else 'no'}, inj pump "
        f"{'yes' if d.injection_pump_required else 'no'}; heat/doublet "
        f"{d.min_heat_per_active_doublet_mw:.2f}–{d.max_heat_per_doublet_mw:.2f} MW "
        f"({dbl.flow_min_active_m3h:.0f}–{dbl.flow_max_m3h:.0f} m³/h, "
        f"{dbl.brine_cooling_max_k:.0f} K); PI {d.productivity_index_m3h_per_bar:.1f} m³/h/bar; "
        f"t_b {tb}; gas {d.associated_gas_fuel_at_max_flow_mw:.2f} MW/doublet"
    )


def load_design_basis(
    request: DesignBasisRequest | None = None,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[DesignBasisReport]:
    """TL-01: load DB-01…19 and derive DB-20…29 (spec §2.1–2.2).

    Reads ``design_basis`` and ``assumptions`` via ``load_config``. Sources: RC-01, RC-02, RC-08.
    """
    basis = load_config("design_basis", config_dir)
    assumptions = load_config("assumptions", config_dir)
    report = derive_quantities(basis, assumptions, request)
    warnings: list[str] = []
    if report.derived.heating_supply_above_reservoir_k > 0.0:
        warnings.append(
            "Reservoir temperature is below the heating supply temperature: "
            "geothermal heat alone cannot reach supply; a top-up is required."
        )
    return ToolResult[DesignBasisReport](
        data=report,
        summary=_summary(report),
        warnings=warnings,
        assumption_ids=[
            ASSUMPTION_BRINE_CP,
            ASSUMPTION_WELL_GEOMETRY,
            ASSUMPTION_AQUIFER_RHOC,
            ASSUMPTION_INJECTIVITY,
        ],
        sources=["spec §2.1–2.2", "RC-01", "RC-02", "RC-08"],
    )


__all__ = [
    "TOOL_ID",
    "BreakthroughPoint",
    "DepthTemperature",
    "DerivedDesignQuantities",
    "DesignBasisReport",
    "DesignBasisRequest",
    "GasMixProperties",
    "breakthrough_time_yr",
    "brine_heat_mw",
    "derive_quantities",
    "gas_mix_properties",
    "hydrostatic_pressure_barg",
    "load_design_basis",
    "mass_flow_kg_s",
    "productivity_index_m3h_per_bar",
    "temperature_at_depth_degc",
]
