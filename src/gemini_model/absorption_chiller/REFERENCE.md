# REFERENCE — absorption chiller (GM-04)

Model: `absorption_chiller.AbsorptionChiller` (registry type `absorption_chiller`), with
`ghc1989.py` (Method A) and `characteristic.py` (Method B + calibration helper).
Source card: `data/knowledge/reference_cards/RC-05_absorption_chiller.md` — single-effect LiBr/H₂O.
Citations: Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989), Ch14
p.261–270 (equations re-implemented, not copied). Spec §3.3, §7.2 GM-04.

**Verification status (RC-05):** Method A decoded from the Guidebook TI-59 listing (p.268), checked
against text values — reliable inside its validity range. Method B form is standard
(Ziegler/Hellmann); its coefficients are **not** in our sources and are calibrated (assumption
COOL-ABS-CAL).

## Method A — `method: "ghc1989"` (temperatures in °F)
- `F_T = (−239 + 1.65 T_hw − 0.001 T_hw²)/100` (T_hw entering hot water)
- `F_c = (323 − 0.838 T_chw − (3.77 − 0.0358 T_chw) T_cw)/100` (T_chw leaving chilled water,
  valid 40–50 °F; T_cw entering cooling water, valid 75–90 °F); F_c ≈ 1.00 at 44/85 °F
- `F = F_T · F_c`; capacity `Q_avail = F · Q_nom`; required nominal size = Q_required / F
- `COP = 0.67 (55.4 + 0.185 T_hw)/100`
- Rating basis (p.261–262): 12 psi steam / ~118 °C hot water, 85 °F cooling, 44 °F chilled, COP 0.65–0.70
- SI: 85 °C → F_T 0.32, COP 0.60; 90 → 0.43, 0.61; 100 → 0.66, 0.63; 105 → 0.77, 0.65; zero capacity ≈ 71.4 °C
- Outside validity (our 20 °C chilled water) a **lower bound** only. Implementation
  (`ghc1989_conservative_clamp`, default true, assumption COOL-ABS-LB): chilled water > 50 °F is
  evaluated at 50 °F and cooling water < 75 °F at 75 °F (F rises with T_chw, falls with T_cw);
  a validity warning is returned. Hot water > 240 °F or between the zero-capacity temperature
  and 180 °F is extrapolated with a warning.
- With a given drive flow, the drive heat is limited so that the outlet stays at or above the
  zero-capacity temperature 71.4 °C (Method A has no flow term; COOL-ABS-DRV).
- Part load: COP(T_hw) unchanged; `Q_drive = Q_cool / COP`. The chilled-water term uses the
  supply set point.

## Method B — `method: "characteristic"` (default)
- `ΔΔt = (t̄_G − t̄_AC) − B (t̄_AC − t̄_E)`, t̄ = arithmetic mean of inlet/outlet of the external
  circuits (G hot water, AC cooling water through absorber + condenser in series, E chilled water)
- `Q_E = s_E (ΔΔt − ΔΔt_min)`, `Q_G = s_G (ΔΔt − ΔΔt_min,G)`, `COP = Q_E / Q_G`
- B = 1.15 (RC-05 [A] 1.1–1.2; COOL-ABS-B)
- Calibration (RC-05 option ii, `characteristic.calibrate_to_ghc1989`): Method A rating points
  T_chw 44 °F, T_cw 85 °F, T_hw 180–240 °F (every 5 °F); circuit means use generator ΔT 7.2 K
  (p.267) [L], cooling-water range 5 K [A RC-07], chilled-water ΔT 6 K [A, DB-32 design ΔT];
  weighted least squares on the relative error. Result (B = 1.15), s per unit of nominal capacity:

  | s_E (1/K) | ΔΔt_min (K) | s_G (1/K) | ΔΔt_min,G (K) | ΔΔt range (K) | max error F | max error COP |
  |---|---|---|---|---|---|---|
  | 0.022260 | 9.503 | 0.032491 | 7.453 | 21.06–54.39 | 1.0 % | 2.1 % |

- A datasheet fit can replace it: `characteristic.fit_coefficients(ΔΔt, capacity_fraction, COP)`
  → parameter `characteristic_coefficients`.
- Part load (COOL-ABS-PL): ΔΔt reduced to `ΔΔt_min + Q_E/s_E` (drive-temperature control);
  `Q_G = s_G (ΔΔt − ΔΔt_min,G)` keeps an offset ≈ 0.067 Q_nom, so the COP falls at low load
  (85 °C drive, 25 °C cooling water: 0.65 full, 0.60 at 30 %, 0.47 at 10 %, 0.21 at 2 %).
- Drive-side consistency (COOL-ABS-DRV): with a given drive flow the outlet stays above the
  minimum generator temperature `t_G,min = ΔΔt_min,G + (1+B) t̄_AC − B t̄_E` exactly when
  `ρ c_p V_G ≥ s_G/2` (independent of the operating point; ≈ 13.9 m³/h of water per MW nominal;
  output `minimum_drive_flow_rate_m3s`). Below it the machine is treated as off (warning).
- Circuits with a given flow: outlet T from `ρ c_p V ΔT = Q`, and the mean temperatures are
  solved together with the duty (linear → closed form). Without a flow: fixed ΔT (generator 7.2 K,
  cooling-water range 5 K) and the required flow is reported. Load given (`cooling_load_w`):
  chilled supply held at the set point, mean = (supply + return)/2.
- Screening [D] (RC-05): 85 °C drive at 20/26 °C chilled ≈ capacity fraction 0.6–0.7, COP
  0.63–0.70 — the calibrated model gives 0.66 / 0.64 at rating cooling water (29.4 °C).
  **Extrapolated, uncertain** — validity warnings say so.
- Capacity is capped at `max_capacity_fraction` × Q_nom = 1.0 (COOL-ABS-CAP).

### Method A vs Method B off the rating line (documented finding)
Method A's product form keeps the zero-capacity drive temperature at 71.4 °C whatever the
chilled/cooling-water temperatures, and its sensitivities to T_chw and T_cw are equal and opposite
(≈ ±0.022 per °F at 44/85 °F), which no ΔΔt form with B ≈ 1.1–1.2 can reproduce. The ±5 % check
therefore holds on the rating points (the T_hw validity range at 44/85 °F); elsewhere in the
validity box the methods differ by up to 0.27 of nominal capacity (180 °F, 50 °F, 75 °F).

## Outputs (W, °C, m³/s)
`cooling_power_w` Q_E, `available_cooling_w`, `unmet_cooling_w`, `capacity_fraction`, `load_ratio`,
`cop`, `drive_heat_w` Q_G = Q_E/COP, `absorber_condenser_heat_w` Q_E + Q_G,
`heat_rejected_w` = Q_E + Q_G + P_aux (exact), drive/cooling-water/chilled-water inlet/outlet
temperatures and flows, `drive_delta_t_k`, `minimum_drive_flow_rate_m3s` (Method B),
`delta_delta_t_k` (Method B, full-load ΔΔt), `capacity_factor_ft`/`_fc`
(Method A), `validity_warnings`, `power_el` = P_aux, `power_th` = Q_E (useful cooling), `emission` 0.

## Auxiliaries and materials
- P_aux = 0.077 kW/ton ≈ 2.2 % of Q_cool (p.269): extra tower fan + cooling-water pump vs electric
  (COOL-ABS-AUX), applied while `include_heat_rejection_auxiliaries` is true (default). Set it to
  false when a `heat_rejection` component is connected, which counts the fan and pumps itself
  (RC-05/RC-06 no-double-counting rule): P_aux = 0, Q_reject = Q_E + Q_G.
- Well/drive pumping chargeable to absorption: 0.20–0.28 kW/ton; can approach 50 % of an electric
  chiller's power (p.262, 263, 270) ⇒ verifier check (not in this model).
- Cu / 90-10 CuNi generator tubes are incompatible with geothermal fluid (H₂S, NH₃, O₂) ⇒ titanium
  tubes (+10–15 % machine cost) or isolation HEX + clean loop (p.266–267). Per-circuit ρ/c_p
  (`drive_density_kg_m3`, `drive_specific_heat_j_kgk`): brine 1030 / 4100 with titanium tubes.
- Double-effect (~177 °C) and ammonia–water (≥ 110–135 °C, COP < 0.55) are not applicable.

## Cost (for WP-08, RC-05, 1986 Means $, `VERIFY`)
Absorption plant `83,000 + 252 (Q_t − 100)/F + 41,000 + 170 (Q_t − 100)`; electric centrifugal
`107,000 + 267 (Q_t − 100)` (Q_t tons, 1 ton = 3.517 kW); OIT installed ≈ $550 per nominal ton.

## Validation (unit tests, `unit_test/gemini_model/absorption_chiller/`)
- F_T(180/230/240 °F) = 0.256/0.876/0.994 ± 0.005; F_c(44, 85) = 0.996 ± 0.002;
  F(240, 44/85) = 0.990 ± 0.005; COP(190 °F) = 0.607 ± 0.005; zero capacity 160.5 °F ≈ 71.4 °C ± 0.5 K;
  SI table.
- OIT case (p.263), **documented comparison, not pass/fail**: the book chose a nominal 312 ton
  machine for 150 ton at 190 °F (F ≈ 0.48); Method A gives F = 0.382 at 44/85 °F (≈ 392 ton) and
  0.416 at 48 °F chilled water — Method A is conservative.
- Method B calibrated at the rating points reproduces Method A within ± 5 % (capacity and COP).
- Energy balance Q_reject = Q_cool + Q_drive + P_aux exactly; each circuit's ρ c_p V ΔT equals
  its heat flow; the closed-form flow solution satisfies the characteristic equation; drive
  outlet ≥ t_G,min above the minimum drive flow, machine off below it; Method A drive-flow limit.
