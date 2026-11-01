"""Multi-stage gas compressor model.

Estimates the shaft power required to compress a gas mass flow from an
inlet to an outlet pressure, assuming ideal intercooling (gas is
cooled back to inlet temperature between stages) and an equal
pressure ratio across every stage.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the standard
# power_el/power_th/emission outputs (power_el = compressor_power) so the compressor can be
# used from the registry like other components (GM-02). Existing outputs are unchanged.

from gemini_model.model_abstract import StaticModel


class Compressor(StaticModel):
    """Multi-stage gas compressor power model."""

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

        ``pressure_in``/``pressure_out`` (bar) and ``temperature_in`` (degC)
        are runtime inputs so the compressor can be wired inline in a gas
        flow chain. ``pressure_in``/``pressure_out``/``mass_flow`` are
        echoed back unchanged as outputs (this model does not itself alter
        the flow or pressures, it only reports the power required to
        achieve them) so a downstream component can chain off them.
        ``power_el`` (W) equals ``compressor_power`` (electric drive assumed;
        the efficiencies are already applied, so ``mechanical_efficiency``
        must include motor/VFD losses for a wire-to-gas figure);
        ``power_th`` and ``emission`` are 0.

        Known limitation (GEMINI formula, unchanged): the per-stage work is
        ``m R T1/(k-1) (r^((k-1)/k) - 1)``, without the factor ``k`` of the
        steady-flow isentropic work ``m k/(k-1) R T1 (r^((k-1)/k) - 1)``, so
        the power is low by a factor ``k`` (about 1.3). Flagged for follow-up
        in docs/hackathon/PROGRESS.md.
        """
        mass_flow = u["mass_flow"]  # kg/s
        pressure_in = u["pressure_in"]  # bar
        pressure_out = u["pressure_out"]  # bar
        temperature_in = u["temperature_in"]  # degC

        compressor_power = self._power_calculation(
            mass_flow, pressure_in, pressure_out, temperature_in
        )

        self.output["compressor_power"] = compressor_power
        self.output["mass_flow"] = mass_flow  # mass_flow is not altered
        self.output["pressure_in"] = pressure_in
        self.output["pressure_out"] = pressure_out
        self.output["temperature_in"] = temperature_in
        self.output["power_el"] = compressor_power
        self.output["power_th"] = 0.0
        self.output["emission"] = 0.0

    def _power_calculation(self, mass_flow, pressure_in, pressure_out, temperature_in):
        """Compute compressor shaft power assuming ideal intercooling.

        Assumptions:
        - ideal intercooling: after each stage, the gas is cooled back
          to the inlet temperature T1.
        - equal pressure ratio: each stage compresses the gas with the
          same pressure ratio.
        """
        e_m = self.parameters["mechanical_efficiency"]
        e_c = self.parameters["compressor_efficiency"]
        k = self.parameters["specific_heat_ratio"]
        gas_constant = self.parameters["gas_constant"]
        t1 = temperature_in + 273.15  # Convert to Kelvin
        p1 = pressure_in
        p2 = pressure_out
        n = self.parameters["number_of_stages"]
        stage_pressure_ratio = (p2 / p1) ** (1 / n)

        compressor_power = (n * mass_flow * gas_constant * t1 / (k - 1)) * (
            stage_pressure_ratio ** ((k - 1) / k) - 1
        )

        # correcting for efficiency factors
        compressor_power = compressor_power / (e_m * e_c)

        return compressor_power

    def get_output(self):
        """Get output of the model."""
        return self.output
