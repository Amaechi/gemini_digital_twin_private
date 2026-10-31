# RC-09 District network and thermal storage

**Feeds:** DB-31/32 return temperatures, TL-23 pipe_heat_loss, GM-09 ThermalStorage, dispatch rules.
**Verification status:** values from Guidebook Ch10/12, Danfoss/Thisted, Shallow DHC review, WEC, TU Delft/RTES papers.

## Temperatures and design practice
| Item | Value | Source |
|---|---|---|
| Heating supply / return default | 100 / 55 °C [A] | spec DB-31 |
| Geothermal practice ΔT | ≥ 17–22 K (30–40 °F) or larger; supply reset; two-way valves | Guidebook p.231, 234 |
| Low-temperature DH (4GDH) | Thisted 76/43 (summer), 78/40 (winter); scenarios 60/35, 65/35 °C; Lystrup 50 °C supply | Danfoss/Thisted p.3–5 |
| Reinjection bonus | −3 K reinjection ⇒ +10 % geothermal capacity | Danfoss/Thisted Table 2 |
| Cooling supply / return | 20 / 26 °C [A]; direct cooling grids ΔT 5–6 K | Shallow DHC review p.8 |
| Peaking rule | geothermal at ~50–60 % of peak covers 80–90 % of annual energy | WEC p.7; Guidebook Fig 12.30 p.244 |

## Pipe heat loss (TL-23)
- `ΔT_pipe = q' L / (ṁ c_p)`; q' (W/m) independent of flow ⇒ check minimum flow.
- Pre-insulated PUR (k ≈ 0.026 W/mK, 25–50 mm): 6″ pipe at 300 gpm, 150 °F ⇒ 0.55 K/km buried; bare buried 3.6 K/km (Guidebook p.211–212).
- Buried layered-resistance example: 8″ FRP pre-insulated, 66 °C water, −7 °C surface ⇒ ≈ 32 W/m (Guidebook p.213–214).
- Distribution losses benchmark: 10.9–17 % of delivered heat (Danfoss/Thisted).
- Network length not given ⇒ default: losses as % of demand [A] 10 %, or a nominal 5 km trunk [A]; flag as missing input.

## Thermal storage (GM-09, optional)
- State: `E_{t+1} = E_t + η_c P_c Δt − P_d Δt / η_d − losses`.
- High-temperature ATES recovery ≈ 63 % after 10 years (TU Delft, papers/); cold RTES 96–99 % (RTES paper citing Pepin 2021).
- Daily storage adds little at daily resolution; ~12 h duration captures most value for cooling peaks (RTES paper) — only relevant with sub-daily data.
- Seasonal ATES requires annual heat/cold balance (Stockholm, Munich regulators).

## Validation tests
- Pipe ΔT example reproduces 0.55 K/km ±10 %.
- Storage energy balance closes exactly over a cycle with η = 1.
