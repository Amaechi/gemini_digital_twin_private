TL-15 on C2-opt (base-year backups); the selected C2-opt-robust installs its robust backups (DR-11, OPT-ROBUST-BACKUP).

| KPI | P10 | P50 | P90 |
|---|---|---|---|
| capex_million | 25.99 | 28.22 | 32.87 |
| co2_t_per_yr | 5397.03 | 7939.11 | 10013.90 |
| lcoc_per_mwh | 20.39 | 27.55 | 37.08 |
| lcoh_per_mwh | 39.86 | 48.63 | 58.64 |
| lcoh_plus_lcoc_per_mwh | 69.66 | 78.36 | 88.85 |
| required_electric_chiller_mw | 8.20 | 8.20 | 8.20 |
| required_peak_boiler_mw | 3.50 | 3.99 | 7.82 |
| thermal_breakthrough_yr | 53.96 | 85.02 | 119.19 |
| unmet_cooling_days | 0.00 | 0.00 | 0.00 |
| unmet_cooling_mwh | 0.00 | 0.00 | 0.00 |
| unmet_heating_days | 0.00 | 2.50 | 49.30 |
| unmet_heating_mwh | 0.00 | 20.77 | 1547.79 |

Samples 12 (failed 0); P(demand met) at installed backups 0.4166666666666667; robust backups {'peak_boiler_mw': 7.85, 'electric_chiller_mw': 8.2} → P 0.8333333333333334

| Case | Register id | Value | LCOH | LCOC | Unmet MWh |
|---|---|---|---|---|---|
| ECO-CO2-GAS=51.53 | ECO-CO2-GAS | 51.53 | 42.9 | 26.3 | 0 |
| DB-31=45 | DB-31 | 45.0 | 37.7 | 26.1 | 0 |
| DB-31=65 | DB-31 | 65.0 | 53.3 | 26.6 | 2606 |
| DB-31=75 | DB-31 | 75.0 | 70.6 | 26.6 | 17876 |
| CHP-EL-FRAC=0.5 | CHP-EL-FRAC | 0.5 | 41.6 | 25.8 | 23 |
| SUB-PBUB.basis=pure_water | SUB-PBUB | pure_water | 43.0 | 26.3 | 0 |
| DB-34b=warm | DB-34b | True | 43.1 | 33.0 | 0 |
| DB-30=B | DB-30 | B | 43.8 | 30.5 | 0 |
