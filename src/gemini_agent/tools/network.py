"""TL-23 ``pipe_heat_loss``: district-network pipe heat loss and temperature drop (RC-09).

* ``ΔT_pipe = q' L / (ṁ c_p)``; q' (W/m) does not depend on flow, so the drop is largest at the
  minimum flow — both are reported (RC-09; Guidebook p.211–214).
* Buried pre-insulated pipe, layered resistances per metre of pipe::

      q' = (T_fluid − T_ground) / (R_ins + R_soil),
      R_ins  = ln(r_ins / r_pipe) / (2π k_ins)            (cylindrical insulation layer)
      R_soil = arccosh(H / r_ins) / (2π k_soil)           (cylinder buried at centre depth H)

  PUR k_ins ≈ 0.026 W/(m·K) (RC-09). Soil conductivity, burial depth and insulation thickness
  are not on RC-09: the caller must supply them (no defaults are invented). The arccosh form
  holds the ground surface at T_ground: default the DB-34c annual-mean air temperature (the
  surface boundary), not the shallow-ground value.
* Network length is not given by the organisers ⇒ without a pipe description the tool falls
  back to the RC-09 default losses = 10 % of demand [A] (NET-LOSS-FRAC) and flags the missing
  input; the nominal trunk length 5 km [A] (NET-TRUNK-M) is reported for costing.

The RC-09 example "0.55 K/km buried for a pre-insulated 6″ pipe at 300 gpm, 150 °F" states only
the result and cannot be reproduced from the card's own insulation data (documented discrepancy,
see the test module); the p.213 example (≈ 32 W/m) is bracketed by plausible inputs.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, StrictModel, ToolResult
from gemini_agent.schemas.config import AssumptionsRegister, load_config

TOOL_ID = "TL-23"
PUR_CONDUCTIVITY_W_PER_M_K = 0.026
"""Polyurethane foam insulation. Source: RC-09 Pipe heat loss (k ≈ 0.026 W/mK)."""
ASSUMPTION_LOSS_FRAC = "NET-LOSS-FRAC"
ASSUMPTION_TRUNK_LENGTH = "NET-TRUNK-M"
ASSUMPTION_GROUND = "DB-34c"
WATER_CP_J_PER_KG_K = 4190.0
"""Fresh network water, 4.19 kJ/(kg K) (the fresh-water value cited in the DB-33 justification)."""
J_PER_MWH = 3.6e9


def pipe_temperature_drop_k(
    q_w_per_m: float, length_m: float, mass_flow_kg_s: float, cp_j_per_kg_k: float
) -> float:
    """``ΔT = q' L / (ṁ c_p)`` (RC-09, Guidebook p.211–212)."""
    if mass_flow_kg_s <= 0 or cp_j_per_kg_k <= 0:
        raise ValueError("mass flow and c_p must be > 0")
    return q_w_per_m * length_m / (mass_flow_kg_s * cp_j_per_kg_k)


def insulation_resistance_m_k_per_w(
    pipe_outer_radius_m: float, insulation_thickness_m: float, k_insulation_w_per_m_k: float
) -> float:
    """``R_ins = ln(r_ins/r_pipe)/(2π k_ins)`` per metre of pipe."""
    if pipe_outer_radius_m <= 0 or insulation_thickness_m < 0 or k_insulation_w_per_m_k <= 0:
        raise ValueError("radius and conductivity must be > 0, thickness >= 0")
    outer = pipe_outer_radius_m + insulation_thickness_m
    return math.log(outer / pipe_outer_radius_m) / (2.0 * math.pi * k_insulation_w_per_m_k)


def soil_resistance_m_k_per_w(
    outer_radius_m: float, burial_depth_m: float, k_soil_w_per_m_k: float
) -> float:
    """``R_soil = arccosh(H/r)/(2π k_soil)``: cylinder with its centre at depth H (per metre)."""
    if burial_depth_m <= outer_radius_m:
        raise ValueError("burial depth (to the pipe centre) must exceed the outer radius")
    if k_soil_w_per_m_k <= 0:
        raise ValueError("soil conductivity must be > 0")
    return math.acosh(burial_depth_m / outer_radius_m) / (2.0 * math.pi * k_soil_w_per_m_k)


def buried_pipe_heat_loss_w_per_m(
    fluid_temperature_degc: float,
    ground_surface_temperature_degc: float,
    *,
    pipe_outer_radius_m: float,
    insulation_thickness_m: float,
    burial_depth_m: float,
    k_soil_w_per_m_k: float,
    k_insulation_w_per_m_k: float = PUR_CONDUCTIVITY_W_PER_M_K,
) -> float:
    """``q' = ΔT / (R_ins + R_soil)`` for one buried pre-insulated pipe (RC-09, p.213–214)."""
    r_ins = insulation_resistance_m_k_per_w(
        pipe_outer_radius_m, insulation_thickness_m, k_insulation_w_per_m_k
    )
    r_soil = soil_resistance_m_k_per_w(
        pipe_outer_radius_m + insulation_thickness_m, burial_depth_m, k_soil_w_per_m_k
    )
    return (fluid_temperature_degc - ground_surface_temperature_degc) / (r_ins + r_soil)


class PipeGeometry(StrictModel):
    """Buried pre-insulated pipe; all values must come from the network design."""

    pipe_outer_radius_m: float = Field(gt=0)
    insulation_thickness_m: float = Field(ge=0)
    burial_depth_m: float = Field(gt=0, description="Ground surface to pipe centre.")
    k_soil_w_per_m_k: float = Field(gt=0)
    k_insulation_w_per_m_k: float = Field(default=PUR_CONDUCTIVITY_W_PER_M_K, gt=0)


class PipeHeatLossRequest(StrictModel):
    """Input of TL-23 (one pipe run; call per supply/return pipe)."""

    fluid_temperature_degc: float
    annual_heat_demand_mwh: float = Field(ge=0, description="For the loss fraction / fallback.")
    design_mass_flow_kg_s: float | None = Field(default=None, gt=0)
    min_mass_flow_kg_s: float | None = Field(default=None, gt=0)
    cp_j_per_kg_k: float = Field(default=WATER_CP_J_PER_KG_K, gt=0, description="Network water.")
    length_m: float | None = Field(default=None, gt=0, description="None ⇒ missing input.")
    geometry: PipeGeometry | None = None
    ground_temperature_degc: float | None = Field(
        default=None,
        description="Surface boundary of the buried-pipe model; None ⇒ DB-34c annual mean air.",
    )

    @model_validator(mode="after")
    def _check_flows(self) -> PipeHeatLossRequest:
        if (
            self.design_mass_flow_kg_s is not None
            and self.min_mass_flow_kg_s is not None
            and self.min_mass_flow_kg_s > self.design_mass_flow_kg_s
        ):
            raise ValueError("min_mass_flow_kg_s must be <= design_mass_flow_kg_s")
        return self


class PipeHeatLossReport(StrictModel):
    """Output of TL-23."""

    method: Literal["layered_resistance", "default_fraction"]
    length_m: float = Field(description="Given length, else the NET-TRUNK-M default.")
    ground_temperature_degc: float
    heat_loss_w_per_m: float | None
    temperature_drop_design_k: float | None
    temperature_drop_min_flow_k: float | None
    annual_heat_loss_mwh: float
    loss_fraction_of_demand: float | None
    missing_inputs: list[str]


def _register_float(register: AssumptionsRegister, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"assumption {assumption_id} must be a number, got {value!r}")
    return float(value)


def pipe_heat_loss(
    request: PipeHeatLossRequest, *, config_dir: Path | str | None = None
) -> ToolResult[PipeHeatLossReport]:
    """TL-23 ``pipe_heat_loss``: layered-resistance q' and ΔT, or the 10 % default (RC-09)."""
    register = load_config("assumptions", config_dir)
    ground = request.ground_temperature_degc
    assumption_ids: list[str] = []
    if ground is None:
        db34c = register.get(ASSUMPTION_GROUND).value
        mean_air = db34c.get("annual_mean_air_degc") if isinstance(db34c, dict) else None
        if not isinstance(mean_air, int | float):
            raise TypeError("DB-34c must hold a numeric annual_mean_air_degc")
        ground = float(mean_air)
        assumption_ids.append(ASSUMPTION_GROUND)
    missing = []
    if request.length_m is None:
        missing.append("network pipe length")
    if request.geometry is None:
        missing.append("pipe geometry, insulation, burial depth and soil conductivity")
    length = request.length_m
    if length is None:
        length = _register_float(register, ASSUMPTION_TRUNK_LENGTH)
        assumption_ids.append(ASSUMPTION_TRUNK_LENGTH)
    demand = request.annual_heat_demand_mwh
    q = dt_design = dt_min = None
    if request.geometry is not None and request.length_m is not None:
        method: Literal["layered_resistance", "default_fraction"] = "layered_resistance"
        g = request.geometry
        q = buried_pipe_heat_loss_w_per_m(
            request.fluid_temperature_degc,
            ground,
            pipe_outer_radius_m=g.pipe_outer_radius_m,
            insulation_thickness_m=g.insulation_thickness_m,
            burial_depth_m=g.burial_depth_m,
            k_soil_w_per_m_k=g.k_soil_w_per_m_k,
            k_insulation_w_per_m_k=g.k_insulation_w_per_m_k,
        )
        if request.design_mass_flow_kg_s is not None:
            dt_design = pipe_temperature_drop_k(
                q, length, request.design_mass_flow_kg_s, request.cp_j_per_kg_k
            )
        if request.min_mass_flow_kg_s is not None:
            dt_min = pipe_temperature_drop_k(
                q, length, request.min_mass_flow_kg_s, request.cp_j_per_kg_k
            )
        annual_loss = q * length * 8760.0 * 3600.0 / J_PER_MWH
        fraction = annual_loss / demand if demand > 0 else None
    else:
        method = "default_fraction"
        fraction = _register_float(register, ASSUMPTION_LOSS_FRAC)
        assumption_ids.append(ASSUMPTION_LOSS_FRAC)
        annual_loss = fraction * demand
    report = PipeHeatLossReport(
        method=method,
        length_m=length,
        ground_temperature_degc=ground,
        heat_loss_w_per_m=q,
        temperature_drop_design_k=dt_design,
        temperature_drop_min_flow_k=dt_min,
        annual_heat_loss_mwh=annual_loss,
        loss_fraction_of_demand=fraction,
        missing_inputs=missing,
    )
    if method == "layered_resistance":
        summary = (
            f"pipe loss {q:.1f} W/m over {length:.0f} m: {annual_loss:.0f} MWh/yr"
            + (f" ({100 * fraction:.1f} % of demand)" if fraction is not None else "")
            + (f"; ΔT {dt_design:.2f} K design" if dt_design is not None else "")
            + (f", {dt_min:.2f} K at min flow" if dt_min is not None else "")
        )
    else:
        summary = (
            f"MISSING INPUT network ({'; '.join(missing)}): default losses "
            f"{100 * fraction:.0f} % of demand = {annual_loss:.0f} MWh/yr; trunk {length:.0f} m [A]"
        )
    return ToolResult[PipeHeatLossReport](
        data=report,
        summary=summary[:SUMMARY_MAX_CHARS],
        warnings=[f"MISSING INPUT: {m}" for m in missing],
        assumption_ids=assumption_ids,
        sources=["RC-09", "spec §6 TL-23"],
    )


__all__ = [
    "PUR_CONDUCTIVITY_W_PER_M_K",
    "TOOL_ID",
    "PipeGeometry",
    "PipeHeatLossReport",
    "PipeHeatLossRequest",
    "buried_pipe_heat_loss_w_per_m",
    "insulation_resistance_m_k_per_w",
    "pipe_heat_loss",
    "pipe_temperature_drop_k",
    "soil_resistance_m_k_per_w",
]
