"""Free cooling: water-side economiser (cooling tower + plate HEX) (GM-07).

Source: RC-07 (``data/knowledge/reference_cards/RC-07_heat_rejection_free_cooling_climate.md``,
copied in ``REFERENCE.md``); spec §3.3 and §7.2 GM-07; seasonal performance factor 24-53
from the Stockholm ATES-GSHP paper [L].

The economiser can deliver chilled-loop water at ``T_fc = T_wb + approach_tower +
approach_HEX`` (defaults 4.5 K [A] and 2 K [A]).

- ``full``: ``T_fc <= T_supply`` (T_wb <= 20 - 4.5 - 2 = 13.5 °C): the whole load is free
  cooled (up to ``nominal_capacity_w`` if given).
- ``partial``: ``T_supply < T_fc < T_return`` (T_wb + 6.5 < 26 °C): the economiser
  pre-cools the chiller return from T_return to T_fc, i.e. the fraction
  ``(T_return - T_fc) / (T_return - T_supply)`` of the load; the chiller covers the rest
  (``residual_cooling_w``) with inlet water at ``chilled_water_temperature_out_degc``
  (the economiser outlet; wire it to the chiller's ``chilled_water_temperature_in_degc``).
- ``off``: ``T_fc >= T_return``.

Power = tower fan + pumps ≈ ``aux_power_fraction`` (2 % [A]) of the heat moved, i.e. an
instantaneous performance factor of 50 (inside the 24-53 seasonal range).
``heat_rejected_w`` = free cooling + power_el (all energy leaves at the tower).
Ambient wet-bulb is an input (the agent side computes it with Stull, TL-02b). If the tower
water (T_wb + approach_tower) would be at or below 0 °C a validity warning is returned
(freeze protection is not modelled). If ``heat_rejected_w`` is wired to a separate
``heat_rejection`` unit, set this model's ``aux_power_fraction`` to 0 to count the fan once.
"""

import math

from gemini_model.fluid.pvt_config import liquid_properties
from gemini_model.model_abstract import StaticModel


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
    value = _finite(value, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def free_cooling_mode(economiser_temperature_degc, supply_degc, return_degc):
    """Return 'full', 'partial' or 'off' for the economiser water temperature (RC-07)."""
    if economiser_temperature_degc <= supply_degc:
        return "full"
    if economiser_temperature_degc < return_degc:
        return "partial"
    return "off"


class FreeCooling(StaticModel):
    """Water-side economiser with full and partial (pre-cooling) free cooling (RC-07)."""

    def __init__(self):
        """Model initialization with RC-07 defaults."""
        density, specific_heat = liquid_properties()
        self.parameters = {
            "tower_approach_k": 4.5,  # [A] 4-5 K
            "hex_approach_k": 2.0,  # [A] plate HEX approach (RC-07; RC-03 2-3 K)
            "aux_power_fraction": 0.02,  # [A] fan + pumps, of the heat moved
            "nominal_capacity_w": None,  # None -> no limit
            "chilled_water_temperature_out_degc": 20.0,  # supply set point (spec)
            "chilled_water_temperature_in_degc": 26.0,  # return, DB-32 [A]
            "chilled_water_density_kg_m3": density,
            "chilled_water_specific_heat_j_kgk": specific_heat,
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
        """Calculate free cooling, residual chiller load and fan/pump power.

        Inputs ``u``: ``ambient_wet_bulb_degc``; exactly one of ``cooling_load_w`` (W) or
        ``chilled_water_flow_rate_m3s`` (m³/s, load = ρ c_p V (T_return - T_supply));
        optional ``chilled_water_temperature_in_degc`` / ``_out_degc`` (return / supply).
        """
        p = self.parameters
        t_wb = _finite(u["ambient_wet_bulb_degc"], "ambient_wet_bulb_degc")
        t_return = _finite(
            u.get("chilled_water_temperature_in_degc", p["chilled_water_temperature_in_degc"]),
            "chilled_water_temperature_in_degc",
        )
        t_supply = _finite(
            u.get("chilled_water_temperature_out_degc", p["chilled_water_temperature_out_degc"]),
            "chilled_water_temperature_out_degc",
        )
        if t_return <= t_supply:
            raise ValueError(
                f"chilled-water return ({t_return} °C) must be above the supply ({t_supply} °C)"
            )
        rho_cp = _positive(p["chilled_water_density_kg_m3"], "chilled_water_density_kg_m3") * (
            _positive(p["chilled_water_specific_heat_j_kgk"], "chilled_water_specific_heat_j_kgk")
        )
        has_load = u.get("cooling_load_w") is not None
        has_flow = u.get("chilled_water_flow_rate_m3s") is not None
        if has_load == has_flow:
            raise ValueError("give exactly one of cooling_load_w or chilled_water_flow_rate_m3s")
        if has_flow:
            flow = _non_negative(u["chilled_water_flow_rate_m3s"], "chilled_water_flow_rate_m3s")
            load = rho_cp * flow * (t_return - t_supply)
        else:
            load = _non_negative(u["cooling_load_w"], "cooling_load_w")
            flow = load / (rho_cp * (t_return - t_supply))

        t_fc = (
            t_wb
            + _non_negative(p["tower_approach_k"], "tower_approach_k")
            + _non_negative(p["hex_approach_k"], "hex_approach_k")
        )
        warnings = []
        t_tower = t_wb + _non_negative(p["tower_approach_k"], "tower_approach_k")
        if t_tower <= 0.0:
            warnings.append(f"tower water {t_tower:.1f} °C <= 0 °C: freeze protection not modelled")
        mode = free_cooling_mode(t_fc, t_supply, t_return)
        if mode == "full":
            fraction = 1.0
        elif mode == "partial":
            fraction = (t_return - t_fc) / (t_return - t_supply)
        else:
            fraction = 0.0
        q_fc = fraction * load
        if p["nominal_capacity_w"] is not None:
            q_fc = min(q_fc, _non_negative(p["nominal_capacity_w"], "nominal_capacity_w"))
        power_el = _non_negative(p["aux_power_fraction"], "aux_power_fraction") * q_fc
        capacity_rate = rho_cp * flow
        t_chiller_in = t_return - q_fc / capacity_rate if capacity_rate > 0.0 else t_return

        self.output = {
            "mode": mode,
            "economiser_temperature_degc": t_fc,
            "cooling_load_w": load,
            "free_cooling_w": q_fc,
            "residual_cooling_w": load - q_fc,
            "free_cooling_fraction": q_fc / load if load > 0.0 else 0.0,
            "chilled_water_temperature_in_degc": t_return,
            "chilled_water_temperature_out_degc": t_chiller_in,
            "chilled_water_flow_rate_m3s": flow,
            "performance_factor": q_fc / power_el if power_el > 0.0 else 0.0,
            "validity_warnings": warnings,
            "heat_rejected_w": q_fc + power_el,
            "power_el": power_el,
            "power_th": q_fc,
            "emission": 0.0,
        }
