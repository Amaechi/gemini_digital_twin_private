# REFERENCE — electric chiller (GM-05)

Model: `electric_chiller.ElectricChiller` (registry type `electric_chiller`), `carnot_cop`.
Source card: `data/knowledge/reference_cards/RC-06_electric_chiller.md`.
Citations: Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989)
p.257–258, 262, 266, 267; RTES data-centre cooling paper. Spec §3.3, §7.2 GM-05.

## Equations
- `COP = η · T_evap / (T_cond − T_evap)` (K); η = 0.40 (implied 0.39–0.40) [L].
  **Known bias (COOL-ELC):** η was implied with the water temperatures as cycle temperatures,
  but the chain below adds −4 K/+3 K approaches; through the chain the Guidebook point (4.39)
  needs η ≈ 0.44 (η 0.39 gives 3.90). η 0.40 (card, spec GM-05) therefore understates the COP by
  ≈ 9–11 % and favours absorption in comparisons; sensitivity case η 0.44. Card-level issue.
- `T_evap = T_chilled,supply − 4 K` [A] (20 °C supply ⇒ 16 °C)
- `T_cond = T_cw,leaving + 3 K` with a cooling tower, `T_cw,leaving = T_wb + approach (4–5 K,
  default 4.5) + range (~5 K)` [A]; or `T_cond = T_db + 8 K` with a dry cooler [A]
- Implementation: if `cooling_water_temperature_in_degc` (water from a `heat_rejection` unit) is
  given, `T_cond = T_cw,in + range + 3 K` for either option (with `dry_cooler` a warning says so:
  a water-loop dry cooler at T_db + 5 K gives T_db + 13 K vs the air-cooled T_db + 8 K, COP 5.26
  vs 6.80 at 25 °C); `evaporator_temperature_degc` /
  `condenser_temperature_degc` inputs override the derived values.
- `P_el = Q_cool / COP`; `Q_reject = Q_cool + P_el` (exact)
- COP capped at 8, floored at 3 [A] (a warning is returned when either binds)
- Capacity = nominal (no temperature derating); unmet load reported. Assumption entry COOL-ELC.

## Calibration and benchmarks
| Item | Value | Source |
|---|---|---|
| η implied | ≈ 0.39–0.40 (COP 4.39 at 45 °F evap / 90 °F cond leaving water; 4.07 at 45/95 °F) | Guidebook p.257–258 |
| Condenser sensitivity | ≈ −2.6 %/K of condenser temperature | Guidebook p.257–258 |
| Centrifugal (1980s) | 0.65 kW/ton (COP 5.4); 0.7 kW/ton (COP 5.0); malting 0.82 kW/ton | Guidebook p.262, 267, 266 |
| Modern, warm chilled water (35–45 °C facility water) | min COP 6.38 (all hours), 8.5 (peak hours) | RTES data-centre paper |

At 20 °C chilled supply and Dutch summer conditions expect COP ≈ 6–8: the model gives 7.76
(tower, design WB 18.4 °C), 6.61 (warm-case WB 21 °C), 6.80 (dry cooler, DB 25 °C).

**Condenser sensitivity (documented check):** the constant-η model gives
`d ln COP / dT_cond = −1/(T_cond − T_evap)` = −4.0 %/K at 7.2/32.2 °C, steeper than the Guidebook
data (≈ −2.6 %/K): the COP falls faster in hot weather; output `condenser_sensitivity_per_k`.
The spec GM-05 row omits the 5 K range (`T_cond = T_wb + approach + 3 K`); the card is followed.

## Outputs (W, °C, m³/s)
`cooling_power_w`, `cooling_load_w`, `unmet_cooling_w`, `load_ratio`, `cop`,
`cop_uncapped`, `evaporator_temperature_degc`, `condenser_temperature_degc`,
`condenser_sensitivity_per_k`, `heat_rejected_w`, chilled-water inlet/outlet temperature and flow,
`validity_warnings`, `power_el`, `power_th` = Q_cool (useful cooling), `emission` 0.

## Validation (unit tests, `unit_test/gemini_model/electric_chiller/`)
- COP 4.39 at 7.2 °C / 32.2 °C with η = 0.39 (± 2 %), directly and through the tower chain.
- Monotonic: COP decreases with T_cond, increases with T_evap.
- Dutch summer COP 6–8; cap/floor; exact energy balance; condenser-sensitivity comparison.
