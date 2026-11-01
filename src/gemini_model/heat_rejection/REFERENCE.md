# REFERENCE — heat rejection: cooling tower / dry cooler (GM-06)

Model: `heat_rejection.HeatRejection` (registry type `heat_rejection`), `chiller_heat_rejection_w`.
Source card: `data/knowledge/reference_cards/RC-07_heat_rejection_free_cooling_climate.md`
("Heat rejection"). Citations: Geothermal Direct Use Engineering & Design Guidebook
(Geo-Heat Center 1989) p.262, 269; RTES data-centre cooling paper; DiPippo, *Geothermal Power
Plants* 3e, Ch23 p.11–12 (Miravalles comparison). Spec §7.2 GM-06.

## Equations
- Heat to reject from a chiller: `Q_reject = Q_cool (1 + 1/COP)` (Guidebook p.262) — 2.43 × Q_cool
  at COP 0.7 vs 1.33 at COP 3. The chiller models output `heat_rejected_w`; wire it to `heat_load_w`.
- Cooling tower leaving water: `T_cw,out = T_wb + approach`, approach 4–5 K [A] (default 4.5 K);
  range ≈ 5 K [A] (Guidebook rating ΔT 9 °F).
- Fan + cooling-water pump ≈ 2 % of heat rejected [A] (Guidebook 0.077 kW/ton incremental, p.269).
- Dry cooler: approach 5 K to dry-bulb [L RTES]; fan power ≈ 3× wet tower [L DiPippo] —
  implemented as 3 × 2 % = 6 % of heat rejected [A]; cost ≈ 3× and area ≈ 3× (economics, WP-08).
- Water consumption (MAY): evaporation ≈ `Q_reject / h_fg`, h_fg = 2.45 MJ/kg (water at ≈ 20 °C,
  steam tables) [A] — an upper bound (all heat by evaporation); 0 for a dry cooler.
- With `water_flow_rate_m3s` the entering water temperature follows from `Q = ρ c_p V (T_in − T_out)`;
  without it the range is fixed (5 K) and the flow is reported.
- `heat_to_ambient_w = heat_load_w + power_el` (all energy leaves as heat). No capacity limit:
  size the unit at the summer design point (DB-34b: 25.0 °C DB / 18.4 °C WB; warm case 30/21 °C).
- Wiring: for an absorption chiller set `include_heat_rejection_auxiliaries: false`, else the
  tower fan/pump is counted twice (COOL-ABS-AUX). The electric chiller's `power_el` is
  compressor power only (RC-06).
- A wet tower whose fixed-approach leaving water is ≤ 0 °C returns a freeze warning
  (freeze protection not modelled).
- Assumption entry COOL-HR.

## Outputs (W, °C, m³/s, kg/s)
`heat_rejected_w`, `heat_to_ambient_w`, `water_temperature_out_degc` (cold, to the chiller),
`water_temperature_in_degc` (hot, from the chiller), `water_flow_rate_m3s`, `range_k`,
`approach_k`, `water_evaporation_kg_s`, `validity_warnings`, `power_el` (fan + pump), `power_th` 0 (no useful heat),
`emission` 0.

## Validation (unit tests, `unit_test/gemini_model/heat_rejection/`)
- `Q_reject/Q_cool` = 2.43 at COP 0.7 and 1.33 at COP 3.
- Tower defaults (T_wb + 4.5, range 5 K, 2 %, Q/h_fg); dry cooler (T_db + 5, 3× power, no water).
- Water-side energy balance exact with a given flow.
