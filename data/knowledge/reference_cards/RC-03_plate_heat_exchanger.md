# RC-03 Plate heat exchanger

**Feeds:** use of GEMINI `HeatExchanger` (ε-NTU rating model), UA sizing helper, VR physical checks, ECO-03.
**Verification status:** values from Guidebook Ch11/12 text and GNS guide; Guidebook worked example values from text extraction — reproduce as test.

## Equations
1. ε-NTU (GEMINI `heat_exchanger.py`, counter-flow): `NTU = UA / C_min`, `C_r = C_min/C_max`, `ε = [1 − exp(−NTU(1−C_r))] / [1 − C_r exp(−NTU(1−C_r))]`, `Q = ε C_min (T_h,in − T_c,in)`.
2. LMTD form (GNS guide p.57–58; DiPippo eq 8.11–8.16): `Q = U A LMTD F_c`, `LMTD = (ΔT_a − ΔT_b)/ln(ΔT_a/ΔT_b)`, F_c = 0.85–1.0 (Guidebook uses 0.90 for preliminary, p.221).
3. Sizing to a target outlet: solve UA such that the GEMINI model hits the target (bisection on UA).
4. Pressure drop (Guidebook p.221): `Δp ≈ 24 kPa per NTU` (3.5 psi/NTU; range 1–6 psi/NTU).

## Parameter values
| Item | Value | Source |
|---|---|---|
| U plate HEX (incl. fouling) | 3.0–4.0 kW/m²K (GNS); 5.1 kW/m²K preliminary, 4.0–6.2 range (Guidebook p.221) | default 4.0 [A] |
| U shell-and-tube water–brine | 0.57–1.14 kW/m²K | DiPippo Table 8.1 |
| U refrigerant evaporating–brine / condensing–water | 0.17–0.85 / 0.45–0.85 kW/m²K | DiPippo Table 8.1 |
| Approach temperature | achievable 1.1 K; economical 2.8–5.6 K; GNS 2–3 K | default 3 K [A] |
| NTU per pass | 0.6–4.0 | Guidebook p.221 |
| Limits | ~150 °C, ~20 bar (gasketed, Viton) (GNS p.40); 400 °F/300 psig welded types (Guidebook p.218); ≤ 1360 m³/h, ≤ 1208 m² per unit | VR check |
| Materials | titanium plates for saline brine (+50 % cost vs SS, Guidebook p.220); 304/316 SS chloride limits ~210/510 ppm at 27 °C, falling with T | RC-12 |
| Gaskets | Viton best; EPDM, Buna-N acceptable; neoprene fails | Guidebook Table 11.1 |
| Redundancy | 2 × 100 % units for cleaning (GNS) | design rule |
| Control valve | at HEX outlet to keep CO₂-rich brine pressurised (Guidebook p.234) | design rule |

## Validation tests
- Guidebook worked example (p.222–223): geothermal 160→130 °F, secondary 110→150 °F, 200 gpm ⇒ NTU ≈ 3.1, A ≈ 259 ft² (U = 900 Btu/h·ft²·°F, F = 0.9), Δp ≈ 10.9 psi. Reproduce NTU and area within ±5 %.
- GNS example (p.57–58): 300 kW, geothermal 90→60 °C, process 45→70 °C ⇒ LMTD 17.4 K; with U = 3 kW/m²K **and C_f = 0.85** ⇒ A = 300/(3 × 17.4 × 0.85) ≈ 6.8 m² (±3 %). (Without C_f the area is 5.75 m² — the book applies the correction factor.)
- ε-NTU vs LMTD consistency on the same case (±2 %).
