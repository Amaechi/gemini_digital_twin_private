"""Electric boiler (peak / back-up heat) with a constant efficiency (GM-08).

Source: spec §7.2 GM-08 (η 0.99); RC-10 "Sourced values" (DEA Electricity & DH catalogue ch. 41
"Electric boilers": η 0.99, register EB-ETA); RC-04 peaking practice (boiler in series on the
return, Guidebook p.260). See ``REFERENCE.md``.

- ``P_el = Q_th / η`` (W); losses ``P_el - Q_th``; no direct emission (grid CO₂ is accounted in
  economics, WP-08).
- Duty either from ``heat_demand_w`` or from a water flow heated to the set point:
  ``Q = ρ c_p V (T_set - T_in)`` (0 if the inlet is already at or above the set point).
- Capacity limit: ``Q_th = min(Q_demand, nominal_capacity_w)``; the unmet part is reported and,
  with a flow, the outlet stays below the set point (``T_out = T_in + Q_th / (ρ c_p V)``).
"""

import math

from gemini_model.fluid.pvt_config import liquid_properties
from gemini_model.model_abstract import StaticModel

DEFAULT_EFFICIENCY = 0.99
"""η 0.99 (spec GM-08; DEA ch. 41), register EB-ETA."""
DEFAULT_SUPPLY_TEMPERATURE_DEGC = 100.0
"""Heating supply set point 100 °C (CLAUDE.md hard limit; spec DB)."""


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _non_negative(value, name):
    value = _finite(value, name)
    if value < 0.0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _positive(value, name):
    if value is None:
        raise ValueError(f"{name} must be set")
    value = _finite(value, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


class ElectricBoiler(StaticModel):
    """Electric boiler: heat = efficiency × electricity, up to the nominal capacity (GM-08)."""

    def __init__(self):
        """Model initialization with GM-08 defaults (nominal capacity must be set)."""
        density, specific_heat = liquid_properties()
        self.parameters = {
            "nominal_capacity_w": None,  # heat output, W
            "efficiency": DEFAULT_EFFICIENCY,  # EB-ETA
            "water_temperature_out_degc": DEFAULT_SUPPLY_TEMPERATURE_DEGC,
            "water_density_kg_m3": density,
            "water_specific_heat_j_kgk": specific_heat,
        }
        self.output = {}

    def update_parameters(self, parameters):
        """Update model parameters.

        Parameters
        ----------
        parameters: dict
            Parameters dict as defined by the model.
        """
        for key, value in parameters.items():
            self.parameters[key] = value

    def initialize_state(self, x):
        """Not applicable: static model."""

    def update_state(self, u, x):
        """Not applicable: static model."""

    def calculate_output(self, u, x=None):
        """Calculate heat output, electricity use and (with a flow) the outlet temperature.

        Inputs ``u``: exactly one of ``heat_demand_w`` (W) or ``water_flow_rate_m3s`` (m³/s,
        with ``water_temperature_in_degc``); optional ``water_temperature_out_degc`` (set point,
        overrides the parameter).
        """
        p = self.parameters
        q_nom = _positive(p["nominal_capacity_w"], "nominal_capacity_w")
        efficiency = _positive(p["efficiency"], "efficiency")
        if efficiency > 1.0:
            raise ValueError(f"efficiency must be <= 1, got {efficiency}")
        t_set = _finite(
            u.get("water_temperature_out_degc", p["water_temperature_out_degc"]),
            "water_temperature_out_degc",
        )
        has_demand = u.get("heat_demand_w") is not None
        has_flow = u.get("water_flow_rate_m3s") is not None
        if has_demand == has_flow:
            raise ValueError("give exactly one of heat_demand_w or water_flow_rate_m3s")

        flow = t_in = t_out = None
        if has_flow:
            flow = _non_negative(u["water_flow_rate_m3s"], "water_flow_rate_m3s")
            t_in = _finite(u["water_temperature_in_degc"], "water_temperature_in_degc")
            rho_cp = _positive(p["water_density_kg_m3"], "water_density_kg_m3") * _positive(
                p["water_specific_heat_j_kgk"], "water_specific_heat_j_kgk"
            )
            demand = rho_cp * flow * max(t_set - t_in, 0.0)
        else:
            demand = _non_negative(u["heat_demand_w"], "heat_demand_w")

        q_th = min(demand, q_nom)
        power_el = q_th / efficiency
        if has_flow:
            t_out = t_in + q_th / (rho_cp * flow) if flow > 0.0 else t_in

        self.output = {
            "heat_output_w": q_th,
            "heat_demand_w": demand,
            "unmet_heat_w": max(demand - q_th, 0.0),
            "load_ratio": q_th / q_nom,
            "efficiency": efficiency,
            "losses_w": power_el - q_th,
            "water_flow_rate_m3s": flow,
            "water_temperature_in_degc": t_in,
            "water_temperature_out_degc": t_out if has_flow else t_set,
            "power_el": power_el,
            "power_th": q_th,
            "emission": 0.0,
        }
