TL-15 on C2-opt (base-year backups); the selected C2-opt-robust installs its robust backups (DR-11, OPT-ROBUST-BACKUP).

| KPI | P10 | P50 | P90 |
|---|---|---|---|
| capex_million | 28.95 | 31.85 | 36.49 |
| co2_t_per_yr | 5407.30 | 7365.02 | 9209.53 |
| lcoc_per_mwh | 20.41 | 27.55 | 37.09 |
| lcoh_per_mwh | 44.56 | 50.24 | 66.36 |
| lcoh_plus_lcoc_per_mwh | 70.16 | 80.25 | 90.61 |
| required_electric_chiller_mw | 8.20 | 8.20 | 8.20 |
| required_peak_boiler_mw | 0.05 | 0.54 | 4.37 |
| thermal_breakthrough_yr | 63.82 | 95.39 | 117.72 |
| unmet_cooling_days | 0.00 | 0.00 | 0.00 |
| unmet_cooling_mwh | 0.00 | 0.00 | 0.00 |
| unmet_heating_days | 0.00 | 2.50 | 49.30 |
| unmet_heating_mwh | 0.00 | 20.74 | 1547.48 |

Samples 12 (failed 0); P(demand met) at installed backups 0.4166666666666667; robust backups {'peak_boiler_mw': 4.4, 'electric_chiller_mw': 8.2} → P 0.8333333333333334

| Case | Register id | Value | LCOH | LCOC | Unmet MWh |
|---|---|---|---|---|---|
| ECO-CO2-GAS=51.53 | ECO-CO2-GAS | 51.53 | 59.0 | 26.4 | 0 |
| DB-31=45 | DB-31 | 45.0 | 53.6 | 26.3 | 0 |
| DB-31=65 | DB-31 | 65.0 | 71.8 | 26.7 | 2606 |
| DB-31=75 | DB-31 | 75.0 | 92.9 | 26.8 | 17877 |
| CHP-EL-FRAC=0.5 | CHP-EL-FRAC | 0.5 | 57.7 | 26.0 | 23 |
| SUB-PBUB.basis=pure_water | SUB-PBUB | pure_water | 59.0 | 26.4 | 0 |
| DB-34b=warm | DB-34b | True | 59.1 | 33.1 | 0 |
| DB-30=B | DB-30 | B | 60.1 | 30.6 | 0 |
