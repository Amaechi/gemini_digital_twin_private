# DR-01 Number of doublets

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: How many doublets should the plant have?
- **Choice: 2-doublets** — 2 doublet(s)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects 2-doublets (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| 2-doublets | 2 doublet(s) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| 3-doublets | 3 doublet(s) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- 3-doublets: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-02 Well depth

- Run: `final_policy` · phase P5 · role subsurface
- Decision source: **policy**
- Question: At which reservoir depth should the wells be drilled?
- **Choice: depth-2500m** — Reservoir at 2500 m TVD
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects depth-2500m (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| depth-2500m | Reservoir at 2500 m TVD | yes | yes | TR-009, TR-010, TR-012, TR-013, config:design_basis.yaml#DB-01 |
| depth-3200m | Reservoir at 3200 m TVD | no | no | TR-009, TR-010, TR-012, TR-013 |
| depth-1500m | Reservoir at 1500 m TVD | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- depth-3200m: Not eligible: candidates: none; excluded by DesignGuard/verifier: C4-2d
- depth-1500m: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-03 Heat top-up technology

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which technology lifts heat to the 100 degC supply?
- **Choice: hthp-series** — Plate HEX + HTHP in series + CHP + peak boiler (C2/C3)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects hthp-series (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| gas-boiler-chp-topup | Plate HEX + gas boiler/CHP top-up (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| hthp-series | Plate HEX + HTHP in series + CHP + peak boiler (C2/C3) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| deeper-direct-hex | Deeper wells, direct plate HEX + small peak boiler (C4) | no | no | TR-009, TR-010, TR-012, TR-013 |
| shallow-large-hp | Shallow wells + large heat pumps (C5) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- gas-boiler-chp-topup: Not a best option under ranking-lowest-lcoh_per_mwh.
- deeper-direct-hex: Not eligible: candidates: none; excluded by DesignGuard/verifier: C4-2d
- shallow-large-hp: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-04 Cooling technology mix

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which cooling technology mix supplies 20 degC?
- **Choice: free-cooling-electric** — Free cooling + electric chillers (C2, C5)
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects free-cooling-electric (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| electric-chillers | Electric chillers (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-electric | Free cooling + electric chillers (C2, C5) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-absorption | Free cooling + absorption + electric peak (C3, C4) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- electric-chillers: Not a best option under ranking-lowest-lcoh_per_mwh.
- free-cooling-absorption: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-05 Storage yes/no

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Should the plant include thermal storage?
- **Choice: no-storage** — No seasonal storage
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects no-storage (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| no-storage | No seasonal storage | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| seasonal-ht-ates | Seasonal HT-ATES storage (C6) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- seasonal-ht-ates: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-06 Maintenance windows

- Run: `final_policy` · phase P6 · role system_architect
- Decision source: **policy**
- Question: How are doublet maintenance windows scheduled?
- **Choice: optimiser-lowest-load** — Outages placed by the optimiser in the lowest-load windows, never overlapping (DB-30)
- Decisive criterion: only option meeting DB-30: non-overlapping outages in the lowest-load windows (key `DB-30` from assumptions register; evidence TR-014, config:assumptions.yaml#DB-30)
- Why: Decisive criterion: only option meeting DB-30: non-overlapping outages in the lowest-load windows; it selects optimiser-lowest-load (TR-014, config:assumptions.yaml#DB-30).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| optimiser-lowest-load | Outages placed by the optimiser in the lowest-load windows, never overlapping (DB-30) | yes | yes | TR-014, config:assumptions.yaml#DB-30 |
| common-summer-block | One common summer block for all doublets | no | no | TR-014, config:assumptions.yaml#DB-30 |

Rejected options:
- common-summer-block: Not eligible: outages would overlap; DB-30 requires non-overlapping outages


# DR-07 Gas handling (degas + CHP vs pressurised reinjection)

- Run: `final_policy` · phase P5 · role subsurface
- Decision source: **policy**
- Question: How is the associated gas handled?
- **Choice: degas-use** — Gas handling: degas_use
- Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics (key `lcoh_per_mwh` from economics; evidence TR-012, TR-013)
- Why: Decisive criterion: lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without unmet demand in economics; it selects degas-use (TR-012, TR-013).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| degas-use | Gas handling: degas_use | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| pressurised-reinject | Gas handling: pressurised_reinject | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- pressurised-reinject: Not a best option under ranking-lowest-lcoh_per_mwh.


# DR-08 Return temperature

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which district-heating return temperature is designed for?
- **Choice: dh-return-55** — District-heating return 55 degC
- Decisive criterion: register default kept for the district-heating return temperature (DB-31) (key `DB-31` from assumptions register; evidence TR-012, config:assumptions.yaml#DB-31)
- Why: Decisive criterion: register default kept for the district-heating return temperature (DB-31); it selects dh-return-55 (TR-012, config:assumptions.yaml#DB-31).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| dh-return-55 | District-heating return 55 degC | yes | yes | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-45 | District-heating return 45 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-65 | District-heating return 65 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-75 | District-heating return 75 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |

Rejected options:
- dh-return-45: Not a best option under db31-answer.
- dh-return-65: Not a best option under db31-answer.
- dh-return-75: Not a best option under db31-answer.


# DR-09 Materials

- Run: `final_policy` · phase P2 · role subsurface
- Decision source: **policy**
- Question: Which materials are used in the brine loop?
- **Choice: titanium-plates-no-cu** — Titanium HEX plates, no copper alloys in contact with brine
- Decisive criterion: materials check of spec §10.1 for saline brine (brine) (key `brine` from load_design_basis; evidence TR-001)
- Why: Decisive criterion: materials check of spec §10.1 for saline brine (brine); it selects titanium-plates-no-cu (TR-001).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| titanium-plates-no-cu | Titanium HEX plates, no copper alloys in contact with brine | yes | yes | TR-001, spec:§10.1 |
| stainless-plates | Stainless-steel HEX plates | no | no | TR-001, spec:§10.1 |

Rejected options:
- stainless-plates: Not eligible: fails the §10.1 materials check for saline brine


# DR-10 Final selection

- Run: `final_policy` · phase P9 · role lead_engineer
- Decision source: **policy**
- Question: Which design is recommended?
- **Choice: C2-opt-robust** — C2-opt-robust (C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP))
- Decisive criterion: selected design whose latest verifier verdict is not REJECTED (key `verdict` from verify_design; evidence TR-021)
- Why: Decisive criterion: selected design whose latest verifier verdict is not REJECTED; it selects C2-opt-robust (TR-021).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| C2-opt-robust | C2-opt-robust (C2-opt with the TL-15 P90 robust backups (OPT-ROBUST-BACKUP)) | yes | yes | TR-019, TR-020, TR-021 |
| C2-opt | C2-opt (TL-13 optimum of C2 (sized from C2-2d)) | no | no | TR-014, TR-015, TR-016, TR-017, TR-018, TR-019 |
| C6-opt | C6-opt (TL-13 optimum of C6 (sized from C6-2d)) | no | no | TR-014 |
| C1-opt | C1-opt (TL-13 optimum of C1 (sized from C1-3d)) | no | no | TR-014 |

Rejected options:
- C2-opt: Not eligible: not the verified design (only the verified design can be selected)
- C6-opt: Not eligible: not the verified design (only the verified design can be selected)
- C1-opt: Not eligible: not the verified design (only the verified design can be selected)


# DR-11 Backup capacity (robustness)

- Run: `final_policy` · phase P8 · role economics
- Decision source: **policy**
- Question: Which peak-backup capacity is installed: the base-year nameplates or the TL-15 robust nameplates?
- **Choice: p90-robust-backup** — Install the TL-15 P90 robust backups (electric_chiller_mw 8.20, peak_boiler_mw 10.90 MW) as C2-opt-robust
- Decisive criterion: register rule OPT-ROBUST-BACKUP: install robust_backup_mw when it raises probability_demand_met (key `OPT-ROBUST-BACKUP` from assumptions register; evidence TR-017, TR-019, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC)
- Why: Decisive criterion: register rule OPT-ROBUST-BACKUP: install robust_backup_mw when it raises probability_demand_met; it selects p90-robust-backup (TR-017, TR-019, config:assumptions.yaml#OPT-ROBUST-BACKUP).

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| installed-backup | Keep the base-year backup nameplates of C2-opt (electric_chiller_mw 8.20, peak_boiler_mw 5.00 MW) | yes | no | TR-019, TR-017, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC |
| p90-robust-backup | Install the TL-15 P90 robust backups (electric_chiller_mw 8.20, peak_boiler_mw 10.90 MW) as C2-opt-robust | yes | yes | TR-019, TR-017, config:assumptions.yaml#OPT-ROBUST-BACKUP, config:assumptions.yaml#UQ-MC |

Rejected options:
- installed-backup: Not a best option under opt-robust-backup.
