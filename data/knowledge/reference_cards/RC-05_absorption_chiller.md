# RC-05 Absorption chiller (single-effect LiBr/H₂O)

**Feeds:** GM-04 AbsorptionChiller, dispatch, ECO-04, VR checks.
**Verification status:** Method A correlations decoded from the Guidebook TI-59 program listing (p.268) and checked digit-by-digit against the key codes, and against text statements (F ≈ 0.26 at 180 °F, 0.88 at 230 °F, 0.99 at 240 °F) — reliable within their validity range. Method B form is standard (Ziegler/Hellmann characteristic equation); coefficients `MISSING` — calibrate to a manufacturer datasheet (external data) or to Method A at rating points.

## Method A — Geo-Heat Center 1989 correlation (conservative; temperatures in °F)
- Hot-water temperature factor: `F_T = (−239 + 1.65 T_hw − 0.001 T_hw²)/100`
- Condition factor: `F_c = (323 − 0.838 T_chw − (3.77 − 0.0358 T_chw) T_cw)/100`
  T_chw = leaving chilled water (valid 40–50 °F), T_cw = entering cooling water (valid 75–90 °F); F_c ≈ 1.00 at 44/85 °F.
- Capacity fraction `F = F_T · F_c`; required nominal size = Q_required / F.
- `COP = 0.67 (55.4 + 0.185 T_hw)/100` (depends only on T_hw).
- Rating basis: 12 psi steam / ~118 °C hot water, 85 °F (29.4 °C) cooling water, 44 °F (6.7 °C) chilled water, COP 0.65–0.70 (p.261–262).
- SI values: T_hw 85 °C → F_T 0.32, COP 0.60; 90 → 0.43, 0.61; 100 → 0.66, 0.63; 105 → 0.77, 0.65; zero capacity ≈ 71 °C.
- Outside validity for our 20 °C chilled water — use as **lower bound** only.

## Method B — characteristic equation (ΔΔt method), required for 20 °C chilled water
- `Q_E = s_E (ΔΔt − ΔΔt_min)` (capacity), `Q_G = s_G (ΔΔt − ΔΔt_min,G)` (drive heat) or `COP = Q_E/Q_G`.
- `ΔΔt = (t̄_G − t̄_AC) − B (t̄_AC − t̄_E)`; t̄ = arithmetic mean of inlet/outlet of external circuits: G generator (hot water), AC absorber+condenser (cooling water in series), E evaporator (chilled water). B ≈ 1.1–1.2 (Dühring slope, LiBr) [A].
- Calibrate s_E, ΔΔt_min, s_G (or COP) to: (i) a datasheet if found, else (ii) Method A at its rating points (T_chw 44 °F, T_cw 85 °F, T_hw 180–240 °F). Then evaluate at t_E 20/26 °C.
- Screening estimate [D]: raising t̄_E by ≈ 14 K is worth ≈ +15–17 K of drive temperature ⇒ 85 °C brine at 20 °C chilled ≈ 100 °C at 6.7 °C chilled ⇒ capacity fraction ≈ 0.6–0.7, COP ≈ 0.63–0.70. Must be confirmed by the calibrated model; report as uncertain.

## Outputs (GEMINI model)
Q_cool, Q_drive = Q_cool/COP, Q_reject = Q_cool + Q_drive + P_aux, drive outlet T (generator ΔT ≈ 7.2 K on hot water, p.267), chilled supply T, power_el, emission (0).

## Auxiliaries and penalties
- Extra tower fan + cooling-water pump vs electric: 0.077 kW/ton ≈ 2.2 % of Q_cool (p.269).
- Well/drive pumping chargeable to absorption: 0.20–0.28 kW/ton; can approach 50 % of electric-chiller power (p.262, 263, 270) ⇒ VR check.
- Materials: standard Cu / 90-10 CuNi generator tubes incompatible with geothermal fluid (H₂S, NH₃, O₂) ⇒ titanium generator tubes (+10–15 % machine cost, manufacturer preferred) or isolation HEX + clean loop (p.266–267). Unenhanced SS tubes ⇒ large derating.
- Double-effect needs ~177 °C — not applicable. Ammonia–water needs ≥ 110–135 °C, COP < 0.55 — not applicable.

## Cost (1986 Means $, decoded p.269, 100–1000 ton, `VERIFY`)
- Absorption plant: `83,000 + 252 (Q_t − 100)/F + 41,000 + 170 (Q_t − 100)` (machine + tower/pumps/piping), Q_t tons (1 ton = 3.517 kW).
- Electric centrifugal plant: `107,000 + 267 (Q_t − 100)`.
- Escalate to 2026 with CEPCI ratio (~2.5 [A]) and convert currency.
- OIT case: installed ≈ $550 per nominal ton (p.263).

## Validation tests
- Method A hot-water factor alone: F_T(180 °F) = 0.256, F_T(230 °F) = 0.876, F_T(240 °F) = 0.994 (±0.005; the book's text rounds these to 0.26 / 0.88 / 0.99). Condition factor F_c(44 °F, 85 °F) = 0.996 (±0.002). Combined F(240 °F, 44/85) = 0.990 ±0.005. COP(190 °F) = 0.607 ±0.005. Zero-capacity temperature 160.5 °F ≈ 71.4 °C (±0.5 K).
- OIT case (p.263) is a **documented comparison, not a pass/fail test**: the book's actual selection was a nominal 312 ton machine for a 150 ton duty at 190 °F (F ≈ 0.48), whereas Method A at 44/85 °F gives F = 0.382 (≈ 392 ton). The case conditions are not fully stated in the extracted text (at 48 °F chilled water F = 0.416). Record the discrepancy in the model docstring; it shows Method A is conservative.
- Method B calibrated at rating points reproduces Method A within ±5 % on the rating line (44/85 °F, T_hw 180–240 °F) only. Clarified by WP-02 (the original wording was "inside Method A validity"): off that line no ΔΔt form with B ≈ 1.1–1.2 can match Method A's product form F_T·F_c (fixed 71.4 °C zero-capacity temperature, equal and opposite T_chw/T_cw sensitivities); the difference reaches ≈ 0.27 of nominal capacity at 180 °F, 50 °F, 75 °F — documented in `src/gemini_model/absorption_chiller/REFERENCE.md`, not a pass/fail test.
- Energy balance Q_reject = Q_cool + Q_drive + P_aux exactly.
