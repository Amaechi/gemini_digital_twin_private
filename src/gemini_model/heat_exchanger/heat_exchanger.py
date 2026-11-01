"""Plate heat exchanger model.

Estimates the primary-side pressure drop (linear flow resistance) and
both outlet temperatures using the NTU-effectiveness method for a
parallel- or counter-flow plate heat exchanger.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: optional
# secondary-side "secondary_fluid_density"/"secondary_specific_heat" parameters (default: the
# primary values, i.e. unchanged behaviour), so brine (primary) and district water
# (secondary) can have their own rho/c_p (WP-02 decision on the WP-01 follow-up).

import math

from gemini_model.model_abstract import StaticModel


class HeatExchanger(StaticModel):
    """Plate heat exchanger pressure drop and NTU-effectiveness model."""

    DEFAULT_FLUID_DENSITY = 1050.0  # kg/m3
    DEFAULT_SPECIFIC_HEAT = 4200.0  # J/(kg.K)
    _MIN_CAPACITY_RATE = 1e-9  # W/K, guards against division by zero

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

        ``secondary_flow_rate`` is the secondary-side flow through this
        heat exchanger. If the secondary network's flow is shared with
        other consumers (e.g. a co-located CHP), use a ``Splitter``
        component to allocate it before feeding it into this model. Both
        the primary and secondary flow rates are conserved across the
        heat exchanger (no mass is added or removed) and are echoed back
        as outputs so they can be passed on, in series, to downstream
        components on the same flow path (e.g. a Boiler fed by this same,
        now-heated, secondary flow).
        """
        pressure_in = u["pressure_in"]
        primary_flow_rate = u["primary_flow_rate"]
        secondary_flow_rate = u["secondary_flow_rate"]

        flow_resistance = self.parameters["flow_resistance"]
        pressure_out = pressure_in - primary_flow_rate * flow_resistance

        heat_duty, primary_temperature_out, secondary_temperature_out = self._effectiveness_ntu(
            u["primary_temperature_in"],
            u["secondary_temperature_in"],
            primary_flow_rate,
            secondary_flow_rate,
        )

        self.output["pressure_in"] = pressure_in
        self.output["pressure_out"] = pressure_out
        self.output["primary_temperature_out"] = primary_temperature_out
        self.output["secondary_temperature_out"] = secondary_temperature_out
        self.output["primary_flow_rate"] = primary_flow_rate
        self.output["secondary_flow_rate"] = secondary_flow_rate
        self.output["heat_duty"] = heat_duty
        self.output["power_el"] = 0.0
        self.output["power_th"] = heat_duty
        self.output["emission"] = 0.0

    def _effectiveness_ntu(
        self,
        primary_temperature_in,
        secondary_temperature_in,
        primary_flow_rate,
        secondary_flow_rate,
    ):
        """Compute heat duty and outlet temperatures with the NTU-effectiveness method."""
        rho = self.parameters.get("fluid_density", self.DEFAULT_FLUID_DENSITY)
        cw = self.parameters.get("specific_heat", self.DEFAULT_SPECIFIC_HEAT)
        # Secondary side defaults to the primary properties (original GEMINI behaviour).
        rho_secondary = self.parameters.get("secondary_fluid_density", rho)
        cw_secondary = self.parameters.get("secondary_specific_heat", cw)

        mass_flow_primary = rho * primary_flow_rate  # kg/s
        mass_flow_secondary = rho_secondary * secondary_flow_rate  # kg/s

        capacity_primary = mass_flow_primary * cw  # W/K
        capacity_secondary = mass_flow_secondary * cw_secondary  # W/K
        capacity_min = min(capacity_primary, capacity_secondary)
        capacity_max = max(capacity_primary, capacity_secondary)

        if capacity_min < self._MIN_CAPACITY_RATE:
            # no (effective) flow on one side: no heat is exchanged
            return 0.0, primary_temperature_in, secondary_temperature_in

        ntu = self.parameters["heat_transfer_coefficient"] / capacity_min
        capacity_ratio = capacity_min / capacity_max
        heat_duty_max = capacity_min * abs(secondary_temperature_in - primary_temperature_in)

        if self.parameters["flow_configuration"] == "parallel":
            effectiveness = (1 - math.exp(-ntu * (1 + capacity_ratio))) / (1 + capacity_ratio)
        else:
            # counter-flow configuration
            if capacity_ratio == 1:
                effectiveness = ntu / (1 + ntu)
            elif capacity_ratio < 0.01:
                effectiveness = 1 - math.exp(-ntu)
            else:
                # Counter-flow, Cr < 1 (standard e-NTU formula, continuous with the
                # Cr == 1 special case above as Cr -> 1).
                effectiveness = (1 - math.exp(-ntu * (1 - capacity_ratio))) / (
                    1 - capacity_ratio * math.exp(-ntu * (1 - capacity_ratio))
                )

        heat_duty = effectiveness * heat_duty_max
        primary_temperature_out = primary_temperature_in - heat_duty / capacity_primary
        secondary_temperature_out = secondary_temperature_in + heat_duty / capacity_secondary

        return heat_duty, primary_temperature_out, secondary_temperature_out

    def get_output(self):
        """Get output of the model."""
        return self.output
