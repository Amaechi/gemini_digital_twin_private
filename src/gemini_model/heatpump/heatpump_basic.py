"""HeatpumpBasic model.

Heatpump model based on Carnot-efficiency model or a Lorenz-efficiency model.

References
----------
Carnot COP equation:
    COP_carnot = eta_carnot * (Th / (Th - Ts))
    where Th and Ts are the (constant) condenser and evaporator temperatures [K].
    See: Carnot, S. (1824). "Reflexions sur la puissance motrice du feu."
    Also: Cengel, Y. A., & Boles, M. A. (2015). "Thermodynamics: An Engineering
    Approach" (8th ed.), McGraw-Hill, Chapter 11 (Refrigeration Cycles).

Lorenz COP equation:
    COP_lorenz = eta_lorenz * (Th / (Th - Ts))
    where Th and Ts are the logarithmic mean temperatures of the heat sink
    (condenser) and heat source (evaporator) glide, respectively:
    Th = (Th_in - Th_out) / ln(Th_in / Th_out) and
    Ts = (Ts_in - Ts_out) / ln(Ts_in / Ts_out).
    The Lorenz cycle extends the Carnot cycle to non-isothermal heat exchange
    (glide), which is relevant for heat pumps with secondary fluids.
    See: Lorenz, H. (1894). "Die Ermittlung der Grenzwerte der thermodynamischen
    Energieumwandlung." Zeitschrift fur die gesamte Kalte-Industrie.
    Also: Kuo, C. S. et al. (2005). "Analysis of a Lorenz refrigeration cycle
    for heat pumps," and ASHRAE Handbook - Fundamentals, Chapter on
    Thermodynamics and Refrigeration Cycles.

Closed-form path (GM-03, RC-04; see ``closed_form.py`` and ``REFERENCE.md``):
    ``solver: "closed_form"`` solves the same operating point without Nelder-Mead
    (closed-form COP, bracketed scalar root) and, by default, caps COP at
    0.6 x Carnot at the refrigerant temperatures (RC-04 default, register
    VER-HP-ETA) unless a cited datasheet gives the fraction. The default
    ``solver: "nelder_mead"`` keeps GEMINI's results unchanged. Both paths
    report the HTHP limit screening and the verifier evidence
    (``heat_pump_point``).
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the closed-form
# solver behind the "solver" parameter (default "nelder_mead" keeps GEMINI behaviour), HTHP limit
# parameters (MISSING until a datasheet is cited) and heat-pump evidence outputs (GM-03, WP-03).

import math

import numpy as np
from scipy.optimize import minimize

from gemini_model.heatpump import closed_form
from gemini_model.model_abstract import StaticModel

SOLVERS = ("nelder_mead", "closed_form")


class HeatpumpBasic(StaticModel):
    """HeatpumpBasic model."""

    def __init__(self):
        """Model initialization.

        Sets up default parameters for the heat pump model, including the
        operating mode ("carnot" or "lorenz"), efficiency factors relative to
        the ideal Carnot/Lorenz COP, a nominal COP used as the optimizer's
        initial guess, fluid properties (specific heat and density for the
        hot/sink and source loops), the target hot side outlet temperature,
        and the minimum allowed source side outlet temperature.
        """
        self.parameters = {
            "mode": "carnot",
            "eta_carnot": 0.5,
            "eta_lorenz": 0.5,
            "COP_0": 3.0,
            "Cp_h": 4181,  # J/kg.K
            "Cp_s": 4181,  # J/kg.K
            "rho_h": 1000,  # kg/m3
            "rho_s": 1000,  # kg/m3
            "Th_out_target": 80,  # °C
            "Ts_in_minimum": 10,  # °C
            # GM-03 (WP-03); defaults keep the GEMINI Nelder-Mead results.
            "solver": "nelder_mead",  # or "closed_form" (RC-04 eq. 1-3, no starting guess)
            "apply_cop_cap": True,  # closed_form only: COP <= max_carnot_fraction x Carnot
            "condenser_approach_k": closed_form.DEFAULT_APPROACH_K,  # HP-APPROACH [A]
            "evaporator_approach_k": closed_form.DEFAULT_APPROACH_K,  # HP-APPROACH [A]
            "datasheet": None,  # citation of an HTHP datasheet (needed for the limits below)
            "max_lift_k": None,  # MISSING until a datasheet -> RC-04 60 K (VER-HP-LIFT)
            # condenser leaving water = sink outlet (RC-04 "max condenser leaving T"); MISSING
            # until a datasheet (no default). Convert a refrigerant condensing limit first.
            "max_sink_temperature_degc": None,
            "max_carnot_fraction": None,  # MISSING until a datasheet -> RC-04 0.6 (VER-HP-ETA)
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
        """Generate an initial state based on user parameters.

        Not applicable: HeatpumpBasic is a static model with no internal
        state, so this method is a no-op.
        """
        pass

    def update_state(self, u, x):
        """Update the state based on input u and state x.

        Not applicable: HeatpumpBasic is a static model with no internal
        state, so this method is a no-op.
        """
        pass

    def calculate_output(self, u, x=None):
        """Calculate output based on input u and state x.

        Converts inputs (temperatures in degC, volumetric flows in m3/s) to
        SI units (K, kg/s), solves for the operating point via `_calculate`,
        and stores the results (COP, outlet temperatures, thermal production
        and electrical consumption) in `self.output`.

        Both solvers also output the RC-04 screening (``condensing_temperature_degc``,
        ``evaporating_temperature_degc``, ``lift_k``, ``cop_cap``, ``within_limits``,
        ``missing_inputs``, ``validity_warnings`` ...), ``source_heat_w`` (heat drawn from the
        source, W) and ``heat_pump_point`` (DesignVerifier evidence, see ``_evidence``).

        Parameters
        ----------
        u: dict
            Model inputs: "Th_in" (degC), "Ts_in" (degC), "hot_flow_rate"
            (m3/s, hot/sink side), "source_flow_rate" (m3/s, source side).
            Optional "hot_pressure_in"/"source_pressure_in" (bar) are echoed
            back unchanged as "hot_pressure_out"/"source_pressure_out" (this
            model does not compute a pressure drop), so a downstream
            pressure chain is not broken when connecting through a heat pump.
        x: state
            Model state (unused, model is static).
        """
        solver = self.parameters.get("solver", "nelder_mead")
        if solver not in SOLVERS:
            raise ValueError(f"solver must be one of {SOLVERS}, got {solver!r}")
        limits = closed_form.hthp_limits(
            self.parameters.get("datasheet"),
            self.parameters.get("max_lift_k"),
            self.parameters.get("max_sink_temperature_degc"),
            self.parameters.get("max_carnot_fraction"),
        )
        # get input
        Th_out = self.parameters["Th_out_target"] + 273.15  # Convert to Kelvin
        Th_in = u["Th_in"] + 273.15  # Convert to Kelvin
        Ts_in = u["Ts_in"] + 273.15  # Convert to Kelvin
        hot_flow_rate = u["hot_flow_rate"]  # m3/s
        source_flow_rate = u["source_flow_rate"]  # m3/s
        mh = hot_flow_rate * self.parameters["rho_h"]  # kg/s
        ms = source_flow_rate * self.parameters["rho_s"]  # kg/s

        if solver == "closed_form":
            point = self._calculate_closed_form(u, mh, ms, limits)
            result = (
                point["cop"],
                point["sink_out_degc"],
                point["source_out_degc"],
                point["sink_heat_w"],
                point["power_el_w"],
            )
        else:
            result = self._calculate(Th_out, Th_in, Ts_in, mh, ms)
            result = (result[0], result[1] - 273.15, result[2] - 273.15, result[3], result[4])
            point = {
                "running": True,
                "cop": result[0],
                "cop_uncapped": result[0],
                "sink_out_degc": result[1],
                "source_out_degc": result[2],
                "sink_heat_w": result[3],
                "source_heat_w": result[3] - result[4],
                "power_el_w": result[4],
                "branch": "nelder_mead",
                "warnings": [],
            }

        self.output["COP"] = result[0]
        self.output["Th_out"] = result[1]  # °C
        self.output["Ts_out"] = result[2]  # °C
        self.output["Thermal_production"] = result[3]
        self.output["electrical_consumption"] = result[4]
        self.output["hot_flow_rate"] = hot_flow_rate
        self.output["hot_flow_rate_m3h"] = hot_flow_rate * 3600.0
        self.output["source_flow_rate"] = source_flow_rate
        self.output["source_flow_rate_m3h"] = source_flow_rate * 3600.0
        self.output["power_th"] = result[3]
        self.output["power_el"] = result[4]
        self.output["emission"] = 0.0

        if "hot_pressure_in" in u:
            self.output["hot_pressure_in"] = u["hot_pressure_in"]
            self.output["hot_pressure_out"] = u["hot_pressure_in"]
        if "source_pressure_in" in u:
            self.output["source_pressure_in"] = u["source_pressure_in"]
            self.output["source_pressure_out"] = u["source_pressure_in"]

        self.output.update(self._evidence(u, solver, point, limits))

    def _approaches(self):
        """Return the (condenser, evaporator) approaches in K (RC-04 eq. 4; must be > 0)."""
        approaches = []
        for key in ("condenser_approach_k", "evaporator_approach_k"):
            value = float(self.parameters.get(key, closed_form.DEFAULT_APPROACH_K))
            if not value > 0.0:
                raise ValueError(f"{key} must be > 0 (RC-04 eq. 4), got {value}")
            approaches.append(value)
        return tuple(approaches)

    def _calculate_closed_form(self, u, mh, ms, limits):
        """Solve with closed-form COP and a bracketed root (RC-04; ``closed_form.py``).

        Temperatures stay in °C. The COP is capped at ``max_carnot_fraction`` × Carnot at the
        refrigerant temperatures (RC-04 default 0.6, register VER-HP-ETA) unless
        ``apply_cop_cap`` is False.
        """
        mode = self.parameters["mode"]
        efficiency = self.parameters["eta_lorenz" if mode == "lorenz" else "eta_carnot"]
        condenser_approach, evaporator_approach = self._approaches()
        fraction = limits["max_carnot_fraction"] if self.parameters["apply_cop_cap"] else None
        return closed_form.solve_operating_point(
            mode=mode,
            efficiency=efficiency,
            sink_in_degc=u["Th_in"],
            sink_out_target_degc=self.parameters["Th_out_target"],
            source_in_degc=u["Ts_in"],
            sink_capacity_rate_w_k=mh * self.parameters["Cp_h"],
            source_capacity_rate_w_k=ms * self.parameters["Cp_s"],
            source_out_min_degc=self.parameters["Ts_in_minimum"],
            carnot_cap_fraction=fraction,
            condenser_approach_k=condenser_approach,
            evaporator_approach_k=evaporator_approach,
        )

    def _evidence(self, u, solver, point, limits):
        """Return the HTHP screening outputs and the verifier's heat-pump evidence (RC-04).

        Condensing = sink outlet + approach, evaporating = source outlet - approach; lift is sink
        outlet - source outlet (DesignVerifier PH-13/PH-14). ``heat_pump_point`` holds the
        ``HeatPumpPoint`` fields except ``name`` (None when idle); its ``max_lift_k`` /
        ``max_carnot_fraction`` are datasheet values only, so the verifier applies VER-HP-LIFT /
        VER-HP-ETA without a datasheet.
        """
        condenser_approach, evaporator_approach = self._approaches()
        sink_out, source_out = point["sink_out_degc"], point["source_out_degc"]
        cop, running = point["cop"], point["running"]
        condensing = sink_out + condenser_approach
        evaporating = source_out - evaporator_approach
        carnot = closed_form.carnot_cop(condensing, evaporating)
        cop_cap = limits["max_carnot_fraction"] * carnot
        lift = sink_out - source_out
        warnings = list(point["warnings"])
        if limits["missing_inputs"]:
            warnings.append(limits["limits_source"])
        low, high = closed_form.RC04_APPROACH_RANGE_K
        for name, value in (("condenser", condenser_approach), ("evaporator", evaporator_approach)):
            if not low <= value <= high:
                warnings.append(f"{name} approach {value:g} K outside RC-04 {low:g}-{high:g} K")
        if solver == "closed_form" and self.parameters["mode"] != "lorenz":
            warnings.append(
                "closed_form with mode 'carnot': GEMINI's Carnot form at (Th_out, Ts_in) with "
                f"eta_carnot {self.parameters['eta_carnot']:g} (GEMINI default, not registered); "
                "RC-04 prefers mode 'lorenz'"
            )
        converged = running
        if running and solver == "nelder_mead" and not self._nelder_mead_converged(u, point):
            converged = False
            warnings.append(
                "Nelder-Mead did not converge to a physical point (equation residual > 0.1 % "
                "or an outlet outside its bounds): no evidence emitted; use solver 'closed_form'"
            )
        checks = [converged, cop >= 1.0, cop <= cop_cap, lift <= limits["max_lift_k"]]
        if running:
            if cop > cop_cap:
                warnings.append(
                    f"COP {cop:.2f} above {limits['max_carnot_fraction']:g} x Carnot "
                    f"{carnot:.2f} = {cop_cap:.2f} ({solver}; RC-04)"
                )
            elif cop < point["cop_uncapped"]:
                warnings.append(
                    f"COP {point['cop_uncapped']:.2f} capped at {limits['max_carnot_fraction']:g}"
                    f" x Carnot = {cop:.2f} (RC-04)"
                )
            if lift > limits["max_lift_k"]:
                warnings.append(f"lift {lift:.1f} K above {limits['max_lift_k']:g} K (RC-04)")
            if limits["max_sink_temperature_degc"] is not None:
                checks.append(sink_out <= limits["max_sink_temperature_degc"])
                if not checks[-1]:
                    warnings.append(
                        f"sink outlet {sink_out:.1f} °C above the datasheet maximum "
                        f"{limits['max_sink_temperature_degc']:g} °C"
                    )
            for side, delta in (
                ("sink", sink_out - u["Th_in"]),
                ("source", u["Ts_in"] - source_out),
            ):
                if delta > closed_form.STANDARD_MAX_WATER_DELTA_T_K:
                    warnings.append(
                        f"{side} water ΔT {delta:.1f} K > 11 K: needs a 4/8-pass "
                        "evaporator/condenser (Guidebook p.250)"
                    )
        assumption_ids = list(limits["assumption_ids"])
        if (condenser_approach, evaporator_approach) == (closed_form.DEFAULT_APPROACH_K,) * 2:
            assumption_ids.append(closed_form.APPROACH_ID)
        if (
            self.parameters["mode"] == "lorenz"
            and self.parameters["eta_lorenz"] == closed_form.DEFAULT_LORENZ_EFFICIENCY
        ):
            assumption_ids.append(closed_form.LORENZ_EFFICIENCY_ID)
        heat_pump_point = None
        if converged and cop >= 1.0:
            heat_pump_point = {
                "cop": cop,
                "condensing_degc": condensing,
                "evaporating_degc": evaporating,
                "sink_out_degc": sink_out,
                "source_out_degc": source_out,
                "max_lift_k": limits["datasheet_max_lift_k"],
                "max_carnot_fraction": limits["datasheet_max_carnot_fraction"],
            }
        return {
            "solver": solver,
            "running": running,
            "converged": converged,
            "operating_branch": point["branch"],
            "cop_uncapped": point["cop_uncapped"],
            "cop_cap": cop_cap,
            "carnot_cop_refrigerant": carnot,
            "condensing_temperature_degc": condensing,
            "evaporating_temperature_degc": evaporating,
            "sink_temperature_out_degc": sink_out,
            "source_temperature_out_degc": source_out,
            "source_heat_w": point["source_heat_w"],
            "lift_k": lift,
            "mean_lift_k": self._mean_lift(u, sink_out, source_out) if converged else None,
            "max_lift_k": limits["max_lift_k"],
            "max_carnot_fraction": limits["max_carnot_fraction"],
            "max_sink_temperature_degc": limits["max_sink_temperature_degc"],
            "datasheet": self.parameters.get("datasheet"),
            "limits_source": limits["limits_source"],
            "missing_inputs": limits["missing_inputs"],
            "assumption_ids": assumption_ids,
            "within_limits": all(checks) if running else None,  # None: off, not a violation
            "dea_reference_cop": closed_form.dea_reference_cop(lift) if running else None,
            "validity_warnings": warnings,
            "heat_pump_point": heat_pump_point,
        }

    @staticmethod
    def _mean_lift(u, sink_out, source_out):
        """Return the Lorenz mean lift (K), or None if it is undefined (never raises)."""
        try:
            return closed_form.mean_lift_k(u["Th_in"], sink_out, u["Ts_in"], source_out)
        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    def _nelder_mead_converged(self, u, point, tolerance=1e-3):
        """Return True if the Nelder-Mead point satisfies both GEMINI equations and its bounds.

        Bounds: Th_in < Th_out <= Th_out_target and Ts_in_minimum <= Ts_out <= Ts_in; COP > 1;
        relative residuals of the COP equation and of COP = Qh/(Qh - Qs) below ``tolerance``.
        """
        p = self.parameters
        th_in, ts_in = u["Th_in"], u["Ts_in"]
        th_out, ts_out, cop = point["sink_out_degc"], point["source_out_degc"], point["cop"]
        eps = 1e-6
        if not (
            th_in < th_out <= p["Th_out_target"] + eps
            and p["Ts_in_minimum"] - eps <= ts_out <= ts_in
            and cop > 1.0
        ):
            return False
        try:
            if p["mode"] == "lorenz":
                model = closed_form.lorenz_cop(th_in, th_out, ts_in, ts_out, p["eta_lorenz"])
            else:
                model = closed_form.carnot_cop(th_out, ts_in, p["eta_carnot"])
        except (ValueError, ZeroDivisionError, OverflowError):
            return False
        q_h = point["sink_heat_w"]
        q_s = p["Cp_s"] * (ts_in - ts_out) * u["source_flow_rate"] * p["rho_s"]
        if not (math.isfinite(model) and q_h > q_s):
            return False
        balance = q_h / (q_h - q_s)
        return abs(cop - model) / cop < tolerance and abs(cop - balance) / cop < tolerance

    def _calculate(self, Th_out, Th_in, Ts_in, mh, ms):
        """Solve for the heat pump operating point.

        (COP, outlet temperatures, thermal production and electrical
        consumption).

        Uses `scipy.optimize.minimize` (Nelder-Mead) to jointly solve the
        thermodynamic efficiency equation (Carnot or Lorenz, selected via
        `self.parameters['mode']`) together with the energy balance equation
        (Qh = Qs + electrical work, expressed as COP = Qh/(Qh-Qs)).

        If the resulting source outlet temperature `Ts_out` drops below
        `Ts_in_minimum`, the problem is re-solved with `Ts_out` fixed at that
        minimum and `Th_out` (the target hot outlet temperature) allowed to
        drop instead, via `carnot_model2`/`lorenz_model2`.

        Parameters
        ----------
        Th_out: float
            Target hot side (sink) outlet temperature [K].
        Th_in: float
            Hot side (sink) inlet temperature [K].
        Ts_in: float
            Source side inlet temperature [K].
        mh: float
            Hot side (sink) mass flow rate [kg/s].
        ms: float
            Source side mass flow rate [kg/s].

        Returns
        -------
        tuple
            (COP, Th_out, Ts_out, thermal_production, electrical_consumption)
        """

        def lorenz_model(x, Th_out, Th_in, Ts_in, mh, ms):
            """Residual function for the Lorenz-efficiency model.

            Solves for (COP, Ts_out) given a fixed target Th_out.

            Combines two residuals: J1 enforces the Lorenz COP equation
            COP = eta_lorenz * (Th / (Th - Ts)), where Th and Ts are the
            logarithmic mean temperatures of the sink and source glide
            (see module reference for the Lorenz cycle); J2 enforces the
            energy balance COP = Qh / (Qh - Qs).
            """
            COP = x[0]
            Ts_out = x[1]

            Qh = self.parameters["Cp_h"] * (Th_out - Th_in) * mh
            Qs = self.parameters["Cp_s"] * (Ts_in - Ts_out) * ms

            Th = (Th_in - Th_out) / np.log(Th_in / Th_out)
            Ts = (Ts_in - Ts_out) / np.log(Ts_in / Ts_out)

            J1 = np.abs(COP - (self.parameters["eta_lorenz"] * (Th / (Th - Ts))))
            J2 = np.abs(COP - Qh / (Qh - Qs))
            J = J1 + J2
            return J

        def lorenz_model2(x, Ts_out, Th_in, Ts_in, mh, ms):
            """Residual function for the Lorenz-efficiency model.

            Solves for (COP, Th_out) given a fixed minimum source outlet
            temperature Ts_out (used when the unconstrained solution
            violates Ts_in_minimum). See `lorenz_model` for the underlying
            equations.
            """
            COP = x[0]
            Th_out = x[1]

            Qh = self.parameters["Cp_h"] * (Th_out - Th_in) * mh
            Qs = self.parameters["Cp_s"] * (Ts_in - Ts_out) * ms

            Th = (Th_in - Th_out) / np.log(Th_in / Th_out)
            Ts = (Ts_in - Ts_out) / np.log(Ts_in / Ts_out)

            J1 = np.abs(COP - (self.parameters["eta_lorenz"] * (Th / (Th - Ts))))
            J2 = np.abs(COP - Qh / (Qh - Qs))
            J = J1 + J2
            return J

        def carnot_model(x, Th_out, Th_in, Ts_in, mh, ms):
            """Residual function for the Carnot-efficiency model.

            Solves for (COP, Ts_out) given a fixed target Th_out.

            Combines two residuals: J1 enforces the Carnot COP equation
            COP = eta_carnot * (Th / (Th - Ts)), where Th = Th_out and
            Ts = Ts_in are treated as constant condenser/evaporator
            temperatures (see module reference for the Carnot cycle);
            J2 enforces the energy balance COP = Qh / (Qh - Qs).
            """
            COP = x[0]
            Ts_out = x[1]

            Qh = self.parameters["Cp_h"] * (Th_out - Th_in) * mh
            Qs = self.parameters["Cp_s"] * (Ts_in - Ts_out) * ms

            Th = Th_out
            Ts = Ts_in

            J1 = np.abs(COP - (self.parameters["eta_carnot"] * (Th / (Th - Ts))))
            J2 = np.abs(COP - Qh / (Qh - Qs))

            J = J1 + J2

            return J

        def carnot_model2(x, Ts_out, Th_in, Ts_in, mh, ms):
            """Residual function for the Carnot-efficiency model.

            Solves for (COP, Th_out) given a fixed minimum source outlet
            temperature Ts_out (used when the unconstrained solution
            violates Ts_in_minimum). See `carnot_model` for the underlying
            equations.
            """
            COP = x[0]
            Th_out = x[1]

            Qh = self.parameters["Cp_h"] * (Th_out - Th_in) * mh
            Qs = self.parameters["Cp_s"] * (Ts_in - Ts_out) * ms

            Th = Th_out
            Ts = Ts_in

            J1 = np.abs(COP - (self.parameters["eta_carnot"] * (Th / (Th - Ts))))
            J2 = np.abs(COP - Qh / (Qh - Qs))

            J = J1 + J2

            return J

        x0 = [self.parameters["COP_0"], Ts_in - 10]
        if self.parameters["mode"] == "carnot":
            result = minimize(
                carnot_model, x0, args=(Th_out, Th_in, Ts_in, mh, ms), method="Nelder-Mead"
            )
        else:
            result = minimize(
                lorenz_model, x0, args=(Th_out, Th_in, Ts_in, mh, ms), method="Nelder-Mead"
            )

        COP = result.x[0]
        Ts_out = result.x[1]

        if Ts_out < self.parameters["Ts_in_minimum"] + 273.15:
            x0 = [self.parameters["COP_0"], Th_out]
            if self.parameters["mode"] == "carnot":
                result = minimize(
                    carnot_model2,
                    x0,
                    args=(self.parameters["Ts_in_minimum"] + 273.15, Th_in, Ts_in, mh, ms),
                    method="Nelder-Mead",
                )
            else:
                result = minimize(
                    lorenz_model2,
                    x0,
                    args=(self.parameters["Ts_in_minimum"] + 273.15, Th_in, Ts_in, mh, ms),
                    method="Nelder-Mead",
                )

            COP = result.x[0]
            Th_out = result.x[1]
            Ts_out = self.parameters["Ts_in_minimum"] + 273.15

        thermal_production = self.parameters["Cp_h"] * (Th_out - Th_in) * mh
        electrical_consumption = thermal_production / COP

        return COP, Th_out, Ts_out, thermal_production, electrical_consumption

    def get_output(self):
        """Get output of the model.

        Returns
        -------
        dict
            Output dict with keys "COP", "Th_out" (°C), "Ts_out" (°C),
            "Thermal_production" (W) and "electrical_consumption" (W),
            as populated by `calculate_output`.
        """
        return self.output
