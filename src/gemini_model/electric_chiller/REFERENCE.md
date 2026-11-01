# REFERENCE — electric chiller (GM-05)

Model: `electric_chiller.ElectricChiller` (registry type `electric_chiller`), `carnot_cop`.
Source card: `data/knowledge/reference_cards/RC-06_electric_chiller.md`.
Citations: Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989)
p.257–258, 262, 266, 267; RTES data-centre cooling paper. Spec §3.3, §7.2 GM-05.

## Equations
- `COP = η · T_evap / (T_cond − T_evap)` (K) on the **refrigerant basis** (offsets below).
- **η (COOL-ELC, [D]):** RC-06 "Temperature basis of η" — the Guidebook's 0.39–0.40 is on the
  water basis. The model calibrates η on its own basis so that COP(7.2 °C chilled / 32.2 °C
  condenser leaving water) = 4.39: T_evap 3.2 °C, T_cond 35.2 °C, η = 4.39 × 32 / 276.35 = 0.50834
  (card ≈ 0.51 / 0.508; condenser offset only 0.44). `carnot_efficiency: null` (default) →
  `calibrated_carnot_efficiency(evaporator_approach_k, condenser_approach_k)`, so changing an
  approach keeps the point reproduced; an explicit value overrides it (same basis). Output
  `carnot_efficiency`. Sensitivity 0.44–0.513 (0.513: the 45/95 °F point on the model basis).
- `T_evap = T_chilled,supply − 4 K` [A] (20 °C supply ⇒ 16 °C)
- `T_cond = T_cw,leaving + 3 K` with a cooling tower, `T_cw,leaving = T_wb + approach (4–5 K,
  default 4.5) + range (~5 K)` [A]; or `T_cond = T_db + 8 K` with a dry cooler [A]
- Implementation: if `cooling_water_temperature_in_degc` (water from a `heat_rejection` unit) is
  given, `T_cond = T_cw,in + range + 3 K` for either option (with `dry_cooler` a warning says so:
  a water-loop dry cooler at T_db + 5 K gives T_db + 13 K vs the air-cooled T_db + 8 K);
  `evaporator_temperature_degc` / `condenser_temperature_degc` inputs override the derived values.
- `P_el = Q_cool / COP` is **compressor power only**; tower fan and condenser-water pump belong to
  the heat-rejection model when one is connected (RC-06); `Q_reject = Q_cool + P_el` (exact)
- COP capped at 8, floored at 3 [A] (a warning is returned when either binds)
- Capacity = nominal (no temperature derating); unmet load reported. Temperature chain and limits:
  assumption entry COOL-ELC-APPROACH.

## Calibration and benchmarks
| Item | Value | Source |
|---|---|---|
| η implied | ≈ 0.39–0.40 (COP 4.39 at 45 °F evap / 90 °F cond leaving water; 4.07 at 45/95 °F) | Guidebook p.257–258 |
| Temperature basis of η (RC-06) | 0.39–0.40 is with the **water** temperatures (7.2 / 32.2 °C) in the COP equation; on the model's −4 K / +3 K refrigerant basis the same point gives **η ≈ 0.51** (condenser offset only: 0.44). Calibrate on the model's own basis, record as [D], do not mix bases. | derived from the Guidebook point |
| Condenser sensitivity | ≈ −2.6 %/K of condenser temperature | Guidebook p.257–258 |
| Centrifugal (1980s) | 0.65 kW/ton (COP 5.4); 0.7 kW/ton (COP 5.0); malting 0.82 kW/ton | Guidebook p.262, 267, 266 |
| Modern, warm chilled water (35–45 °C facility water) | min COP 6.38 (all hours), 8.5 (peak hours) | RTES data-centre paper |

At 20 °C chilled supply and Dutch summer conditions expect COP ≈ 6–8: with η 0.508 the cap of 8
binds at the design points (uncapped 9.87 tower WB 18.4 °C, 8.40 WB 21 °C, 8.65 dry cooler DB
25 °C); a dry cooler at 30 °C DB gives 6.68.

**Condenser sensitivity (documented check):** the constant-η model gives
`d ln COP / dT_cond = −1/(T_cond − T_evap)` = −3.1 %/K at the Guidebook point (lift 32 K), steeper
than the Guidebook data (≈ −2.6 %/K): the COP falls faster in hot weather; output `condenser_sensitivity_per_k`.
The spec GM-05 row omits the 5 K range (`T_cond = T_wb + approach + 3 K`); the card is followed.

## Outputs (W, °C, m³/s)
`cooling_power_w`, `cooling_load_w`, `unmet_cooling_w`, `load_ratio`, `cop`,
`cop_uncapped`, `carnot_efficiency`, `evaporator_temperature_degc`, `condenser_temperature_degc`,
`condenser_sensitivity_per_k`, `heat_rejected_w`, chilled-water inlet/outlet temperature and flow,
`validity_warnings`, `power_el`, `power_th` = Q_cool (useful cooling), `emission` 0.

## Validation (unit tests, `unit_test/gemini_model/electric_chiller/`)
- RC-06 validation on the model basis: COP(7.2 °C chilled / 32.2 °C condenser leaving water) =
  4.39 with the calibrated η = 0.508 (via condenser water and via the tower chain); the water-basis
  reading (η 0.39, no offsets, ± 2 %) is checked on `carnot_cop` only. η on each basis
  (0.508 / 0.44 / 0.39); recalibration when the approaches change; explicit override.
- Monotonic: COP decreases with T_cond, increases with T_evap.
- Dutch summer COP 6–8; cap/floor; exact energy balance; condenser-sensitivity comparison.
