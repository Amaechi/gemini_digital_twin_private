**Verdict: ACCEPTED** (DesignVerifier) — DesignVerifier C2-opt-robust: ACCEPTED; all 46 checks pass; tightest PH-02 margin 0 K

Violations: none; not evaluated: none

| Check | Layer | Name | Status | Value | Limit | Margin | Unit |
|---|---|---|---|---|---|---|---|
| SY-01 | syntactic | GEMINI project JSON loads | pass | – | – | – |  |
| SY-02 | syntactic | schemas valid and consistent | pass | – | – | – |  |
| SY-03 | syntactic | unit suffixes present | pass | – | – | – |  |
| SE-01 | semantic | unique component names | pass | – | – | – |  |
| SE-02 | semantic | connections reference existing components and keys | pass | – | – | – |  |
| SE-03 | semantic | splitter fractions sum to 1 | pass | 1 | 1 | 0 | - |
| SE-04 | semantic | each producer paired with an injector | pass | 2 | 2 | – | count |
| PH-24 | physical | 1–3 doublets, matching the results | pass | 2 | 3 | 1 | count |
| PH-01 | physical | daily energy balance < 1 % (supplied + unmet = demand) | pass | 0 | 0.01 | 0.01 | frac |
| PH-02 | physical | brine cooling 0–40 K | pass | 40 | 40 | 0 | K |
| PH-03 | physical | 50–300 m3/h per active doublet, 0 on outage | pass | 50 | 300 | 0 | m3/h |
| PH-04 | physical | Q_inj = Q_prod | pass | 0 | 0 | 0 | m3/h |
| PH-05 | physical | heating supply 100 °C met | pass | 100 | 100 | 0 | degC |
| PH-06 | physical | cooling supply 20 °C met | pass | 20 | 20 | 0 | degC |
| PH-22 | physical | brine-side energy balance < 1 % (heat drawn = ρ·Q·c_p·ΔT; first law per user) | pass | 0 | 0.01 | 0.01 | frac |
| PH-07 | physical | plate-HEX approach ≥ 1.1 K | pass | 3 | 1.1 | 1.9 | K |
| PH-08 | physical | plate-HEX temperature within rating (gasketed 150 °C, welded 204 °C) | pass | 85.22 | – | 64.78 | degC |
| PH-09 | physical | plate-HEX pressure within rating (gasketed 20 bar, welded 20.7 bar(g)) | pass | 0 | – | 20 | bar |
| PH-10 | physical | plate-HEX Δp 1–6 psi per NTU (≈ 24 kPa/NTU) | pass | 24.13 | 41.37 | 17.24 | kPa |
| PH-11 | physical | plate-HEX NTU per pass 0.6–4.0 | pass | 3.024 | 4 | 0.9756 | - |
| PH-12 | physical | plate-HEX unit ≤ 1360 m3/h and ≤ 1208 m2 | pass | 0.6608 | 1 | 0.3392 | frac |
| PH-23 | physical | plate-HEX NTU consistent with its terminal temperatures (F_c 0.85–1) | pass | 9.073 | – | 0 | - |
| PH-13 | physical | heat-pump COP ≤ Carnot (condensing/evaporating temperatures) | pass | 3.328 | – | 2.219 | - |
| PH-14 | physical | heat-pump lift and COP within datasheet (or RC-04 defaults) | pass | 1 | 1 | 0 | frac |
| PH-15 | physical | absorption COP and capacity within GM-04 bounds | not_applicable | – | – | – |  |
| PH-16 | physical | absorption pumping ≤ 50 % of electric-chiller power | not_applicable | – | – | – |  |
| PH-17 | physical | pump shaft power = QΔp/0.7 | pass | 0 | 0.01 | 0.01 | frac |
| PH-18 | physical | ESP intake ≥ 1.2 · bubble point (RC-01 item 9 setpoint) | pass | 55.12 | – | 0.8707 | bar |
| PH-19 | physical | NPSH_A ≥ NPSH_R + 15 m | pass | 545.5 | 15 | 530.5 | m |
| PH-20 | physical | no negative flows or powers | pass | 0 | 0 | 0 | MW |
| PH-21 | physical | simulation constraint margins ≥ 0 | pass | 0 | 0 | 0 | suffix |
| PH-25 | physical | TL-05 design point feasible | pass | 0 | 0 | 0 | infeasible points |
| PH-26 | physical | TL-05 ESP flow solve error ≤ 1 % | pass | 0 | 0.01 | 0.01 | frac |
| PH-27 | physical | ESP intake ≥ 1.2 · p_b at the design point (TL-05) | pass | 0.8707 | 0 | 0.8707 | bar |
| PH-28 | physical | cooling and heat-pump models within validity (RC-04…RC-07) | pass | 0 | 0 | 0 | invalid model points |
| PH-29 | physical | daily result flags (DayResult.flags) | pass | 0 | 0 | 0 | failing day flags |
| PH-30 | physical | degassed brine below its boiling point at the letdown pressure | pass | 85.22 | 100 | 14.78 | degC |
| PD-01 | performance_design | unmet demand = 0 | pass | 0 | 0 | 0 | MWh |
| PD-02 | performance_design | planned outages do not overlap | pass | 1 | 1 | 0 | doublets |
| PD-03 | performance_design | well spacing ≥ 1.2 km | pass | 1200 | 1200 | 0 | m |
| PD-04 | performance_design | inter-doublet interference ≤ 1 bar | pass | 1 | 1 | 0 | bar |
| PD-05 | performance_design | thermal breakthrough ≥ 30 yr | pass | 33.95 | 30 | 3.946 | yr |
| PD-11 | performance_design | TL-08 interference within limit at the design separation | pass | 1 | 1 | 0 | flag |
| PD-12 | performance_design | TL-07 thermal breakthrough after the analysis horizon | pass | 1 | 1 | 0 | flag |
| PD-06 | performance_design | fast vs full simulation agree (≤ 5 % daily, per source) | pass | 0.004511 | 0.05 | 0.04549 | frac |
| PD-07 | performance_design | scaling screen passed or mitigated | pass | 0 | 0 | 0 | count |
| PD-08 | performance_design | materials compatible (no Cu in brine; Ti plates for saline brine) | pass | 0 | 0 | 0 | count |
| PD-09 | performance_design | sanity envelope: production temperature ≤ reservoir temperature | pass | 85.22 | 87 | 1.781 | degC |
| PD-10 | performance_design | pressurised brine loop ≥ CO₂-release setpoint (RC-01 item 9) | not_applicable | – | – | – |  |
| EX-01 | exergy | exergy utilisation reported | info | – | – | – | frac |
