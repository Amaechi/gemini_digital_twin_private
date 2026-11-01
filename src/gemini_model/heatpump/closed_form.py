"""Closed-form heat-pump COP, robust operating point and HTHP limit screening (GM-03).

Source: RC-04 (``data/knowledge/reference_cards/RC-04_heat_pump.md``, copied in
``REFERENCE.md``); Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989)
Ch13 p.250, 258, Table 13.2; spec §7.2 GM-03. DEA COP-by-lift figures from RC-10 (plausibility
only).

- RC-04 eq. 1 (Carnot): ``COP_C = T_cond / (T_cond - T_evap)`` (K).
- RC-04 eq. 2 (Lorenz): ``T̄ = (T_out - T_in) / ln(T_out / T_in)`` (log mean, K) for sink and
  source; ``COP = η_L · T̄_sink / (T̄_sink - T̄_source)``.
- RC-04 eq. 3: ``Q_h = Q_src · COP / (COP - 1)``; ``P_el = Q_h / COP``.
- RC-04 eq. 4: refrigerant condensing / evaporating temperature = sink outlet + approach /
  source outlet - approach, approach 3-5 K [A] (register HP-APPROACH: 4 K).

HTHP limits at 100 °C are not covered by the 1989 book (Table 13.2: 82-104 °C, outdated): they
come from a cited modern datasheet and stay ``MISSING`` until one is supplied. Without a datasheet
the RC-04 default caps apply -- COP ≤ 0.6 × Carnot at the refrigerant temperatures and lift (sink
outlet - source outlet) ≤ 60 K [A]; they are the DesignVerifier's register entries VER-HP-ETA and
VER-HP-LIFT (check PH-14), reused here, not duplicated. The maximum sink temperature has no
default: it is reported as missing and not checked.

:func:`solve_operating_point` solves the same problem as GEMINI's Nelder-Mead path of
:class:`~gemini_model.heatpump.heatpump_basic.HeatpumpBasic` (sink outlet at its target, source
outlet unknown; if the source outlet would fall below its minimum, it is fixed there and the sink
outlet drops instead) without a starting guess: the COP is the closed-form expression above and the
one unknown temperature is a bracketed scalar root (``brentq``) of the energy balance.
"""

import itertools
import math

from scipy.optimize import brentq

KELVIN = 273.15

# Register defaults (config/assumptions.yaml); unit_test/gemini_model/heatpump checks equality.
DEFAULT_LORENZ_EFFICIENCY = 0.5
"""η_L 0.50 [A] (RC-04; GEMINI default), register HP-ETA-L; sensitivity 0.40-0.55."""
DEFAULT_APPROACH_K = 4.0
"""Evaporator and condenser approach 4 K [A] (RC-04 eq. 4: 3-5 K), register HP-APPROACH."""
RC04_DEFAULT_CARNOT_FRACTION = 0.6
"""COP ≤ 0.6 × Carnot without a datasheet (RC-04 [A]), register VER-HP-ETA."""
RC04_DEFAULT_MAX_LIFT_K = 60.0
"""Lift ≤ 60 K without a datasheet (RC-04 [A]), register VER-HP-LIFT."""
RC04_APPROACH_RANGE_K = (3.0, 5.0)
"""RC-04 eq. 4 approach range [A]; outside it the model warns."""
CARNOT_FRACTION_ID = "VER-HP-ETA"
MAX_LIFT_ID = "VER-HP-LIFT"
LORENZ_EFFICIENCY_ID = "HP-ETA-L"
APPROACH_ID = "HP-APPROACH"

STANDARD_MAX_WATER_DELTA_T_K = 11.0
"""Evaporator/condenser water ΔT: 5.6 K rating, up to 11 K standard, more needs 4/8-pass
(Guidebook p.250, RC-04)."""

DEA_COP_BY_LIFT = ((40.0, 4.45), (65.0, 2.75), (80.0, 2.4), (100.0, 2.05))
"""DEA nominal HTHP COP by lift (RC-10 'Sourced values', DEA Industrial Process Heat ch. 2). No
entry below 40 K: plausibility band only, never the COP source (RC-10 conventions)."""

_COP_CLIP = 1.0e9
"""Finite stand-in for an infinite COP inside root functions (no lift)."""


def log_mean_temperature_k(t_in_k, t_out_k):
    """Return the log-mean temperature (K) of a stream gliding from t_in_k to t_out_k (RC-04 eq. 2).

    Equal temperatures (no glide) return that temperature.
    """
    if t_in_k <= 0.0 or t_out_k <= 0.0:
        raise ValueError(f"absolute temperatures must be > 0 K, got {t_in_k}, {t_out_k}")
    if math.isclose(t_in_k, t_out_k, rel_tol=1e-12, abs_tol=0.0):
        return 0.5 * (t_in_k + t_out_k)
    return (t_out_k - t_in_k) / math.log(t_out_k / t_in_k)


def carnot_cop(condensing_degc, evaporating_degc, efficiency=1.0):
    """Return ``efficiency × T_cond / (T_cond - T_evap)`` (RC-04 eq. 1); inf if T_cond ≤ T_evap.

    Same arithmetic as the DesignVerifier's PH-13/PH-14 (°C + 273.15), so a COP capped here at
    ``fraction × carnot_cop(...)`` has a margin of exactly 0 there.
    """
    cond_k = condensing_degc + KELVIN
    evap_k = evaporating_degc + KELVIN
    if cond_k <= evap_k:
        return math.inf
    return efficiency * (cond_k / (cond_k - evap_k))


def lorenz_cop(sink_in_degc, sink_out_degc, source_in_degc, source_out_degc, efficiency=1.0):
    """Return ``efficiency × T̄_sink / (T̄_sink - T̄_source)`` (RC-04 eq. 2), water temperatures °C.

    Returns inf if the mean sink temperature does not exceed the mean source temperature (no lift).
    """
    t_sink = log_mean_temperature_k(sink_in_degc + KELVIN, sink_out_degc + KELVIN)
    t_source = log_mean_temperature_k(source_in_degc + KELVIN, source_out_degc + KELVIN)
    if t_sink <= t_source:
        return math.inf
    return efficiency * t_sink / (t_sink - t_source)


def mean_lift_k(sink_in_degc, sink_out_degc, source_in_degc, source_out_degc):
    """Return the Lorenz mean lift T̄_sink - T̄_source (K, RC-04 eq. 2)."""
    t_sink = log_mean_temperature_k(sink_in_degc + KELVIN, sink_out_degc + KELVIN)
    t_source = log_mean_temperature_k(source_in_degc + KELVIN, source_out_degc + KELVIN)
    return t_sink - t_source


def sink_heat_from_source(source_heat_w, cop):
    """Return ``Q_h = Q_src · COP / (COP - 1)`` (RC-04 eq. 3); COP must exceed 1."""
    if not cop > 1.0:
        raise ValueError(f"COP must be > 1 to draw heat from the source, got {cop}")
    return source_heat_w * cop / (cop - 1.0)


def dea_reference_cop(lift_k):
    """Return the DEA nominal COP interpolated at ``lift_k`` (40-100 K), else None (RC-10).

    Plausibility only: RC-10 does not state DEA's lift definition, and has no entry below 40 K.
    """
    lifts = [item[0] for item in DEA_COP_BY_LIFT]
    if lift_k is None or not lifts[0] <= lift_k <= lifts[-1]:
        return None
    for (l0, c0), (l1, c1) in itertools.pairwise(DEA_COP_BY_LIFT):
        if l0 <= lift_k <= l1:
            return c0 + (c1 - c0) * (lift_k - l0) / (l1 - l0)
    return None  # pragma: no cover - the loop covers the closed range


class _CopModel:
    """COP at given sink and source outlet temperatures (°C), with the optional Carnot cap."""

    def __init__(
        self,
        mode,
        efficiency,
        sink_in_degc,
        source_in_degc,
        carnot_cap_fraction,
        condenser_approach_k,
        evaporator_approach_k,
    ):
        if mode not in ("carnot", "lorenz"):
            raise ValueError(f"mode must be 'carnot' or 'lorenz', got {mode!r}")
        self.mode = mode
        self.efficiency = efficiency
        self.sink_in = sink_in_degc
        self.source_in = source_in_degc
        self.fraction = carnot_cap_fraction
        self.condenser_approach = condenser_approach_k
        self.evaporator_approach = evaporator_approach_k

    def uncapped(self, sink_out, source_out):
        """Return GEMINI's COP: Lorenz on water log means, or Carnot at (Th_out, Ts_in)."""
        if self.mode == "lorenz":
            return lorenz_cop(self.sink_in, sink_out, self.source_in, source_out, self.efficiency)
        return carnot_cop(sink_out, self.source_in, self.efficiency)

    def cap(self, sink_out, source_out):
        """Return ``fraction × Carnot`` at the refrigerant temperatures (inf without a cap)."""
        if self.fraction is None:
            return math.inf
        condensing = sink_out + self.condenser_approach
        evaporating = source_out - self.evaporator_approach
        return self.fraction * carnot_cop(condensing, evaporating)

    def __call__(self, sink_out, source_out):
        return min(self.uncapped(sink_out, source_out), self.cap(sink_out, source_out))


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _idle(sink_in_degc, source_in_degc, reason):
    return {
        "running": False,
        "cop": 0.0,
        "cop_uncapped": 0.0,
        "sink_out_degc": sink_in_degc,
        "source_out_degc": source_in_degc,
        "sink_heat_w": 0.0,
        "source_heat_w": 0.0,
        "power_el_w": 0.0,
        "branch": "idle",
        "warnings": [f"heat pump idle: {reason}"],
    }


def solve_operating_point(
    *,
    mode,
    efficiency,
    sink_in_degc,
    sink_out_target_degc,
    source_in_degc,
    sink_capacity_rate_w_k,
    source_capacity_rate_w_k,
    source_out_min_degc,
    carnot_cap_fraction=None,
    condenser_approach_k=DEFAULT_APPROACH_K,
    evaporator_approach_k=DEFAULT_APPROACH_K,
):
    """Solve the heat-pump operating point with closed-form COP and a bracketed root (RC-04).

    Same problem as GEMINI's Nelder-Mead path: the sink leaves at ``sink_out_target_degc``; the
    source outlet follows from ``Q_src = Q_h (1 - 1/COP)`` (RC-04 eq. 3). If it would fall below
    ``source_out_min_degc`` it is fixed there and the sink outlet drops instead. Capacity rates are
    ``ṁ c_p`` (W/K). ``carnot_cap_fraction`` (None = no cap) caps COP at that fraction of Carnot at
    the refrigerant temperatures (sink outlet + condenser approach, source outlet - evaporator
    approach).

    Returns a dict: ``running``, ``cop``, ``cop_uncapped``, ``sink_out_degc``, ``source_out_degc``,
    ``sink_heat_w``, ``source_heat_w``, ``power_el_w``, ``branch`` ("target", "source_floor" or
    "idle") and ``warnings``. ``sink_heat_w = source_heat_w + power_el_w`` exactly. The heat pump
    is idle (no exception) when a flow is zero, there is no heating need, the source is at or
    below its minimum, COP ≤ 1, or there is no lift (a heat exchanger suffices). Negative flows
    and non-finite inputs raise ValueError.
    """
    for name, value in (
        ("sink_in_degc", sink_in_degc),
        ("sink_out_target_degc", sink_out_target_degc),
        ("source_in_degc", source_in_degc),
        ("source_out_min_degc", source_out_min_degc),
    ):
        _finite(value, name)
    for name, value in (
        ("sink_capacity_rate_w_k", sink_capacity_rate_w_k),
        ("source_capacity_rate_w_k", source_capacity_rate_w_k),
    ):
        if _finite(value, name) < 0.0:
            raise ValueError(
                f"{name} must be >= 0 (a negative flow is a wiring error), got {value}"
            )
    if not _finite(efficiency, "efficiency") > 0.0:
        raise ValueError(f"efficiency must be > 0, got {efficiency}")
    if carnot_cap_fraction is not None and not 0.0 < _finite(carnot_cap_fraction, "fraction") <= 1:
        raise ValueError(f"carnot_cap_fraction must be in (0, 1], got {carnot_cap_fraction}")
    sink_in_degc, source_in_degc = float(sink_in_degc), float(source_in_degc)
    model = _CopModel(
        mode,
        efficiency,
        sink_in_degc,
        source_in_degc,
        carnot_cap_fraction,
        condenser_approach_k,
        evaporator_approach_k,
    )
    c_h, c_s = float(sink_capacity_rate_w_k), float(source_capacity_rate_w_k)
    target, t_min = float(sink_out_target_degc), float(source_out_min_degc)
    if c_h <= 0.0:
        return _idle(sink_in_degc, source_in_degc, "no sink flow")
    q_h_target = c_h * (target - sink_in_degc)
    if q_h_target <= 0.0:
        return _idle(sink_in_degc, source_in_degc, "sink inlet at or above the outlet target")
    if c_s <= 0.0:
        return _idle(sink_in_degc, source_in_degc, "no source flow")
    if source_in_degc <= t_min:
        return _idle(sink_in_degc, source_in_degc, "source inlet at or below Ts_in_minimum")

    def clipped(sink_out, source_out):
        return min(model(sink_out, source_out), _COP_CLIP)

    def source_out_at(cop):
        return source_in_degc - q_h_target * (1.0 - 1.0 / cop) / c_s

    def residual(cop):  # strictly increasing in cop: a higher COP cools the source more
        return cop - clipped(target, source_out_at(cop))

    if residual(1.0) >= 0.0:
        return _idle(sink_in_degc, source_in_degc, "COP ≤ 1 at these temperatures")

    branch = "target"
    available = (source_in_degc - t_min) * c_s / q_h_target  # = 1 - 1/COP at the source floor
    if available >= 1.0:
        if not math.isfinite(model.uncapped(target, source_in_degc - q_h_target / c_s)):
            return _idle(sink_in_degc, source_in_degc, "no lift: a heat exchanger suffices")
        cop_hi = 2.0
        while residual(cop_hi) < 0.0:
            cop_hi *= 2.0
            if cop_hi > _COP_CLIP:
                return _idle(sink_in_degc, source_in_degc, "no operating point")
    else:
        cop_hi = 1.0 / (1.0 - available)
        if residual(cop_hi) < 0.0:
            branch = "source_floor"

    if branch == "target":
        cop = brentq(residual, 1.0, cop_hi, xtol=1e-14, rtol=1e-15, maxiter=500)
        sink_out = target
        source_out = max(source_out_at(cop), t_min)
    else:
        q_s = c_s * (source_in_degc - t_min)
        sink_low = sink_in_degc + q_s / c_h  # sink outlet at COP → ∞

        def floor_residual(sink_out):
            q_h = c_h * (sink_out - sink_in_degc)
            return q_h / (q_h - q_s) - clipped(sink_out, t_min)

        # Scan down from the target (GEMINI starts there) for the first sign change.
        upper = target
        lower = None
        steps = 400
        for k in range(1, steps + 1):
            fraction = 1.0 - k / steps if k < steps else 1e-9
            candidate = sink_low + (target - sink_low) * fraction
            if not math.isfinite(model.uncapped(candidate, t_min)):
                break  # no lift below this sink outlet
            if floor_residual(candidate) >= 0.0:
                lower = candidate
                break
            upper = candidate
        if lower is None:
            return _idle(sink_in_degc, source_in_degc, "no operating point at the source floor")
        sink_out = brentq(floor_residual, lower, upper, xtol=1e-12, rtol=1e-15, maxiter=500)
        source_out = t_min

    cop = model(sink_out, source_out)
    # No lift on the water side (uncapped COP infinite): the cap must not make the pump run.
    if not cop < _COP_CLIP or not math.isfinite(model.uncapped(sink_out, source_out)):
        return _idle(sink_in_degc, source_in_degc, "no lift: a heat exchanger suffices")
    sink_heat = c_h * (sink_out - sink_in_degc)
    power_el = sink_heat / cop
    warnings = []
    if branch == "source_floor":
        warnings.append(
            f"source outlet held at Ts_in_minimum {t_min:g} °C: sink outlet "
            f"{sink_out:.2f} °C below the {target:g} °C target"
        )
    return {
        "running": True,
        "cop": cop,
        "cop_uncapped": model.uncapped(sink_out, source_out),
        "sink_out_degc": sink_out,
        "source_out_degc": source_out,
        "sink_heat_w": sink_heat,
        "source_heat_w": sink_heat - power_el,
        "power_el_w": power_el,
        "branch": branch,
        "warnings": warnings,
    }


def _datasheet_value(value, name, low=None, high=None):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if low is not None and value <= low:
        raise ValueError(f"{name} must be > {low}, got {value}")
    if high is not None and value > high:
        raise ValueError(f"{name} must be ≤ {high}, got {value}")
    return value


def hthp_limits(
    datasheet=None, max_lift_k=None, max_sink_temperature_degc=None, max_carnot_fraction=None
):
    """Return the HTHP limits in force: cited datasheet values, else RC-04 defaults / MISSING.

    Datasheet values need a ``datasheet`` citation (never invented). Returns a dict with
    ``max_lift_k`` / ``max_carnot_fraction`` in force, the datasheet-only values (None when
    missing) for the verifier's ``HeatPumpPoint``, ``max_sink_temperature_degc`` (None = MISSING,
    not checked), ``missing_inputs``, ``assumption_ids`` (VER-HP-LIFT / VER-HP-ETA when their
    defaults apply) and ``limits_source``.
    """
    lift = _datasheet_value(max_lift_k, "max_lift_k", low=0.0)
    sink = _datasheet_value(max_sink_temperature_degc, "max_sink_temperature_degc")
    fraction = _datasheet_value(max_carnot_fraction, "max_carnot_fraction", low=0.0, high=1.0)
    given = [v for v in (lift, sink, fraction) if v is not None]
    if given and not (isinstance(datasheet, str) and datasheet.strip()):
        raise ValueError(
            "HTHP limits (max_lift_k, max_sink_temperature_degc, max_carnot_fraction) need a "
            "cited 'datasheet' (RC-04: never invent them)"
        )
    missing = [
        name
        for name, value in (
            ("max_lift_k", lift),
            ("max_sink_temperature_degc", sink),
            ("max_carnot_fraction", fraction),
        )
        if value is None
    ]
    assumption_ids = []
    if fraction is None:
        assumption_ids.append(CARNOT_FRACTION_ID)
    if lift is None:
        assumption_ids.append(MAX_LIFT_ID)
    if missing:
        source = (
            f"MISSING HTHP datasheet values {missing}: RC-04 defaults "
            f"{', '.join(assumption_ids) or 'none'}; max sink temperature "
            f"{'not checked' if sink is None else 'from the datasheet'}"
        )
        if datasheet:
            source = f"datasheet: {datasheet}; " + source
    else:
        source = f"datasheet: {datasheet}"
    return {
        "max_lift_k": RC04_DEFAULT_MAX_LIFT_K if lift is None else lift,
        "max_carnot_fraction": RC04_DEFAULT_CARNOT_FRACTION if fraction is None else fraction,
        "datasheet_max_lift_k": lift,
        "datasheet_max_carnot_fraction": fraction,
        "max_sink_temperature_degc": sink,
        "missing_inputs": missing,
        "assumption_ids": assumption_ids,
        "limits_source": source,
    }
