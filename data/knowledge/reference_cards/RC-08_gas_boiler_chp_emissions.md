# RC-08 Associated gas, boiler, CHP, emissions

**Feeds:** GM-12 separator gas composition, GEMINI Boiler/CHP use, ECO-05, DR-07.
**Verification status:** gas properties derived [D] from the design basis; emission factors from GEMINI and Gippsland paper.

## Associated gas (GLR 0.8 Nm³/m³; 80 mol % CH₄, 20 mol % CO₂; Nm³ at 0 °C, 1.01325 bar)
- Molar volume 22.414 Nm³/kmol. Mix molar mass = 0.8·16.04 + 0.2·44.01 = 21.64 kg/kmol ⇒ density 0.965 kg/Nm³.
- CH₄ LHV 35.8 MJ/Nm³ ⇒ mix LHV 28.6 MJ/Nm³ = 29.7 MJ/kg (GEMINI example uses 50 MJ/kg pure methane — wrong for this mix).
- Per m³ brine: 0.64 Nm³ CH₄ (0.46 kg) + 0.16 Nm³ CO₂ (0.32 kg).
- Per doublet at 300 m³/h: 240 Nm³/h gas ⇒ fuel ≈ 1.91 MW (LHV).
- CO₂ when burned (combustion + inherent): 1.964 kg CO₂/Nm³ mix ⇒ 68.6 kg/GJ_LHV.
- Venting CH₄ is not allowed [A] (would dominate emissions). Options (DR-07): (a) degas → CHP/boiler (precedent: Pleasant Bayou burned separated methane, DiPippo §9.6.3); (b) keep pressurised loop, reinject dissolved gas (zero emission; avoids calcite scaling — DiPippo Ch23, WEC p.15).
- Methane solubility falls 3–4 % per 10,000 ppm TDS (DiPippo §9.6.3) — informative only.

## Boiler (GEMINI `boiler.py`)
`P_th = η · LHV · ṁ_gas`; η = 0.80 (example) / 0.90 modern [A]; emission = factor[kg/GJ] × 1e-9 × fuel W.

## CHP (GEMINI `chp.py`)
Electrical fraction hard-coded 1/3 of fuel power after efficiency (line 19). Electricity offsets heat-pump and pump demand in dispatch.

## Emission factors
| Item | Value | Source |
|---|---|---|
| Grid natural gas | 56.6 kg/GJ (GEMINI example) or 51.53 kgCO₂e/GJ (Gippsland) — choose one, state it | GEMINI / papers |
| Associated gas | 68.6 kg/GJ incl. inherent CO₂ | [D] |
| Grid electricity | 0.3 kg/kWh | GEMINI example [A] |
| Gas boiler (benchmark) | 80 % efficiency, 90 % availability, US$270/kW_th | NREL desalination report |

## Validation tests
- Density 0.965 ±0.002 kg/Nm³; LHV 29.7 ±0.2 MJ/kg; 68.6 ±0.5 kg/GJ.
- Fuel power at 300 m³/h = 1.91 ±0.02 MW.
