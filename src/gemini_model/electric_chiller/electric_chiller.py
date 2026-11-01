"""Electric (vapour-compression) chiller with a Carnot-efficiency COP (GM-05).

Source: RC-06 (``data/knowledge/reference_cards/RC-06_electric_chiller.md``, copied in
``REFERENCE.md``); Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center
1989) p.257-258, 262, 266; spec §3.3 and §7.2 GM-05.

- ``COP = η T_evap / (T_cond - T_evap)`` (K) on the **refrigerant basis** (T_evap and T_cond
  below include the -4 K / +3 K offsets). RC-06 "Temperature basis of η": the Guidebook's
  0.39-0.40 (COP 4.39 at 45 °F chilled / 90 °F condenser leaving water, p.257-258) is on the
  water basis; on the model's basis the same point gives η = 4.39 × 32 / 276.35 = 0.508 [D].
  With ``carnot_efficiency: None`` (default) η is calibrated by
  :func:`calibrated_carnot_efficiency` from the model's own approach parameters, so the
  Guidebook point is always reproduced (COOL-ELC); an explicit value overrides it (keep it
  on the refrigerant basis -- do not mix bases).
- ``T_evap = T_chilled,supply - 4 K`` [A] (20 °C supply -> 16 °C).
- ``T_cond = T_cw,leaving + 3 K`` with a cooling tower, ``T_cw,leaving = T_wb + approach
  (4-5 K, default 4.5) + range (~5 K)`` [A]; or ``T_cond = T_db + 8 K`` with a dry cooler [A].
  If ``cooling_water_temperature_in_degc`` (water from a tower or dry cooler) is given,
  ``T_cond = T_cw,in + range + 3 K`` (water-cooled condenser) for either option. The
  ``dry_cooler`` option is RC-06's air-cooled condensing (T_db + 8 K); a water-loop dry
  cooler (``heat_rejection`` dry cooler, T_db + 5 K) feeding the condenser gives T_db + 13 K
  (COP 5.26 vs 6.80 at 25 °C) -- a warning says which formula was used.
- COP capped at 8 and floored at 3 [A]; ``P_el = Q_cool / COP`` is compressor power only (tower
  fan and condenser-water pump belong to a ``heat_rejection`` component, RC-06);
  ``Q_reject = Q_cool + P_el``. With η 0.508 the cap of 8 binds at the Dutch design points.

Condenser sensitivity: the model gives ``d ln COP / dT_cond = -1/(T_cond - T_evap)``
(output ``condenser_sensitivity_per_k``), -3.1 %/K at the Guidebook point (lift 32 K on the
refrigerant basis), steeper than the Guidebook data (≈ -2.6 %/K of condenser-water
temperature, p.257-258): the model's COP falls faster in hot weather (documented check).

Capacity is the nominal capacity (no temperature derating); unmet load is reported.
At 20 °C chilled supply and Dutch summer conditions RC-06 expects COP ≈ 6-8; with the
calibrated η the model reaches the cap (8) there and drops below it only in hotter weather
(e.g. 6.7 with a dry cooler at 30 °C dry-bulb).
"""

import math

from gemini_model.fluid.pvt_config import liquid_properties
from gemini_model.model_abstract import StaticModel

KELVIN = 273.15
HEAT_REJECTION_OPTIONS = ("cooling_tower", "dry_cooler")
# RC-06 calibration point (Guidebook p.257-258): COP 4.39 at 45 °F chilled water and 90 °F
# condenser leaving water (water temperatures; the card rounds them to 7.2 / 32.2 °C).
GUIDEBOOK_COP = 4.39
GUIDEBOOK_CHILLED_WATER_DEGC = 7.2
GUIDEBOOK_CONDENSER_WATER_OUT_DEGC = 32.2


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


def carnot_cop(evaporator_temperature_degc, condenser_temperature_degc, carnot_efficiency):
    """Return η T_evap / (T_cond - T_evap) (RC-06), uncapped; inf if T_cond <= T_evap."""
    t_evap = evaporator_temperature_degc + KELVIN
    lift = condenser_temperature_degc - evaporator_temperature_degc
    if lift <= 0.0:
        return math.inf
    return carnot_efficiency * t_evap / lift


def calibrated_carnot_efficiency(
    evaporator_approach_k=4.0,
    condenser_approach_k=3.0,
    cop=GUIDEBOOK_COP,
    chilled_water_degc=GUIDEBOOK_CHILLED_WATER_DEGC,
    condenser_water_out_degc=GUIDEBOOK_CONDENSER_WATER_OUT_DEGC,
):
    """Return η on the model's refrigerant basis that reproduces a COP at water temperatures.

    T_evap = chilled water - evaporator approach, T_cond = condenser leaving water +
    condenser approach; ``η = COP (T_cond - T_evap) / T_evap`` (K). With the RC-06 defaults
    (COP 4.39 at 7.2 / 32.2 °C, approaches 4 K / 3 K) η = 4.39 × 32 / 276.35 = 0.508 [D].
    """
    t_evap = chilled_water_degc - evaporator_approach_k
    t_cond = condenser_water_out_degc + condenser_approach_k
    if t_cond <= t_evap:
        raise ValueError("calibration point needs T_cond > T_evap")
    return cop * (t_cond - t_evap) / (t_evap + KELVIN)


class ElectricChiller(StaticModel):
    """Electric vapour-compression chiller (RC-06)."""

    def __init__(self):
        """Model initialization with RC-06 defaults (nominal capacity must be set)."""
        density, specific_heat = liquid_properties()
        self.parameters = {
            "nominal_capacity_w": None,
            "carnot_efficiency": None,  # None -> calibrated, refrigerant basis (0.508) [D]
            "heat_rejection": "cooling_tower",  # or "dry_cooler"
            "evaporator_approach_k": 4.0,  # [A] T_evap = T_supply - 4 K
            "condenser_approach_k": 3.0,  # [A] T_cond = T_cw,leaving + 3 K
            "tower_approach_k": 4.5,  # [A] 4-5 K
            "cooling_water_range_k": 5.0,  # [A] ~5 K
            "dry_cooler_condenser_offset_k": 8.0,  # [A] T_cond = T_db + 8 K
            "cop_max": 8.0,  # [A]
            "cop_min": 3.0,  # [A]
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

    def carnot_efficiency(self):
        """Return η in use: the parameter, or the RC-06 calibration on the model's basis."""
        p = self.parameters
        if p.get("carnot_efficiency") is not None:
            return _positive(p["carnot_efficiency"], "carnot_efficiency")
        return calibrated_carnot_efficiency(
            _non_negative(p["evaporator_approach_k"], "evaporator_approach_k"),
            _non_negative(p["condenser_approach_k"], "condenser_approach_k"),
        )

    def _rho_cp(self):
        """Return ρ c_p (J/(m³ K)) of the chilled water."""
        p = self.parameters
        return _positive(p["chilled_water_density_kg_m3"], "chilled_water_density_kg_m3") * (
            _positive(p["chilled_water_specific_heat_j_kgk"], "chilled_water_specific_heat_j_kgk")
        )

    def condenser_temperature_degc(self, u, warnings=None):
        """Return the condensing temperature (°C) from the inputs (RC-06).

        Appends a note to ``warnings`` (a list) when a dry cooler is configured but the
        water-cooled formula is used because condenser water is given.
        """
        p = self.parameters
        if u.get("condenser_temperature_degc") is not None:
            return _finite(u["condenser_temperature_degc"], "condenser_temperature_degc")
        approach = _non_negative(p["condenser_approach_k"], "condenser_approach_k")
        cw_range = _non_negative(p["cooling_water_range_k"], "cooling_water_range_k")
        if u.get("cooling_water_temperature_in_degc") is not None:
            t_cw_in = _finite(
                u["cooling_water_temperature_in_degc"], "cooling_water_temperature_in_degc"
            )
            if p["heat_rejection"] == "dry_cooler" and warnings is not None:
                warnings.append(
                    "dry_cooler with condenser water given: water-cooled formula "
                    "T_cw,in + range + 3 K used, not the RC-06 air-cooled T_db + 8 K"
                )
            return t_cw_in + cw_range + approach
        option = p["heat_rejection"]
        if option == "cooling_tower":
            t_wb = _finite(u["ambient_wet_bulb_degc"], "ambient_wet_bulb_degc")
            tower_approach = _non_negative(p["tower_approach_k"], "tower_approach_k")
            return t_wb + tower_approach + cw_range + approach
        if option == "dry_cooler":
            t_db = _finite(u["ambient_dry_bulb_degc"], "ambient_dry_bulb_degc")
            offset = _non_negative(
                p["dry_cooler_condenser_offset_k"], "dry_cooler_condenser_offset_k"
            )
            return t_db + offset
        raise ValueError(f"heat_rejection must be one of {HEAT_REJECTION_OPTIONS}, got {option!r}")

    def calculate_output(self, u, x=None):
        """Calculate cooling duty, COP, electric power and rejected heat.

        Inputs ``u``: exactly one of ``cooling_load_w`` (W) or ``chilled_water_flow_rate_m3s``
        (m³/s, load = ρ c_p V (T_return - T_supply)); ``ambient_wet_bulb_degc`` (cooling
        tower) or ``ambient_dry_bulb_degc`` (dry cooler); optional
        ``cooling_water_temperature_in_degc`` (condenser water from a heat-rejection unit),
        ``chilled_water_temperature_in_degc`` / ``_out_degc`` (return / supply set point),
        and direct overrides ``evaporator_temperature_degc`` / ``condenser_temperature_degc``.
        """
        p = self.parameters
        q_nom = _positive(p["nominal_capacity_w"], "nominal_capacity_w")
        eta = self.carnot_efficiency()
        cop_max = _positive(p["cop_max"], "cop_max")
        cop_min = _positive(p["cop_min"], "cop_min")
        if cop_min > cop_max:
            raise ValueError(f"cop_min ({cop_min}) must not exceed cop_max ({cop_max})")

        t_in = _finite(
            u.get("chilled_water_temperature_in_degc", p["chilled_water_temperature_in_degc"]),
            "chilled_water_temperature_in_degc",
        )
        t_set = _finite(
            u.get("chilled_water_temperature_out_degc", p["chilled_water_temperature_out_degc"]),
            "chilled_water_temperature_out_degc",
        )
        has_load = u.get("cooling_load_w") is not None
        has_flow = u.get("chilled_water_flow_rate_m3s") is not None
        if has_load == has_flow:
            raise ValueError("give exactly one of cooling_load_w or chilled_water_flow_rate_m3s")
        if has_flow:
            flow = _non_negative(u["chilled_water_flow_rate_m3s"], "chilled_water_flow_rate_m3s")
            load = self._rho_cp() * flow * max(t_in - t_set, 0.0)
        else:
            load = _non_negative(u["cooling_load_w"], "cooling_load_w")
            if load > 0.0 and t_in <= t_set:
                raise ValueError(
                    f"chilled-water return ({t_in} °C) must be above the supply set point "
                    f"({t_set} °C) when cooling_load_w > 0"
                )

        if u.get("evaporator_temperature_degc") is not None:
            t_evap = _finite(u["evaporator_temperature_degc"], "evaporator_temperature_degc")
        else:
            t_evap = t_set - _non_negative(p["evaporator_approach_k"], "evaporator_approach_k")
        warnings = []
        t_cond = self.condenser_temperature_degc(u, warnings)
        cop_carnot = carnot_cop(t_evap, t_cond, eta)
        cop = min(max(cop_carnot, cop_min), cop_max)
        if cop_carnot > cop_max:
            warnings.append(f"COP {cop_carnot:.2f} capped at {cop_max:g}")
        elif cop_carnot < cop_min:
            warnings.append(f"COP {cop_carnot:.2f} floored at {cop_min:g}")
        sensitivity = (
            -1.0 / (t_cond - t_evap) if cop_min < cop_carnot < cop_max and t_cond > t_evap else 0.0
        )

        q_cool = min(load, q_nom)
        power_el = q_cool / cop
        if has_flow:
            t_out = t_in - q_cool / (self._rho_cp() * flow) if flow > 0.0 else t_in
        else:
            t_out = t_set
            flow = q_cool / (self._rho_cp() * (t_in - t_set)) if q_cool > 0.0 else 0.0

        self.output = {
            "cooling_power_w": q_cool,
            "cooling_load_w": load,
            "unmet_cooling_w": max(load - q_cool, 0.0),
            "load_ratio": q_cool / q_nom,
            "cop": cop,
            "cop_uncapped": cop_carnot,
            "carnot_efficiency": eta,
            "evaporator_temperature_degc": t_evap,
            "condenser_temperature_degc": t_cond,
            "condenser_sensitivity_per_k": sensitivity,
            "heat_rejected_w": q_cool + power_el,
            "chilled_water_temperature_in_degc": t_in,
            "chilled_water_temperature_out_degc": t_out,
            "chilled_water_flow_rate_m3s": flow,
            "validity_warnings": warnings,
            "power_el": power_el,
            "power_th": q_cool,
            "emission": 0.0,
        }
