# GeoAgent — speaker notes for the 10-slide deck

Companion to `DECK_OUTLINE.md`. Every number below is copied from `docs/results/REPORT.md`
(section given in brackets), which in turn names the run file it was read from. Nothing here is
typed from memory; if a number and `REPORT.md` ever disagree, `REPORT.md` wins.

How to use: each slide has (a) what the slide claims, (b) the background a judge may probe,
(c) the numbers with their source, (d) likely questions with answers.

---

## Slide 1 — Answer first

**Claim.** The agent selected **C2-opt-robust**: two doublets at 2 500 m, a titanium plate heat
exchanger (HEX) followed by a high-temperature heat pump (HTHP) on the brine, a CHP engine on the
associated gas, a peak gas boiler, free cooling first and electric chillers for the rest. The
DesignVerifier accepted it (46/46 checks) and a human approved it at P10.

**What "C2" means.** The brief asks for ≥ 8 alternatives. The spec defines concept families
C0–C8 (§3.4): C0 = no geothermal (gas boilers + electric chillers, the counterfactual);
C1 = three doublets, no heat pump; C2 = two doublets with HEX + HTHP; C3 = C2 but absorption
cooling driven by brine; C4 = two deeper doublets (3.2 km, 108 °C brine, no heat pump); C5 = three
shallow doublets (1.5 km) with large heat pumps; C6 = C2 + seasonal storage; C7 = C2 with a
pressurised loop (no degassing); C8 = C2 + a spare doublet. "-opt" = after the optimiser (TL-13)
trimmed sizes; "-robust" = after DR-11 installed the P90 backup boiler.

**Numbers [§1].** LCOH 44.6 USD/MWh (heat), LCOC 26.3 USD/MWh (cooling), 30-year basis; CAPEX
30.1 MUSD; CO₂ 7 360 t/yr = −72 % vs C0 (26 332 t/yr); unmet demand 0 days / 0 MWh on both
services; P(demand met) 0.90 across 200 Monte Carlo futures; exergy efficiency 0.44.

**Why the three lines on the slide.** (1) "verified, accepted, approved" = the three gates of
the design principle (slide 3). (2) Cost + CO₂ + zero unmet days = the 50 % "system design"
score. (3) "the model only chose" = the agent story in one sentence.

**Q&A.**
- *Why USD?* The cost basis was an open question to the organisers (spec §17 Q5); RC-10's
  sources are in USD/EUR; everything is stored natively and converted at ECB 1.13 USD/EUR
  (assumption ECO-CUR). Easy to re-express in EUR.
- *Is 44.6 USD/MWh good?* The Dutch regulated heat price cap for 2026 is ≈ 138 USD/MWh
  (ACM, in RC-10); the C0 gas counterfactual lands at 137. We are at a third of that.

---

## Slide 2 — Problem, design basis, four hard facts

**Claim.** The design basis is fixed by the organisers and loaded from one file,
`config/design_basis.yaml` (DB-01…DB-19); it is never re-typed in code.

**The basis.** Reservoir mid-depth 2 500 m, thickness 100 m, gradient 30 °C/km, surface 12 °C
⇒ reservoir 87 °C; pressure 250 bar(g); permeability 200 mD; brine 1 030 kg/m³, 0.40 mPa·s;
gas–liquid ratio 0.8 Nm³/m³ (80 % CH₄ / 20 % CO₂); 1–3 doublets; 50–300 m³/h per active doublet;
brine cooling ≤ 40 K; pump efficiency 70 %; injection = production; heating supply 100 °C,
cooling supply 20 °C; demand CSVs: heat peak 30 MW on 28 Jan, 111.67 GWh/yr; cooling peak
10 MW on 19 Jul, 23.74 GWh/yr over 219 days.

**The four hard facts** (spec §3.1; each one shapes the design):
1. *The brine is 85 °C at the wellhead* (87 °C reservoir minus wellbore losses, TL-05) but the
   network needs 100 °C ⇒ a plate HEX alone cannot do it; a heat pump (or a deeper well, C4)
   must lift the last 15–20 K.
2. *Cooling at 20 °C is mostly free* in the Dutch climate: with a cooling tower, free cooling is
   available whenever wet-bulb + approaches ≤ 20 °C, i.e. ≈ 13.5 °C wet-bulb (RC-07), which is
   most of the year except high summer.
3. *Each doublet is out 35 days a year* (DB-30, our reading of "330 daily steps"): the plant
   must meet demand on all 365 days, so outages are scheduled by the optimiser into the
   lowest-load windows and never overlap (DR-06).
4. *The associated gas is a fuel*: 0.8 Nm³ per m³ of brine ⇒ ≈ 1.9 MW of fuel per doublet at
   300 m³/h (DB-27). Degas it and burn it in a CHP (DR-07) rather than vent it (never allowed).

**Numbers.** Load-duration figure `fig_load_duration.png`; energy balance
`fig_energy_balance.png`: 111.7 GWh heat, 23.7 GWh cooling.

**Q&A.** *What did you assume that the brief did not give?* Everything is in
`config/assumptions.yaml` with id, value, justification, source and sensitivity — e.g. DB-31
return temperature 55 °C, DB-33 brine c_p 4.1 kJ/kg·K, grid CO₂ 0.3 kg/kWh (DB-37). Each DR cites
the ids it used.

---

## Slide 3 — Architecture

**Claim.** "Agents propose, tools compute, the verifier accepts, the human approves."

**The layers (README diagram).** User (CLI) → workflow runner (deterministic phases P0–P10,
SQLite event log, per-phase budgets, pause/resume; adapted from Plant Debugger's incident runner)
→ agents (Pydantic AI role agents on a local Ollama model: Lead Engineer, Requirements,
Subsurface, System Architect, Model Builder, Economics, Reviewer, Reporter; structured outputs,
≤ 2 retries, then a deterministic PolicyEngine fallback) → tools (TL-01…TL-23, deterministic
unit-tested Python) → engine (GEMINI models + simulator, with 7 new and 4 extended models)
→ verification (DesignGuard, DesignVerifier with 46 checks in 4 layers, Reviewer agent, human
approval) → evidence (events, tool results TR-*.json, llm_calls.jsonl, token_summary.json,
Decision Records, figures).

**The phases.** P0 intake/scope check · P1 design basis + missing inputs (asks the user) ·
P2 subsurface (reservoir, PI, doublet operating point, breakthrough) · P3 candidates +
DesignGuard · P4 model-gap analysis (Model Builder) · P5 screening (fast year + economics for
every candidate) · P6 optimisation (TL-13) · P7 full-year simulation · P8 uncertainty (TL-15)
and the DR-11 backup decision · P9 verification (DesignVerifier + Reviewer agent) · P10 report +
human approval.

**Why this shape.** Open-weight models are unreliable at free-form tool calling (spec §5.4).
So the order of phases is code, the required tools per phase are code, budgets are code; the
model only decides *within* a phase — which option, which lever — and every number it sees was
computed by a tool. Four golden rules: the LLM never computes a number; no invented coefficients
(every value traces to a reference card or a register entry); hard limits enforced in code;
only the DesignVerifier can mark ACCEPTED.

**Q&A.**
- *What stops the model from hallucinating a size?* Decisions are typed Pydantic objects
  validated against the allowed options; a rationale may not even contain digits (AG-02); a
  choice must name its decisive criterion and an evidence id that exists.
- *What if the model fails?* After 2 invalid outputs the PolicyEngine (a deterministic
  rule-based chooser) takes that decision and the event is logged as a fallback; the final run
  had 2 fallbacks in 34 calls [§8].

---

## Slide 4 — The agent at work

**Three snippets.**
(a) *S2* — the request "design a geothermal heating system for 30 MW" with no CSVs: the run
stops in P1 with an INPUT_REQUEST listing the missing inputs and offering the repository
defaults; nothing is assumed before the answer (eval S2 = pass).
(b) *S7* — the free-cooling model was removed from the registry; the Model Builder detected the
gap in P4, wrote `FreeCoolingRc07` from reference card RC-07 in a sandbox, ran its own tests:
draft 1 failed 1 of 2 tests, draft 2 passed both, and the class was registered at runtime
[§12]. ~70 s per draft, ~3 k output tokens each, on the 12B model.
(c) *DR-03* — a Decision Record: options, decisive criterion ("lowest LCOH + LCOC among
guard-passed candidates without unmet demand"), evidence ids (TR-012, TR-013), chosen option.

**Why it matters.** This is the "generated and referenced from textbooks" requirement
(spec §7.1): new models are written from cards that cite the book and page, and the agent can
extend the digital twin itself — with tests as the gate.

**Q&A.** *Did a human edit the generated model?* No; the sandbox output is committed unchanged
under `docs/results/s7_demo/`. *Is that code used in the final design?* No — S7 is a separate
scenario; the submitted design uses the hand-written WP-02 models.

---

## Slide 5 — GEMINI contributions

**Claim.** Seven new model packages — absorption chiller (two methods), electric chiller, heat
rejection (wet tower / dry cooler), free cooling, electric boiler, thermal storage, brine PVT —
and four extended: HTHP (closed-form solver next to GEMINI's Nelder–Mead), separator (gas
composition, fuel power, inherent CO₂), well pressure drop with brine PVT, CHP (electrical
fraction parameter). Each has `REFERENCE.md` (the card), a textbook-example test and a `.rst`
documentation page; registry entries; 1 400+ tests in CI.

**Licensing.** Modified GEMINI files carry the Apache-2.0 "Modified by" notice (SUB-21);
Plant Debugger code we adapted (runner, guard, verifier, store, logging, eval scripts) is
credited file by file with its commit (SUB-20, `NOTICE`).

**Q&A.** *Did you change GEMINI's behaviour?* No: every change is behind a parameter whose
default keeps the 91 original tests green and the doublet example output byte-identical.

---

## Slide 6 — Alternatives and why they lost

**Table [§2]** (LCOH / LCOC USD/MWh, CO₂ cut vs C0, CAPEX MUSD):
C0 137.0 / 36.0, –, 13.7 · C1 54.2 / 23.6, −47 %, 28.3 · C2 50.3 / 26.3, −75 %, 34.3 ·
C3 50.0 / 40.4, −75 %, 37.0 · C4 29.4 / 38.7, −83 %, 34.7 **excluded (PH-19)** ·
C5 104.5 / 26.1, −46 %, 56.4 · C6 50.3 / 26.3 · C7 62.1 / 27.3 · C8 57.0 / 26.3, 41.4.
After optimisation: C2-opt 42.5 / 26.3, 27.9 MUSD; C1-opt 53.8 / 23.5; C6-opt 42.6 / 26.3;
**C2-opt-robust 44.6 / 26.3, 30.1 MUSD** (selected).

**The stories.**
- *C4 (3.2 km, no heat pump)* is cheapest on heat (29.4) but **excluded by the verifier**: at
  that depth the ESP intake pressure cannot stay above the brine bubble point (PH-19 NPSH /
  PH-18 release pressure), so the pump would cavitate / the gas would break out. Depths 3 000,
  3 200 and 3 500 m are all excluded on that basis [§3 "Depths excluded"].
- *C3 (absorption cooling driven by brine)* costs 40.4 USD/MWh of cooling vs 26.3: single-stage
  LiBr at ≈ 85 °C drive has a COP ≈ 0.6–0.7 (RC-05) and needs a lot of expensive drive heat.
- *C5 (1.5 km, 57 °C brine)* needs ≈ 43 K of heat-pump lift ⇒ 104.5 USD/MWh and 56 MUSD.
- *C1 (three doublets, no heat pump)* uses the gas boiler/CHP for the lift ⇒ only −47 % CO₂.
- *C6 (storage)* equals C2: the summer surplus is small with two doublets, so storage adds cost
  without benefit. *C7 (pressurised loop)* loses the CHP fuel ⇒ 62.1. *C8 (spare doublet)* pays
  for an idle well.
- *C2 wins on LCOH + LCOC* (the optimiser's objective, OPT-OBJ); the optimiser then trims flow
  to 296 m³/h and right-sizes HTHP/boiler/chillers.

**Q&A.** *Why is "LCOH + LCOC" the objective rather than LCOH alone?* The brief scores heating
and cooling together; summing both levelised costs weights them by delivered energy.

---

## Slide 7 — Final design

**Sizing [§3].** 2 doublets; 2 500 m; 296 m³/h each; gas handling = degas and use (DR-07);
heating return 55 °C (DB-31), cooling return 26 °C (DB-32); HTHP 4.10 MW heating; peak boiler
10.90 MW (robust, DR-11; the base-year optimum was 5.0 MW); electric chillers 8.20 MW; installed
heating 36.3 MW, installed cooling 16.8 MW.

**Wells and pumps [§3].** Wellhead temperature 85.2 °C at design flow; ESP at 58.1 Hz,
0.446 MW electric per doublet; ESP intake margin 0.86 bar above 1.2 × bubble point (the
Henry's-law bubble point ≈ 46 bar(a), RC-01 item 9); injector wellhead 26.0 bar(g), injection
pump 0.339 MW per doublet; doublet capacity factors 0.52 / 0.47.

**Longevity.** Thermal breakthrough 34.4 yr at design flow (≥ 30 yr required, ENG-06), 66.1 yr
at the annual-mean flow; wells 1 200 m apart (SUB-SPACING ≥ 1.2 km; the ≤ 1 bar interference
minimum is 1 131 m).

**Process order** (CFG-LAYOUT): producer → degasser → filter → titanium plate HEX (brine
85 → ≈ 58 °C) → HTHP evaporator (brine → ≈ 45 °C) → injector. District side: return 55 °C →
HEX → CHP heat → HTHP condenser to 100 °C → peak boiler when needed.

**Q&A.** *Why titanium plates?* DR-09: saline brine with CO₂ ⇒ chloride corrosion; titanium
(+50 % cost, RC-10) over stainless. *Scaling?* TL-22 returns "insufficient data" (no brine
chemistry in the brief) and lists mitigations: calcite inhibitor, no copper, oxygen exclusion,
titanium plates.

---

## Slide 8 — Annual performance

**Heat by source** (annual summary of the full-year run, MWh): plate HEX 36 515 + 33 233 =
69.7 GWh; HTHP 15 091 + 12 252 = 27.3 GWh; CHP 10.5 GWh; peak boiler 4.1 GWh; total 111.7 GWh.
**Cooling:** free cooling 20.6 GWh, electric chillers 3.2 GWh (of 23.7). **Electricity**
consumed 13.2 GWh (ESPs, injection pumps, HTHP, chillers), generated by the CHP 5.3 GWh; gas fuel
20.9 GWh (associated gas first, grid gas for the boiler); CO₂ 7 360 t/yr.

**Dispatch order** (SIM-03): cooling — free cooling → (absorption if present) → electric;
heating — HEX → CHP → HTHP → peak boiler, with brine ΔT ≤ 40 K enforced daily.

**Outages.** Two 35-day windows starting on days 119 and 238 (DR-06: lowest-load windows,
non-overlapping), so demand is met on all 365 days.

**Q&A.** *Why does the boiler run at all?* The two outage windows and the January peak
(30 MW vs ≈ 14 MW geothermal + HTHP per doublet) need top-up; 4.1 GWh is 3.6 % of heat.

---

## Slide 9 — Verification and uncertainty

**Verifier [§4].** Four layers — syntactic (project JSON loads, schemas, unit suffixes),
semantic (unique names, valid connections, splitter fractions, producer/injector pairing),
physical (energy balance < 1 %, brine cooling 0–40 K, 50–300 m³/h, Q_inj = Q_prod, 100 °C /
20 °C met, ESP intake above bubble point, NPSH, HEX approach, heat-pump COP ≤ η·Carnot, …) and
performance/design (breakthrough ≥ 30 yr, interference ≤ 1 bar, materials, scaling). 46 checks,
all pass. Tightest margins: PH-02 brine cooling exactly 40 K (margin 0 — the optimiser uses the
full allowed ΔT) and PD-04 interference exactly 1 bar (wells at the minimum spacing). Both are
limits the brief fixes, hit deliberately.

**Monte Carlo [§5].** 200 samples (permeability, gradient, skin, demand ±10 %, return
temperature, climate, COP/absorption ±15 %, cost placeholders ±30 %): LCOH P10/P50/P90 =
33.8 / 47.3 / 66.1 USD/MWh; LCOC 20.9 / 26.7 / 34.5; CAPEX 23.6 / 27.0 / 30.5 MUSD.
With the base-year 5.0 MW boiler, demand is met in only **46 %** of futures (P90 unmet heating
71 days); DR-11 installs the P90 backup (10.9 MW boiler) ⇒ **90 %**, for +2.05 USD/MWh LCOH and
+2.21 MUSD CAPEX.

**Sensitivity cases [§5].** Return temperature is the big lever: DB-31 = 45 °C ⇒ LCOH 36.5;
65 °C ⇒ 54.7 with 2 605 MWh unmet; 75 °C ⇒ 74.9. Warm climate case ⇒ LCOC 32.9. DB-30
interpretation B ⇒ LCOH 43.4, LCOC 30.4. CHP electrical fraction 0.5 ⇒ 41.4. Gas CO₂ factor
51.53 vs 56.6 kg/GJ ⇒ no cost change.

**Q&A.** *Why install a backup that is idle most years?* It is the difference between meeting
demand in half the plausible futures and nine in ten, for ≈ 5 % on LCOH; the brief's first
criterion is meeting demand. The agent made this decision (DR-11) from TL-15 evidence, under a
register rule (OPT-ROBUST-BACKUP) a human wrote.

---

## Slide 10 — Economics, CO₂, exergy, tokens, novelty

**Tokens of the final run [§8].** 34 LLM calls, 44 requests (10 retries), 2 policy fallbacks
(P7 and P9), 93 699 input + 5 602 output = 99 301 tokens, 299 s of model latency (≈ 14.5 min
wall for the whole run including the tools), on gemma4:12b-it-q4_K_M via Ollama on a MacBook
Pro M3 36 GB. Zero stub tools.

**S10 [§9].** Same task with the full event history in every prompt ("naive") vs the
compacted context (last 12 events, tool-result summaries, MEM-06): 173 909 → 108 937 tokens,
**−37 %**; the reporter and model-builder roles save the most.

**S11 [§10].** Five models on the same task: gemma4:12b completed (ACCEPTED, 97 % valid calls,
1 fallback, 88 k tokens, 373 s); qwen3.6:27b completed (82 %, 7 fallbacks, 127 k, 673 s);
qwen3.6:35b-a3b, gemma4:26b-a4b, qwen3.5:9b escalated on phase budgets. A second **full**
pipeline with qwen3.6:27b reached the same design and verdict (`runs/final_qwen27b/`).
A 12B model on a laptop is enough *because* the workflow does the heavy lifting.

**Reproducibility.** `PYTHONPATH=src python -m gemini_agent.cli run --engine policy` reproduces
the design offline, no GPU, same selected design and verdict (`runs/final_policy/`).

**Novelty.** (1) A deterministic workflow where the model only chooses among tool-computed
options; (2) a verifier with sole authority to accept, with margins reported per check;
(3) a Model Builder that extends the digital twin from reference cards with tests as the gate;
(4) after submission: what-if on a finished run, an interactive review loop, a web view, and a
voice front-end — all on the same bounded decision schema.

**Q&A.** *What are the limitations?* README "Limitations": RC-05 chiller cost functions are
1986 values escalated by CEPCI (tagged VERIFY); three eval checks are still "pending"
(not automated); the HEX model uses one ρ/c_p for both sides; the RC-09 pipe-loss figure could
not be reproduced from the card; alternative Ollama tags are unverified.

---

## Appendix pointers
A1 transcripts `runs/final/eval/gemma4_12b-it-q4_K_M/` · A2 tokens `REPORT.md` §8–9 ·
A3 `config/assumptions.yaml` · A4 `runs/final/final_llm/tool_results/` · A5 DR-01…DR-11
`REPORT.md` §7 · A6 reference cards · A7 `docs/results/s7_demo/` · A8 README limitations and
API placeholders · settings study `docs/results/SETTINGS_STUDY.md` (thinking on: 68 % validity,
11 fallbacks, 1 006 s; 32 K context: identical to 64 K).

---

## Slide 11 — Talking to a finished run (added after review)

**Claim.** Three front-ends share one bounded schema: `cli whatif --set LEVER=value`, the web
Design Commander (`cli serve`: runs, phases, tokens, records, answer and approve; no lever
controls) and push-to-talk voice (`cli talk`: Enter to record, Enter to stop, Whisper
transcribes, the assistant echoes "I heard …" and asks "apply? [Y/n]").

**Sentence shape.** One lever keyword, one direction or one number in digits, nothing else:
"change the return temperature to 65", "cooling return 26", "one more doublet", "the boiler is
too big", "drill deeper", "flow 200 per doublet", "try C3 instead". Decisions are single words:
yes / no at the approval; adopt / another / stop after a what-if.

**Gotchas.** Digits not words; no unit conversion; one lever per sentence; Enter at the final
approval records "pending"; the model never computes, it only maps words to a lever id.

Full detail, including the sentences that fail and why: `docs/results/DECK_QA.md` §3.

## Slide 12 — Appendix
Unchanged from the appendix pointers above; row A10 now points to `SPEAKER_NOTES.md` and
`DECK_QA.md` (glossary and questions).
