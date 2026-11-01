"""Nodal (flow-rate) analysis for production/injection wells.

Reproduces, in gemini_model terms, the classic nodal-analysis idea also
used by ``gemini_application.productionwell.ProductionWellPerformance``
(sweeping candidate flow rates until the well-side and reservoir-side
bottomhole pressures match). Here the same idea is solved directly with
a root finder (``scipy.optimize.brentq``) instead of a brute-force sweep,
which is faster and more precise.

The nodal point is the well bottomhole: for a given flow rate ``Q``,

- the *reservoir* side delivers a bottomhole pressure ``p_bh_reservoir(Q)``
  (via an ``IPR`` model), and
- the *well* side requires a bottomhole pressure ``p_bh_well(Q)`` to
  sustain flow ``Q`` up to a fixed wellhead pressure (via one or two
  ``DPDT`` well segments, optionally boosted by an ``ESP`` in between).

The flow rate that reconciles the two (``p_bh_well(Q) == p_bh_reservoir(Q)``)
is the physically consistent solution.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01 (WP-05): optional
# ``friction_opposes_flow`` for the injection solves (RC-01 eq 12: friction is a loss for the
# downward injection flow). The default (False) keeps GEMINI's original sign, where friction
# adds to the injector bottomhole pressure as if the flow went up.

from scipy.optimize import brentq


def _reservoir_bottomhole_pressure(ipr, flow_rate):
    """Bottomhole pressure delivered by the reservoir at ``flow_rate`` (Pa)."""
    ipr.calculate_output({"flow": flow_rate}, None)
    return ipr.get_output()["bottomhole_pressure"]


def _well_bottomhole_pressure_production(
    dpdt_top,
    dpdt_bottom,
    esp,
    flow_rate,
    wellhead_pressure,
    wellhead_temperature,
    ambient_temperature,
    esp_freq,
):
    """Bottomhole pressure required to sustain production ``flow_rate`` (Pa).

    Propagates *up* from the wellhead is not how VLP direction works here;
    instead, following ``gemini_application.productionwell``, the well is
    walked *forward* (top-down, ``direction="down"``) from the wellhead to
    the ESP intake, boosted backward across the ESP, and forward again
    down to bottomhole. If ``esp`` is ``None``, ``dpdt_bottom`` alone
    carries the flow from wellhead to bottomhole.
    """
    u_top = {
        "pressure": wellhead_pressure,
        "temperature": wellhead_temperature,
        "temperature_ambient": ambient_temperature,
        "flowrate": flow_rate,
        "direction": "down",
    }
    dpdt_top.calculate_output(u_top, None)
    y_top = dpdt_top.get_output()

    if esp is None:
        return y_top["pressure_output"]

    esp.calculate_output({"pump_freq": esp_freq, "pump_flow": flow_rate}, None)
    pump_head = esp.get_output()["pump_head"]
    # ESP discharges what dpdt_top delivered; the intake (pre-boost) pressure
    # is lower by the pump head.
    intake_pressure = max(0.0, y_top["pressure_output"] - pump_head)

    u_bottom = {
        "pressure": intake_pressure,
        "temperature": y_top["temperature_output"],
        "temperature_ambient": ambient_temperature,
        "flowrate": flow_rate,
        "direction": "down",
    }
    dpdt_bottom.calculate_output(u_bottom, None)
    return dpdt_bottom.get_output()["pressure_output"]


def solve_production_flow_rate(
    ipr,
    dpdt_top,
    wellhead_pressure,
    wellhead_temperature,
    ambient_temperature,
    dpdt_bottom=None,
    esp=None,
    esp_freq=None,
    flow_bracket=(1e-6, 0.2),
):
    """Solve for the production flow rate that balances well and reservoir.

    Parameters
    ----------
    ipr: gemini_model.reservoir.inflow_performance.IPR
        Reservoir inflow model (parameterized with ``type="production_reservoir"``).
    dpdt_top: gemini_model.well.pressure_drop.DPDT
        Well segment from wellhead to the ESP intake (or straight to
        bottomhole if ``dpdt_bottom``/``esp`` are not given).
    wellhead_pressure, wellhead_temperature: float
        Fixed wellhead boundary conditions, in Pa and K.
    ambient_temperature: float
        Ambient/formation temperature for heat-loss calculations, in K.
    dpdt_bottom: gemini_model.well.pressure_drop.DPDT, optional
        Well segment from the ESP discharge (intake side, production
        direction) to bottomhole. Required if ``esp`` is given.
    esp: gemini_model.pump.esp.ESP, optional
        ESP model boosting pressure between ``dpdt_top`` and ``dpdt_bottom``.
    esp_freq: float, optional
        ESP operating frequency, required if ``esp`` is given.
    flow_bracket: tuple[float, float]
        ``(low, high)`` flow rate bracket (m3/s) to search for the root.
        Must bracket a sign change; widen if ``brentq`` raises.

    Returns
    -------
    float
        The solved flow rate (m3/s).
    """
    if esp is not None and (dpdt_bottom is None or esp_freq is None):
        raise ValueError("dpdt_bottom and esp_freq are required when esp is given.")

    def residual(flow_rate):
        p_well = _well_bottomhole_pressure_production(
            dpdt_top,
            dpdt_bottom,
            esp,
            flow_rate,
            wellhead_pressure,
            wellhead_temperature,
            ambient_temperature,
            esp_freq,
        )
        p_reservoir = _reservoir_bottomhole_pressure(ipr, flow_rate)
        return p_well - p_reservoir

    return brentq(residual, flow_bracket[0], flow_bracket[1])


def _well_bottomhole_pressure_injection(
    dpdt, flow_rate, wellhead_pressure, wellhead_temperature, friction_opposes_flow=False
):
    """Bottomhole pressure resulting from injecting ``flow_rate`` (Pa).

    Injection flows *down* the well from a fixed wellhead pressure; no
    ESP is involved on the injection side.

    ``DPDT`` treats a positive ``flowrate`` as upward (production) flow,
    whatever the walking ``direction``: walked down, it returns
    ``P_wh + rho g H + dP_fric``. With ``friction_opposes_flow=False``
    (default, GEMINI's original behaviour) the injection rate is passed
    as is, so friction *raises* the bottomhole pressure. With ``True`` it
    is passed as a negative (downward) rate: the Darcy-Weisbach term
    ``f (L/D) rho |v| v / 2`` then opposes the downward flow, giving
    ``P_bh = P_wh + rho g H - dP_fric`` (RC-01 eq 12, Pajak et al.), and
    the heat-exchange term has the sign of a fluid moving down. The
    corrected mode is single-phase only (Beggs-Brill is not set up for
    downward two-phase flow); a ``ValueError`` is raised otherwise.
    """
    if friction_opposes_flow and flow_rate != 0:
        _require_single_phase(dpdt, wellhead_pressure, wellhead_temperature)
    u = {
        "pressure": wellhead_pressure,
        "temperature": wellhead_temperature,
        "temperature_ambient": wellhead_temperature,
        "flowrate": -flow_rate if friction_opposes_flow else flow_rate,
        "direction": "down",
    }
    dpdt.calculate_output(u, None)
    return dpdt.get_output()["pressure_output"]


def _require_single_phase(dpdt, pressure, temperature):
    """Raise ``ValueError`` if the well fluid has a gas mass fraction (two-phase)."""
    gas_mass_fraction = dpdt.PVT.get_pvt(pressure, temperature)[2]
    if gas_mass_fraction > 0:
        raise ValueError(
            "friction_opposes_flow supports single-phase injection only "
            f"(gas mass fraction {gas_mass_fraction})."
        )


def solve_injection_flow_rate(
    ipr,
    dpdt,
    wellhead_pressure,
    wellhead_temperature,
    flow_bracket=(1e-6, 0.2),
    friction_opposes_flow=False,
):
    """Solve for the injection flow rate that balances well and reservoir.

    Parameters
    ----------
    ipr: gemini_model.reservoir.inflow_performance.IPR
        Reservoir inflow model (parameterized with ``type="injection_reservoir"``).
    dpdt: gemini_model.well.pressure_drop.DPDT
        Well segment from wellhead to bottomhole (``direction="down"``).
    wellhead_pressure, wellhead_temperature: float
        Fixed wellhead boundary conditions, in Pa and K.
    flow_bracket: tuple[float, float]
        ``(low, high)`` flow rate bracket (m3/s) to search for the root.
    friction_opposes_flow: bool
        ``False`` (default) keeps GEMINI's original friction sign; ``True``
        makes friction a loss for the downward injection flow (RC-01 eq 12,
        see :func:`_well_bottomhole_pressure_injection`).

    Returns
    -------
    float
        The solved flow rate (m3/s).
    """

    def residual(flow_rate):
        p_well = _well_bottomhole_pressure_injection(
            dpdt, flow_rate, wellhead_pressure, wellhead_temperature, friction_opposes_flow
        )
        p_reservoir = _reservoir_bottomhole_pressure(ipr, flow_rate)
        return p_well - p_reservoir

    return brentq(residual, flow_bracket[0], flow_bracket[1])


def solve_injection_wellhead_pressure(
    ipr,
    dpdt,
    flow_rate,
    wellhead_temperature,
    pressure_bracket=(1e3, 1e8),
    friction_opposes_flow=False,
):
    """Solve for the wellhead (surface) pressure to inject a flow rate.

    Solves for the pressure required to inject a known ``flow_rate``
    into the reservoir.

    This is the inverse problem of :func:`solve_injection_flow_rate`: in a
    geothermal doublet, the injection flow rate is normally already known
    (it equals the producer side's solved flow rate, by mass balance), and
    what an injector pump needs to deliver is the surface pressure that
    reconciles the well (``dpdt``) and reservoir (``ipr``) bottomhole
    pressures for that fixed flow rate.

    Parameters
    ----------
    ipr: gemini_model.reservoir.inflow_performance.IPR
        Reservoir inflow model (parameterized with ``type="injection_reservoir"``).
    dpdt: gemini_model.well.pressure_drop.DPDT
        Well segment from wellhead to bottomhole (``direction="down"``).
    flow_rate: float
        The (known) injection flow rate, in m3/s.
    wellhead_temperature: float
        Wellhead (surface) fluid temperature, in K.
    pressure_bracket: tuple[float, float]
        ``(low, high)`` wellhead pressure bracket (Pa) to search for the
        root. Must bracket a sign change; widen if ``brentq`` raises.
    friction_opposes_flow: bool
        ``False`` (default) keeps GEMINI's original friction sign, which
        *lowers* the required wellhead pressure; ``True`` makes friction a
        loss for the downward injection flow, ``P_wh = P_bh,req - rho g H +
        dP_fric`` (RC-01 eq 12, see :func:`_well_bottomhole_pressure_injection`).

    Returns
    -------
    float
        The required wellhead (surface) injection pressure (Pa). If even
        the bracket's lower bound already over-delivers bottomhole
        pressure (i.e. the well's own hydrostatic column, descending to
        depth, provides more driving pressure than the reservoir needs --
        common for deep injection wells, where an injector pump isn't
        actually needed and a choke would be used instead), the bracket's
        lower bound is returned as a practical floor rather than raising
        (this occurs for deep wells where gravity alone already
        over-delivers the required bottomhole pressure).
    """
    p_reservoir = _reservoir_bottomhole_pressure(ipr, flow_rate)

    def residual(wellhead_pressure):
        p_well = _well_bottomhole_pressure_injection(
            dpdt, flow_rate, wellhead_pressure, wellhead_temperature, friction_opposes_flow
        )
        return p_well - p_reservoir

    residual_low = residual(pressure_bracket[0])
    if residual_low >= 0:
        # Even the lowest wellhead pressure over-delivers bottomhole
        # pressure; no pump boost is needed at all (physically, a choke
        # would be used instead). Return the floor rather than raising.
        return pressure_bracket[0]

    return brentq(residual, pressure_bracket[0], pressure_bracket[1])
