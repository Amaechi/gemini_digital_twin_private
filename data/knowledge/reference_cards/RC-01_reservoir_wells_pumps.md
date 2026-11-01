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
9. Gas break-out / CO₂-release depth: set ESP intake pressure above the bubble/CO₂-release pressure of the brine (DiPippo eq 4.16–4.20 flash-horizon method adapted; Ch8 p.4 "keep pressure above flash and bubble point"). The design basis gives no bubble point ⇒ **compute it with Henry's law** (replaces the former 20 bar(g) [A] default, which was NOT conservative):
   - `p_b = Σ_i c_i / H_i(T)` with `c_i = GLR · y_i / 0.022414` mol/m³ (Nm³ at 0 °C, 1.01325 bar), `H_i(T) = H_i° · exp[B_i (1/T − 1/298.15)]` in mol m⁻³ Pa⁻¹ (van 't Hoff form, single parameter — valid as an estimate, the compilation notes multi-parameter fits exist in the originals). Ideal gas, no fugacity or water-vapour correction (both small; water vapour at 87 °C ≈ 0.6 bar).
   - Constants (Sander, *Compilation of Henry's law constants (version 5.0.0) for water as solvent*, Atmos. Chem. Phys. 23, 10901–12440, 2023; entries attributed to Burkholder et al. 2019, JPL Publication 19-5; read at henrys-law.org 1 Nov 2026) [L]:

     | Gas | H° pure water (mol m⁻³ Pa⁻¹) | B pure (K) | H° sea water | B sea (K) |
     |---|---|---|---|---|
     | CH₄ | 1.4e-5 | 1600 | 1.2e-5 | 1100 |
     | CO₂ | 3.4e-4 | 2300 | 2.8e-4 | 2600 |

   - **Use the sea-water constants** for our brine (1030 kg/m³ ≈ 45 g/L TDS, slightly above sea water 35 g/L — salting-out makes the true p_b a little higher) and report the pure-water result as the second bound.
   - At 87 °C, GLR 0.8 (80 % CH₄ / 20 % CO₂): H_CH₄ = 0.636 (sea) / 0.556 (pure) mol m⁻³ bar⁻¹; H_CO₂ = 6.24 / 9.01; c_CH₄ = 28.55 mol/m³, c_CO₂ = 7.14 ⇒ **p_b ≈ 46 bar(a) (sea-water basis), 52 bar(a) (pure-water basis)**; CO₂ contributes only ≈ 1 bar — methane sets the bubble point.
   - **ESP-intake setpoint (VER-ESP-PB): p_intake ≥ 1.2 · p_b ≈ 60 bar(a)** — the 1.2 factor is a margin for the van 't Hoff extrapolation (298 → 360 K; CH₄ solubility has its minimum near 80–100 °C) and for salinity above sea water [A]. Record p_b, basis and margin in the assumptions register. Illustration (no friction): at 200 m³/h the flowing column reaches 60 bar(a) at ≈ 770 m depth, so the ESP sits deeper than that plus NPSH margin.
   - Consequence for surface design (DR-07, RC-03, RC-12): a pressurised no-degassing loop must hold > p_b everywhere, which exceeds the plate-HEX ratings on RC-03 (gasketed ≈ 20 bar, welded 300 psig). Degassing + downhole inhibitor is the base route; the pressurised variant needs a shell-and-tube/plate-and-shell unit (no cost on the cards — flag).
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
- Bubble point (item 9): H_CH₄(360.15 K, sea water) = 0.636 mol m⁻³ bar⁻¹ (±1 %); p_b(87 °C, GLR 0.8, 80/20, sea water) = 46.1 bar(a) ±0.5; pure water 52.2 ±0.5; CO₂ partial pressure 1.1 ±0.1 bar; setpoint 1.2 p_b = 55.3 bar(a) (sea) — round up to 60 bar(a) as the default. Monotonic: p_b rises with GLR and (over 25–100 °C) with T for CH₄.
