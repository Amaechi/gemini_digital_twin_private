# REFERENCE — free cooling: water-side economiser (GM-07)

Model: `free_cooling.FreeCooling` (registry type `free_cooling`), `free_cooling_mode`.
Source card: `data/knowledge/reference_cards/RC-07_heat_rejection_free_cooling_climate.md`
("Free cooling", "Climate"). Citations: Stockholm ATES-GSHP paper (seasonal performance factor
24–53); spec §3.3, §7.2 GM-07.

## Equations
- Economiser water temperature `T_fc = T_wb + approach_tower + approach_HEX` (4.5 K and 2 K [A];
  RC-03 gives a plate-HEX approach of 2–3 K).
- **Full** free cooling when `T_fc ≤ T_chilled,supply` ⇒ T_wb ≤ 20 − 4.5 − 2 = 13.5 °C.
- **Partial** (pre-cooling the chiller return) when `T_supply < T_fc < T_return` (T_wb + 6.5 < 26 °C):
  free cooling = load × (T_return − T_fc)/(T_return − T_supply); the chiller gets the rest
  (`residual_cooling_w`) with inlet water at `chilled_water_temperature_out_degc` = T_fc.
- **Off** when `T_fc ≥ T_return`.
- Optional `nominal_capacity_w` caps the free cooling; the chilled-water balance then gives the
  economiser outlet `T_return − Q_fc/(ρ c_p V)`.
- Power = fan + pumps ≈ 2 % of heat moved [A] ⇒ performance factor 50 (seasonal 24–53, papers).
  `heat_rejected_w = Q_fc + power_el` (if wired to a separate `heat_rejection` unit, set this
  model's `aux_power_fraction` to 0). Tower water (T_wb + 4.5 K) ≤ 0 °C returns a freeze warning.
- Ambient wet-bulb is an input; the agent side computes it with Stull (2011) in
  `gemini_agent.tools.climate` (TL-02b): `T_wb = T·atan(0.151977·(RH + 8.313659)^0.5) + atan(T + RH)
  − atan(RH − 1.676331) + 0.00391838·RH^1.5·atan(0.023101·RH) − 4.686035` (RH 5–99 %, T −20…50 °C).
- Assumption entry COOL-FC; supply/return 20/26 °C (spec, DB-32).

## Climate screening (RC-07, De Bilt normals 1991–2020, Stull wet-bulb)
| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T_wb °C | 2.4 | 2.4 | 4.6 | 7.3 | 10.6 | 13.3 | 15.4 | 15.2 | 12.7 | 9.3 | 5.8 | 3.0 |

Monthly means make Apr, May, Jun (marginal), Sep, Oct fully eligible (Jul/Aug partial) ⇒ ≈ 12.2 of
23.7 GWh cooling [D, screening]; the daily simulation (WP-07) decides.

## Outputs (W, °C, m³/s)
`mode`, `economiser_temperature_degc`, `cooling_load_w`, `free_cooling_w`, `residual_cooling_w`,
`free_cooling_fraction`, chilled-water inlet/outlet temperature and flow, `performance_factor`,
`validity_warnings`,
`heat_rejected_w`, `power_el`, `power_th` = free cooling (useful cooling), `emission` 0.

## Validation (unit tests, `unit_test/gemini_model/free_cooling/`)
- Thresholds 13.5 °C (full/partial) and 19.5 °C (partial/off); July partial fraction.
- RC-07 monthly table: Apr, May, Jun, Sep, Oct full; Jul/Aug partial.
- Power 2 % of heat moved, performance factor within 24–53; chilled-water balance exact.
