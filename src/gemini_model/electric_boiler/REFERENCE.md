# REFERENCE — electric boiler (GM-08)

Model: `electric_boiler.ElectricBoiler` (registry type `electric_boiler`).
Sources: spec §7.2 GM-08 (η 0.99); RC-10 "Sourced values" — DEA Electricity & DH catalogue,
ch. 41 "Electric boilers" (η 0.99, life 20 yr, capex in `config/cost_assumptions.yaml`); RC-04
peaking practice ("boiler in series on return", Guidebook p.260).

## Equations
- `P_el = Q_th / η`, η = 0.99 (register EB-ETA); losses `P_el − Q_th`; `emission` 0 (grid CO₂
  is counted by economics, WP-08).
- Duty: `heat_demand_w`, or a water flow heated to the set point (default 100 °C, the heating
  supply hard limit): `Q = ρ c_p V (T_set − T_in)`, 0 if `T_in ≥ T_set`.
- Capacity: `Q_th = min(Q, nominal_capacity_w)`; `unmet_heat_w` reported; with a flow
  `T_out = T_in + Q_th / (ρ c_p V)`.
- Water ρ, c_p default to `liquid_properties()` (GEMINI water, 1000 kg/m³, 4217.4 J/kg K);
  set them for brine if ever placed on the brine side.

## Validation tests
`unit_test/gemini_model/electric_boiler/test_electric_boiler.py`: P_el = Q/0.99; capacity
limit; flow mode reaches 100 °C; capped outlet; register equality.
