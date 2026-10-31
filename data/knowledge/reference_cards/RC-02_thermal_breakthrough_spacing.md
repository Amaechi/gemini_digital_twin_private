# RC-02 Thermal breakthrough, well spacing, interference

**Feeds:** TL-07 thermal_breakthrough / production_temperature_curve, TL-08 pressure_interference, ENG-06, SIM-06.
**Verification status:** closed-form t_b is the standard Gringarten–Sauty (1975) result (not printed in the source paper) — `VERIFY` if possible; benchmark numbers are from the papers.

## Equations
1. Breakthrough time of a doublet in a confined aquifer (Gringarten & Sauty 1975; papers/ "Flow and Thermal Modeling of a Geothermal Doublet … Paris Basin"):
   `t_b = π h D² (ρC)_a / (3 (ρC)_w Q)`
   h thickness (m), D well spacing at reservoir (m), Q (m³/s), (ρC)_a aquifer volumetric heat capacity ≈ 2.5e6 J/m³K [A], (ρC)_w = ρ c_p ≈ 1030 × 4100 = 4.22e6 J/m³K.
   Check: h = 100, D = 1000, Q = 300 m³/h ⇒ t_b ≈ 23.6 yr; D = 1200 ⇒ 34 yr.
2. Production temperature after breakthrough (paper, burden-free limit): `T_prod = (1 − θ/π) T_res + (θ/π) T_inj` along streamline angle θ; with conductive burdens use erfc form `T_w = T_rock + (T_inj − T_rock) erfc[((t − t_a)/τ)^0.5]`, τ = λ t_a. Arrival times: t_a(π/2) = 3 t_a(0), t_a(3π/4) = 20.13 t_a(0). Implement the simple form; erfc form MAY.
3. Heterogeneity uncertainty band: ±6 °C on 30-yr production temperature (Boolean lens case), ≈ ±2 °C smoother (paper).
4. Pressure interference between doublets (line source, steady state approx.): `ΔP(r) = Q μ / (2π k h) · ln(r_inj / r_prod)` summed over wells (superposition of a source and a sink). Limit ≤ 1 bar at neighbouring wells (regulator rule, papers/ "Comparison of the Modelled Hydraulic Impact … Dogger").

## Defaults
D ≥ 1200 m (target t_b ≥ 30 yr). Separate doublets so interference ≤ 1 bar.

## Cautionary knowledge (for agent explanations)
- Hatchobaru: producer 140 m from injector feedzone cooled ~50 °C in 3 years (DiPippo Ch14).
- Paris/Dogger doublets: 1–1.3 km spacing standard; Neustadt-Glewe 1,350 m; Landau ~1,200 m (DiPippo Ch22).

## Validation tests
- t_b(h=100, D=1000, Q=300 m³/h, ρC_a=2.5e6, ρC_w=4.223e6) = 23.6 yr ±2 %.
- t_b scales with D² and 1/Q (property test).
