# RC-01 Reservoir, well hydraulics and pumps

**Feeds:** TL-03 reservoir_conditions, TL-04 productivity_index, TL-05 doublet_operating_point, TL-06 performance map, GM-01 brine PVT.
**Verification status:** DiPippo equations are given in standard form matching the cited equation numbers (equation images did not extract) — `VERIFY` against the book if available. GEMINI implementations are authoritative for code behaviour.

## Inputs (design basis)
z_res = 2500 m TVD; h = 100 m; k = 200 mD (1 mD = 9.869e-16 m²); P_res = 250 bar(g); T_res = 12 + 0.030·z °C; ρ = 1030 kg/m³; μ = 0.40e-3 Pa·s; c_p = 4100 J/kg·K [A]; GLR 0.8 Nm³/m³ (80 % CH₄, 20 % CO₂).

## Equations
1. Reservoir temperature at depth z (m): `T = 12 + 0.030 z` [P].
2. Pressure at other depths: hydrostatic `P = ρ g z` (g = 9.81) [P]. At 2.5 km: 252.6 bar ≈ given 250 bar ⇒ normally pressured.
3. Radial steady inflow (DiPippo eq 4.2–4.4, std form; GEMINI `reservoir_pressuredrop.bottomhole_skin_dp`):
   `ΔP = Q μ [ln(r_e/r_w) + S] / (2π k h)`; `PI = Q/ΔP = 2π k h / (μ [ln(r_e/r_w) + S])`.
   Defaults [A]: r_w = 0.108 m, r_e = 1000 m, S = 0. Expected PI ≈ 12–16 m³/h/bar.
   Unit conversion used by GEMINI `well_flow.py`: PI[m³/h/bar] × (1/3.6e8) = PI[m³/s/Pa].
4. Drawdown form (DiPippo eq 4.14–4.15): `P_wf = P_res − Q/PI` (production); injection `P_bh = P_res + Q/II` (II ≈ PI [A]).
5. Single-phase well pressure loss (DiPippo eq 4.10–4.11; GEMINI `darcyweisbach.py`):
   `ΔP = ρ g L + f (L/D) ρ v²/2`; Swamee–Jain `f = 0.25 / [log10(ε/(3.7D) + 5.74/Re^0.9)]²`; steel ε = 0.0457 mm.
6. Pump hydraulic power (challenge rule): `P_hyd = Q ΔP`; shaft `P_shaft = Q ΔP / 0.70` [P]; electrical `P_el = P_shaft / (η_motor η_VFD)` with η_motor 0.93, η_VFD 0.97 [A] (Guidebook examples use 60–65 % wire-to-water, p.256–263).
7. ESP head target for flow Q: `ΔP_ESP = ρ g z_pump_TVD-equivalent + friction + P_wh_required − P_intake`, where `P_intake = P_wf − ρ g (z_res − z_pump) − friction_below_pump`. GEMINI ESP: polynomial head/power in bbl/d scaled by stages and (f/60)², (f/60)³ — solve frequency f for target Q (brentq, 30–70 Hz).
8. NPSH available (Guidebook p.173): `NPSHA = Ha/SG + H1 − Hf − Hvp/SG` (heads in m). Require NPSHA ≥ NPSHR + margin (≥ 15 m heuristic, DiPippo Problem 4.5) — add margin for dissolved gas.
9. Gas break-out / CO₂-release depth: set ESP intake pressure above the bubble/CO₂-release pressure of the brine (DiPippo eq 4.16–4.20 flash-horizon method adapted; Ch8 p.4 "keep pressure above flash and bubble point"). Bubble-point pressure is NOT given ⇒ missing input; default: keep intake ≥ 20 bar(g) [A], flag.
10. Pump temperature correction (Guidebook p.167–169): `η_op = 1 − (1 − η_test)(ν_op/ν_test)^n`, n = 0.05–0.1 (optional).
11. Pump selection: lineshaft economical < ~730–800 m setting depth; ESP > 1 km, deviated wells, ≤ ~250 kg/s (Jacobs Table 2; Guidebook p.167, 181–183). 300 m³/h → 12″ pump in 16″ casing (Guidebook Table 6.3, p.109); production casing ≥ 13⅜″.
12. Injection pressure (Pająk et al., papers/): `P_inj,wh = P_res + ΔP_res + ΔP_skin + ΔP_friction − ρ g H`; include thermal skin optional.

## Defaults and expected envelope (sanity)
- Drawdown at 300 m³/h ≈ 20–25 bar; ESP Δp ≈ 30–40 bar; ESP shaft ≈ 0.4 MW; injection pump ≈ 0.3 MW per doublet at full flow.
- Wellhead T ≈ 85–86 °C at 300 m³/h (GEMINI DPDT "up", U = 10 W/m²K hard-coded).

## Validation tests
- PI with S = 0, r_e/r_w = 1000/0.108 → 12.2 m³/h/bar ±2 %.
- Hydrostatic 2500 m, 1030 kg/m³ → 252.6 bar ±0.1 bar; brine vs water difference over 2500 m → 7.36 bar.
- Swamee–Jain at Re = 1e6, ε/D = 0.0003 → f = 0.0158 (±1 %).
- GEMINI regression: `unit_test/gemini_simulator/test_well_flow.py` still passes after GM-01 (PVT default unchanged = water).
