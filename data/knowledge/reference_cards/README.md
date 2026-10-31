# Reference cards

Each card is the **only** engineering source a coding session needs for one model or tool. The source books/papers are NOT in this repository (copyright); the cards paraphrase equations and cite them.

Rules for every session that implements a model or tool from a card:
1. Implement the equations exactly as written on the card; put the card ID and citations in the module docstring.
2. Write the "Validation tests" listed on the card (they reproduce worked examples or hand checks). Tolerances as stated.
3. Respect the **Verification status** line: items marked `VERIFY` were reconstructed (OCR/decoding or standard form) — keep them, but log them in the assumptions register and do not claim them as exact textbook values.
4. Never invent coefficients. If a value is missing, use the `[A]` default on the card and flag it via `request_missing_input` / assumptions register.
5. If you discover a card is wrong, fix the card in the same PR and say why.

| Card | Topic | Feeds |
|---|---|---|
| RC-01 | Reservoir, well hydraulics, pumps | TL-03/04/05/06, GM-01 |
| RC-02 | Thermal breakthrough, spacing, interference | TL-07/08 |
| RC-03 | Plate heat exchanger | GEMINI HeatExchanger use, VR checks, ECO |
| RC-04 | Heat pump (incl. high-temperature) | GM-03 |
| RC-05 | Absorption chiller | GM-04 |
| RC-06 | Electric chiller | GM-05 |
| RC-07 | Heat rejection, free cooling, Dutch climate | GM-06/07, TL-02b |
| RC-08 | Associated gas, boiler, CHP, emissions | GM-12, ECO-05 |
| RC-09 | District network & thermal storage | DB-31, TL-23, GM-09 |
| RC-10 | Economics | TL-14, ECO |
| RC-11 | Exergy | TL-14 exergy, VR exergy KPI |
| RC-12 | Scaling, corrosion, materials | TL-22, ENG-09 |
