.. Modified by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01: added the
   project-JSON "pvt" block (GM-01).

Fluid: PVT Properties
==============================

Description
---------------------------

The ``gemini_model.fluid.pvt_water_stp.PVTConstantSTP`` model provides
representative water/CO2-brine PVT (pressure-volume-temperature) properties
used by other models (well hydraulics, corrosion, erosion) that need fluid
density, viscosity, heat capacity, or surface tension.

The current implementation returns **constant properties**, valid near
standard conditions (STP), rather than a full equation-of-state calculation.
It exposes valid pressure/temperature ranges so calling models can flag
extrapolation.

Parameters
---------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Parameter
     - Description
   * - ``pressure_min`` / ``pressure_max``
     - Valid pressure range (Pa)
   * - ``temperature_min`` / ``temperature_max``
     - Valid temperature range (K)
   * - ``RHOG`` / ``RHOL``
     - Gas / liquid density (kg/m³)
   * - ``GMF``
     - Gas mass fraction (-)
   * - ``VISG`` / ``VISL``
     - Gas / liquid viscosity (Pa.s)
   * - ``CPG`` / ``CPL``
     - Gas / liquid heat capacity (J/kg.K)
   * - ``HG`` / ``HL``
     - Gas / liquid enthalpy (kJ/kg)
   * - ``TCG`` / ``TCL``
     - Gas / liquid thermal conductivity (W/m.K)
   * - ``SIGMA``
     - Surface tension (N/m)
   * - ``SG`` / ``SL``
     - Gas / liquid entropy (kJ/kg.K)

Outputs
---------------------------

Calling ``get_pvt(pressure, temperature)`` returns the tuple
``(rho_g, rho_l, gmf, eta_g, eta_l, cp_g, cp_l, K_g, K_l, sigma)`` used
directly by the well hydraulics model (see :doc:`model_well_hydraulics`) to
compute mixture density, friction, and heat transfer.

Setting the PVT from project JSON
---------------------------------

The ``well``, ``producer_well`` and ``injector_well`` components accept an
optional ``"pvt"`` parameter block, built into a ``PVTConstantSTP`` by
``gemini_model.fluid.pvt_config.build_pvt``. Keys are the native parameter
names above or unit-suffixed aliases (``liquid_density_kg_m3``,
``liquid_viscosity_pa_s``, ``liquid_specific_heat_j_kgk``,
``gas_mass_fraction``, ...). Unknown keys and unphysical values raise an
error; without the block the water defaults above are used. For example, the
design-basis brine:

.. code-block:: json

    "pvt": {"liquid_density_kg_m3": 1030.0,
            "liquid_viscosity_pa_s": 0.0004,
            "liquid_specific_heat_j_kgk": 4100.0}

Over 2.5 km the brine column is about 7.4 bar heavier than water. The same
block sets the primary-loop density and heat capacity in
``gemini_simulator.kpi``. Use ``heat_exchanger_fluid_parameters`` to set the
matching ``fluid_density`` and ``specific_heat`` parameters of the heat
exchanger, boiler and CHP; they are not copied automatically, and
``kpi.fluid_consistency_issues`` reports a mismatch. See
``src/gemini_model/fluid/REFERENCE.md``.
