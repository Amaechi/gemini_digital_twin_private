# RC-06 Electric (vapour-compression) chiller

**Feeds:** GM-05 ElectricChiller, dispatch, ECO-04.
**Verification status:** calibration from Guidebook p.257–258, 262, 266 text; RTES paper Table/Fig values.

## Equations
- `COP = η · T_evap / (T_cond − T_evap)` (K), with
  - T_evap = T_chilled,supply − 4 K [A] (20 °C supply ⇒ 16 °C),
  - T_cond = T_cooling_water,leaving + 3 K with cooling tower (T_cw,leaving = T_wb + approach 4–5 K + range ~5 K) [A], or T_db + 8 K with dry cooler [A].
- `P_el = Q_cool / COP`; `Q_reject = Q_cool + P_el`.
- Cap COP at 8 [A]; floor at 3.

## Calibration and benchmarks
| Item | Value | Source |
|---|---|---|
| η implied | ≈ 0.39–0.40 (COP 4.39 at 45 °F evap / 90 °F cond leaving water; 4.07 at 45/95 °F) | Guidebook p.257–258 |
| Condenser sensitivity | ≈ −2.6 %/K of condenser temperature | Guidebook p.257–258 |
| Centrifugal (1980s) | 0.65 kW/ton (COP 5.4); 0.7 kW/ton (COP 5.0); malting 0.82 kW/ton | Guidebook p.262, 267, 266 |
| Modern, warm chilled water (35–45 °C facility water) | min COP 6.38 (all hours), 8.5 (peak hours) | papers/ RTES data-centre paper |

At 20 °C chilled supply and Dutch summer conditions expect COP ≈ 6–8.

## Validation tests
- Reproduce COP 4.39 at 7.2 °C / 32.2 °C with η = 0.39 (±2 %).
- Monotonic: COP decreases with T_cond, increases with T_evap (property tests).
