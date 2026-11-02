TL-15 on C2-opt (base-year backups); the selected C2-opt-robust installs its robust backups (DR-11, OPT-ROBUST-BACKUP).

| KPI | P10 | P50 | P90 |
|---|---|---|---|
| capex_million | 23.58 | 27.03 | 30.52 |
| co2_t_per_yr | 5311.90 | 8329.11 | 11919.68 |
| lcoc_per_mwh | 20.86 | 26.70 | 34.45 |
| lcoh_per_mwh | 33.79 | 47.30 | 66.14 |
| lcoh_plus_lcoc_per_mwh | 59.35 | 73.86 | 96.52 |
| required_electric_chiller_mw | 8.20 | 8.20 | 8.20 |
| required_peak_boiler_mw | 5.00 | 5.38 | 10.85 |
| thermal_breakthrough_yr | 47.98 | 70.51 | 101.14 |
| unmet_cooling_days | 0.00 | 0.00 | 0.00 |
| unmet_cooling_mwh | 0.00 | 0.00 | 0.00 |
| unmet_heating_days | 0.00 | 2.00 | 71.00 |
| unmet_heating_mwh | 0.00 | 13.31 | 2772.52 |

Samples 200 (failed 0); P(demand met) at installed backups 0.46; robust backups {'peak_boiler_mw': 10.9, 'electric_chiller_mw': 8.2} → P 0.9

| Case | Register id | Value | LCOH | LCOC | Unmet MWh |
|---|---|---|---|---|---|
| ECO-CO2-GAS=51.53 | ECO-CO2-GAS | 51.53 | 42.5 | 26.3 | 0 |
| DB-31=45 | DB-31 | 45.0 | 36.5 | 26.1 | 0 |
| DB-31=65 | DB-31 | 65.0 | 54.7 | 26.5 | 2605 |
| DB-31=75 | DB-31 | 75.0 | 74.9 | 26.5 | 17727 |
| CHP-EL-FRAC=0.5 | CHP-EL-FRAC | 0.5 | 41.4 | 25.8 | 23 |
| SUB-PBUB.basis=pure_water | SUB-PBUB | pure_water | 42.6 | 26.3 | 0 |
| DB-34b=warm | DB-34b | True | 42.7 | 32.9 | 0 |
| DB-30=B | DB-30 | B | 43.4 | 30.4 | 0 |
