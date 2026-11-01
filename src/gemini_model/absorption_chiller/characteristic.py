"""Method B: characteristic equation (ΔΔt method) of a single-effect LiBr absorption chiller.

Source: RC-05 Method B (standard Ziegler/Hellmann characteristic-equation form; the card
gives the form, the coefficients are ``MISSING`` and must be calibrated).

- ``ΔΔt = (t̄_G - t̄_AC) - B (t̄_AC - t̄_E)`` with t̄ the arithmetic mean of inlet and outlet
  of the external circuits: G generator (hot water), AC absorber + condenser (cooling water
  in series), E evaporator (chilled water). B ≈ 1.1-1.2 (Dühring slope, LiBr) [A].
- Capacity ``Q_E = s_E (ΔΔt - ΔΔt_min)``; drive heat ``Q_G = s_G (ΔΔt - ΔΔt_min,G)``;
  ``COP = Q_E / Q_G``.

Calibration (RC-05 option ii, no datasheet found): fit s_E, ΔΔt_min, s_G, ΔΔt_min,G to
Method A at its rating points (T_chw 44 °F, T_cw 85 °F, T_hw 180-240 °F). Method A gives
inlet/outlet temperatures, so the mean temperatures need the external-circuit temperature
changes at rating: generator ΔT 7.2 K on hot water [L Guidebook p.267], cooling-water range
5 K [A RC-07], chilled-water ΔT 6 K [A, the DB-32 design ΔT 20/26 °C; the card gives no
rating value]. The fit is a weighted least-squares line through (ΔΔt, F) and (ΔΔt, F/COP)
with weights 1/y, i.e. it minimises the relative error. s_E and s_G are fractions of the
nominal capacity per kelvin; multiply by the nominal capacity for W/K.

Coefficients for the default B = 1.15 (``DOCUMENTED_COEFFICIENTS``, reproduced by
``calibrate_to_ghc1989()`` and checked in the unit tests): s_E = 0.022260 1/K,
ΔΔt_min = 9.503 K, s_G = 0.032491 1/K, ΔΔt_min,G = 7.453 K (calibrated ΔΔt range 21.06-54.39 K).
On the rating line the fit reproduces Method A within 1.0 % (capacity) and 2.1 % (COP).
"""

from functools import lru_cache

import numpy as np

from gemini_model.absorption_chiller import ghc1989

DEFAULT_DUHRING_SLOPE = 1.15  # B, RC-05 [A] 1.1-1.2 (mid-point)
DEFAULT_GENERATOR_DELTA_T_K = 7.2  # hot-water ΔT at rating, RC-05 (Guidebook p.267) [L]
DEFAULT_COOLING_WATER_RANGE_K = 5.0  # RC-07 [A] (Guidebook rating ΔT 9 °F)
DEFAULT_CHILLED_WATER_DELTA_T_K = 6.0  # [A] DB-32 design ΔT (20/26 °C), rating ΔT not on card
DEFAULT_RATING_HOT_WATER_DEGF = tuple(float(t) for t in range(180, 241, 5))

# Output of calibrate_to_ghc1989() with the defaults above (rounded; see module docstring).
DOCUMENTED_COEFFICIENTS = {
    "s_e_per_k": 0.022260,
    "ddt_min_k": 9.503,
    "s_g_per_k": 0.032491,
    "ddt_min_g_k": 7.453,
}

COEFFICIENT_KEYS = ("s_e_per_k", "ddt_min_k", "s_g_per_k", "ddt_min_g_k")


def delta_delta_t_k(generator_mean_degc, absorber_condenser_mean_degc, evaporator_mean_degc, b):
    """Return the characteristic temperature difference ΔΔt (K) (RC-05 Method B)."""
    return (generator_mean_degc - absorber_condenser_mean_degc) - b * (
        absorber_condenser_mean_degc - evaporator_mean_degc
    )


def validate_coefficients(coefficients):
    """Check a characteristic-coefficient dict and return it with float values.

    Requires the keys in :data:`COEFFICIENT_KEYS`, s_E > 0, s_G > 0 and
    ΔΔt_min,G ≤ ΔΔt_min (drive heat must not vanish while the machine still cools).
    """
    missing = [key for key in COEFFICIENT_KEYS if key not in coefficients]
    if missing:
        raise ValueError(f"characteristic_coefficients is missing {missing}")
    values = {key: float(coefficients[key]) for key in COEFFICIENT_KEYS}
    if not all(np.isfinite(list(values.values()))):
        raise ValueError(f"characteristic_coefficients must be finite: {values}")
    if values["s_e_per_k"] <= 0.0 or values["s_g_per_k"] <= 0.0:
        raise ValueError("characteristic_coefficients s_e_per_k and s_g_per_k must be > 0")
    if values["ddt_min_g_k"] > values["ddt_min_k"]:
        raise ValueError(
            "characteristic_coefficients need ddt_min_g_k <= ddt_min_k "
            "(otherwise the COP is unbounded near zero load)"
        )
    return values


def _weighted_line(x, y):
    """Fit y = s (x - x0) by least squares on the relative error (weights 1/y)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2 or np.any(y <= 0.0):
        raise ValueError("calibration needs at least two points with positive values")
    slope, intercept = np.polyfit(x, y, 1, w=1.0 / y)
    if slope <= 0.0:
        raise ValueError("calibration gave a non-positive slope")
    return float(slope), float(-intercept / slope)


def fit_coefficients(delta_delta_t, capacity_fraction, cop):
    """Fit the characteristic-equation coefficients to operating points.

    Use it for a manufacturer datasheet (RC-05 option i) or for Method A (option ii).

    Parameters
    ----------
    delta_delta_t: sequence of float
        ΔΔt of each point (K), from :func:`delta_delta_t_k`.
    capacity_fraction: sequence of float
        Cooling capacity of each point as a fraction of the nominal capacity (-).
    cop: sequence of float
        Thermal COP of each point (-); the drive heat fraction is capacity / COP.

    Returns
    -------
    dict
        ``s_e_per_k``, ``ddt_min_k``, ``s_g_per_k``, ``ddt_min_g_k`` (validated).
    """
    capacity_fraction = np.asarray(capacity_fraction, dtype=float)
    drive_fraction = capacity_fraction / np.asarray(cop, dtype=float)
    s_e, ddt_min = _weighted_line(delta_delta_t, capacity_fraction)
    s_g, ddt_min_g = _weighted_line(delta_delta_t, drive_fraction)
    return validate_coefficients(
        {"s_e_per_k": s_e, "ddt_min_k": ddt_min, "s_g_per_k": s_g, "ddt_min_g_k": ddt_min_g}
    )


def rating_delta_delta_t_k(
    hot_water_degf,
    duhring_slope=DEFAULT_DUHRING_SLOPE,
    generator_delta_t_k=DEFAULT_GENERATOR_DELTA_T_K,
    chilled_water_delta_t_k=DEFAULT_CHILLED_WATER_DELTA_T_K,
    cooling_water_range_k=DEFAULT_COOLING_WATER_RANGE_K,
    chilled_water_degf=ghc1989.RATING_CHILLED_WATER_DEGF,
    cooling_water_degf=ghc1989.RATING_COOLING_WATER_DEGF,
):
    """Return ΔΔt (K) of a Method A operating point (inlet/leaving °F → circuit means).

    Hot water enters at T_hw (mean T_hw - ΔT_G/2); chilled water leaves at T_chw
    (mean T_chw + ΔT_E/2); cooling water enters at T_cw (mean T_cw + range/2).
    """
    generator_mean = ghc1989.degf_to_degc(np.asarray(hot_water_degf, float)) - (
        generator_delta_t_k / 2.0
    )
    absorber_condenser_mean = ghc1989.degf_to_degc(cooling_water_degf) + cooling_water_range_k / 2
    evaporator_mean = ghc1989.degf_to_degc(chilled_water_degf) + chilled_water_delta_t_k / 2.0
    return delta_delta_t_k(generator_mean, absorber_condenser_mean, evaporator_mean, duhring_slope)


@lru_cache(maxsize=32)
def _calibrate_cached(
    duhring_slope,
    generator_delta_t_k,
    chilled_water_delta_t_k,
    cooling_water_range_k,
    hot_water_degf,
    chilled_water_degf,
    cooling_water_degf,
):
    ddt = rating_delta_delta_t_k(
        hot_water_degf,
        duhring_slope,
        generator_delta_t_k,
        chilled_water_delta_t_k,
        cooling_water_range_k,
        chilled_water_degf,
        cooling_water_degf,
    )
    hot_water = np.asarray(hot_water_degf, dtype=float)
    fraction = ghc1989.capacity_fraction(hot_water, chilled_water_degf, cooling_water_degf)
    cop = ghc1989.cop(hot_water)
    coefficients = fit_coefficients(ddt, fraction, cop)

    fitted_fraction = coefficients["s_e_per_k"] * (ddt - coefficients["ddt_min_k"])
    fitted_drive = coefficients["s_g_per_k"] * (ddt - coefficients["ddt_min_g_k"])
    report = {
        "max_rel_error_capacity": float(np.max(np.abs(fitted_fraction / fraction - 1.0))),
        "max_rel_error_cop": float(np.max(np.abs((fitted_fraction / fitted_drive) / cop - 1.0))),
        "ddt_range_k": (float(np.min(ddt)), float(np.max(ddt))),
    }
    return coefficients, report


def calibrate_to_ghc1989(
    duhring_slope=DEFAULT_DUHRING_SLOPE,
    generator_delta_t_k=DEFAULT_GENERATOR_DELTA_T_K,
    chilled_water_delta_t_k=DEFAULT_CHILLED_WATER_DELTA_T_K,
    cooling_water_range_k=DEFAULT_COOLING_WATER_RANGE_K,
    hot_water_degf=DEFAULT_RATING_HOT_WATER_DEGF,
    chilled_water_degf=ghc1989.RATING_CHILLED_WATER_DEGF,
    cooling_water_degf=ghc1989.RATING_COOLING_WATER_DEGF,
    with_report=False,
):
    """Calibrate Method B to Method A at its rating points (RC-05 option ii).

    Rating points: T_chw 44 °F, T_cw 85 °F, T_hw 180-240 °F (every 5 °F by default).
    Returns the coefficient dict (see :func:`fit_coefficients`); with ``with_report``
    also a dict with the maximum relative capacity/COP error on the rating points and
    the calibrated ΔΔt range (K). Results are cached per argument set.
    """
    coefficients, report = _calibrate_cached(
        float(duhring_slope),
        float(generator_delta_t_k),
        float(chilled_water_delta_t_k),
        float(cooling_water_range_k),
        tuple(float(t) for t in hot_water_degf),
        float(chilled_water_degf),
        float(cooling_water_degf),
    )
    if with_report:
        return dict(coefficients), dict(report)
    return dict(coefficients)
