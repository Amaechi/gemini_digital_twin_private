# GeoAgent results pack — run `final_llm`

Built by `scripts/build_report_pack.py` from tool outputs only; each table names its source file. Rebuild with `PYTHONPATH=src python scripts/build_report_pack.py`.

Run: engine `llm+policy_fallback`, model `gemma4:12b-it-q4_K_M`, input mode `ask`, status `completed` in phase P10, selected `C2-opt-robust`, verdict **ACCEPTED**, approval `approved`, tool calls 23 (0 stubs), LLM fallbacks 2. Source: `runs/final/final_llm/run_summary.json`.

## 1. Answer

Source: `runs/final/final_llm/report_draft.md` (section *Answer*).

- Recommended design: **C2-opt-robust** — C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP)
- Verifier verdict: **ACCEPTED**
- Unmet demand: heating 0 d / 0 MWh, cooling 0 d / 0 MWh
- LCOH 44.6 USD/MWh, LCOC 26.3 USD/MWh (30 yr, TL-14); CAPEX 30.1 MUSD
- CO₂ 7360 t/yr, 72% below the C0 counterfactual
- Exergy efficiency 0.44 (T0 = daily dry-bulb (DB-34d), De Bilt (KNMI) 2027: mean 10.5 °C, range 3.6–18.2 °C)
- Robustness (TL-15 on C2-opt, 200 samples): demand met in 46% of samples with the base-year backups {'peak_boiler_mw': 5.0, 'electric_chiller_mw': 8.2} MW; robust backups {'peak_boiler_mw': 10.9, 'electric_chiller_mw': 8.2} MW → 90% (installed by DR-11, llm)
- Backup capacity (DR-11): C2-opt-robust vs C2-opt: ΔLCOH +2.05, ΔLCOC +0.00 USD/MWh, ΔCAPEX +2.21 MUSD (full year, TL-12 + TL-14)

## 2. Candidate comparison C0–C8 and optima

Source: `runs/final/final_llm/figures/candidates.md` (also `candidates.csv`).

| candidate | concept | doublets | depth_m | flow_m3h | unmet_heat_d | unmet_cool_d | capex_musd | lcoh_usd_mwh | lcoc_usd_mwh | co2_t_yr | co2_cut_vs_c0 | exergy_eff | excluded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0-0d | C0 | 0 | – | – | 0 | 0 | 13.7 | 137.0 | 36.0 | 26332 | – | 0.17 | – |
| C1-3d | C1 | 3 | 2500 | 300 | 0 | 0 | 28.3 | 54.2 | 23.6 | 13963 | 47% | 0.26 | – |
| C2-2d | C2 | 2 | 2500 | 300 | 0 | 0 | 34.3 | 50.3 | 26.3 | 6522 | 75% | 0.54 | – |
| C3-2d | C3 | 2 | 2500 | 300 | 0 | 0 | 37.0 | 50.0 | 40.4 | 6696 | 75% | 0.52 | – |
| C4-2d | C4 | 2 | 3200 | 300 | 0 | 0 | 34.7 | 29.4 | 38.7 | 4597 | 83% | 0.41 | excluded (PH-19) |
| C5-3d | C5 | 3 | 1500 | 300 | 0 | 0 | 56.4 | 104.5 | 26.1 | 14327 | 46% | 0.39 | – |
| C6-2d | C6 | 2 | 2500 | 300 | 0 | 0 | 34.3 | 50.3 | 26.3 | 6522 | 75% | 0.54 | – |
| C7-2d | C7 | 2 | 2500 | 300 | 0 | 0 | 34.8 | 62.1 | 27.3 | 6218 | 76% | 0.52 | – |
| C8-3d | C8 | 3 | 2500 | 300 | 0 | 0 | 41.4 | 57.0 | 26.3 | 6513 | 75% | 0.55 | – |
| C2-opt | C2 | 2 | 2500 | 296 | 0 | 0 | 27.9 | 42.5 | 26.3 | 7360 | 72% | 0.44 | – |
| C6-opt | C6 | 2 | 2500 | 296 | 0 | 0 | 27.9 | 42.6 | 26.3 | 7361 | 72% | 0.44 | – |
| C1-opt | C1 | 3 | 2500 | 265 | 0 | 0 | 28.2 | 53.8 | 23.5 | 13284 | 50% | 0.27 | – |
| C2-opt-robust * | C2 | 2 | 2500 | 296 | 0 | 0 | 30.1 | 44.6 | 26.3 | 7360 | 72% | 0.44 | – |

## 3. Final design sizing

Source: `runs/final/final_llm/figures/sizing.md`.

| Item | Value | Basis |
|---|---|---|
| Design | C2-opt-robust | C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP) |
| Doublets | 2 | DB-10: 1–3 |
| Reservoir depth | 2500 m | TL-13 (verifiable depth options) |
| Design flow per doublet | 296 m³/h | DB-11: 50–300 |
| Gas handling | degas_use | DR-07 |
| Heating return (DB-31) | 55 °C | supply 100 °C |
| Cooling return (DB-32) | 26 °C | supply 20 °C |
| electric_chiller_mw | 8.20 | TL-13 size / right-sized backup |
| hthp_heating_mw | 4.10 | TL-13 size / right-sized backup |
| peak_boiler_mw | 10.90 | TL-15 robust backup (DR-11, OPT-ROBUST-BACKUP); C2-opt: 5.00 |
| Outage start days (0-based) | [119, 238] | DR-06 |
| Depths excluded | [3000.0, 3200.0, 3500.0] | PH-19 NPSH / PH-30 letdown |
| Installed heating | 36.3 MW | TL-12 |
| Installed cooling | 16.8 MW | TL-12 |
| Doublet capacity factors (flow-based) | 0.52, 0.47 | TL-12 mean/design |
| Thermal breakthrough (annual-mean flow) | 66.1 yr | TL-12 end of life via TL-07 |
| Wellhead temperature (design flow) | 85.2 °C | TL-05 |
| ESP frequency | 58.1 Hz | TL-05 |
| ESP electric power per doublet | 0.446 MW | TL-05 QΔp/0.70/(0.93·0.97) |
| ESP intake margin over 1.2 · p_b | 0.86 bar | TL-05 (SUB-PBUB basis) |
| Injector wellhead pressure | 26.0 bar(g) | TL-05 |
| Injection pump electric power per doublet | 0.339 MW | TL-05 |
| Thermal breakthrough (design flow) | 34.4 yr | TL-07, ≥ 30 yr (ENG-06) |
| Doublet separation | 1131 m | TL-08 minimum for ≤ 1 bar |
| Well spacing | 1200 m | SUB-SPACING (≥ 1.2 km) |
| Scaling screen (TL-22) | insufficient data | calcite_inhibitor, no_copper, oxygen_exclusion, titanium_plates |
| PH-02 brine cooling 0–40 K | worst 40 K | margin 0 (verifier) |
| PH-18 ESP intake ≥ 1.2 · bubble point (RC-01 item 9 setpoint) | worst 55.1 bar | margin 0.864 (verifier) |
| PH-30 degassed brine below its boiling point at the letdown pressure | worst 85.2 degC | margin 14.8 (verifier) |
| PD-04 inter-doublet interference ≤ 1 bar | worst 1 bar | margin 0 (verifier) |
| PD-05 thermal breakthrough ≥ 30 yr | worst 34.4 yr | margin 4.4 (verifier) |

## 4. Verification report

Source: `runs/final/final_llm/figures/verification.md` (DesignVerifier, TR-021).

**Verdict: ACCEPTED** (DesignVerifier) — DesignVerifier C2-opt-robust: ACCEPTED; all 46 checks pass; tightest PH-02 margin 0 K

Violations: none; not evaluated: none

| Check | Layer | Name | Status | Value | Limit | Margin | Unit |
|---|---|---|---|---|---|---|---|
| SY-01 | syntactic | GEMINI project JSON loads | pass | – | – | – |  |
| SY-02 | syntactic | schemas valid and consistent | pass | – | – | – |  |
| SY-03 | syntactic | unit suffixes present | pass | – | – | – |  |
| SE-01 | semantic | unique component names | pass | – | – | – |  |
| SE-02 | semantic | connections reference existing components and keys | pass | – | – | – |  |
| SE-03 | semantic | splitter fractions sum to 1 | pass | 1 | 1 | 0 | - |
| SE-04 | semantic | each producer paired with an injector | pass | 2 | 2 | – | count |
| PH-24 | physical | 1–3 doublets, matching the results | pass | 2 | 3 | 1 | count |
| PH-01 | physical | daily energy balance < 1 % (supplied + unmet = demand) | pass | 0 | 0.01 | 0.01 | frac |
| PH-02 | physical | brine cooling 0–40 K | pass | 40 | 40 | 0 | K |
| PH-03 | physical | 50–300 m3/h per active doublet, 0 on outage | pass | 50 | 300 | 0 | m3/h |
| PH-04 | physical | Q_inj = Q_prod | pass | 0 | 0 | 0 | m3/h |
| PH-05 | physical | heating supply 100 °C met | pass | 100 | 100 | 0 | degC |
| PH-06 | physical | cooling supply 20 °C met | pass | 20 | 20 | 0 | degC |
| PH-22 | physical | brine-side energy balance < 1 % (heat drawn = ρ·Q·c_p·ΔT; first law per user) | pass | 0 | 0.01 | 0.01 | frac |
| PH-07 | physical | plate-HEX approach ≥ 1.1 K | pass | 3 | 1.1 | 1.9 | K |
| PH-08 | physical | plate-HEX temperature within rating (gasketed 150 °C, welded 204 °C) | pass | 85.2 | – | 64.8 | degC |
| PH-09 | physical | plate-HEX pressure within rating (gasketed 20 bar, welded 20.7 bar(g)) | pass | 0 | – | 20 | bar |
| PH-10 | physical | plate-HEX Δp 1–6 psi per NTU (≈ 24 kPa/NTU) | pass | 24.13 | 41.37 | 17.24 | kPa |
| PH-11 | physical | plate-HEX NTU per pass 0.6–4.0 | pass | 3.022 | 4 | 0.9783 | - |
| PH-12 | physical | plate-HEX unit ≤ 1360 m3/h and ≤ 1208 m2 | pass | 0.6514 | 1 | 0.3486 | frac |
| PH-23 | physical | plate-HEX NTU consistent with its terminal temperatures (F_c 0.85–1) | pass | 9.065 | – | 0 | - |
| PH-13 | physical | heat-pump COP ≤ Carnot (condensing/evaporating temperatures) | pass | 3.328 | – | 2.219 | - |
| PH-14 | physical | heat-pump lift and COP within datasheet (or RC-04 defaults) | pass | 1 | 1 | 0 | frac |
| PH-15 | physical | absorption COP and capacity within GM-04 bounds | not_applicable | – | – | – |  |
| PH-16 | physical | absorption pumping ≤ 50 % of electric-chiller power | not_applicable | – | – | – |  |
| PH-17 | physical | pump shaft power = QΔp/0.7 | pass | 0 | 0.01 | 0.01 | frac |
| PH-18 | physical | ESP intake ≥ 1.2 · bubble point (RC-01 item 9 setpoint) | pass | 55.11 | – | 0.864 | bar |
| PH-19 | physical | NPSH_A ≥ NPSH_R + 15 m | pass | 545.4 | 15 | 530.4 | m |
| PH-20 | physical | no negative flows or powers | pass | 0 | 0 | 0 | MW |
| PH-21 | physical | simulation constraint margins ≥ 0 | pass | 0 | 0 | 0 | suffix |
| PH-25 | physical | TL-05 design point feasible | pass | 0 | 0 | 0 | infeasible points |
| PH-26 | physical | TL-05 ESP flow solve error ≤ 1 % | pass | 0 | 0.01 | 0.01 | frac |
| PH-27 | physical | ESP intake ≥ 1.2 · p_b at the design point (TL-05) | pass | 0.864 | 0 | 0.864 | bar |
| PH-28 | physical | cooling and heat-pump models within validity (RC-04…RC-07) | pass | 0 | 0 | 0 | invalid model points |
| PH-29 | physical | daily result flags (DayResult.flags) | pass | 0 | 0 | 0 | failing day flags |
| PH-30 | physical | degassed brine below its boiling point at the letdown pressure | pass | 85.2 | 100 | 14.8 | degC |
| PD-01 | performance_design | unmet demand = 0 | pass | 0 | 0 | 0 | MWh |
| PD-02 | performance_design | planned outages do not overlap | pass | 1 | 1 | 0 | doublets |
| PD-03 | performance_design | well spacing ≥ 1.2 km | pass | 1200 | 1200 | 0 | m |
| PD-04 | performance_design | inter-doublet interference ≤ 1 bar | pass | 1 | 1 | 0 | bar |
| PD-05 | performance_design | thermal breakthrough ≥ 30 yr | pass | 34.4 | 30 | 4.405 | yr |
| PD-11 | performance_design | TL-08 interference within limit at the design separation | pass | 1 | 1 | 0 | flag |
| PD-12 | performance_design | TL-07 thermal breakthrough after the analysis horizon | pass | 1 | 1 | 0 | flag |
| PD-06 | performance_design | fast vs full simulation agree (≤ 5 % daily, per source) | pass | 0.00298 | 0.05 | 0.04702 | frac |
| PD-07 | performance_design | scaling screen passed or mitigated | pass | 0 | 0 | 0 | count |
| PD-08 | performance_design | materials compatible (no Cu in brine; Ti plates for saline brine) | pass | 0 | 0 | 0 | count |
| PD-09 | performance_design | sanity envelope: production temperature ≤ reservoir temperature | pass | 85.2 | 87 | 1.804 | degC |
| PD-10 | performance_design | pressurised brine loop ≥ CO₂-release setpoint (RC-01 item 9) | not_applicable | – | – | – |  |
| EX-01 | exergy | exergy utilisation reported | info | – | – | – | frac |

## 5. Uncertainty and sensitivity

Source: `runs/final/final_llm/report_draft.md` (section *Uncertainty*; TR-017, TR-018, TR-019).

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

## 6. Model gaps and limitations (TL-17)

Source: `runs/final/final_llm/report_draft.md` (section *Model gaps and limitations*).

- Missing component types: none
- LIM-SUB-01 ESP has no flow affinity: ESP curve power is approximate at part load.
- LIM-SUB-02 Constant brine PVT: Injection pressure and pump power underestimated at the injector.
- LIM-SUB-03 Steady wellbore heat loss: Low-flow wellhead temperature is conservative (heat underestimated).
- LIM-SUB-04 ESP motor heating not modelled: Wellhead temperature slightly underestimated.
- LIM-SUB-05 No fracture-pressure limit: A high-rate injection point could exceed a fracture limit unnoticed.
- LIM-SUB-06 No flashing check above 100 °C: Deep-concept surface pressures may be optimistic.
- LIM-SUB-07 ESP far from BEP at minimum flow: Minimum-flow efficiency and pump life uncertain.
- LIM-SUB-08 Standalone esp type is water-only: A project using 'esp' directly underestimates brine pump Δp and power.
- LIM-PLT-01 No storage component in the plant generator: C6 benefit rests on dispatch bookkeeping (STO-REC-HT), not a simulated storage loop.
- LIM-PLT-02 Heat-pump fouling not modelled: COP slightly optimistic over the plant life.
- LIM-PLT-03 HEX imprecise for balanced flows: Balanced-flow HEX duty has a small error.
- LIM-PLT-04 HEX temperature difference uses abs(): A mis-wired or off-design HEX can report positive duty.
- LIM-PLT-05 No absorption isolation loop: Drive temperature optimistic by one HEX approach.
- LIM-GEM-01 GEMINI Compressor omits k and drive losses: Unused by the plant; would underestimate power if used.
- LIM-TOOL-01 Private well_flow helpers used as API: Renaming them breaks TL-05/TL-06.

## 7. Decision Records DR-01…DR-11

Source: `runs/final/final_llm/decision_records/` (index and one file per record).

# Decision Records

| DR | Title | Choice | Source | Stub evidence |
|---|---|---|---|---|
| [DR-01](../../runs/final/final_llm/decision_records/DR-01.md) | Number of doublets | 2-doublets | llm | no |
| [DR-02](../../runs/final/final_llm/decision_records/DR-02.md) | Well depth | depth-2500m | llm | no |
| [DR-03](../../runs/final/final_llm/decision_records/DR-03.md) | Heat top-up technology | hthp-series | llm | no |
| [DR-04](../../runs/final/final_llm/decision_records/DR-04.md) | Cooling technology mix | free-cooling-electric | llm | no |
| [DR-05](../../runs/final/final_llm/decision_records/DR-05.md) | Storage yes/no | no-storage | llm | no |
| [DR-06](../../runs/final/final_llm/decision_records/DR-06.md) | Maintenance windows | optimiser-lowest-load | llm | no |
| [DR-07](../../runs/final/final_llm/decision_records/DR-07.md) | Gas handling (degas + CHP vs pressurised reinjection) | degas-use | llm | no |
| [DR-08](../../runs/final/final_llm/decision_records/DR-08.md) | Return temperature | dh-return-55 | llm | no |
| [DR-09](../../runs/final/final_llm/decision_records/DR-09.md) | Materials | titanium-plates-no-cu | llm | no |
| [DR-10](../../runs/final/final_llm/decision_records/DR-10.md) | Final selection | C2-opt-robust | policy_fallback | no |
| [DR-11](../../runs/final/final_llm/decision_records/DR-11.md) | Backup capacity (robustness) | p90-robust-backup | llm | no |

# DR-01 Number of doublets

- Run: `final_llm` · phase P5 · role system_architect
- Decision source: **llm**
- Question: How many doublets should the plant have?
- **Choice: 2-doublets** — 2 doublet(s)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: lowest lcoh_per_mwh among guard-passed options in economics based on TR-013.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| 2-doublets | 2 doublet(s) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| 3-doublets | 3 doublet(s) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- 3-doublets: Higher lcoh_per_mwh compared to the selected option according to TR-013.

# DR-02 Well depth

- Run: `final_llm` · phase P5 · role subsurface
- Decision source: **llm**
- Question: At which reservoir depth should the wells be drilled?
- **Choice: depth-2500m** — Reservoir at 2500 m TVD
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: The choice is based on the lowest lcoh_per_mwh among guard-passed options in economics, as identified in the criteria. depth-2500m is the best option because it contains the top-ranked candidates in the economics evaluation. depth-3200m was rejected because it is ineligible. depth-1500m was rejected as it did not contain the top-ranked candidates.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| depth-2500m | Reservoir at 2500 m TVD | yes | yes | TR-009, TR-010, TR-012, TR-013, config:design_basis.yaml#DB-01 |
| depth-3200m | Reservoir at 3200 m TVD | no | no | TR-009, TR-010, TR-012, TR-013 |
| depth-1500m | Reservoir at 1500 m TVD | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- depth-3200m: Not chosen.
- depth-1500m: Not chosen.

# DR-03 Heat top-up technology

- Run: `final_llm` · phase P5 · role system_architect
- Decision source: **llm**
- Question: Which technology lifts heat to the 100 degC supply?
- **Choice: hthp-series** — Plate HEX + HTHP in series + CHP + peak boiler (C2/C3)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Selected because it achieved the lowest lcoh_per_mwh among guard-passed options in economics as evidenced by TR-013. gas-boiler-chp-topup was rejected for higher lcoh_per_mwh. deeper-direct-hex was rejected due to eligibility status. shallow-large-hp was rejected for higher lcoh_per_mwh.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| gas-boiler-chp-topup | Plate HEX + gas boiler/CHP top-up (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| hthp-series | Plate HEX + HTHP in series + CHP + peak boiler (C2/C3) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| deeper-direct-hex | Deeper wells, direct plate HEX + small peak boiler (C4) | no | no | TR-009, TR-010, TR-012, TR-013 |
| shallow-large-hp | Shallow wells + large heat pumps (C5) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- gas-boiler-chp-topup: Not chosen.
- deeper-direct-hex: Not chosen.
- shallow-large-hp: Not chosen.

# DR-04 Cooling technology mix

- Run: `final_llm` · phase P5 · role system_architect
- Decision source: **llm**
- Question: Which cooling technology mix supplies 20 degC?
- **Choice: free-cooling-electric** — Free cooling + electric chillers (C2, C5)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Selected based on the ranking-lowest-lcoh_per_mwh among guard-passed options in economics. C1-3d was rejected for higher cost. C3-2d was rejected because C4-2d was excluded by the design guard.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| electric-chillers | Electric chillers (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-electric | Free cooling + electric chillers (C2, C5) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-absorption | Free cooling + absorption + electric peak (C3, C4) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- electric-chillers: Not chosen.
- free-cooling-absorption: Not chosen.

# DR-05 Storage yes/no

- Run: `final_llm` · phase P5 · role system_architect
- Decision source: **llm**
- Question: Should the plant include thermal storage?
- **Choice: no-storage** — No seasonal storage
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: lowest lcoh_per_mwh among guard-passed options in economics. Option seasonal-ht-ates was rejected because it had a higher lcoh_per_mwh than the best ranked options in the no-storage group according to TR-013.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| no-storage | No seasonal storage | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| seasonal-ht-ates | Seasonal HT-ATES storage (C6) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- seasonal-ht-ates: Not chosen.

# DR-06 Maintenance windows

- Run: `final_llm` · phase P6 · role system_architect
- Decision source: **llm**
- Question: How are doublet maintenance windows scheduled?
- **Choice: optimiser-lowest-load** — Outages placed by the optimiser in the lowest-load windows, never overlapping (DB-30)
- Decisive criterion: only option meeting DB-30: non-overlapping outages in the lowest-load windows (key `DB-30` from assumptions register; evidence TR-014, config:assumptions.yaml#DB-30)
- Why: lowest lcoh_per_mwh among guard-passed options in economics; option common-summer-block violates DB-30 as it causes overlapping outages.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| optimiser-lowest-load | Outages placed by the optimiser in the lowest-load windows, never overlapping (DB-30) | yes | yes | TR-014, config:assumptions.yaml#DB-30 |
| common-summer-block | One common summer block for all doublets | no | no | TR-014, config:assumptions.yaml#DB-30 |

Rejected options:
- common-summer-block: violates DB-30 requirement for non-overlapping outages

# DR-07 Gas handling (degas + CHP vs pressurised reinjection)

- Run: `final_llm` · phase P5 · role subsurface
- Decision source: **llm**
- Question: How is the associated gas handled?
- **Choice: degas-use** — Gas handling: degas_use
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Selected based on lowest lcoh_per_mwh among guard-passed options in economics as per TR-013. Option pressurised-reinject was rejected because it did not achieve the lowest lcoh_per_mwh.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| degas-use | Gas handling: degas_use | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| pressurised-reinject | Gas handling: pressurised_reinject | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- pressurised-reinject: Not chosen.

# DR-08 Return temperature

- Run: `final_llm` · phase P5 · role system_architect
- Decision source: **llm**
- Question: Which district-heating return temperature is designed for?
- **Choice: dh-return-55** — District-heating return 55 degC
- Decisive criterion: register default kept for the district-heating return temperature (DB-31) (key `DB-31` from assumptions register; evidence TR-012, config:assumptions.yaml#DB-31)
- Why: The selected option matches the register default for the district-heating return temperature (DB-31) as specified in the assumptions register.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| dh-return-55 | District-heating return 55 degC | yes | yes | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-45 | District-heating return 45 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-65 | District-heating return 65 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-75 | District-heating return 75 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |

Rejected options:
- dh-return-45: Does not match the register default for the district-heating return temperature (DB-31).
- dh-return-65: Does not match the register default for the district-heating return temperature (DB-31).
- dh-return-75: Does not match the register default for the district-heating return temperature (DB-31).

# DR-09 Materials

- Run: `final_llm` · phase P2 · role subsurface
- Decision source: **llm**
- Question: Which materials are used in the brine loop?
- **Choice: titanium-plates-no-cu** — Titanium HEX plates, no copper alloys in contact with brine
- Decisive criterion: materials check of spec §10.1 for saline brine (brine) (key `brine` from load_design_basis; evidence TR-001)
- Why: The selection is based on the brine materials check among guard-passed options in economics. Option titanium-plates-no-cu satisfies the brine requirement in spec §10.1 as shown in TR-001. Option stainless-plates was rejected because it fails the materials check for saline brine in spec §10.1.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| titanium-plates-no-cu | Titanium HEX plates, no copper alloys in contact with brine | yes | yes | TR-001, spec:§10.1 |
| stainless-plates | Stainless-steel HEX plates | no | no | TR-001, spec:§10.1 |

Rejected options:
- stainless-plates: Not chosen.

# DR-10 Final selection

- Run: `final_llm` · phase P9 · role lead_engineer
- Decision source: **policy_fallback**
- Question: Which design is recommended?
- **Choice: C2-opt-robust** — C2-opt-robust (C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP))
- Decisive criterion: selected design whose latest verifier verdict is not REJECTED (key `verdict` from verify_design; evidence TR-021)
- Why: Decisive criterion: selected design whose latest verifier verdict is not REJECTED; it selects C2-opt-robust (TR-021).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| C2-opt-robust | C2-opt-robust (C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP)) | yes | yes | TR-019, TR-020, TR-021 |
| C2-opt | C2-opt (TL-13 optimum of C2 (sized from C2-2d)) | no | no | TR-014, TR-015, TR-016, TR-017, TR-019 |
| C6-opt | C6-opt (TL-13 optimum of C6 (sized from C6-2d)) | no | no | TR-014 |
| C1-opt | C1-opt (TL-13 optimum of C1 (sized from C1-3d)) | no | no | TR-014 |

Rejected options:
- C2-opt: Not eligible: not the verified design (only the verified design can be selected)
- C6-opt: Not eligible: not the verified design (only the verified design can be selected)
- C1-opt: Not eligible: not the verified design (only the verified design can be selected)

# DR-11 Backup capacity (robustness)

- Run: `final_llm` · phase P8 · role economics
- Decision source: **llm**
- Question: Which peak-backup capacity is installed: the base-year nameplates or the TL-15 robust nameplates?
- **Choice: p90-robust-backup** — Install the TL-15 P90 robust backups (electric_chiller_mw 8.20, peak_boiler_mw 10.90 MW) as C2-opt-robust
- Decisive criterion: register rule OPT-ROBUST-BACKUP: install robust_backup_mw when it raises probability_demand_met (key `OPT-ROBUST-BACKUP` from assumptions register; evidence TR-017, TR-019, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC)
- Why: The selection follows the OPT-ROBUST-BACKUP criterion, which requires installing robust_backup_mw to improve probability_demand_met. Evidence TR-019 confirms that this option increases the probability_demand_met compared to the base-year nameplates shown in TR-017.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| installed-backup | Keep the base-year backup nameplates of C2-opt (electric_chiller_mw 8.20, peak_boiler_mw 5.00 MW) | yes | no | TR-019, TR-017, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC |
| p90-robust-backup | Install the TL-15 P90 robust backups (electric_chiller_mw 8.20, peak_boiler_mw 10.90 MW) as C2-opt-robust | yes | yes | TR-019, TR-017, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC |

Rejected options:
- installed-backup: This option does not meet the OPT-ROBUST-BACKUP criterion as it maintains a lower probability_demand_met described in TR-017.

## 8. LLM calls and tokens of the final run

Source: `runs/final/final_llm/token_summary.json` (every call: `runs/final/final_llm/llm_calls.jsonl`).

**Totals**

| Group | calls | requests | retries | fallbacks | input_tokens | output_tokens | total_tokens | latency_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| total | 34 | 44 | 10 | 2 | 93,699 | 5,602 | 99,301 | 299.3 |

**By phase**

| Group | calls | requests | retries | fallbacks | input_tokens | output_tokens | total_tokens | latency_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0 | 1 | 1 | 0 | 0 | 1,096 | 59 | 1,155 | 8.8 |
| P1 | 3 | 5 | 2 | 0 | 10,071 | 1,085 | 11,156 | 32.3 |
| P2 | 3 | 3 | 0 | 0 | 7,006 | 540 | 7,546 | 28.7 |
| P3 | 3 | 4 | 1 | 0 | 8,973 | 401 | 9,374 | 23.1 |
| P4 | 2 | 2 | 0 | 0 | 4,140 | 155 | 4,295 | 13.1 |
| P5 | 8 | 8 | 0 | 0 | 18,291 | 976 | 19,267 | 69.5 |
| P6 | 3 | 3 | 0 | 0 | 5,536 | 267 | 5,803 | 18.7 |
| P7 | 3 | 5 | 2 | 1 | 9,669 | 500 | 10,169 | 21.8 |
| P8 | 3 | 4 | 1 | 0 | 8,844 | 500 | 9,344 | 26.7 |
| P9 | 3 | 7 | 4 | 1 | 16,747 | 943 | 17,690 | 42.7 |
| P10 | 2 | 2 | 0 | 0 | 3,326 | 176 | 3,502 | 13.8 |

**By agent**

| Group | calls | requests | retries | fallbacks | input_tokens | output_tokens | total_tokens | latency_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lead_engineer | 5 | 9 | 4 | 2 | 17,815 | 968 | 18,783 | 46.7 |
| requirements | 3 | 5 | 2 | 0 | 10,071 | 1,085 | 11,156 | 32.3 |
| subsurface | 5 | 5 | 0 | 0 | 11,738 | 766 | 12,504 | 46.2 |
| system_architect | 12 | 13 | 1 | 0 | 28,068 | 1,418 | 29,486 | 93.8 |
| model_builder | 2 | 2 | 0 | 0 | 4,140 | 155 | 4,295 | 13.1 |
| economics | 3 | 4 | 1 | 0 | 8,844 | 500 | 9,344 | 26.7 |
| reviewer | 2 | 4 | 2 | 0 | 9,697 | 534 | 10,231 | 26.6 |
| reporter | 2 | 2 | 0 | 0 | 3,326 | 176 | 3,502 | 13.8 |

**By decision source**

| Group | calls | requests | retries | fallbacks | input_tokens | output_tokens | total_tokens | latency_s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llm | 32 | 38 | 6 | 0 | 80,281 | 4,846 | 85,127 | 268.6 |
| policy_fallback | 2 | 6 | 4 | 2 | 13,418 | 756 | 14,174 | 30.7 |

## 9. S10 — naive vs optimised context (token saving)

Source: `runs/final/eval/gemma4_12b-it-q4_K_M/S10-token_comparison.json` (runs `S10-naive/`, `S10-optimised/`).

| Agent | naive_tokens | optimised_tokens | saved_tokens | saved_frac | naive_calls | optimised_calls |
| --- | --- | --- | --- | --- | --- | --- |
| total | 173,909 | 108,937 | 64,972 | 0.3736 | 34 | 33 |
| economics | 19,177 | 6,460 | 12,717 | 0.6631 | 3 | 3 |
| lead_engineer | 10,107 | 12,333 | -2,226 | -0.2202 | 3 | 4 |
| model_builder | 27,537 | 2,352 | 25,185 | 0.9146 | 4 | 1 |
| reporter | 16,617 | 1,889 | 14,728 | 0.8863 | 2 | 1 |
| requirements | 9,057 | 7,895 | 1,162 | 0.1283 | 3 | 3 |
| reviewer | 22,961 | 10,160 | 12,801 | 0.5575 | 3 | 2 |
| subsurface | 17,966 | 15,273 | 2,693 | 0.1499 | 6 | 6 |
| system_architect | 50,487 | 52,575 | -2,088 | -0.0414 | 10 | 13 |

## 10. S11 — model comparison (same task, one S11 run per model)

Sources: `runs/final/eval/gemma4_12b-it-q4_K_M/eval_results.json`, `runs/final/eval/gemma4_26b-a4b/eval_results.json`, `runs/final/eval/qwen3.5_9b-q4_K_M/eval_results.json`, `runs/final/eval/qwen3.6_27b/eval_results.json`, `runs/final/eval/qwen3.6_35b-a3b/eval_results.json`.

| Model | Outcome | Status | Phase | Verdict | LLM calls | Valid | Validity | Retries | Fallbacks | Tokens | Runtime s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemma4:12b-it-q4_K_M | pending | completed | P10 | ACCEPTED | 33 | 32 | 97% | 7 | 1 | 88,524 | 372.6 |
| gemma4:26b-a4b | fail | escalated | P5 | - | 27 | 19 | 70% | 14 | 7 | 101,871 | 175.7 |
| qwen3.5:9b-q4_K_M | fail | escalated | P5 | - | 22 | 15 | 68% | 17 | 7 | 83,724 | 203.2 |
| qwen3.6:27b | pending | completed | P10 | ACCEPTED | 38 | 31 | 82% | 17 | 7 | 126,855 | 672.5 |
| qwen3.6:35b-a3b | fail | escalated | P6 | - | 29 | 28 | 97% | 7 | 1 | 79,061 | 167.3 |

`runs/final/eval/model_comparison.json` (generated 2026-11-04T07:59:01.288960+00:00) sums every scenario in each eval folder; for the chosen model that is S1–S11, so its like-for-like S11 line is the one above, taken from its `eval_results.json`.

## 11. S1–S11 evaluation of the chosen model

Source: `runs/final/eval/gemma4_12b-it-q4_K_M/eval_results.json` (`EVAL_REPORT.md` alongside).

| Scenario | Expected | Outcome | Status | Phase | Verdict | Tool calls (stub) | LLM calls | Validity | Fallbacks | Tokens | Runtime s | Pending / failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | Complete design, ACCEPTED | pass | completed | P10 | ACCEPTED | 28 (0) | 33 | 94% | 2 | 93,795 | 387.6 | – |
| S2 | Lists missing inputs, asks, does not invent | pass | paused | P1 | - | 0 (0) | 1 | 100% | 0 | 1,121 | 4.6 | – |
| S3 | Polite refusal/redirect | pass | refused | P0 | - | 0 (0) | 1 | 100% | 0 | 1,128 | 2.7 | – |
| S4 | Flags DB-10/11 violation, proposes compliant option | pass | completed | P10 | ACCEPTED | 29 (0) | 36 | 97% | 1 | 106,067 | 434.4 | – |
| S5 | Notes conflict with DB-16, asks to confirm change of basis | pass | completed | P10 | ACCEPTED | 30 (0) | 33 | 91% | 3 | 104,179 | 419.1 | – |
| S6 | Asks or records DB-31 assumption + sensitivity | pass | completed | P10 | ACCEPTED | 25 (0) | 33 | 97% | 1 | 95,618 | 380.6 | – |
| S7 | Gap detected; Model Builder writes model + passes reference-example test | pending | completed | P10 | ACCEPTED | 27 (0) | 36 | 86% | 5 | 120,985 | 448.0 | the Model Builder writes the model from its reference card and it passes the reference-example test |
| S8 | Re-sizes and explains | pass | completed | P10 | ACCEPTED | 24 (0) | 33 | 97% | 1 | 98,497 | 379.3 | – |
| S9 | Detects inconsistency | pass | completed | P10 | ACCEPTED | 23 (0) | 32 | 94% | 2 | 96,790 | 364.0 | – |
| S10 | Token comparison chart | pending | completed | P10 | ACCEPTED | 56 (0) | 67 | 94% | 4 | 282,846 | 973.7 | token comparison chart (figure) |
| S11 | Tool-call validity, retries, fallbacks, tokens, quality | pending | completed | P10 | ACCEPTED | 25 (0) | 33 | 97% | 1 | 88,524 | 372.6 | the 16 GB-class and 36 GB-class models are compared |

## 12. S7 — model gap closed by the Model Builder (local run after #23)

Source: `docs/results/s7_demo/s7_report.json` (evidence folder `docs/results/s7_demo/`).

| Field | Value |
| --- | --- |
| Removed registry type | `free_cooling` |
| Gap detected / closed | True / True |
| Registered class | `FreeCoolingRc07` (card RC-07) |
| Status | registered |
| Types still missing | none |
| Draft 1 | llm, 1907 in / 3068 out tokens, 69.6 s; pytest 2 tests, 1 failure(s), passed = False |
| Draft 2 | llm, 2520 in / 3086 out tokens, 68.8 s; pytest 2 tests, 0 failure(s), passed = True |

## 13. Figures

Copies of `runs/final/final_llm/figures/*.png` in `docs/results/final_llm/` (TR-022 `plot_figures`).

| Figure | Caption |
| --- | --- |
| `docs/results/final_llm/fig_candidates.png` | Levelised cost (LCOH + LCOC stacked, 30 yr, TL-14) and CO₂ emissions (ECO-05) of every candidate: C0–C8 screening points, the TL-13 optima and the selected C2-opt-robust. |
| `docs/results/final_llm/fig_daily_heat.png` | Daily heat supply by source (geothermal plate HEX, HTHP, CHP on associated gas, peak boiler on grid gas) against the heating demand, C2-opt-robust, 2027. |
| `docs/results/final_llm/fig_daily_cooling.png` | Daily cooling supply by source (free cooling, electric chiller) against the cooling demand, C2-opt-robust, 2027. |
| `docs/results/final_llm/fig_load_duration.png` | Heating and cooling load-duration curves (days sorted by demand) with the supply stack of the selected design. |
| `docs/results/final_llm/fig_energy_balance.png` | Annual energy balance: heat delivered by source, cooling delivered by source, and the inputs (brine heat drawn, gas fuel, electricity consumed), GWh/yr. |
| `docs/results/final_llm/fig_uncertainty.png` | TL-15 Monte Carlo (200 samples, C2-opt): cumulative distributions of LCOH and LCOC with the P10–P90 band, P50 and the base value. |
| `docs/results/final_llm/fig_tornado.png` | TL-15 one-at-a-time sensitivity: swing of the objective (LCOH + LCOC + unmet penalty) per input at its low and high value. |
| `docs/results/final_llm/fig_tokens.png` | LLM tokens (input + output) per phase, the run's own TL-21 chart (redrawn from token_summary.json after submission, scripts/regenerate_fig_tokens.py; the run had drawn the placeholder because the P10 plot preceded the summary). |
| `docs/results/final_llm/fig_tokens_by_phase.png` | LLM tokens of the final run per phase (input and output, stacked) and per agent, drawn by this script from token_summary.json. |

## 14. Assumptions and answers recorded in the run

Source: `runs/final/final_llm/run_summary.json` → `assumptions_and_answers`.

- DB-30 = {"interpretation":"A","doublet_available_days_per_yr":330,"planned_outage_days_per_doublet":35,"outages_may_overlap":false,"outage_schedule":"chosen by the optimiser","demand_met_days_per_yr":365} (assumed: register default)
- DB-31 = 55.0 degC (assumed: register default)
- DB-32 = 26.0 degC (assumed: register default)
- Human approval: approved (user)
