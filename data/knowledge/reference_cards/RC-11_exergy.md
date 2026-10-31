# RC-11 Exergy

**Feeds:** TL-14 exergy KPI, VR exergy layer, report.
**Verification status:** standard forms matching DiPippo Ch10 / §9.7 equation numbers (equation images not extracted) — `VERIFY` if the book is available. Cold-exergy form is standard thermodynamics (not in DiPippo).

## Equations
- Dead state T₀ = daily ambient dry-bulb (K) (DiPippo Ch10 p.7: use local environment; wet-bulb if wet tower dominates). State T₀ with every result.
- Specific flow exergy (eq 10.11–10.12): `e = (h − h₀) − T₀ (s − s₀)`; for incompressible liquid with constant c: `e = c [(T − T₀) − T₀ ln(T/T₀)]`.
- Heat exergy (eq 10.15): `E_Q = Q (1 − T₀/T)`; work exergy (eq 10.16): `E_W = W`.
- Heat delivered to a stream cooled from T_in to T_out (eq 9.28–9.29): `E_heat = ṁ c [(T_in − T_out) − T₀ ln(T_in/T_out)]`.
- Cooling delivered at T < T₀: `E_cold = Q_cool (T₀/T − 1)` (use log-mean of chilled supply/return); zero when T ≥ T₀ — note our 20 °C cooling is above T₀ in most months ⇒ cold exergy small or zero; report honestly.
- Utilisation efficiency (eq 9.27 adapted): `η_u = (W_net + E_heat + E_cold) / (ṁ_brine · e_reservoir)`; also functional efficiency = desired exergy out / exergy spent (incl. electricity and fuel exergy ≈ LHV × 1.04 [A] or LHV).
- HEX exergy efficiencies (eq 10.31–10.35): brute-force `η_bf = ΣE_out/ΣE_in`; functional `η_f1 = ṁ_c(e₂ − e₁)/ṁ_h(e_a − e_b)`.

## Benchmarks
- Pumped vs artesian well example (DiPippo Ch10 p.26–29): 180 °C reservoir, 75 kg/s, 666.8 kW pump ⇒ functional efficiency 36.7 %.
- CHP + district heat raised utilisation 31.2 → 33.4 % (DiPippo §9.7).
- Raising HEX LMTD 9.1 → 10.4 K increased exergy destruction 17.7 % (DiPippo Ch10 p.17–18) — narrative for approach-temperature choice.

## Validation tests
- e(T = T₀) = 0; E_heat ≥ 0 and < Q; E_Q(T → T₀) → 0.
- Liquid exergy: c = 4.1 kJ/kgK, T = 360.15 K, T₀ = 283.15 K ⇒ e = 4.1[(77) − 283.15 ln(360.15/283.15)] ≈ 36.4 kJ/kg (±0.5).
