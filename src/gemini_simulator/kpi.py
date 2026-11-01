"""KPI helpers for doublet plant projects.

Provides plant-level post-processing for secondary outlet temperature,
thermal/electrical power balance, and emissions.

These functions are deliberately *not* wired as component outputs inside
the solver's connection graph, which keeps them free of the solver's own
per-timestep wiring/ordering concerns: each one only needs a solved
:class:`~gemini_simulator.solver.Solver` (or the handful of scalar
outputs read from it) plus the same-shape scalar values (flow rates,
valve fractions) used to build the project.

Component names, fluid density and specific heat are keyword parameters
(GM-10), so the helpers work for any topology. Their defaults are the
component names used by ``simulator_examples/projects/doublet_plant.json``
(``"water_mixer"``, ``"filter_a"``, ``"heat_exchanger"``, ``"boiler"``,
``"chp"``) and the original constants, so existing callers get exactly
the same results. For a different layout -- e.g.
``simulator_examples/projects/parallel_hex_plant.json`` (``hex_1``/
``hex_2``/``hex_3``, a ``hex_mixer``, no boiler/CHP) -- pass your own names,
e.g. ``secondary_outlet_temperature(solver, mixer="hex_mixer")`` or
``total_emission_kg_s(solver, emitters=())``.

Primary-loop fluid properties have one source of truth (GM-01): if the
producer well (or the component named by ``pvt_component``) has a
``"pvt"`` parameter block, :func:`doublet_thermal_power_w` uses that
block's liquid density and heat capacity -- the same values the well
model uses. Without a ``"pvt"`` block it keeps the legacy defaults
:data:`FLUID_DENSITY` and :data:`SPECIFIC_HEAT`.

Units at the KPI layer follow the project file's own convention (bar,
degC, m3/h were used for parameters; here power/emission outputs are SI:
Watt and kg/s) -- convert to e.g. MW / kg per day at the call site as
needed.
"""

# Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: generalised (GM-10) --
# component names, density and specific heat are keyword parameters with backward-compatible
# defaults; primary-loop density/heat capacity come from the producer well's "pvt" block
# (GM-01); added fluid_properties and sum_component_outputs.

from gemini_model.fluid.pvt_config import liquid_properties

FLUID_DENSITY = 1050.0  # kg/m3, default water density used in KPI heat-flow calculations
SPECIFIC_HEAT = 4200.0  # J/(kg.K), default water specific heat used in KPI heat-flow calculations
JOULE_PER_KWH = 3.6e6  # J per kWh, used to convert electricity emission factors (kg/kWh) to kg/J

# Default component names (those of simulator_examples/projects/doublet_plant.json).
DEFAULT_SECONDARY_MIXER = "water_mixer"
DEFAULT_PRIMARY_INLET = ("filter_a", "temperature_out")
DEFAULT_PRIMARY_OUTLET = ("heat_exchanger", "primary_temperature_out")
DEFAULT_GENERATORS = ("chp",)
DEFAULT_EMITTERS = ("boiler", "chp")
DEFAULT_PVT_COMPONENT = "producer_well"


def fluid_properties(
    solver,
    component=DEFAULT_PVT_COMPONENT,
    default_density=FLUID_DENSITY,
    default_specific_heat=SPECIFIC_HEAT,
):
    """Return ``(density_kg_m3, specific_heat_j_kgk)`` of a component's fluid.

    Reads the ``"pvt"`` parameter block of ``component`` (GM-01; e.g. a
    ``producer_well``, ``injector_well`` or ``well``) and returns the
    liquid density and heat capacity the well model uses for it (keys the
    block leaves out keep the ``PVTConstantSTP`` defaults). If the
    component does not exist or has no ``"pvt"`` block, returns
    ``(default_density, default_specific_heat)``.
    """
    components = getattr(solver, "components", None) or {}
    model = components.get(component)
    pvt = getattr(model, "parameters", {}).get("pvt") if model is not None else None
    if pvt is None:
        return default_density, default_specific_heat
    return liquid_properties(pvt)


def sum_component_outputs(solver, key, components):
    """Return the sum of output ``key`` over the named ``components``.

    For example ``sum_component_outputs(solver, "emission", ("boiler", "chp"))``.
    A missing component or output raises ``KeyError``, so a misspelt name
    cannot silently drop a term. ``None`` values (e.g. ESP pressures of a
    well without an ESP) count as 0.
    """
    total = 0.0
    for name in components:
        value = solver.get_output(name, key)
        if value is not None:
            total += value
    return total


def secondary_outlet_temperature(solver, mixer=DEFAULT_SECONDARY_MIXER):
    """Return the combined secondary-network outlet temperature (degC).

    Reads the flow-weighted mix of the secondary outlets directly from
    the project's mixer component (a
    :class:`~gemini_model.mixer.mixer.Mixer`, default ``"water_mixer"``,
    which in ``doublet_plant.json`` recombines the boiler's and CHP's
    water outlets using a mass-weighted average).

    Parameters
    ----------
    solver: gemini_simulator.solver.Solver
        A solver that has been ``.run()`` and includes the ``mixer``
        component (e.g. built via
        :func:`gemini_simulator.project_loader.load_project` from a project
        JSON file such as ``simulator_examples/projects/doublet_plant.json``).
    mixer: str
        Name of the mixer component whose ``temperature`` output is the
        secondary outlet temperature.
    """
    return solver.get_output(mixer, "temperature")


def plant_thermal_power_w(
    solver,
    secondary_flow_m3s,
    secondary_temperature_in,
    *,
    mixer=DEFAULT_SECONDARY_MIXER,
    fluid_density=FLUID_DENSITY,
    specific_heat=SPECIFIC_HEAT,
):
    """Return the net thermal power delivered by the secondary network (W).

    ``Q_s * rho * Cw * (T_s_out - T_s_in)``. ``fluid_density`` (kg/m3) and
    ``specific_heat`` (J/kg.K) are the *secondary* (district) water's
    properties, not the brine's.
    """
    t_s_out = secondary_outlet_temperature(solver, mixer=mixer)
    return secondary_flow_m3s * fluid_density * specific_heat * (t_s_out - secondary_temperature_in)


def doublet_thermal_power_w(
    solver,
    primary_flow_m3s,
    *,
    inlet=DEFAULT_PRIMARY_INLET,
    outlet=DEFAULT_PRIMARY_OUTLET,
    fluid_density=None,
    specific_heat=None,
    pvt_component=DEFAULT_PVT_COMPONENT,
):
    """Return the thermal power extracted from the primary (doublet) flow.

    Computed across the heat exchanger (W):
    ``Q * rho * Cw * (T_in - T_out)``, with ``T_in`` read from
    ``inlet = (component, output_key)`` (default the ``filter_a`` outlet)
    and ``T_out`` from ``outlet`` (default the heat exchanger's primary
    outlet). ``fluid_density``/``specific_heat`` default to
    :func:`fluid_properties` of ``pvt_component`` (the brine PVT of the
    producer well, or the legacy constants if it has no ``"pvt"`` block).
    """
    if fluid_density is None or specific_heat is None:
        pvt_density, pvt_specific_heat = fluid_properties(solver, pvt_component)
        fluid_density = pvt_density if fluid_density is None else fluid_density
        specific_heat = pvt_specific_heat if specific_heat is None else specific_heat
    t_in = solver.get_output(*inlet)
    t_out = solver.get_output(*outlet)
    return primary_flow_m3s * fluid_density * specific_heat * (t_in - t_out)


def electricity_surplus_w(
    solver,
    esp_power_w=0.0,
    injector_pump_power_w=0.0,
    *,
    generators=DEFAULT_GENERATORS,
    consumers=(),
):
    """Return the net *electrical* power balance of the plant (W).

    Electricity generated by ``generators`` (default the CHP) minus ESP and
    injector pump consumption, minus the ``power_el`` of any ``consumers``
    components (excludes the secondary network's thermal power):
    ``E_CHP - E_ESP - E_InjectorPump - sum(E_consumers)``.
    ``esp_power_w``/``injector_pump_power_w`` are passed in separately
    (e.g. from ``gemini_simulator.well_flow.producer_esp_power_w`` or the
    ``producer_well``/``injector_well`` components' ``power_el`` outputs);
    do not list those components in ``consumers`` as well, or they count
    twice.
    """
    generated_w = sum_component_outputs(solver, "power_el", generators)
    consumed_w = sum_component_outputs(solver, "power_el", consumers)
    return generated_w - esp_power_w - injector_pump_power_w - consumed_w


# Kept as an alias for any earlier callers; prefer electricity_surplus_w.
net_electrical_power_w = electricity_surplus_w


def power_total_w(
    power_plant_w,
    solver,
    esp_power_w=0.0,
    injector_pump_power_w=0.0,
    *,
    generators=DEFAULT_GENERATORS,
    consumers=(),
):
    """Return the total plant power balance (W).

    Thermal power delivered by the secondary network, plus electricity
    generated, minus ESP, injector pump and other consumers' electricity:
    ``power_plant - E_ESP - E_InjectorPump + E_CHP`` (see
    :func:`electricity_surplus_w` for ``generators``/``consumers``).
    """
    return power_plant_w + electricity_surplus_w(
        solver,
        esp_power_w,
        injector_pump_power_w,
        generators=generators,
        consumers=consumers,
    )


def electrical_emission_kg_s(power_w, electricity_emission_factor_kg_per_kwh):
    """Return the CO2 emission rate (kg/s) of electricity.

    Associated with consuming/generating ``power_w`` (W) of electricity
    at a given emission factor (kg/kWh).
    """
    return electricity_emission_factor_kg_per_kwh / JOULE_PER_KWH * power_w


def esp_emission_kg_s(esp_power_w, chp_power_el_w, electricity_emission_factor_kg_per_kwh):
    """Return the CO2 emission rate (kg/s) of the ESP's electricity use.

    Net of on-site CHP generation used to offset it first:
    ``factor / 3.6E6 * (E_ESP - min(E_CHP, E_ESP))`` -- i.e. only the
    portion of the ESP's power draw *not* already covered by the CHP's
    own electricity generation is charged an emission (grid draw is
    assumed for that portion; onsite CHP generation is assumed
    emission-free here, since the CHP's gas emission is already
    accounted for separately via the CHP component's own emission output).
    """
    grid_power_w = max(0.0, esp_power_w - min(chp_power_el_w, esp_power_w))
    return electrical_emission_kg_s(grid_power_w, electricity_emission_factor_kg_per_kwh)


def total_emission_kg_s(
    solver,
    esp_emission_kg_s=0.0,
    injector_pump_emission_kg_s=0.0,
    *,
    emitters=DEFAULT_EMITTERS,
):
    """Return the total CO2 emission rate (kg/s).

    The ``emission`` outputs of ``emitters`` (default boiler + CHP gas)
    plus ESP and injector pump (electricity) emissions.
    """
    return (
        sum_component_outputs(solver, "emission", emitters)
        + esp_emission_kg_s
        + injector_pump_emission_kg_s
    )
