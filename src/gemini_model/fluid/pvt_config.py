"""Project-JSON-settable PVT properties for the well models (GM-01).

GEMINI's :class:`~gemini_model.fluid.pvt_water_stp.PVTConstantSTP` hard-codes
fresh water at standard conditions (rho 1000 kg/m3, mu 1e-3 Pa.s, c_p
4217.4 J/kg.K). The design-basis brine is heavier and less viscous
(rho 1030 kg/m3, mu 0.40e-3 Pa.s [P, DB-08]; c_p 4100 J/kg.K [A, DB-33]),
which adds about 7.4 bar of hydrostatic head over 2.5 km.

This module turns an optional ``"pvt"`` block in a component's project-JSON
parameters into a configured ``PVTConstantSTP``. The same block is the single
source of truth for the primary-loop liquid density and heat capacity:
:mod:`gemini_simulator.kpi` reads it from the producer well, and
:func:`heat_exchanger_fluid_parameters` gives the matching parameters to set on
the heat exchanger, boiler or CHP (the project builder must copy them there;
``kpi.fluid_consistency_issues`` reports a mismatch).

A ``"pvt"`` block may use the native ``PVTConstantSTP`` keys (``RHOL``,
``VISL``, ``CPL`` ...) or the unit-suffixed aliases in
:data:`PVT_PARAMETER_ALIASES`. Keys that start with ``_`` are comments and are
ignored. An empty or missing block gives exactly the GEMINI defaults.

Source: RC-01 "Inputs" and validation tests (hydrostatic head 252.6 bar for
1030 kg/m3 over 2500 m; 7.36 bar more than water).
"""

import math
import numbers

from gemini_model.fluid.pvt_water_stp import PVTConstantSTP

# Unit-suffixed alias -> native PVTConstantSTP parameter key.
PVT_PARAMETER_ALIASES = {
    "liquid_density_kg_m3": "RHOL",
    "liquid_viscosity_pa_s": "VISL",
    "liquid_specific_heat_j_kgk": "CPL",
    "liquid_thermal_conductivity_w_mk": "TCL",
    "gas_density_kg_m3": "RHOG",
    "gas_viscosity_pa_s": "VISG",
    "gas_specific_heat_j_kgk": "CPG",
    "gas_thermal_conductivity_w_mk": "TCG",
    "gas_mass_fraction": "GMF",
    "surface_tension_n_m": "SIGMA",
}

# Native keys that must be strictly positive (a zero or negative value would
# divide by zero or flip the sign of a pressure/heat term; Beggs-Brill uses
# (rho_l / SIGMA) ** 0.25).
_STRICTLY_POSITIVE_KEYS = ("RHOL", "RHOG", "VISL", "VISG", "CPL", "CPG", "TCL", "TCG", "SIGMA")


def _native_keys():
    """Return the parameter keys a default ``PVTConstantSTP`` accepts."""
    return frozenset(PVTConstantSTP().parameters)


def normalise_pvt_parameters(pvt_parameters):
    """Validate a ``"pvt"`` block and map it onto native ``PVTConstantSTP`` keys.

    Parameters
    ----------
    pvt_parameters: dict or None
        The ``"pvt"`` block from a component's project-JSON parameters.

    Returns
    -------
    dict
        Native-key overrides (empty for ``None`` or an empty block).

    Raises
    ------
    TypeError
        If the block is not a dict, a key is not a string, or a value is not
        a real number.
    ValueError
        If a key is unknown, an alias and its native key disagree, a value is
        not finite, a density/viscosity/heat capacity/conductivity/surface
        tension is not positive, or ``GMF`` is outside ``[0, 1)``.

    Python and numpy real numbers (``int``, ``float``, ``np.int64``,
    ``np.float32`` ...) are accepted; booleans are not.
    """
    if pvt_parameters is None:
        return {}
    if not isinstance(pvt_parameters, dict):
        raise TypeError(f"'pvt' must be a dict, got {type(pvt_parameters).__name__}.")

    native_keys = _native_keys()
    normalised = {}
    for key, value in pvt_parameters.items():
        if not isinstance(key, str):
            raise TypeError(f"'pvt' keys must be strings, got {key!r}.")
        if key.startswith("_"):
            continue  # comment/description key, same convention as project JSON
        native = PVT_PARAMETER_ALIASES.get(key, key)
        if native not in native_keys:
            allowed = sorted(native_keys | set(PVT_PARAMETER_ALIASES))
            raise ValueError(f"Unknown 'pvt' key '{key}'. Allowed keys: {', '.join(allowed)}.")
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise TypeError(f"'pvt' value for '{key}' must be a number, got {value!r}.")
        if not math.isfinite(value):
            raise ValueError(f"'pvt' value for '{key}' must be finite, got {value!r}.")
        if native in normalised and normalised[native] != value:
            raise ValueError(
                f"'pvt' sets '{native}' twice with different values "
                f"({normalised[native]!r} and {value!r})."
            )
        normalised[native] = float(value)

    for key in _STRICTLY_POSITIVE_KEYS:
        if key in normalised and normalised[key] <= 0.0:
            raise ValueError(f"'pvt' value for '{key}' must be > 0, got {normalised[key]!r}.")
    if "GMF" in normalised and not 0.0 <= normalised["GMF"] < 1.0:
        raise ValueError(f"'pvt' GMF must be in [0, 1), got {normalised['GMF']!r}.")
    return normalised


def build_pvt(pvt_parameters=None):
    """Return a fresh ``PVTConstantSTP`` with a ``"pvt"`` block applied.

    ``build_pvt(None)`` is identical to ``PVTConstantSTP()`` (GEMINI default,
    fresh water at STP).
    """
    pvt = PVTConstantSTP()
    overrides = normalise_pvt_parameters(pvt_parameters)
    if overrides:
        pvt.update_parameters(overrides)
    return pvt


def liquid_properties(pvt_parameters=None):
    """Return ``(density_kg_m3, specific_heat_j_kgk)`` of the liquid phase.

    These are the values the well models use for the same ``"pvt"`` block;
    keys the block does not set keep the ``PVTConstantSTP`` defaults.
    """
    parameters = build_pvt(pvt_parameters).parameters
    return float(parameters["RHOL"]), float(parameters["CPL"])


def heat_exchanger_fluid_parameters(pvt_parameters=None):
    """Return the ``fluid_density``/``specific_heat`` parameters for HEX, boiler and CHP.

    GEMINI's ``HeatExchanger``, ``Boiler`` and ``CHP`` take the liquid density
    and heat capacity as ``fluid_density`` (kg/m3) and ``specific_heat``
    (J/kg.K) parameters. Use this helper so a component on the brine loop gets
    the same values as the wells (single source of truth, GM-01). Note that
    GEMINI's ``HeatExchanger`` applies one ``fluid_density``/``specific_heat``
    to both its primary (brine) and secondary (water) sides.
    """
    density_kg_m3, specific_heat_j_kgk = liquid_properties(pvt_parameters)
    return {"fluid_density": density_kg_m3, "specific_heat": specific_heat_j_kgk}
