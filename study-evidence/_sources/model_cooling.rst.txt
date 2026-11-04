.. Added by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01 (WP-02, GM-04...GM-07).

Cooling
==============================================

Description
---------------------------

The cooling models describe how district cooling (20 °C supply, 26 °C return) can be produced
next to a geothermal doublet: a hot-water driven **absorption chiller**, an **electric
(vapour-compression) chiller**, **heat rejection** to ambient (wet cooling tower or dry cooler) and
**free cooling** (a water-side economiser). All four are **static models**
(``gemini_model.model_abstract.StaticModel``) registered for project JSON files as
``absorption_chiller``, ``electric_chiller``, ``heat_rejection`` and ``free_cooling``.

Units follow the GEMINI surface models: temperatures in °C, flows in m³/s, powers in W. New keys
carry unit suffixes (``_degc``, ``_m3s``, ``_w``, ``_k``). Every model outputs ``power_el``
(electricity used, W), ``power_th`` (useful cooling delivered, W; 0 for heat rejection) and
``emission`` (0 kg/s). Ambient dry-bulb and wet-bulb temperatures are **inputs**; the agent
computes daily values from the KNMI normals (Stull wet-bulb) in ``gemini_agent.tools.climate``.

Each model folder holds a ``REFERENCE.md`` with the equations, defaults and citations (reference
cards RC-05, RC-06, RC-07). The defaults are recorded in ``config/assumptions.yaml``
(``COOL-ABS-*``, ``COOL-ELC``, ``COOL-HR``, ``COOL-FC``).

Absorption chiller
---------------------------

``gemini_model.absorption_chiller.absorption_chiller.AbsorptionChiller`` models a single-effect
LiBr/H₂O chiller (Geo-Heat Center Guidebook 1989, Ch14 p.261–270). Two methods:

**Method A** (``method: "ghc1989"``), temperatures in °F:

.. math::

   F_T = \frac{-239 + 1.65\,T_{hw} - 0.001\,T_{hw}^2}{100}, \qquad
   F_c = \frac{323 - 0.838\,T_{chw} - (3.77 - 0.0358\,T_{chw})\,T_{cw}}{100}

.. math::

   Q_{avail} = F_T\,F_c\,Q_{nom}, \qquad COP = \frac{0.67\,(55.4 + 0.185\,T_{hw})}{100}

valid for 40–50 °F chilled and 75–90 °F cooling water. Outside, Method A is a lower bound: by
default chilled water above 50 °F is evaluated at 50 °F and cooling water below 75 °F at 75 °F.

**Method B** (``method: "characteristic"``, default), the characteristic equation:

.. math::

   \Delta\Delta t = (\bar t_G - \bar t_{AC}) - B\,(\bar t_{AC} - \bar t_E), \qquad
   Q_E = s_E\,(\Delta\Delta t - \Delta\Delta t_{min}), \qquad
   Q_G = s_G\,(\Delta\Delta t - \Delta\Delta t_{min,G})

with :math:`B = 1.15` and coefficients calibrated to Method A at its rating points (44 °F chilled,
85 °F cooling water, 180–240 °F hot water) by
``gemini_model.absorption_chiller.characteristic.calibrate_to_ghc1989``:
:math:`s_E = 0.022260` and :math:`s_G = 0.032491` per K (fractions of nominal capacity),
:math:`\Delta\Delta t_{min} = 9.503` K, :math:`\Delta\Delta t_{min,G} = 7.453` K. On the rating
points it reproduces Method A within 1.0 % (capacity) and 2.1 % (COP). Results at 20 °C chilled
water are extrapolated and flagged in ``validity_warnings``. Capacity never exceeds the nameplate.

Energy balance: :math:`Q_{reject} = Q_E + Q_G + P_{aux}` with :math:`P_{aux}` = 0.077 kW/ton
(≈ 2.2 % of :math:`Q_E`, extra tower fan and pump) while ``include_heat_rejection_auxiliaries`` is
true (default); set it to false when a ``heat_rejection`` component is connected, which counts the
fan and pumps itself (:math:`P_{aux} = 0`). Each external circuit (drive, cooling water,
chilled water) has its own density and heat capacity; with a given flow its outlet temperature
follows from :math:`\rho c_p V \Delta T = Q`, otherwise a fixed ΔT is used (generator 7.2 K,
cooling-water range 5 K) and the required flow is reported. With a given drive flow, Method B
needs :math:`\rho c_p V_G \ge s_G/2` (output ``minimum_drive_flow_rate_m3s``; otherwise the drive
outlet would fall below the minimum generator temperature and the machine is treated as off), and
Method A keeps the drive outlet at or above its 71.4 °C zero-capacity temperature. At part load
Method B lowers :math:`\Delta\Delta t`, so the COP falls at low load.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Input
     - Description
   * - ``drive_temperature_in_degc``
     - Hot-water (brine or clean loop) inlet temperature (°C)
   * - ``cooling_water_temperature_in_degc``
     - Cooling-water inlet temperature (°C), e.g. a heat-rejection unit's ``water_temperature_out_degc``
   * - ``cooling_load_w`` *or* ``chilled_water_flow_rate_m3s``
     - Cooling demand (W), or chilled-water flow (m³/s) with load :math:`\rho c_p V (T_{return} - T_{supply})`
   * - ``drive_flow_rate_m3s``, ``cooling_water_flow_rate_m3s``
     - *(optional)* circuit flows (m³/s)
   * - ``chilled_water_temperature_in_degc`` / ``_out_degc``
     - *(optional)* return temperature / supply set point (defaults 26 / 20 °C)

Main outputs: ``cooling_power_w``, ``available_cooling_w``, ``unmet_cooling_w``, ``cop``,
``drive_heat_w``, ``heat_rejected_w``, ``drive_temperature_out_degc``, ``drive_flow_rate_m3s``,
``cooling_water_temperature_out_degc``, ``chilled_water_temperature_out_degc``,
``validity_warnings``.

Electric chiller
---------------------------

``gemini_model.electric_chiller.electric_chiller.ElectricChiller`` (Guidebook p.257–258):

.. math::

   COP = \eta\,\frac{T_{evap}}{T_{cond} - T_{evap}}, \qquad P_{el} = \frac{Q}{COP}, \qquad
   Q_{reject} = Q + P_{el}

with :math:`T_{evap} = T_{supply} - 4` K, :math:`T_{cond} = T_{wb} + 4.5 + 5 + 3`
(cooling tower) or :math:`T_{db} + 8` (dry cooler), COP between 3 and 8. Inputs: ``cooling_load_w``
or ``chilled_water_flow_rate_m3s``; ``ambient_wet_bulb_degc`` or ``ambient_dry_bulb_degc``; optional
``cooling_water_temperature_in_degc`` (then :math:`T_{cond} = T_{cw,in} + 5 + 3`). η is on this
**refrigerant basis**: calibrated so that the Guidebook point (COP 4.39 at 7.2 °C chilled / 32.2 °C
condenser leaving water) is reproduced, :math:`\eta = 4.39 \times 32 / 276.35 = 0.508` (the
Guidebook's 0.39–0.40 is on the water basis; RC-06). With this η the COP cap of 8 binds at the
Dutch design points. :math:`P_{el}` is compressor power only.

Heat rejection
---------------------------

``gemini_model.heat_rejection.heat_rejection.HeatRejection`` (``technology``: ``cooling_tower`` or
``dry_cooler``): leaving water :math:`T_{wb} + 4.5` K (tower) or :math:`T_{db} + 5` K (dry cooler),
range 5 K, fan + pump power 2 % (tower) or 6 % (dry cooler) of ``heat_load_w``, tower evaporation
:math:`Q / h_{fg}`. ``chiller_heat_rejection_w`` gives :math:`Q_{reject} = Q_{cool}(1 + 1/COP)`.

Free cooling
---------------------------

``gemini_model.free_cooling.free_cooling.FreeCooling``: the economiser delivers water at
:math:`T_{fc} = T_{wb} + 4.5 + 2` K. Full free cooling when :math:`T_{fc} \le 20` °C
(:math:`T_{wb} \le 13.5` °C); partial pre-cooling of the 26 °C return while :math:`T_{fc} < 26` °C
(fraction :math:`(26 - T_{fc})/6`, the rest goes to a chiller via ``residual_cooling_w`` and
``chilled_water_temperature_out_degc``); power 2 % of the heat moved.

Example project
---------------------------

.. code-block:: json

   {
     "components": {
       "economiser": {"type": "free_cooling", "parameters": {}},
       "chiller": {"type": "electric_chiller", "parameters": {"nominal_capacity_w": 5e6}},
       "tower": {"type": "heat_rejection", "parameters": {}}
     },
     "boundary_inputs": [
       {"component": "economiser", "key": "ambient_wet_bulb_degc", "value": 15.4},
       {"component": "economiser", "key": "cooling_load_w", "value": 4e6},
       {"component": "chiller", "key": "ambient_wet_bulb_degc", "value": 15.4},
       {"component": "tower", "key": "ambient_wet_bulb_degc", "value": 15.4}
     ],
     "connections": [
       {"predecessor": "economiser", "successor": "chiller",
        "ports": [["residual_cooling_w", "cooling_load_w"],
                  ["chilled_water_temperature_out_degc", "chilled_water_temperature_in_degc"]]},
       {"predecessor": "chiller", "successor": "tower",
        "predecessor_output": "heat_rejected_w", "successor_input": "heat_load_w"}
     ]
   }

References
---------------------------

- Geothermal Direct Use Engineering & Design Guidebook, Geo-Heat Center (1989): Ch14 absorption
  cooling p.261–270; p.257–258 (electric chiller COP).
- DiPippo, R., *Geothermal Power Plants*, 3rd ed., Ch23 p.11–12 (wet vs dry heat rejection).
- RTES data-centre cooling paper (dry-cooler approach, modern chiller COP); Stockholm ATES-GSHP
  paper (free-cooling seasonal performance factor 24–53).
- Stull, R. (2011), Wet-bulb temperature from relative humidity and air temperature, J. Appl.
  Meteor. Climatol. 50, 2267–2269 (used on the agent side).
