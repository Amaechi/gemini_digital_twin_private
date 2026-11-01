"""Gas-fired combined heat and power (CHP) model.

Estimates the electrical power, heat output and CO2 emissions of a
gas-fired CHP unit burning a given gas flow. If the gas or water
flow is shared with other consumers (e.g. a co-located ``Boiler``), the
allocation between them should be handled by a ``Splitter`` component.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: optional
# "electrical_fraction" parameter (electricity share of the useful output; default
# ELECTRICAL_POWER_FRACTION = 1/3, i.e. unchanged behaviour) for the CHP-EL-FRAC 0.50
# sensitivity (DEA gas engines, RC-08/RC-10; WP-11).

from gemini_model.model_abstract import StaticModel


class CHP(StaticModel):
    """Gas-fired CHP electrical/heat output and emission model."""

    DEFAULT_GAS_DENSITY = 0.7  # kg/Nm3, used only if gas_volumetric_flow_rate is given
    DEFAULT_FLUID_DENSITY = 1050.0  # kg/m3
    DEFAULT_SPECIFIC_HEAT = 4200.0  # J/(kg.K)
    JOULE_PER_GJ = 1e9
    ELECTRICAL_POWER_FRACTION = 1 / 3  # fraction of fuel power converted to electricity
    _MIN_MASS_FLOW = 1e-9  # kg/s, guards against division by zero

    def __init__(self):
        """Model initialization."""
        self.parameters = {}
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
        """Generate an initial state based on user parameters."""
        pass

    def update_state(self, u, x):
        """Update the state based on input u and state x."""
        pass

    def calculate_output(self, u, x=None):
        """Calculate output based on input u.

        The gas flow burned by this CHP unit can be given either as a
        mass flow, ``u["gas_flow_rate"]`` (kg/s), or as a volumetric
        flow, ``u["gas_volumetric_flow_rate"]`` (m3/s), which is
        converted to a mass flow using the ``gas_density`` parameter
        (default 0.7 kg/Nm3). Provide exactly one of the two.
        ``u["water_flow_rate"]`` is the water-side flow through this CHP
        unit. If either flow is shared with other consumers (e.g. a
        co-located Boiler), use a ``Splitter`` component to allocate it
        before feeding it into this model. ``water_flow_rate`` is echoed
        back as an output so it can be passed on, in series, to a
        downstream component on the same water flow path. The optional
        ``electrical_fraction`` parameter (default 1/3) splits the useful
        output into electricity and heat.
        """
        temperature_in = u["temperature_in"]
        water_flow_rate = u["water_flow_rate"]

        if "gas_flow_rate" in u:
            gas_mass_flow_rate = u["gas_flow_rate"]  # kg/s
        else:
            gas_density = self.parameters.get("gas_density", self.DEFAULT_GAS_DENSITY)
            gas_mass_flow_rate = u["gas_volumetric_flow_rate"] * gas_density  # kg/s

        fluid_density = self.parameters.get("fluid_density", self.DEFAULT_FLUID_DENSITY)
        cw = self.parameters.get("specific_heat", self.DEFAULT_SPECIFIC_HEAT)

        gas_energy_flow = self.parameters["caloric_value"] * gas_mass_flow_rate  # W
        fuel_power = self.parameters["efficiency_factor"] * gas_energy_flow
        fraction = self.parameters.get("electrical_fraction", self.ELECTRICAL_POWER_FRACTION)
        if not 0.0 < fraction < 1.0:
            raise ValueError(f"electrical_fraction must be in (0, 1), got {fraction}")
        power_el = fraction * fuel_power
        power_th = (1 - fraction) * fuel_power

        mass_flow_water = fluid_density * water_flow_rate  # kg/s
        if mass_flow_water < self._MIN_MASS_FLOW:
            temperature_out = temperature_in
        else:
            temperature_out = temperature_in + power_th / (cw * mass_flow_water)

        emission = self.parameters["gas_emission_factor"] / self.JOULE_PER_GJ * gas_energy_flow

        self.output["temperature_in"] = temperature_in
        self.output["temperature_out"] = temperature_out
        self.output["water_flow_rate"] = water_flow_rate
        self.output["power_el"] = power_el
        self.output["power_th"] = power_th
        self.output["emission"] = emission

    def get_output(self):
        """Get output of the model."""
        return self.output
