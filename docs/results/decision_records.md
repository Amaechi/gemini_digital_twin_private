# DR-01 Number of doublets

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: How many doublets should the plant have?
- **Choice: 2-doublets** — 2 doublet(s)
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| 2-doublets | 2 doublet(s) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| 3-doublets | 3 doublet(s) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- 3-doublets: Ranked below the recommended option.


# DR-02 Well depth

- Run: `final_policy` · phase P5 · role subsurface
- Decision source: **policy**
- Question: At which reservoir depth should the wells be drilled?
- **Choice: depth-2500m** — Reservoir at 2500 m TVD
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| depth-2500m | Reservoir at 2500 m TVD | yes | yes | TR-009, TR-010, TR-012, TR-013, config:design_basis.yaml#DB-01 |
| depth-3200m | Reservoir at 3200 m TVD | no | no | TR-009, TR-010, TR-012, TR-013 |
| depth-1500m | Reservoir at 1500 m TVD | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- depth-3200m: Not eligible: candidates: none; excluded by DesignGuard/verifier: C4-2d
- depth-1500m: Ranked below the recommended option.


# DR-03 Heat top-up technology

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which technology lifts heat to the 100 degC supply?
- **Choice: hthp-series** — Plate HEX + HTHP in series + CHP + peak boiler (C2/C3)
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| gas-boiler-chp-topup | Plate HEX + gas boiler/CHP top-up (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| hthp-series | Plate HEX + HTHP in series + CHP + peak boiler (C2/C3) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| deeper-direct-hex | Deeper wells, direct plate HEX + small peak boiler (C4) | no | no | TR-009, TR-010, TR-012, TR-013 |
| shallow-large-hp | Shallow wells + large heat pumps (C5) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- gas-boiler-chp-topup: Ranked below the recommended option.
- deeper-direct-hex: Not eligible: candidates: none; excluded by DesignGuard/verifier: C4-2d
- shallow-large-hp: Ranked below the recommended option.


# DR-04 Cooling technology mix

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which cooling technology mix supplies 20 degC?
- **Choice: free-cooling-electric** — Free cooling + electric chillers (C2, C5)
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| electric-chillers | Electric chillers (C1) | yes | no | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-electric | Free cooling + electric chillers (C2, C5) | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| free-cooling-absorption | Free cooling + absorption + electric peak (C3, C4) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- electric-chillers: Ranked below the recommended option.
- free-cooling-absorption: Ranked below the recommended option.


# DR-05 Storage yes/no

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Should the plant include thermal storage?
- **Choice: no-storage** — No seasonal storage
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| no-storage | No seasonal storage | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| seasonal-ht-ates | Seasonal HT-ATES storage (C6) | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- seasonal-ht-ates: Ranked below the recommended option.


# DR-06 Maintenance windows

- Run: `final_policy` · phase P6 · role system_architect
- Decision source: **policy**
- Question: How are doublet maintenance windows scheduled?
- **Choice: optimiser-lowest-load** — Outages placed by the optimiser in the lowest-load windows, never overlapping (DB-30)
- Why: Recommended by the deterministic tool ranking or the register default.

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
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| degas-use | Gas handling: degas_use | yes | yes | TR-009, TR-010, TR-012, TR-013 |
| pressurised-reinject | Gas handling: pressurised_reinject | yes | no | TR-009, TR-010, TR-012, TR-013 |

Rejected options:
- pressurised-reinject: Ranked below the recommended option.


# DR-08 Return temperature

- Run: `final_policy` · phase P5 · role system_architect
- Decision source: **policy**
- Question: Which district-heating return temperature is designed for?
- **Choice: dh-return-55** — District-heating return 55 degC
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| dh-return-55 | District-heating return 55 degC | yes | yes | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-45 | District-heating return 45 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-65 | District-heating return 65 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |
| dh-return-75 | District-heating return 75 degC | yes | no | config:assumptions.yaml#DB-31, TR-012 |

Rejected options:
- dh-return-45: Ranked below the recommended option.
- dh-return-65: Ranked below the recommended option.
- dh-return-75: Ranked below the recommended option.


# DR-09 Materials

- Run: `final_policy` · phase P2 · role subsurface
- Decision source: **policy**
- Question: Which materials are used in the brine loop?
- **Choice: titanium-plates-no-cu** — Titanium HEX plates, no copper alloys in contact with brine
- Why: Recommended by the deterministic tool ranking or the register default.

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
- **Choice: C2-opt** — C2-opt (TL-13 optimum of C2 (sized from C2-2d))
- Why: Recommended by the deterministic tool ranking or the register default.

| Option | Label | Eligible | Recommended | Evidence |
|---|---|---|---|---|
| C2-opt | C2-opt (TL-13 optimum of C2 (sized from C2-2d)) | yes | yes | TR-014, TR-015, TR-016, TR-017, TR-018, TR-019, TR-020 |
| C6-opt | C6-opt (TL-13 optimum of C6 (sized from C6-2d)) | no | no | TR-014 |
| C1-opt | C1-opt (TL-13 optimum of C1 (sized from C1-3d)) | no | no | TR-014 |

Rejected options:
- C6-opt: Not eligible: not the verified design (only the verified design can be selected)
- C1-opt: Not eligible: not the verified design (only the verified design can be selected)
