# RC-12 Scaling, corrosion and materials

**Feeds:** TL-22 scaling_check, ENG-09/10, DR-07 (gas handling), DR-09 (materials), VR performance layer.
**Verification status:** from Guidebook Ch8/10/11 and DiPippo §4.4/§6.6/Ch23 text. Brine chemistry (Ca, alkalinity, pH, Cl, SO₄, TDS) is NOT provided ⇒ `MISSING INPUT` — the tool must flag it and fall back to qualitative rules + mitigation.

## Scaling
- Calcite: CO₂ loss on degassing raises pH ⇒ CaCO₃ supersaturation; calcite solubility **decreases with rising T** (retrograde), so cooling alone does not precipitate it (DiPippo §4.4; Guidebook p.160). Rule: **keep pressure above the CO₂ release point** at every point upstream of the degasser and downstream if degassing is avoided (Guidebook p.160, 234; DiPippo Ch8 p.4; WEC p.15 — sedimentary doublets usually stay pressurised and reinject without degassing).
- If degassing (C2/C3): dose calcite inhibitor downhole below the release depth (polyacrylate 0.5–2.5 ppm, typical 0.75 ppm; inhibitor failure scales a well within days — DiPippo Ch16 Miravalles).
- Ryznar index (Guidebook p.160–163): `RI = 2 pH_s − pH_a`, `pH_s = pCa + pALK + K_c(T)`; RI < 4 extreme scaling, 4–5.5 heavy, 5.5–6.2 slight, 6.2–7 little/none, > 7 none (corrosive tendency).
- Gypsum relative saturation: `RS = 10^(log Ca + log SO₄ − log K_gyp)`; ambiguous 0.8–1.2.
- Silica: scaling governed by amorphous silica; precipitation rate peaks at pH 6–7.5 (DiPippo §6.6). At 87 °C → 46 °C outlet, check only if SiO₂ data available.

## Corrosion and materials
- Key species: O₂, pH, Cl⁻, H₂S, CO₂, NH₃, SO₄ (Guidebook Table 8.1). CO₂ lowers pH and raises carbon-steel corrosion; corrosion drops sharply above pH 8.
- Oxygen: 30 ppb ⇒ 4× corrosion; > 50 ppb severe pitting; aeration 4–15× (Guidebook p.150–152) ⇒ closed, oxygen-free loop; storage tanks need bladder/inert blanket (p.254–255).
- Copper alloys and solder unacceptable with ppb H₂S (Guidebook p.152, 211) ⇒ no Cu/CuNi in brine contact (incl. absorption generator tubes).
- Stainless: 304/316 localised-corrosion thresholds ≈ 210/510 ppm Cl at 27 °C, falling with T; SCC risk > 60 °C (Guidebook p.155, 159, 219) ⇒ **titanium plates** for saline brine (Neustadt-Glewe used titanium HEX for 220,000 ppm TDS brine, DiPippo Ch22).
- Carbon-steel class Va median 0.32 mm/yr, pitting to 2.1 mm/yr ⇒ Schedule 80 (Guidebook p.153, 157) — classes valid only < 5000 ppm key species (not saline brines).
- CO₂ attacks Portland cement ⇒ dense, low w/c cement in wells (Guidebook p.116); dissolved gases can embrittle casing (DiPippo Ch22 Habanero).
- Galvanizing useless > 57 °C. Elastomers: Viton best.
- Use GEMINI `CO2PartialPressureModel` / `CO2Corrosion` (DLD/DLM/NORSOK) for a CO₂ corrosion rate estimate (accepts GLR and CO₂ fraction).

## Tool behaviour (TL-22)
- With chemistry: compute RI, RS, CO₂ partial pressure, corrosion rate; pass/fail with margins.
- Without chemistry: return `status = "insufficient data"`, list missing analytes, and require mitigations in the design (pressurised loop or inhibitor; Ti plates; O₂ exclusion; no Cu) — recorded in DR-07/DR-09.

## Validation tests
- RI classification boundaries correct; missing-data path returns the analyte list.
