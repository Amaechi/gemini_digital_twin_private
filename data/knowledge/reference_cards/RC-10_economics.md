# RC-10 Economics

**Feeds:** TL-14 economics, ECO-01…07, OPT objective.
**Verification status:** NREL well-cost table read from Fig. 5 markers (±0.15 M$, dollar-year not stated) — chart reading, verified 31 Oct 2026; Guidebook chiller cost functions decoded `VERIFY`; others from text.

## Levelised cost
- LCOH (Gippsland Eq. 1): `LCOH = Σ_t (C_t + M_t + F_t)/(1+r)^t ÷ Σ_t H_t/(1+r)^t` — C capex, M O&M, F energy/fuel, H heat delivered.
- LCOC analogous with cooling delivered. Allocate shared costs (wells, pumps) between heat and cooling by energy share; state it.
- Fixed-charge-rate alternative (NREL desalination App. E): `LCOH = (FCR · TCC + FOC)/AEP + VOC`.
- Finance view (ESMAP Annex 3): `NPV = Σ FCF_t/(1+WACC)^t`, `WACC = i_d (1 − tax) D + R_e E`; base i_d 6 %, tax 20 %, R_e 25 %, E 30 %. ESMAP's spreadsheet (p.144) reports **WACC 11.22 %**; the simple formula above with D = 70 % gives 10.9 % (the spreadsheet's equity share varies over the construction years). Use 11.2 % as the quoted base value and say so.
- Defaults: r = 8 % (Gippsland, Guidebook); life 30 yr and a 20-yr case (Guidebook advises ≤ 20 yr); general inflation 2.5 %, energy 3–7 %.

## Capex items
| Item | Value | Source |
|---|---|---|
| Well cost vs depth (large-diameter 12¼″ vertical, open hole) — **use this table with linear interpolation** (read from NREL Fig. 5 markers, "2025 Revised Baseline", ±0.15 M$) | z (m) → M$: 1000 → 2.1; 1500 → 2.5; 2000 → 2.9; **2500 → 3.45**; 3000 → 6.0; 3500 → 6.75; 4000 → 7.5; 4500 → 8.3; 5000 → 13.6. Casing-program **steps** between 2.5–3 km and 4.5–5 km — do not smooth across them (the paper's dotted trend lines sit ~1 M$ above the markers at 2–2.5 km). Dollar-year not stated (implicitly Lowry 2017 basis); contingency 10 % already included. | NREL 2025 cost curves, Fig. 5 (chart reading) |
| Smooth fit (only for sensitivity sweeps ≤ 2.5 km) | `C ≈ 1.45 M$ · exp(3.66e-4 z)` — within ±5 % of the markers at 1–2.5 km (3.6 M$ at 2.5 km); invalid above 2.5 km | fit by us |
| Small-diameter 8½″ vertical | 1500 → 1.6; 2000 → 1.9; 2500 → 2.9; 3000 → 4.1; 4500 → 5.7; 5000 → 10.8 M$ (same step depths) | same figure |
| Horizontal/deviated multiplier | 1.10–1.15 | same |
| Contingency | 10 % | same |
| Cross-check | full-size well US$2–6 M; 4 wells 2.5–3 km US$10–25 M | ESMAP p.69 |
| Plate HEX | `2000 + 48 · ṁ[kg/s] · ΔT[K]` AUD (1 AUD = 0.63 USD); titanium +50 % | Gippsland; Guidebook p.220 |
| Pre-insulated pipe | 500 AUD/m (100 AUD/m uninsulated HDPE) | Gippsland |
| Absorption vs electric chiller plant | see RC-05 cost functions (1986 $) | Guidebook p.269 |
| Heat pump (1987 $) | ≈ $91/kW (2.9 MW centrifugal) — outdated for HTHP | Guidebook Table 13.3 |
| HTHP, CHP, cooling tower, electric boiler, free-cooling HEX | `[A] placeholder` ranges in `config/cost_assumptions.yaml` with source field | spec ECO-04 |
| Escalation 1986/87 → 2026 | CEPCI ratio ≈ 2.5 [A] | — |

## Opex
- O&M 1–2 % of capex/yr (Gippsland); Guidebook Table 18.7: maintenance ≈ 1 %, insurance 0.35 %, property tax 0.15 %; plant maintenance 1.5–2.5 % (ESMAP p.74).
- Replacement: pumps every 5–10 yr (Guidebook) / 7 yr (Gippsland); heat pump at 15 yr.
- Energy prices, CO₂ price: `[A]` in YAML, ±30 % sensitivity.

## Benchmarks (sanity)
- Gippsland GRAC LCOH 19.16 AUD/GJ (gas 31); SGIL 16.81; capacity factor drives LCOH; shared networks −12 %.
- WVU heating + absorption cooling: $40–43/MWh_th full retrofit (2012 $), $18–22 hot-water case.
- NREL desalination: new wells LCOH US$0.010–0.014/kWh_th (2014 $).
- World Energy Council: district-energy cost structure ≈ 75 % capital, 15 % O&M, 10 % ancillary energy.

## Validation tests
- LCOH of a constant annuity case equals hand calculation (±0.1 %).
- Well cost from the table: (2500 m) = 3.45 M$ ±0.05 (table node); (2750 m) = 4.7 M$ ±0.1 (interpolated across the step — flag as uncertain); (3000 m) = 6.0 M$; (3200 m) = 6.3 M$ ±0.1. The smooth fit at 2500 m = 3.6 M$ ±0.1 (self-consistency only).
- HEX cost(25 kg/s, 10 K) = 14,000 AUD exactly.
