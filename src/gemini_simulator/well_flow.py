"""Solve a producer well's primary flow rate from a wellhead pressure.

Uses a project's own "producer_well" JSON section, so plant project
files can drive their primary flow rate through nodal analysis instead
of assuming a fixed value.

This is a thin, JSON-config-driven wrapper around
:func:`gemini_simulator.nodal_solver.solve_production_flow_rate`: it
builds the ``IPR``/``DPDT``/``ESP`` objects the solver needs directly
from a plain dict (see :func:`solve_primary_flow_m3h`'s docstring for
the expected shape), so a project file can describe its own well/
reservoir/ESP configuration directly, with no extra conversion code.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: optional "pvt" block in the
# well configs (GM-01) is passed to every DPDT built here; without it the fluid is GEMINI's
# default water PVT, as before.
# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01 (WP-01 review): the ESP's
# water head/power curves are scaled by the "pvt" block's specific gravity (RHOL / 1000), so the
# pump sees the same fluid as the well column; without a block SG = 1 and nothing changes.
# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01 (WP-05): optional
# "friction_opposes_flow" key in the injector config (RC-01 eq 12 friction sign, see
# nodal_solver); absent or false keeps GEMINI's original injector friction sign.

import numpy as np

from gemini_model.fluid.pvt_config import build_pvt, liquid_properties
from gemini_model.pump.esp import ESP
from gemini_model.reservoir.inflow_performance import IPR
from gemini_model.well.pressure_drop import DPDT
from gemini_simulator.nodal_solver import (
    solve_injection_wellhead_pressure,
    solve_production_flow_rate,
)

CELSIUS_TO_KELVIN = 273.15


def _trajectory_table_to_segments(trajectory_table):
    """Convert a cumulative MD/TVD/ID well-survey trajectory to segments.

    Uses the same nodal-analysis convention used across the other
    Gemini applications, e.g. ``gemini_framework``'s
    ``injectionwell_trajectory_table``/``productionwell_trajectory_table``
    (see ``gemini_framework.modules.injectionwell.calculate_bottomhole_pressure``)
    -- into the internal per-segment ``{"angle", "depth", "diameter"}``
    list :func:`_build_dpdt`/:func:`_split_trajectory_at_depth` expect.

    ``trajectory_table``: list of ``{"MD": m, "TVD": m, "ID": m}`` dicts,
    one per survey station, each MD/TVD measured cumulatively from
    surface (station 0 is normally the wellhead, ``MD=TVD=0``). Between
    consecutive stations ``ii-1 -> ii``, the segment length/diameter/
    angle are derived as ``depth = MD[ii] - MD[ii-1]`` (segment length
    along the wellbore), ``diameter = ID[ii]`` (this segment's inner
    diameter), and ``angle = 90 - degrees(acos(TVD_delta / MD_delta))``
    (degrees from horizontal; 90 = vertical, 0 = horizontal) -- matching
    the formula used elsewhere in the Gemini suite.
    """
    segments = []
    for ii in range(1, len(trajectory_table)):
        md_delta = trajectory_table[ii]["MD"] - trajectory_table[ii - 1]["MD"]
        tvd_delta = trajectory_table[ii]["TVD"] - trajectory_table[ii - 1]["TVD"]
        angle_deg = 90.0 - np.degrees(np.arccos(tvd_delta / md_delta))
        segments.append(
            {"angle": angle_deg, "depth": md_delta, "diameter": trajectory_table[ii]["ID"]}
        )
    return segments


def _resolve_trajectory_segments(well_config):
    """Return the per-segment trajectory list the DPDT builders expect.

    Returns the ``{"angle", "depth", "diameter"}`` list
    :func:`_build_dpdt`/:func:`_split_trajectory_at_depth` expect, from a
    well config that carries either a ``"trajectory_table"`` (preferred:
    cumulative MD/TVD/ID survey stations, see
    :func:`_trajectory_table_to_segments`) or the legacy
    ``"welltrajectory"`` (per-segment angle/depth/diameter) key.
    """
    if "trajectory_table" in well_config:
        return _trajectory_table_to_segments(well_config["trajectory_table"])
    return well_config["welltrajectory"]


def _split_trajectory_at_depth(trajectory, split_depth):
    """Split a well trajectory into "top" and "bottom" segment lists.

    ``trajectory`` is a list of ``{"angle", "depth", "diameter"}``
    segment dicts (angle in degrees, depth in m); it is split at
    cumulative ``split_depth`` (m): segments above the split make up the
    "top" (wellhead-to-ESP) section, segments below make up the "bottom"
    (ESP-to-bottomhole) section. A segment straddling the split point is
    itself split into two partial segments (same diameter/angle,
    proportional depths).
    """
    top, bottom = [], []
    cumulative = 0.0
    for segment in trajectory:
        seg_start, seg_end = cumulative, cumulative + segment["depth"]
        if seg_end <= split_depth:
            top.append(segment)
        elif seg_start >= split_depth:
            bottom.append(segment)
        else:
            top.append({**segment, "depth": split_depth - seg_start})
            bottom.append({**segment, "depth": seg_end - split_depth})
        cumulative = seg_end
    return top, bottom


def _build_dpdt(segments, roughness=0.01, pvt=None):
    """Build a ready-to-use DPDT well-segment model from a segment list.

    ``roughness`` is the pipe wall roughness (m) used by the well's
    friction correlation; defaults to 0.01 m if not overridden by a
    well config's own ``roughness_m``. ``pvt`` is the well config's
    optional ``"pvt"`` block (see
    :func:`gemini_model.fluid.pvt_config.normalise_pvt_parameters`);
    ``None`` gives GEMINI's default water PVT.
    """
    well = DPDT()
    well.update_parameters(
        {
            "diameter": np.array([s["diameter"] for s in segments], dtype=float),
            "length": np.array([s["depth"] for s in segments], dtype=float),
            "angle": np.deg2rad(np.array([s["angle"] for s in segments], dtype=float)),
            "roughness": np.full(len(segments), roughness),
            "friction_correlation": "darcy_weisbach",
            "friction_correlation_2p": "BeggsBrill",
            "correction_factors": [1, 0],
        }
    )
    well.PVT = build_pvt(pvt)
    return well


class _FluidESP(ESP):
    """GEMINI ``ESP`` corrected for the pumped fluid's specific gravity (GM-01).

    The ESP head/power polynomials are water catalogue curves: the head is
    in feet of liquid (independent of the fluid) and is turned into a
    pressure rise with water's ``rho g`` (``ESP.FT_HEAD_TO_PA``); the brake
    power is for specific gravity 1. For a denser fluid both scale with
    ``SG = rho_l / rho_water``: ``dP = SG rho_water g H`` and
    ``P_shaft = Q dP / eta = SG P_shaft,water`` (RC-01 items 6-8: ESP
    pressure rise, ``P_hyd = Q dP``, heads divided by SG). The efficiency
    (``Q H / P``) does not change. ``specific_gravity=1.0`` reproduces the
    GEMINI ``ESP`` exactly.
    """

    def __init__(self, specific_gravity=1.0):
        """Model initialization with the fluid's specific gravity (-)."""
        super().__init__()
        self.specific_gravity = specific_gravity

    def head_function(self, pump_flow, freq):
        """Return the ESP pressure rise (Pa) for the configured fluid."""
        return super().head_function(pump_flow, freq) * self.specific_gravity

    def power_function(self, pump_flow, freq):
        """Return the ESP brake power (W) for the configured fluid."""
        return super().power_function(pump_flow, freq) * self.specific_gravity


def _esp_specific_gravity(pvt=None):
    """Return the well fluid's specific gravity for the ESP curves (-).

    ``RHOL`` of the ``"pvt"`` block divided by GEMINI's default water
    density (``PVTConstantSTP`` ``RHOL`` = 1000 kg/m3), so a missing block
    or one that leaves ``RHOL`` at its default gives exactly 1.0. (The ESP's
    own ``FT_HEAD_TO_PA`` implies 999.74 kg/m3; the 0.03 % difference is
    GEMINI's and is left as is.)
    """
    density_kg_m3, _ = liquid_properties(pvt)
    water_density_kg_m3, _ = liquid_properties(None)
    return density_kg_m3 / water_density_kg_m3


def _build_esp(esp_config, pvt=None):
    """Build the producer's ESP, its curves scaled to the ``"pvt"`` fluid (GM-01)."""
    esp = _FluidESP(_esp_specific_gravity(pvt))
    esp.update_parameters(esp_config)
    return esp


def solve_primary_flow_m3h(
    well_config,
    wellhead_pressure_bar,
    wellhead_temperature_c,
    ambient_temperature_c=20.0,
    flow_bracket_m3h=(0.01, 700.0),
):
    """Solve the producer well's primary flow rate (m3/h).

    Solved for a given wellhead pressure, from a plain "producer_well"
    config dict.

    This performs nodal analysis: reconciling the reservoir's linear
    inflow-performance (PI) model against the well's vertical-lift
    correlation at the bottomhole, using ``gemini_model``'s own ``IPR``
    (reservoir) + ``DPDT`` (well, Beggs-Brill/Darcy-Weisbach) + optional
    ``ESP`` models.

    Parameters
    ----------
    well_config: dict
        Expected shape::

            {
              "reservoir_pressure_bar": float,
              "productivity_index_m3h_per_bar": float,
              "trajectory_table": [{"MD": m, "TVD": m, "ID": m}, ...],  # preferred
              # or, legacy per-segment form:
              # "welltrajectory": [{"angle": deg, "depth": m, "diameter": m}, ...],
              "roughness_m": float,                  # optional, defaults to 0.01
              "esp_depth_m": float,                  # optional, omit for no ESP
              "esp": {"no_stages": int, "head_coeff": [...], "power_coeff": [...]},
              "esp_freq_hz": float,
              "pvt": {"liquid_density_kg_m3": 1030.0, ...},  # optional, GM-01
            }

        ``"pvt"`` overrides the well fluid's PVT properties (native
        ``PVTConstantSTP`` keys or the unit-suffixed aliases in
        :data:`gemini_model.fluid.pvt_config.PVT_PARAMETER_ALIASES`);
        omit it for GEMINI's default water PVT. Its liquid density also
        scales the ESP's water head/power curves (specific gravity
        ``RHOL / 1000``, see :class:`_FluidESP`).
        ``"trajectory_table"`` is a list of cumulative MD/TVD/ID survey
        stations (station 0 is normally the wellhead, ``MD=TVD=0``),
        matching the MD/TVD/ID nodal-analysis convention used across the
        other Gemini applications -- see
        :func:`_trajectory_table_to_segments`. Provide either
        ``"trajectory_table"`` or ``"welltrajectory"``, not both;
        ``"trajectory_table"`` takes precedence if both are present.
    wellhead_pressure_bar, wellhead_temperature_c: float
        Fixed wellhead boundary conditions.
    ambient_temperature_c: float
        Formation ambient temperature for well heat-loss, defaults to
        20 C.
    flow_bracket_m3h: tuple[float, float]
        ``(low, high)`` flow-rate bracket (m3/h) to search for the root;
        converted internally to m3/s for ``nodal_solver``. Widen if
        ``brentq`` raises for your own reservoir/well parameters.

    Returns
    -------
    float
        The solved primary flow rate, in m3/h.
    """
    # Pbh[bar] = Pres[bar] - Q[m3/h]/PI[m3/h per bar]. gemini_model's
    # IPR works in Pa and (m3/s)/Pa, hence the 3.6e8 = 3600 * 1e5 factor
    # (m3/h->m3/s and bar->Pa) converting the m3/h-per-bar productivity
    # index into gemini's productivity_index.
    ipr = IPR()
    ipr.update_parameters(
        {
            "type": "production_reservoir",
            "reservoir_pressure": well_config["reservoir_pressure_bar"] * 1e5,
            "productivity_index": well_config["productivity_index_m3h_per_bar"] / 3.6e8,
        }
    )

    esp_depth = well_config.get("esp_depth_m")
    esp_config = well_config.get("esp")
    esp_freq = well_config.get("esp_freq_hz")
    roughness_m = well_config.get("roughness_m", 0.01)
    pvt = well_config.get("pvt")

    trajectory_segments = _resolve_trajectory_segments(well_config)

    if esp_depth is not None and esp_config is not None:
        top_segments, bottom_segments = _split_trajectory_at_depth(trajectory_segments, esp_depth)
        dpdt_top = _build_dpdt(top_segments, roughness=roughness_m, pvt=pvt)
        dpdt_bottom = _build_dpdt(bottom_segments, roughness=roughness_m, pvt=pvt)
        esp = _build_esp(esp_config, pvt)
    else:
        dpdt_top = _build_dpdt(trajectory_segments, roughness=roughness_m, pvt=pvt)
        dpdt_bottom = None
        esp = None

    flow_bracket_m3s = (flow_bracket_m3h[0] / 3600.0, flow_bracket_m3h[1] / 3600.0)

    flow_m3s = solve_production_flow_rate(
        ipr,
        dpdt_top,
        wellhead_pressure=wellhead_pressure_bar * 1e5,
        wellhead_temperature=wellhead_temperature_c + CELSIUS_TO_KELVIN,
        ambient_temperature=ambient_temperature_c + CELSIUS_TO_KELVIN,
        dpdt_bottom=dpdt_bottom,
        esp=esp,
        esp_freq=esp_freq,
        flow_bracket=flow_bracket_m3s,
    )
    return flow_m3s * 3600.0


def producer_esp_power_w(well_config, flow_m3h):
    """Return the electrical power (W) drawn by the producer well's ESP.

    At a known flow rate, from the same "producer_well" config dict
    used by :func:`solve_primary_flow_m3h`.

    Once the primary flow rate is solved, the ESP's electrical power draw
    at that flow/frequency is read directly off its power curve (no
    further root-finding needed). Returns ``0.0`` if ``well_config`` has
    no ``"esp"``/``"esp_freq_hz"`` section (i.e. no ESP modeled, matching
    a well without artificial lift).
    """
    esp_config = well_config.get("esp")
    esp_freq = well_config.get("esp_freq_hz")
    if esp_config is None or esp_freq is None:
        return 0.0

    esp = _build_esp(esp_config, well_config.get("pvt"))
    esp.calculate_output({"pump_freq": esp_freq, "pump_flow": flow_m3h / 3600.0})
    return esp.get_output()["pump_power"]


def producer_esp_pressures_bar(
    well_config, flow_m3h, wellhead_pressure_bar, wellhead_temperature_c=20.0
):
    """Return the ESP's intake/discharge pressure (bar) at a known flow.

    Computed at a known flow rate and wellhead pressure, from the same
    "producer_well" config dict used by :func:`solve_primary_flow_m3h`.

    The well is walked forward (top-down) from the wellhead to the ESP,
    giving the ESP's discharge (downstream) pressure; the intake
    (upstream) pressure is the discharge pressure minus the ESP's pump
    head at that flow/frequency. Returns ``(None, None)`` if
    ``well_config`` has no ESP section.
    """
    esp_depth = well_config.get("esp_depth_m")
    esp_config = well_config.get("esp")
    esp_freq = well_config.get("esp_freq_hz")
    if esp_depth is None or esp_config is None or esp_freq is None:
        return None, None

    top_segments, _ = _split_trajectory_at_depth(
        _resolve_trajectory_segments(well_config), esp_depth
    )
    dpdt_top = _build_dpdt(
        top_segments,
        roughness=well_config.get("roughness_m", 0.01),
        pvt=well_config.get("pvt"),
    )
    wellhead_temperature_k = wellhead_temperature_c + CELSIUS_TO_KELVIN
    dpdt_top.calculate_output(
        {
            "pressure": wellhead_pressure_bar * 1e5,
            "temperature": wellhead_temperature_k,
            "temperature_ambient": wellhead_temperature_k,
            "flowrate": flow_m3h / 3600.0,
            "direction": "down",
        },
        None,
    )
    # dpdt_top delivers the ESP's discharge (pre-boost, downstream) pressure;
    # the intake (upstream, pre-boost) pressure is lower by the pump head.
    discharge_pressure_pa = dpdt_top.get_output()["pressure_output"]

    esp = _build_esp(esp_config, well_config.get("pvt"))
    esp.calculate_output({"pump_freq": esp_freq, "pump_flow": flow_m3h / 3600.0})
    pump_head_pa = esp.get_output()["pump_head"]

    intake_pressure_pa = max(0.0, discharge_pressure_pa - pump_head_pa)
    return intake_pressure_pa / 1e5, discharge_pressure_pa / 1e5


def solve_injector_wellhead_pressure_bar(
    injector_well_config,
    flow_m3h,
    wellhead_temperature_c,
    pressure_bracket_bar=(0.001, 500.0),
):
    """Solve the injector well's required wellhead (surface) pressure.

    Solved (bar) for a known injection flow rate, from a plain
    "injector_well" config dict (same shape as "producer_well", minus
    the ESP -- the injector side typically has no artificial lift,
    boosted instead by a surface injector pump).

    This is the injector-side counterpart of :func:`solve_primary_flow_m3h`:
    there, the flow rate is unknown and solved from a fixed wellhead
    pressure; here, the flow rate is already known (by mass balance, it
    equals the producer side's solved flow rate for a doublet with no
    losses), so what's solved for is the wellhead pressure an injector
    pump must deliver to push that flow into the injection reservoir
    (``gemini_model.reservoir.inflow_performance.IPR`` with
    ``type="injection_reservoir"``, plus a single ``DPDT`` well segment,
    no ESP split needed).

    Parameters
    ----------
    injector_well_config: dict
        Expected shape::

            {
              "reservoir_pressure_bar": float,
              "productivity_index_m3h_per_bar": float,
              "welltrajectory": [{"angle": deg, "depth": m, "diameter": m}, ...],
              "roughness_m": float,                  # optional, defaults to 0.01
              "pvt": {...},                          # optional, GM-01
              "friction_opposes_flow": bool,         # optional, defaults to False
            }

        (Same ``"trajectory_table"``-vs-``"welltrajectory"`` choice and
        optional ``"pvt"`` block as :func:`solve_primary_flow_m3h` -- see
        its docstring.) ``"friction_opposes_flow": true`` makes friction a
        loss for the downward injection flow (RC-01 eq 12, ``P_wh =
        P_bh,req - rho g H + dP_fric``); the default keeps GEMINI's original
        sign (see :func:`gemini_simulator.nodal_solver.solve_injection_wellhead_pressure`).
    flow_m3h: float
        The (known) injection flow rate, in m3/h.
    wellhead_temperature_c: float
        Surface fluid temperature entering the injector well (e.g. the
        primary network's return temperature after the last surface
        component), in degC.
    pressure_bracket_bar: tuple[float, float]
        ``(low, high)`` wellhead pressure bracket (bar) to search for the
        root; converted internally to Pa. Widen if ``brentq`` raises for
        your own reservoir/well parameters.

    Returns
    -------
    float
        The required wellhead injection pressure, in bar.
    """
    ipr = IPR()
    ipr.update_parameters(
        {
            "type": "injection_reservoir",
            "reservoir_pressure": injector_well_config["reservoir_pressure_bar"] * 1e5,
            "injectivity_index": injector_well_config["productivity_index_m3h_per_bar"] / 3.6e8,
        }
    )
    friction_opposes_flow = injector_well_config.get("friction_opposes_flow", False)
    if not isinstance(friction_opposes_flow, bool):
        raise ValueError(
            f"friction_opposes_flow must be true or false, got {friction_opposes_flow!r}"
        )
    dpdt = _build_dpdt(
        _resolve_trajectory_segments(injector_well_config),
        roughness=injector_well_config.get("roughness_m", 0.01),
        pvt=injector_well_config.get("pvt"),
    )

    pressure_bracket_pa = (pressure_bracket_bar[0] * 1e5, pressure_bracket_bar[1] * 1e5)
    wellhead_pressure_pa = solve_injection_wellhead_pressure(
        ipr,
        dpdt,
        flow_rate=flow_m3h / 3600.0,
        wellhead_temperature=wellhead_temperature_c + CELSIUS_TO_KELVIN,
        pressure_bracket=pressure_bracket_pa,
        friction_opposes_flow=friction_opposes_flow,
    )
    return wellhead_pressure_pa / 1e5
