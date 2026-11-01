"""Single-effect LiBr/H2O absorption chiller driven by geothermal hot water (GM-04).

Source: RC-05 (``data/knowledge/reference_cards/RC-05_absorption_chiller.md``) and the
copy in ``REFERENCE.md``; Geothermal Direct Use Engineering & Design Guidebook
(Geo-Heat Center 1989) Ch14 p.261-270; spec §3.3 and §7.2 GM-04.

Two methods (parameter ``method``):

- ``"ghc1989"`` -- Method A, the Guidebook correlation (:mod:`.ghc1989`). Capacity
  ``F Q_nom`` with ``F = F_T(T_hw) F_c(T_chw, T_cw)``, ``COP(T_hw)``. Valid for 40-50 °F
  chilled and 75-90 °F cooling water; outside, it is a conservative lower bound (RC-05):
  with ``ghc1989_conservative_clamp`` (default) chilled water above 50 °F is evaluated at
  50 °F and cooling water below 75 °F at 75 °F (F rises with T_chw and falls with T_cw, so
  this never overstates capacity); other excursions are extrapolated. Part load: COP(T_hw).
- ``"characteristic"`` (default) -- Method B, the ΔΔt characteristic equation
  (:mod:`.characteristic`), needed for our 20 °C chilled water. Coefficients are calibrated
  to Method A at its rating points by :func:`.characteristic.calibrate_to_ghc1989` unless
  ``characteristic_coefficients`` gives datasheet values. Part load is a reduction of ΔΔt
  (drive-temperature control): ``ΔΔt = ΔΔt_min + Q_E/s_E`` and ``Q_G = s_G (ΔΔt -
  ΔΔt_min,G)``, so the COP falls slightly at part load. Results at 20 °C chilled water are
  extrapolated from the 6.7 °C calibration and are uncertain (RC-05 screening [D]: 85 °C
  drive at 20 °C chilled ≈ capacity fraction 0.6-0.7, COP 0.63-0.70).

Drive-side consistency (COOL-ABS-DRV): with a given drive flow, Method B needs a drive
capacity rate of at least s_G/2 (output ``minimum_drive_flow_rate_m3s``), otherwise the drive
outlet would fall below the machine's minimum generator temperature and the machine is treated
as off; Method A limits the drive heat so that the outlet stays at or above its zero-capacity
temperature (71.4 °C). Part load (COOL-ABS-PL): Method B's COP falls at low load (≈ 0.47 at
10 % load at 85 °C drive) and the drive heat drops to 0 only when the machine stops.

External circuits: each circuit either has a given flow (its outlet temperature follows
from the energy balance and, for Method B, its mean temperature is solved together with
the duty -- the equations are linear, so the solution is closed-form) or no flow (fixed
temperature change: generator ``generator_delta_t_k`` 7.2 K [L p.267], cooling water
``cooling_water_range_k`` 5 K [A RC-07]; the required flow is reported). The chilled side
is set either by ``cooling_load_w`` (supply held at the set point, flow = Q_E / (ρ c_p ΔT);
unmet load is reported, not passed through) or by
``chilled_water_flow_rate_m3s`` (load = ρ c_p V (T_return - T_supply,set)). Each circuit has
its own density and heat capacity (defaults: GEMINI water PVT, 1000 kg/m³, 4217.4 J/(kg K));
set the drive side to the brine values (1030, 4100) when brine flows through titanium
generator tubes.

Outputs: ``cooling_power_w`` Q_E, ``drive_heat_w`` Q_G = Q_E/COP, ``heat_rejected_w``
= Q_E + Q_G + P_aux (exact), ``power_el`` = P_aux = ``aux_power_fraction`` Q_E
(0.077 kW/ton ≈ 2.2 %: extra tower fan + cooling-water pump vs an electric chiller,
Guidebook p.269), ``power_th`` = Q_E (useful cooling), ``emission`` 0, circuit outlet
temperatures and flows, ``validity_warnings``. ``include_heat_rejection_auxiliaries``
(default True) switches P_aux on; set it to False when a ``heat_rejection`` component is
connected, which counts the tower fan and pumps itself (RC-05/RC-06 no-double-counting rule;
P_aux = 0 and Q_reject = Q_E + Q_G).

Materials (RC-05, p.266-267): Cu / 90-10 CuNi generator tubes are incompatible with
geothermal brine -> titanium generator tubes (+10-15 % machine cost) or an isolation HEX
with a clean loop (then the drive inlet is the clean-loop temperature).

OIT case (Guidebook p.263) -- documented comparison, not a pass/fail test: the book
selected a nominal 312 ton machine for a 150 ton duty at 190 °F (F ≈ 0.48), whereas
Method A at 44/85 °F gives F = 0.382 (≈ 392 ton) and at 48 °F chilled water F = 0.416;
the case conditions are not fully stated in the extracted text. Method A is conservative.
"""

import math

from gemini_model.absorption_chiller import characteristic, ghc1989
from gemini_model.fluid.pvt_config import liquid_properties
from gemini_model.model_abstract import StaticModel

METHODS = ("characteristic", "ghc1989")
_TOLERANCE = 1e-9  # range checks: ignore °C <-> °F round-off at the edges


def _outside(value, low, high):
    """Return True if value lies outside [low, high] by more than the round-off tolerance."""
    return value < low - _TOLERANCE or value > high + _TOLERANCE


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


class AbsorptionChiller(StaticModel):
    """Single-effect LiBr absorption chiller (RC-05 Method A or Method B)."""

    def __init__(self):
        """Model initialization with RC-05 defaults (nominal capacity must be set)."""
        density, specific_heat = liquid_properties()
        self.parameters = {
            "method": "characteristic",
            "nominal_capacity_w": None,
            "max_capacity_fraction": 1.0,  # [A] never credit more than nameplate
            "duhring_slope": characteristic.DEFAULT_DUHRING_SLOPE,
            "characteristic_coefficients": None,  # None -> calibrate to Method A
            "calibration_chilled_water_delta_t_k": characteristic.DEFAULT_CHILLED_WATER_DELTA_T_K,
            "generator_delta_t_k": characteristic.DEFAULT_GENERATOR_DELTA_T_K,
            "cooling_water_range_k": characteristic.DEFAULT_COOLING_WATER_RANGE_K,
            "chilled_water_temperature_out_degc": 20.0,  # supply set point (spec)
            "chilled_water_temperature_in_degc": 26.0,  # return, DB-32 [A]
            "aux_power_fraction": 0.077 / ghc1989.KW_PER_TON,  # 0.077 kW/ton, p.269
            # False when a heat_rejection component is connected (it counts fan + pumps).
            "include_heat_rejection_auxiliaries": True,
            "ghc1989_conservative_clamp": True,
            "drive_density_kg_m3": density,
            "drive_specific_heat_j_kgk": specific_heat,
            "cooling_water_density_kg_m3": density,
            "cooling_water_specific_heat_j_kgk": specific_heat,
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

    def characteristic_coefficients(self):
        """Return the Method B coefficients in use (datasheet values or the Method A fit)."""
        p = self.parameters
        if p.get("characteristic_coefficients") is not None:
            return characteristic.validate_coefficients(p["characteristic_coefficients"])
        return characteristic.calibrate_to_ghc1989(
            duhring_slope=p["duhring_slope"],
            generator_delta_t_k=p["generator_delta_t_k"],
            chilled_water_delta_t_k=p["calibration_chilled_water_delta_t_k"],
            cooling_water_range_k=p["cooling_water_range_k"],
        )

    def _capacity_rate(self, u, flow_key, prefix):
        """Return ρ c_p V (W/K) of a circuit with a given flow, else None."""
        if u.get(flow_key) is None:
            return None
        return self._rho_cp(prefix) * _non_negative(u[flow_key], flow_key)

    def _rho_cp(self, prefix):
        """Return ρ c_p (J/(m³ K)) of a circuit ('drive', 'cooling_water', 'chilled_water')."""
        density = f"{prefix}_density_kg_m3"
        specific_heat = f"{prefix}_specific_heat_j_kgk"
        return _positive(self.parameters[density], density) * _positive(
            self.parameters[specific_heat], specific_heat
        )

    def _chilled_side(self, u):
        """Return (T_return, T_supply_set, load_w, capacity rate W/K or None) of the chilled water.

        The capacity rate is None when the load is given (supply held at the set point).
        """
        p = self.parameters
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
            rate = self._capacity_rate(u, "chilled_water_flow_rate_m3s", "chilled_water")
            return t_in, t_set, rate * max(t_in - t_set, 0.0), rate
        load = _non_negative(u["cooling_load_w"], "cooling_load_w")
        if load > 0.0 and t_in <= t_set:
            raise ValueError(
                f"chilled-water return ({t_in} °C) must be above the supply set point "
                f"({t_set} °C) when cooling_load_w > 0"
            )
        return t_in, t_set, load, None

    def calculate_output(self, u, x=None):
        """Calculate the chiller duty, drive heat, rejected heat and outlet temperatures.

        Inputs ``u``: ``drive_temperature_in_degc`` and ``cooling_water_temperature_in_degc``
        (required); ``drive_flow_rate_m3s`` and ``cooling_water_flow_rate_m3s`` (optional,
        m³/s); exactly one of ``cooling_load_w`` (W) or ``chilled_water_flow_rate_m3s``;
        optional ``chilled_water_temperature_in_degc`` (return) and
        ``chilled_water_temperature_out_degc`` (supply set point) overriding the parameters.
        """
        p = self.parameters
        method = p["method"]
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {method!r}")
        q_nom = _positive(p["nominal_capacity_w"], "nominal_capacity_w")
        q_cap = _non_negative(p["max_capacity_fraction"], "max_capacity_fraction") * q_nom
        aux_fraction = _non_negative(p["aux_power_fraction"], "aux_power_fraction")
        include_aux = p["include_heat_rejection_auxiliaries"]
        if not isinstance(include_aux, bool):
            raise TypeError(
                f"include_heat_rejection_auxiliaries must be True or False, got {include_aux!r}"
            )
        dt_generator = _positive(p["generator_delta_t_k"], "generator_delta_t_k")
        cw_range = _positive(p["cooling_water_range_k"], "cooling_water_range_k")

        t_g_in = _finite(u["drive_temperature_in_degc"], "drive_temperature_in_degc")
        t_ac_in = _finite(
            u["cooling_water_temperature_in_degc"], "cooling_water_temperature_in_degc"
        )
        t_e_in, t_e_set, load, c_e = self._chilled_side(u)
        c_g = self._capacity_rate(u, "drive_flow_rate_m3s", "drive")
        c_ac = self._capacity_rate(u, "cooling_water_flow_rate_m3s", "cooling_water")

        warnings = []
        details = {
            "capacity_factor_ft": None,
            "capacity_factor_fc": None,
            "delta_delta_t_k": None,
            "minimum_drive_flow_rate_m3s": None,
        }
        if method == "ghc1989":
            q_avail, q_e, q_g = self._method_a(
                t_g_in, t_e_set, t_ac_in, c_g, q_nom, q_cap, load, details, warnings
            )
        else:
            q_avail, q_e, q_g = self._method_b(
                (t_g_in, t_ac_in, t_e_in, t_e_set),
                (c_g, c_ac, c_e),
                (dt_generator, cw_range),
                (q_nom, q_cap, load),
                details,
                warnings,
            )

        p_aux = aux_fraction * q_e if include_aux else 0.0
        q_ac = q_e + q_g
        q_reject = q_ac + p_aux

        t_g_out, v_g = self._outlet(q_g, t_g_in, -1.0, c_g, dt_generator, u, "drive")
        t_ac_out, v_ac = self._outlet(q_ac, t_ac_in, 1.0, c_ac, cw_range, u, "cooling_water")
        if c_e is None:  # load given: supply held at the set point, flow at the design ΔT
            t_e_out = t_e_set
            v_e = q_e / (self._rho_cp("chilled_water") * (t_e_in - t_e_set)) if q_e > 0.0 else 0.0
        else:
            t_e_out = t_e_in - q_e / c_e if c_e > 0.0 else t_e_in
            v_e = float(u["chilled_water_flow_rate_m3s"])

        self.output = {
            "method": method,
            "cooling_power_w": q_e,
            "available_cooling_w": q_avail,
            "unmet_cooling_w": max(load - q_e, 0.0),
            "cooling_load_w": load,
            "capacity_fraction": q_avail / q_nom,
            "load_ratio": q_e / q_nom,
            "cop": q_e / q_g if q_g > 0.0 else 0.0,
            "drive_heat_w": q_g,
            "absorber_condenser_heat_w": q_ac,
            "heat_rejected_w": q_reject,
            "aux_power_w": p_aux,
            "drive_temperature_in_degc": t_g_in,
            "drive_temperature_out_degc": t_g_out,
            "drive_flow_rate_m3s": v_g,
            "drive_delta_t_k": t_g_in - t_g_out,
            "cooling_water_temperature_in_degc": t_ac_in,
            "cooling_water_temperature_out_degc": t_ac_out,
            "cooling_water_flow_rate_m3s": v_ac,
            "chilled_water_temperature_in_degc": t_e_in,
            "chilled_water_temperature_out_degc": t_e_out,
            "chilled_water_flow_rate_m3s": v_e,
            **details,
            "validity_warnings": warnings,
            "power_el": p_aux,
            "power_th": q_e,
            "emission": 0.0,
        }

    def _outlet(self, heat_w, t_in, sign, capacity_rate, fixed_delta_t, u, prefix):
        """Return (T_out, flow) of a circuit receiving (+1) or giving (-1) ``heat_w``."""
        if capacity_rate is not None:
            if capacity_rate <= 0.0:
                return t_in, 0.0
            return t_in + sign * heat_w / capacity_rate, float(u[f"{prefix}_flow_rate_m3s"])
        if heat_w <= 0.0:
            return t_in, 0.0
        return t_in + sign * fixed_delta_t, heat_w / (self._rho_cp(prefix) * fixed_delta_t)

    def _method_a(self, t_g_in, t_e_set, t_ac_in, c_g, q_nom, q_cap, load, details, warnings):
        """Apply Method A (Guidebook 1989) and return (Q_available, Q_E, Q_G) in W.

        Method A has no drive-flow term (it assumes the rating generator ΔT). With a given
        drive flow (capacity rate ``c_g``, W/K) the drive heat is limited so that the drive
        outlet does not fall below the zero-capacity temperature (71.4 °C): hot water colder
        than that cannot drive the machine (COOL-ABS-DRV).
        """
        t_hw = ghc1989.degc_to_degf(t_g_in)
        t_chw = ghc1989.degc_to_degf(t_e_set)
        t_cw = ghc1989.degc_to_degf(t_ac_in)
        clamp = bool(self.parameters["ghc1989_conservative_clamp"])
        chw_lo, chw_hi = ghc1989.CHILLED_WATER_VALIDITY_DEGF
        cw_lo, cw_hi = ghc1989.COOLING_WATER_VALIDITY_DEGF
        if _outside(t_chw, chw_lo, chw_hi):
            note = (
                f"; evaluated at {chw_hi:.0f} °F (lower bound)" if clamp and t_chw > chw_hi else ""
            )
            warnings.append(
                f"ghc1989: chilled water {t_chw:.1f} °F outside validity "
                f"{chw_lo:.0f}-{chw_hi:.0f} °F{note}"
            )
            if clamp and t_chw > chw_hi:
                t_chw = chw_hi
        if _outside(t_cw, cw_lo, cw_hi):
            note = f"; evaluated at {cw_lo:.0f} °F (lower bound)" if clamp and t_cw < cw_lo else ""
            warnings.append(
                f"ghc1989: cooling water {t_cw:.1f} °F outside validity "
                f"{cw_lo:.0f}-{cw_hi:.0f} °F{note}"
            )
            if clamp and t_cw < cw_lo:
                t_cw = cw_lo
        hw_lo, hw_hi = ghc1989.RATING_HOT_WATER_RANGE_DEGF
        t_zero_degf = ghc1989.zero_capacity_temperature_degf()
        if t_hw > hw_hi + _TOLERANCE:
            warnings.append(
                f"ghc1989: hot water {t_hw:.1f} °F above the rating range "
                f"{hw_lo:.0f}-{hw_hi:.0f} °F (extrapolated)"
            )
        elif t_zero_degf < t_hw < hw_lo - _TOLERANCE:
            warnings.append(
                f"ghc1989: hot water {t_hw:.1f} °F below the rating range "
                f"{hw_lo:.0f}-{hw_hi:.0f} °F (extrapolated towards zero capacity)"
            )
        f_t = ghc1989.hot_water_factor(t_hw)
        f_c = ghc1989.condition_factor(t_chw, t_cw)
        details["capacity_factor_ft"] = f_t
        details["capacity_factor_fc"] = f_c
        if f_t <= 0.0:
            warnings.append(
                f"ghc1989: drive {t_g_in:.1f} °C at or below the zero-capacity temperature "
                f"{ghc1989.degf_to_degc(ghc1989.zero_capacity_temperature_degf()):.1f} °C"
            )
        q_avail = min(max(f_t, 0.0) * max(f_c, 0.0) * q_nom, q_cap)
        cop = ghc1989.cop(t_hw)
        if c_g is not None:
            t_zero = ghc1989.degf_to_degc(t_zero_degf)
            q_flow_limit = c_g * max(t_g_in - t_zero, 0.0) * cop
            if q_flow_limit < q_avail:
                warnings.append(
                    f"ghc1989: capacity limited by the drive flow (outlet kept at the "
                    f"zero-capacity temperature {t_zero:.1f} °C)"
                )
                q_avail = q_flow_limit
        q_e = min(load, q_avail)
        q_g = q_e / cop if q_e > 0.0 else 0.0
        return q_avail, q_e, q_g

    def _method_b(self, temperatures, capacity_rates, fixed_deltas, duties, details, warnings):
        """Apply Method B (characteristic equation) and return (Q_available, Q_E, Q_G) in W.

        ``temperatures`` = (T_G,in, T_AC,in, T_E,in, T_E,set) °C; ``capacity_rates`` =
        (C_G, C_AC, C_E) W/K (None = no flow given / load given); ``fixed_deltas`` =
        (ΔT_G, range) K; ``duties`` = (Q_nom, Q_cap, load) W.

        Mean temperatures: t̄_G = T_G,in - Q_G/(2 C_G) (or - ΔT_G/2 without a flow),
        t̄_AC = T_AC,in + (Q_E + Q_G)/(2 C_AC) (or + range/2), t̄_E = T_E,in - Q_E/(2 C_E)
        (or the supply/return mean when the load is given). With Q linear in ΔΔt the full-load
        ΔΔt is ``(x0 + k_G m_G + k_E m_E) / (1 + k_G + k_E)``.
        """
        t_g_in, t_ac_in, t_e_in, t_e_set = temperatures
        c_g, c_ac, c_e = capacity_rates
        dt_generator, cw_range = fixed_deltas
        q_nom, q_cap, load = duties
        coefficients = self.characteristic_coefficients()
        b = _positive(self.parameters["duhring_slope"], "duhring_slope")
        s_e = coefficients["s_e_per_k"] * q_nom
        s_g = coefficients["s_g_per_k"] * q_nom
        m_e = coefficients["ddt_min_k"]
        m_g = coefficients["ddt_min_g_k"]

        # Drive outlet >= minimum generator temperature t_G,min = ΔΔt_min,G + (1+B) t̄_AC - B t̄_E
        # <=> (t̄_G - t_G,out) <= ΔΔt - ΔΔt_min,G <=> Q_G/(2 C_G) <= Q_G/s_G <=> C_G >= s_G/2,
        # independent of the operating point (COOL-ABS-DRV).
        details["minimum_drive_flow_rate_m3s"] = 0.5 * s_g / self._rho_cp("drive")
        infeasible_drive = c_g is not None and 0.0 < c_g < 0.5 * s_g
        if infeasible_drive:
            warnings.append(
                f"characteristic: drive flow {c_g / self._rho_cp('drive'):.4g} m³/s below the "
                f"minimum {details['minimum_drive_flow_rate_m3s']:.4g} m³/s (s_G/(2 ρ c_p)): the "
                "drive outlet would fall below the minimum generator temperature; machine off"
            )
        g, offset_g = (0.5 / c_g, 0.0) if c_g else (0.0, dt_generator / 2.0)
        a, offset_ac = (0.5 / c_ac, 0.0) if c_ac else (0.0, cw_range / 2.0)
        e, offset_e = (0.5 / c_e, 0.0) if c_e else (0.0, max(t_e_in - t_e_set, 0.0) / 2.0)
        if (c_g is not None and c_g <= 0.0) or (c_ac is not None and c_ac <= 0.0):
            q_avail, ddt = 0.0, None  # no drive or no cooling-water flow: machine off
        elif infeasible_drive:
            q_avail, ddt = 0.0, None
        else:
            x0 = (t_g_in - offset_g) - (1.0 + b) * (t_ac_in + offset_ac) + b * (t_e_in - offset_e)
            k_g = (g + (1.0 + b) * a) * s_g
            k_e = ((1.0 + b) * a + b * e) * s_e
            ddt = (x0 + k_g * m_g + k_e * m_e) / (1.0 + k_g + k_e)
            q_avail = min(max(s_e * (ddt - m_e), 0.0), q_cap)
        details["delta_delta_t_k"] = ddt

        self._method_b_warnings(t_e_set, t_ac_in, ddt, warnings)
        q_e = min(load, q_avail)
        if q_e <= 0.0:
            return q_avail, 0.0, 0.0
        ddt_operating = m_e + q_e / s_e
        return q_avail, q_e, s_g * (ddt_operating - m_g)

    def _method_b_warnings(self, t_e_set, t_ac_in, ddt, warnings):
        """Flag Method B extrapolation beyond its Method A calibration (RC-05: uncertain)."""
        if self.parameters.get("characteristic_coefficients") is not None:
            return  # datasheet coefficients: validity is the datasheet's, not Method A's
        t_chw = ghc1989.degc_to_degf(t_e_set)
        t_cw = ghc1989.degc_to_degf(t_ac_in)
        chw_lo, chw_hi = ghc1989.CHILLED_WATER_VALIDITY_DEGF
        cw_lo, cw_hi = ghc1989.COOLING_WATER_VALIDITY_DEGF
        if _outside(t_chw, chw_lo, chw_hi):
            warnings.append(
                f"characteristic: chilled water {t_e_set:.1f} °C extrapolated from the "
                f"Method A calibration ({ghc1989.degf_to_degc(chw_lo):.1f}-"
                f"{ghc1989.degf_to_degc(chw_hi):.1f} °C) with B = "
                f"{self.parameters['duhring_slope']}; result uncertain (RC-05)"
            )
        if _outside(t_cw, cw_lo, cw_hi):
            warnings.append(
                f"characteristic: cooling water {t_ac_in:.1f} °C extrapolated from the "
                f"Method A calibration ({ghc1989.degf_to_degc(cw_lo):.1f}-"
                f"{ghc1989.degf_to_degc(cw_hi):.1f} °C); result uncertain (RC-05)"
            )
        if ddt is None:
            return
        p = self.parameters
        _, report = characteristic.calibrate_to_ghc1989(
            duhring_slope=p["duhring_slope"],
            generator_delta_t_k=p["generator_delta_t_k"],
            chilled_water_delta_t_k=p["calibration_chilled_water_delta_t_k"],
            cooling_water_range_k=p["cooling_water_range_k"],
            with_report=True,
        )
        low, high = report["ddt_range_k"]
        if _outside(ddt, low, high):
            warnings.append(
                f"characteristic: ΔΔt {ddt:.2f} K outside the calibrated range "
                f"{low:.2f}-{high:.2f} K (Method A T_hw 180-240 °F)"
            )
