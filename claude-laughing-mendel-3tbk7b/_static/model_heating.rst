.. Added by the GeoAgent team for SPE GeoHackathon 2026, 2026-11-01 (WP-03, GM-03, GM-08, GM-09).

Heat pump, electric boiler and thermal storage
==============================================

Description
---------------------------

These models top up and shift geothermal heat for the 100 °C district-heating supply: a
**high-temperature heat pump** (HTHP) that lifts district water with heat drawn from the brine, an
**electric boiler** for peaks and back-up, and a **thermal storage** (seasonal HT-ATES or cold
storage). The heat pump and the electric boiler are **static models**; the storage is a
**dynamic model** (``gemini_model.model_abstract.DynamicModel``) whose state is the stored energy.
They are registered for project JSON files as ``heatpump``, ``electric_boiler`` and
``thermal_storage``.

Every model outputs ``power_el`` (electricity used, W), ``power_th`` (useful heat delivered, W)
and ``emission`` (0 kg/s; grid CO₂ is counted by the economics). Each model folder holds a
``REFERENCE.md`` with the equations, defaults and citations (reference cards RC-04, RC-09, RC-10).
The defaults are recorded in ``config/assumptions.yaml`` (``HP-ETA-L``, ``HP-APPROACH``,
``VER-HP-ETA``, ``VER-HP-LIFT``, ``EB-ETA``, ``STO-REC-HT``, ``STO-REC-COLD``).

Heat pump: closed-form path
---------------------------

``gemini_model.heatpump.heatpump_basic.HeatpumpBasic`` keeps GEMINI's Nelder-Mead solver as the
default (``solver: "nelder_mead"``; see :doc:`model_surface_equipment`). With
``solver: "closed_form"`` the same operating point is found without a starting guess (Geo-Heat
Center Guidebook 1989, Ch13; RC-04):

.. math::

   \bar T = \frac{T_{out} - T_{in}}{\ln(T_{out}/T_{in})}, \qquad
   COP = \eta_L \frac{\bar T_{sink}}{\bar T_{sink} - \bar T_{source}}, \qquad
   Q_h = Q_{src} \frac{COP}{COP - 1}, \qquad P_{el} = \frac{Q_h}{COP}

with :math:`\eta_L = 0.5` (``mode: "lorenz"``, water log-mean temperatures in K). The sink leaves
at ``Th_out_target``; the source outlet is the bracketed root of the energy balance. If it would
fall below ``Ts_in_minimum`` it is held there and the sink outlet drops instead (as GEMINI). Where
Nelder-Mead converges to a physical point the two solvers agree within 2 % (in practice 1e-5).

HTHP cap and datasheet limits
-----------------------------

On the closed-form path the result is capped at a fraction of the ideal cycle at the refrigerant
temperatures (condensing = sink outlet + 4 K, evaporating = source outlet − 4 K):

.. math::

   COP \le f \, \frac{T_{cond}}{T_{cond} - T_{evap}}

with :math:`f = 0.6` without a datasheet (RC-04 default, the verifier's ``VER-HP-ETA``). For the
RC-04 design case (district water 82 → 100 °C, brine 60 °C in) the uncapped Lorenz value is 4.79
and the capped one 3.71.

**HTHP limits are MISSING until a datasheet is cited.** The parameters ``datasheet`` (citation),
``max_lift_k``, ``max_sink_temperature_degc`` and ``max_carnot_fraction`` default to ``None``;
limits without a citation are refused. Without a datasheet the outlet-to-outlet temperature
difference is screened against 60 K (``VER-HP-LIFT``) and the maximum sink temperature is not
checked. The
outputs ``missing_inputs``, ``within_limits``, ``validity_warnings``, ``source_heat_w`` and
``heat_pump_point`` (the verifier's ``HeatPumpPoint`` evidence) are given by both solvers.
``dea_reference_cop`` interpolates the DEA performance table (40–100 K) as a plausibility band
only.

Electric boiler
---------------------------

``gemini_model.electric_boiler.electric_boiler.ElectricBoiler``: :math:`P_{el} = Q_{th} / 0.99`
(spec GM-08, DEA). Inputs ``heat_demand_w``, or ``water_flow_rate_m3s`` with
``water_temperature_in_degc`` (heated to ``water_temperature_out_degc``, default 100 °C). The duty
is capped at ``nominal_capacity_w`` and the rest reported as ``unmet_heat_w``.

Thermal storage
---------------------------

``gemini_model.thermal_storage.thermal_storage.ThermalStorage`` (RC-09):

.. math::

   E_{t+1} = E_t - \lambda \frac{\Delta t}{1\,d} E_t + \eta_c P_c \Delta t - \frac{P_d \Delta t}{\eta_d}

Inputs ``charge_power_w`` or ``discharge_power_w`` (W) and optional ``time_step_s`` (default one
day). Parameters: ``capacity_mwh``, ``initial_soc``, power limits, ``storage_type`` (``ht_ates`` or
``cold``). The cycle recovery :math:`\eta_c \eta_d` is put on discharge: 0.63 for HT-ATES (TU Delft)
and 0.96 for cold storage (RTES 0.96–0.99). Outputs include ``energy_mwh``, ``soc``, ``losses_w``,
the cumulative charged and discharged energy and an energy-balance residual.

Example project
---------------------------

.. code-block:: json

   {
     "components": {
       "hthp": {"type": "heatpump", "parameters": {"mode": "lorenz", "solver": "closed_form",
                "Th_out_target": 100.0, "Ts_in_minimum": 10.0, "Cp_s": 4100.0, "rho_s": 1030.0}},
       "eboiler": {"type": "electric_boiler", "parameters": {"nominal_capacity_w": 5.0e6}}
     },
     "boundary_inputs": [
       {"component": "hthp", "key": "Th_in", "value": 82.0},
       {"component": "hthp", "key": "Ts_in", "value": 60.0},
       {"component": "hthp", "key": "hot_flow_rate", "value": 0.02778},
       {"component": "hthp", "key": "source_flow_rate", "value": 0.02222}
     ],
     "connections": [
       {"predecessor": "hthp", "successor": "eboiler",
        "ports": [["Th_out", "water_temperature_in_degc"],
                  ["hot_flow_rate", "water_flow_rate_m3s"]]}
     ]
   }

References
---------------------------

- Geothermal Direct Use Engineering & Design Guidebook, Geo-Heat Center (1989): Ch13 heat pumps
  p.250 (water ΔT, fouling), p.258 (COP 4.41 at 15.6 / 48.9 °C), p.260 (peaking practice),
  Table 13.2 (1989 condenser limits, outdated).
- Danish Energy Agency technology catalogues: Industrial Process Heat ch. 2 (high-temperature heat
  pumps, COP by lift); Electricity & District Heating ch. 41 (electric boilers, η 0.99).
- TU Delft campus HT-ATES study (recovery ≈ 63 % after 10 years); RTES data-centre cooling paper
  (cold storage recovery 96–99 %, citing Pepin 2021).
