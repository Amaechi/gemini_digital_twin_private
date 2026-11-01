"""Producer/injector well components wrapping the nodal well solve.

Wraps the nodal (root-find) well solve from
:mod:`gemini_simulator.well_flow` behind a normal, single-call
``calculate_output(u, x)`` interface.

:mod:`gemini_simulator.solver`'s ``Solver.connect()``/``run()`` only
supports one forward ``calculate_output(u, x)`` call per component per
timestep -- it has no notion of an internal iterative sub-solve. The
producer/injector well nodal balance, however, is a root-find (searching
over trial flow rates or wellhead pressures until the reservoir and well
models agree). ``ProducerWell`` and ``InjectorWell`` hide that root-find
inside their own ``calculate_output``, so they can be declared as normal
``"components"`` entries and wired with plain ``connect()`` calls -- e.g.
producer well -> separator -> ... -> injector well -> injector pump --
instead of being solved by standalone Python calls outside the Solver's
connection graph.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: optional "pvt" parameter
# (GM-01) is validated on update_parameters and passed to the well solve; default behaviour
# (GEMINI water PVT) is unchanged.

from gemini_model.fluid.pvt_config import normalise_pvt_parameters
from gemini_model.model_abstract import StaticModel
from gemini_simulator.well_flow import (
    producer_esp_power_w,
    producer_esp_pressures_bar,
    solve_injector_wellhead_pressure_bar,
    solve_primary_flow_m3h,
)

JOULE_PER_KWH = 3.6e6


class ProducerWell(StaticModel):
    """Producer well + reservoir + (optional) ESP.

    Nodal-solved for its primary flow rate.

    Parameters (via ``update_parameters``) are the same shape as
    :func:`gemini_simulator.well_flow.solve_primary_flow_m3h`'s
    ``well_config`` argument (``reservoir_pressure_bar``,
    ``productivity_index_m3h_per_bar``, ``trajectory_table`` (preferred)
    or legacy ``welltrajectory``, optional
    ``esp_depth_m``/``esp``/``esp_freq_hz``, ``pvt``), plus optional
    ``ambient_temperature_c`` (default 20.0) and ``flow_bracket_m3h``
    (default ``(0.01, 700.0)``). ``pvt`` sets the well fluid's PVT
    properties (GM-01, see :mod:`gemini_model.fluid.pvt_config`); it is
    checked when set, so a bad key fails at project build time.

    Inputs (``u``): ``wellhead_pressure`` (bar), ``wellhead_temperature``
    (C) -- the surface boundary condition the well is produced against.

    Outputs: ``flow_rate`` (m3/h), ``flow_rate_m3s`` (m3/s, convenience
    for components expecting SI flow, e.g. the heat exchanger/boiler/
    CHP), ``pressure_out``/``temperature_out`` (the wellhead
    pressure/temperature passed straight through, so this component can
    feed a downstream separator's pressure/temperature/flow_rate inputs
    directly), ``esp_power_el`` (W, 0.0 if no ESP configured),
    ``esp_intake_pressure``/``esp_discharge_pressure`` (bar, ``None`` if
    no ESP configured).
    """

    def __init__(self):
        """Model initialization."""
        self.parameters = {}
        self.output = {}

    def update_parameters(self, parameters):
        """Update model parameters.

        Parameters
        ----------
        parameters: dict
            Parameters dict as defined by the model (see class docstring).
        """
        for key, value in parameters.items():
            if key == "pvt":
                normalise_pvt_parameters(value)  # raises on a bad block
            self.parameters[key] = value

    def initialize_state(self, x):
        """Generate an initial state based on user parameters."""
        pass

    def update_state(self, u, x):
        """Update the state based on input u and state x."""
        pass

    def calculate_output(self, u, x=None):
        """Calculate output based on input u."""
        wellhead_pressure = u["wellhead_pressure"]
        wellhead_temperature = u["wellhead_temperature"]

        flow_rate_m3h = solve_primary_flow_m3h(
            self.parameters,
            wellhead_pressure_bar=wellhead_pressure,
            wellhead_temperature_c=wellhead_temperature,
            ambient_temperature_c=self.parameters.get("ambient_temperature_c", 20.0),
            flow_bracket_m3h=self.parameters.get("flow_bracket_m3h", (0.01, 700.0)),
        )
        esp_power_el = producer_esp_power_w(self.parameters, flow_rate_m3h)
        esp_intake_pressure, esp_discharge_pressure = producer_esp_pressures_bar(
            self.parameters,
            flow_rate_m3h,
            wellhead_pressure_bar=wellhead_pressure,
            wellhead_temperature_c=wellhead_temperature,
        )

        self.output["flow_rate"] = flow_rate_m3h
        self.output["flow_rate_m3s"] = flow_rate_m3h / 3600.0
        self.output["pressure_out"] = wellhead_pressure
        self.output["temperature_out"] = wellhead_temperature
        self.output["esp_power_el"] = esp_power_el
        self.output["esp_intake_pressure"] = esp_intake_pressure
        self.output["esp_discharge_pressure"] = esp_discharge_pressure
        self.output["power_el"] = esp_power_el
        self.output["power_th"] = 0.0
        self.output["emission"] = 0.0

    def get_output(self):
        """Get output of the model."""
        return self.output


class InjectorWell(StaticModel):
    """Injector well + reservoir.

    Nodal-solved for the wellhead (surface) injection pressure a known
    flow rate requires.

    Parameters (via ``update_parameters``) are the same shape as
    :func:`gemini_simulator.well_flow.solve_injector_wellhead_pressure_bar`'s
    ``injector_well_config`` argument (``reservoir_pressure_bar``,
    ``productivity_index_m3h_per_bar``, ``trajectory_table`` (preferred)
    or legacy ``welltrajectory``, optional ``pvt`` as for
    :class:`ProducerWell`), plus optional
    ``pressure_bracket_bar`` (default ``(0.001, 500.0)``) and an optional
    ``injector_pump`` sub-dict (``efficiency_factor``,
    ``electricity_emission_factor``) describing the surface pump that
    boosts the returning water up to the wellhead pressure this well
    requires; if omitted, ``power_el``/``emission`` are reported as 0.

    Inputs (``u``): ``flow_rate`` (m3/h, by mass balance equal to the
    producer side's solved flow rate for a doublet with no losses),
    ``wellhead_temperature`` (C, the surface fluid temperature entering
    the injector well, e.g. the last surface component's outlet
    temperature), and ``pressure_in`` (Pa, the pressure of the returning
    water arriving at the injector pump's inlet, e.g. the last surface
    component's outlet pressure).

    Outputs: ``wellhead_pressure`` (bar) -- the pressure an injector pump
    must deliver at the surface to push ``flow_rate`` into the injection
    reservoir (``wellhead_pressure_pa`` echoes the same pressure in Pa).
    ``power_el``/``emission`` are the injector pump's electrical power (W)
    and CO2 emission (kg/s) needed to boost ``pressure_in`` up to
    ``wellhead_pressure_pa``, using the ``injector_pump`` parameters.
    """

    def __init__(self):
        """Model initialization."""
        self.parameters = {}
        self.output = {}

    def update_parameters(self, parameters):
        """Update model parameters.

        Parameters
        ----------
        parameters: dict
            Parameters dict as defined by the model (see class docstring).
        """
        for key, value in parameters.items():
            if key == "pvt":
                normalise_pvt_parameters(value)  # raises on a bad block
            self.parameters[key] = value

    def initialize_state(self, x):
        """Generate an initial state based on user parameters."""
        pass

    def update_state(self, u, x):
        """Update the state based on input u and state x."""
        pass

    def calculate_output(self, u, x=None):
        """Calculate output based on input u."""
        flow_rate = u["flow_rate"]
        wellhead_temperature = u["wellhead_temperature"]
        pressure_in = u.get("pressure_in", 0.0)

        wellhead_pressure = solve_injector_wellhead_pressure_bar(
            self.parameters,
            flow_m3h=flow_rate,
            wellhead_temperature_c=wellhead_temperature,
            pressure_bracket_bar=self.parameters.get("pressure_bracket_bar", (0.001, 500.0)),
        )
        wellhead_pressure_pa = wellhead_pressure * 1e5

        injector_pump_parameters = self.parameters.get("injector_pump", {})
        efficiency_factor = injector_pump_parameters.get("efficiency_factor", 1.0)
        emission_factor = injector_pump_parameters.get("electricity_emission_factor", 0.0)
        power_el = (
            flow_rate / 3600.0 * max(0.0, wellhead_pressure_pa - pressure_in) / efficiency_factor
        )
        emission = emission_factor / JOULE_PER_KWH * power_el

        self.output["wellhead_pressure"] = wellhead_pressure
        self.output["wellhead_pressure_pa"] = wellhead_pressure_pa
        self.output["flow_rate_m3s"] = flow_rate / 3600.0
        self.output["power_el"] = power_el
        self.output["power_th"] = 0.0
        self.output["emission"] = emission

    def get_output(self):
        """Get output of the model."""
        return self.output
