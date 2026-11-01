"""Heat rejection to ambient: wet cooling tower or dry cooler (GM-06).

Source: RC-07 (``data/knowledge/reference_cards/RC-07_heat_rejection_free_cooling_climate.md``,
copied in ``REFERENCE.md``); Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat
Center 1989) p.262, 269; RTES data-centre cooling paper; DiPippo, Geothermal Power Plants 3e,
Ch23 p.11-12; spec §7.2 GM-06.

- Heat to reject from a chiller ``Q_reject = Q_cool (1 + 1/COP)`` (Guidebook p.262):
  2.43 Q_cool at COP 0.7, 1.33 Q_cool at COP 3 (:func:`chiller_heat_rejection_w`). The
  chiller models already output ``heat_rejected_w``; wire that to ``heat_load_w``. For the
  absorption chiller set ``include_heat_rejection_auxiliaries: false``: its default 2.2 % aux
  is the extra tower fan and pump, which this unit already counts (COOL-ABS-AUX). The
  electric chiller's ``power_el`` is compressor power only (RC-06).
- Cooling tower: leaving (cold) water ``T_out = T_wb + approach``, approach 4-5 K
  (default 4.5) [A]; range ≈ 5 K [A]; fan + cooling-water pump ≈ 2 % of heat rejected [A]
  (Guidebook 0.077 kW/ton incremental, p.269).
- Dry cooler: ``T_out = T_db + 5 K`` (RTES paper) [L]; fan power ≈ 3× the wet tower
  (DiPippo Ch23 Miravalles comparison) [L] -> fan + pump 3 × 2 % = 6 % [A, the 3× is
  applied to the whole 2 %]; cost ≈ 3× and area ≈ 3× (for economics, not modelled here).
- Water use (RC-07 MAY): evaporation ≈ Q_reject / h_fg, h_fg 2.45 MJ/kg (water at ≈ 20 °C,
  steam tables) [A] -- an upper bound (all heat by evaporation); 0 for a dry cooler.

With ``water_flow_rate_m3s`` the entering (hot) water temperature follows from the energy
balance ``Q = ρ c_p V (T_in - T_out)``; without it the range is fixed and the flow is
reported. All energy taken in leaves as heat: ``heat_to_ambient_w = heat_load_w +
power_el``. There is no capacity limit: size the unit at the summer design point (DB-34b).
A wet tower whose fixed-approach leaving water would be at or below 0 °C is flagged in
``validity_warnings`` (freeze protection -- fan control, bypass -- is not modelled).
"""

import math

from gemini_model.fluid.pvt_config import liquid_properties
from gemini_model.model_abstract import StaticModel

TECHNOLOGIES = ("cooling_tower", "dry_cooler")
DEFAULT_APPROACH_K = {"cooling_tower": 4.5, "dry_cooler": 5.0}


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


def chiller_heat_rejection_w(cooling_w, cop):
    """Return Q_reject = Q_cool (1 + 1/COP) (W) for a chiller (RC-07, Guidebook p.262)."""
    return _non_negative(cooling_w, "cooling_w") * (1.0 + 1.0 / _positive(cop, "cop"))


class HeatRejection(StaticModel):
    """Wet cooling tower or dry cooler with a fixed approach (RC-07)."""

    def __init__(self):
        """Model initialization with RC-07 defaults (wet cooling tower)."""
        density, specific_heat = liquid_properties()
        self.parameters = {
            "technology": "cooling_tower",  # or "dry_cooler"
            "approach_k": None,  # None -> 4.5 K (tower) [A] / 5 K (dry cooler) [L]
            "range_k": 5.0,  # [A] ≈ 5 K
            "aux_power_fraction": 0.02,  # [A] fan + pump of a wet tower, of heat rejected
            "dry_cooler_power_factor": 3.0,  # [L] dry-cooler fan power ≈ 3× wet
            "latent_heat_j_kg": 2.45e6,  # [A] h_fg of water at ≈ 20 °C (steam tables)
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
        """Calculate leaving-water temperature, flow, fan/pump power and water use.

        Inputs ``u``: ``heat_load_w`` (W, ≥ 0); ``ambient_wet_bulb_degc`` (cooling tower)
        or ``ambient_dry_bulb_degc`` (dry cooler); optional ``water_flow_rate_m3s``.
        """
        p = self.parameters
        technology = p["technology"]
        if technology not in TECHNOLOGIES:
            raise ValueError(f"technology must be one of {TECHNOLOGIES}, got {technology!r}")
        approach = p["approach_k"]
        approach = DEFAULT_APPROACH_K[technology] if approach is None else approach
        approach = _non_negative(approach, "approach_k")
        aux_fraction = _non_negative(p["aux_power_fraction"], "aux_power_fraction")
        if technology == "cooling_tower":
            ambient = _finite(u["ambient_wet_bulb_degc"], "ambient_wet_bulb_degc")
        else:
            ambient = _finite(u["ambient_dry_bulb_degc"], "ambient_dry_bulb_degc")
            aux_fraction *= _non_negative(p["dry_cooler_power_factor"], "dry_cooler_power_factor")

        q_load = _non_negative(u["heat_load_w"], "heat_load_w")
        rho_cp = _positive(p["water_density_kg_m3"], "water_density_kg_m3") * _positive(
            p["water_specific_heat_j_kgk"], "water_specific_heat_j_kgk"
        )
        t_out = ambient + approach
        if u.get("water_flow_rate_m3s") is not None:
            flow = _non_negative(u["water_flow_rate_m3s"], "water_flow_rate_m3s")
            if q_load > 0.0 and flow <= 0.0:
                raise ValueError("water_flow_rate_m3s must be > 0 when heat_load_w > 0")
            water_range = q_load / (rho_cp * flow) if flow > 0.0 else 0.0
        else:
            water_range = _positive(p["range_k"], "range_k") if q_load > 0.0 else 0.0
            flow = q_load / (rho_cp * water_range) if q_load > 0.0 else 0.0

        warnings = []
        if technology == "cooling_tower" and t_out <= 0.0:
            warnings.append(
                f"tower leaving water {t_out:.1f} °C <= 0 °C: freeze protection not modelled"
            )
        power_el = aux_fraction * q_load
        if technology == "cooling_tower":
            evaporation = q_load / _positive(p["latent_heat_j_kg"], "latent_heat_j_kg")
        else:
            evaporation = 0.0

        self.output = {
            "technology": technology,
            "heat_rejected_w": q_load,
            "heat_to_ambient_w": q_load + power_el,
            "water_temperature_out_degc": t_out,
            "water_temperature_in_degc": t_out + water_range,
            "water_flow_rate_m3s": flow,
            "range_k": water_range,
            "approach_k": approach,
            "water_evaporation_kg_s": evaporation,
            "validity_warnings": warnings,
            "power_el": power_el,
            "power_th": 0.0,
            "emission": 0.0,
        }
