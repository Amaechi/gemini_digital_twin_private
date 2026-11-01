"""Surface separator model.

Estimates the pressure and temperature drop across a surface separator
vessel, given a linear flow-dependent pressure resistance and a fixed
temperature drop.

Optionally (GM-12) it also characterises the separated associated gas from
its CH4/CO2 composition: gas density, fuel LHV, fuel power and the CO2 that
the gas already contains ("inherent" CO2). Source: RC-08 "Associated gas"
(Nm3 at 0 degC, 1.01325 bar; CH4 LHV 35.8 MJ/Nm3).
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: gas composition parameters
# (ch4_fraction, co2_fraction, fuel_lhv_j_kg; gas_density derived when not given) and outputs
# (fuel_power_w, inherent_co2_kg_s, ...) for GM-12. Without the new parameters the outputs are
# zero and every existing output is unchanged. WP-01 review: an explicit fuel_lhv_j_kg with gas
# flow but no gas density (no gas_density, no composition) raises instead of giving 0 W fuel.

from gemini_model.model_abstract import StaticModel

MOLAR_VOLUME_NM3_PER_KMOL = 22.414  # ideal gas at 0 degC, 1.01325 bar (RC-08)
MOLAR_MASS_CH4_KG_PER_KMOL = 16.04  # RC-08
MOLAR_MASS_CO2_KG_PER_KMOL = 44.01  # RC-08
CH4_LHV_J_PER_NM3 = 35.8e6  # lower heating value of methane (RC-08)
_FRACTION_SUM_TOLERANCE = 1e-6


def associated_gas_properties(ch4_fraction, co2_fraction):
    """Return the properties of a binary CH4/CO2 associated gas.

    Source: RC-08 "Associated gas" [D]. Mole fractions equal volume
    fractions for an ideal gas, and the two must add up to 1.

    Parameters
    ----------
    ch4_fraction, co2_fraction: float
        Mole (= Nm3) fractions of CH4 and CO2 (-).

    Returns
    -------
    dict
        ``density_kg_nm3`` (kg/Nm3), ``lhv_j_nm3`` (J/Nm3), ``lhv_j_kg``
        (J/kg), ``ch4_kg_per_nm3`` and ``co2_kg_per_nm3`` (kg of each species
        per Nm3 of gas; the CO2 one is the inherent CO2) and
        ``co2_if_burned_kg_per_nm3`` (inherent CO2 plus CO2 from burning the
        CH4, kg/Nm3). For 80/20 mol %: 0.965 kg/Nm3, 29.7 MJ/kg and
        1.964 kg/Nm3 (= 68.6 kg/GJ_LHV).

    Raises
    ------
    ValueError
        If a fraction is outside [0, 1] or the two do not add up to 1.
    """
    for name, value in (("ch4_fraction", ch4_fraction), ("co2_fraction", co2_fraction)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {value!r}.")
    if abs(ch4_fraction + co2_fraction - 1.0) > _FRACTION_SUM_TOLERANCE:
        raise ValueError(
            "ch4_fraction + co2_fraction must equal 1 (binary CH4/CO2 gas), got "
            f"{ch4_fraction + co2_fraction!r}; give gas_density and fuel_lhv_j_kg explicitly "
            "for other compositions."
        )
    kmol_per_nm3 = 1.0 / MOLAR_VOLUME_NM3_PER_KMOL
    ch4_kg_per_nm3 = ch4_fraction * kmol_per_nm3 * MOLAR_MASS_CH4_KG_PER_KMOL
    co2_kg_per_nm3 = co2_fraction * kmol_per_nm3 * MOLAR_MASS_CO2_KG_PER_KMOL
    density_kg_nm3 = ch4_kg_per_nm3 + co2_kg_per_nm3
    lhv_j_nm3 = ch4_fraction * CH4_LHV_J_PER_NM3
    # One mole of CO2 per mole of CH4 burned, plus the CO2 already in the gas.
    co2_if_burned_kg_per_nm3 = (
        (ch4_fraction + co2_fraction) * kmol_per_nm3 * MOLAR_MASS_CO2_KG_PER_KMOL
    )
    return {
        "density_kg_nm3": density_kg_nm3,
        "lhv_j_nm3": lhv_j_nm3,
        "lhv_j_kg": lhv_j_nm3 / density_kg_nm3,
        "ch4_kg_per_nm3": ch4_kg_per_nm3,
        "co2_kg_per_nm3": co2_kg_per_nm3,
        "co2_if_burned_kg_per_nm3": co2_if_burned_kg_per_nm3,
    }


class Separator(StaticModel):
    """Surface separator pressure/temperature drop model."""

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

        ``u["direction"]`` selects the calculation direction (defaults to
        ``"forward"`` if not provided): ``"forward"``
        computes the outlet state from the inlet state, ``"backward"``
        computes the inlet state from the outlet state. All flow rates
        (``flow_rate``, ``gas_flow_rate``) are in SI units (m3/s). In addition to the
        liquid-phase pressure/temperature calculation, this also reports the
        co-produced gas phase separated out of the flow: ``gas_flow_rate``
        (volumetric, m3/s) and ``gas_mass_flow_rate`` (kg/s), using the
        ``gas_water_ratio``/``gas_density`` parameters (both default to 0,
        so existing configurations without a gas phase are unaffected). The
        liquid-phase ``flow_rate`` itself is echoed back as an output so
        it can be passed on unchanged to a downstream component; a further
        output, ``flow_rate_m3h``, echoes the same liquid flow converted
        to m3/h, for downstream components that expect that convention
        (e.g. a well model).

        Gas composition (GM-12, all optional): ``ch4_fraction`` and
        ``co2_fraction`` (mole fractions, must add up to 1) give the gas
        density and fuel LHV via :func:`associated_gas_properties`;
        explicit ``gas_density`` (kg/Nm3) and ``fuel_lhv_j_kg`` (J/kg)
        parameters override the derived values. ``gas_water_ratio`` is in
        Nm3 gas per m3 liquid, so ``gas_flow_rate`` is in Nm3/s. Extra
        outputs: ``gas_density_kg_nm3``, ``fuel_lhv_j_kg``,
        ``fuel_power_w`` (W, LHV basis = gas mass flow x LHV),
        ``ch4_mass_flow_kg_s``, ``inherent_co2_kg_s`` (CO2 contained in the
        gas, kg/s) and ``co2_if_burned_kg_s`` (inherent plus combustion
        CO2, kg/s). The species flows are the composition's mass fractions
        times ``gas_mass_flow_rate``, so they always add up to it (also
        with an explicit ``gas_density``). Without a composition the
        species/CO2 outputs are 0, and ``fuel_power_w`` needs both
        ``gas_density`` and ``fuel_lhv_j_kg``: it is 0 without an LHV,
        and an LHV with ``gas_water_ratio`` > 0 but no gas density raises
        ``ValueError`` (the fuel would silently be 0 W). ``emission`` stays 0: the separator
        does not emit; the CO2 is counted where the gas is burned (or
        vented).
        """
        pressure = u["pressure"]
        temperature = u["temperature"]
        flow_rate = u["flow_rate"]
        direction = u.get("direction", "forward")

        flow_resistance = self.parameters["flow_resistance"]
        temperature_drop = self.parameters["temperature_drop"]
        gas_water_ratio = self.parameters.get("gas_water_ratio", 0.0)
        gas = self._gas_properties()
        gas_density = self.parameters.get(
            "gas_density", gas["density_kg_nm3"] if gas is not None else 0.0
        )
        fuel_lhv_j_kg = self.parameters.get(
            "fuel_lhv_j_kg", gas["lhv_j_kg"] if gas is not None else 0.0
        )
        if fuel_lhv_j_kg > 0.0 and gas_water_ratio > 0.0 and gas_density <= 0.0:
            raise ValueError(
                "Separator: the fuel LHV is > 0 but the gas density is <= 0, so fuel_power_w would "
                "be 0 W; give gas_density (kg/Nm3) > 0 or ch4_fraction/co2_fraction."
            )

        if direction == "forward":
            pressure_in = pressure
            pressure_out = pressure_in - flow_rate * flow_resistance
            temperature_in = temperature
            temperature_out = temperature_in - temperature_drop
        elif direction == "backward":
            pressure_out = pressure
            pressure_in = pressure_out + flow_rate * flow_resistance
            temperature_out = temperature
            temperature_in = temperature_out + temperature_drop
        else:
            raise ValueError(f"Unsupported direction '{direction}'. Use 'forward' or 'backward'.")

        gas_flow_rate = gas_water_ratio * flow_rate
        gas_mass_flow_rate = gas_flow_rate * gas_density
        fuel_power_w = gas_mass_flow_rate * fuel_lhv_j_kg
        if gas is not None:
            # Mass fractions from the composition; scaled to the (possibly explicit) density.
            ch4_mass_flow_kg_s = gas_mass_flow_rate * gas["ch4_kg_per_nm3"] / gas["density_kg_nm3"]
            inherent_co2_kg_s = gas_mass_flow_rate * gas["co2_kg_per_nm3"] / gas["density_kg_nm3"]
            # Burning 1 kmol CH4 gives 1 kmol CO2.
            co2_if_burned_kg_s = (
                inherent_co2_kg_s
                + ch4_mass_flow_kg_s * MOLAR_MASS_CO2_KG_PER_KMOL / MOLAR_MASS_CH4_KG_PER_KMOL
            )
        else:
            ch4_mass_flow_kg_s = inherent_co2_kg_s = co2_if_burned_kg_s = 0.0

        self.output["pressure_in"] = pressure_in
        self.output["pressure_out"] = pressure_out
        self.output["temperature_in"] = temperature_in
        self.output["temperature_out"] = temperature_out
        self.output["flow_rate"] = flow_rate
        self.output["flow_rate_m3h"] = flow_rate * 3600.0
        self.output["gas_flow_rate"] = gas_flow_rate
        self.output["gas_mass_flow_rate"] = gas_mass_flow_rate
        self.output["gas_density_kg_nm3"] = gas_density
        self.output["fuel_lhv_j_kg"] = fuel_lhv_j_kg
        self.output["fuel_power_w"] = fuel_power_w
        self.output["ch4_mass_flow_kg_s"] = ch4_mass_flow_kg_s
        self.output["inherent_co2_kg_s"] = inherent_co2_kg_s
        self.output["co2_if_burned_kg_s"] = co2_if_burned_kg_s
        self.output["power_el"] = 0.0
        self.output["power_th"] = 0.0
        self.output["emission"] = 0.0

    def _gas_properties(self):
        """Return :func:`associated_gas_properties` for the configured composition.

        Returns ``None`` if neither ``ch4_fraction`` nor ``co2_fraction`` is
        set (legacy configuration without a gas composition).
        """
        if "ch4_fraction" not in self.parameters and "co2_fraction" not in self.parameters:
            return None
        return associated_gas_properties(
            self.parameters.get("ch4_fraction", 0.0),
            self.parameters.get("co2_fraction", 0.0),
        )

    def get_output(self):
        """Get output of the model."""
        return self.output
