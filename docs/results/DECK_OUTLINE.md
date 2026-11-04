# GeoAgent — 10-slide deck outline, appendix list and ≤ 5-min video script

Scoring map (challenge brief): **system design 50 %**, agent/LLM approach, reproducibility and
open-source contribution. Every number on a slide is read from the file named next to it; the
report pack `docs/results/REPORT.md` cites the source of each table. Figures: `docs/results/final_llm/`.

## Slides

### 1. Answer first — *system design*
- **Show:** `docs/results/final_llm/fig_candidates.png` (right half) + a 4-number strip from
  `docs/results/REPORT.md` §1: LCOH 44.6 / LCOC 26.3 USD/MWh, CO₂ −72 % vs C0, 0 unmet days,
  P(demand met) 0.90.
- Speaker: "Two doublets at 2.5 km, plate HEX plus a high-temperature heat pump, CHP on the
  associated gas, a peak boiler, free cooling with electric chillers. Verified by 46 checks,
  accepted by the verifier, approved by a human."
- "It meets every day of heating at 100 °C and cooling at 20 °C in 2027 at 44.6 and 26.3 USD per
  MWh, cutting CO₂ by 72 % against gas boilers and electric chillers."
- "Everything you see was computed by deterministic tools; the language model only chose."

### 2. Problem, design basis and the four hard facts — *system design*
- **Show:** table from `config/design_basis.yaml` (DB-01…DB-19: 2 500 m, 87 °C, 200 mD, 100 °C /
  20 °C supply, 1–3 doublets, 50–300 m³/h, ≤ 40 K brine cooling) + `fig_load_duration.png`.
- "The brine is 85 °C at the wellhead: 100 °C needs a lift. Cooling at 20 °C is mostly free
  cooling in the Dutch climate. Each doublet is out 35 days a year. The associated gas is a fuel."
- "The demand CSVs give 111.7 GWh of heat and 23.7 GWh of cooling (`fig_energy_balance.png`)."

### 3. Architecture — *agent/LLM*
- **Show:** the box diagram from `README.md` ("Design principle").
- "Agents propose, tools compute, the verifier accepts, the human approves. Phases P0–P10 are
  deterministic code; at each phase an open-weight model chooses among bounded options."
- "Every tool is unit-tested Python with a reference card; every assumption is a register entry
  with a source; every LLM call is logged with its tokens."

### 4. The agent at work — *agent/LLM*
- **Show:** three transcript snippets: (a) S2 asks for DB-30 before designing
  (`runs/final/eval/gemma4_12b-it-q4_K_M/S2/events.jsonl`, the INPUT_REQUEST), (b) S7 gap
  closed: `docs/results/s7_demo/README.md` result table (2 drafts, draft 1 fails 1 of 2 tests,
  draft 2 passes), (c) a Decision Record, `runs/final/final_llm/decision_records/DR-03.md`.
- "When the registry lacks a free-cooling model, the Model Builder writes it from reference card
  RC-07, runs its own tests in a sandbox, and registers it at runtime after the second draft."
- "Each decision names its decisive criterion and evidence ids; a validator rejects any choice
  that does not."

### 5. GEMINI contributions — *open source*
- **Show:** list from `NOTICE` §1 and `README.md` repository map: 7 new model packages
  (absorption chiller, electric chiller, heat rejection, free cooling, electric boiler, thermal
  storage, brine PVT) + 4 extended (HTHP closed form, separator gas, well pressure drop with PVT,
  CHP), registry entries, `.rst` pages, 1 300+ tests.
- "Each new model has a reference card, a textbook-example test and a documentation page."
- "Modified GEMINI files carry the Apache-2.0 notice; Plant Debugger code we adapted is credited
  file by file."

### 6. Alternatives C0–C8 and why they lost — *system design*
- **Show:** `fig_candidates.png` + the candidate table of `docs/results/REPORT.md` §2.
- "C4, drilling to 3.2 km without a heat pump, is cheapest on paper but is excluded by the
  verifier's NPSH check (PH-19) at that depth. C3 absorption cooling costs 40 USD/MWh of cooling.
  C5 at 1.5 km costs 104 USD/MWh of heat: too much heat pump."
- "C2 wins on LCOH + LCOC; the optimiser then trims the flow to 296 m³/h and sizes the backups."

### 7. Final design — *system design*
- **Show:** process diagram (draw from `config/` CFG-LAYOUT: degasser → filter → Ti plate HEX →
  HTHP evaporator → injector; district: HEX → CHP → HTHP → boiler) + the sizing table of
  `docs/results/REPORT.md` §3.
- "Two doublets, 296 m³/h each, ESP at 58 Hz, 0.45 MW electric per doublet; HTHP 4.1 MW, CHP on
  degassed gas, peak boiler 10.9 MW, chillers 8.2 MW, free cooling first."
- "Thermal breakthrough 34 years at design flow, 66 at the annual-mean flow; wells 1 200 m apart."

### 8. Annual performance — *system design*
- **Show:** `fig_daily_heat.png`, `fig_daily_cooling.png`, `fig_energy_balance.png` (numbers:
  `runs/final/final_llm/annual_C2-opt-robust_full_summary.json` → `kpis.heat_by_source_mwh`,
  `cool_by_source_mwh`).
- "Geothermal heat covers 69.7 of 111.7 GWh directly, the heat pump 27.3, CHP 10.5, the boiler
  4.1; free cooling delivers 20.6 of 23.7 GWh."
- "The two 35-day outages sit in the lowest-load windows (DR-06), never overlapping."

### 9. Verification and uncertainty — *system design*
- **Show:** verification table excerpt (`REPORT.md` §4: 46 checks, tightest margins PH-02 brine
  cooling 40 K, PD-04 interference 1 bar) + `fig_uncertainty.png` + `fig_tornado.png`.
- "Four verification layers: syntax, semantics, physics, performance. The first design passed
  with 46 of 46; the tightest margins are the ones the brief fixes."
- "200 Monte Carlo samples: LCOH P10–P90 33.8–66.1 USD/MWh. With the base-year boiler the demand
  was met in 46 % of futures; DR-11 installs the P90 boiler and raises that to 90 % for
  +2 USD/MWh."

### 10. Economics, CO₂, exergy, tokens, novelty — *agent/LLM, reproducibility*
- **Show:** `fig_tokens_by_phase.png` + the S11 table of `REPORT.md` §10 + the S10 line
  (173 909 → 108 937 tokens, −37 %).
- "The final run used 34 calls and 99 k tokens on a 12-billion-parameter model on a laptop, with
  two policy fallbacks and zero stub tools. Compact history and tool-result summaries save 37 %."
- "Reproduce it with one command on the policy engine, no GPU: same design, same records."
- "Novelty: a deterministic workflow where the model only chooses, a verifier with sole authority
  to accept, and a Model Builder that extends the digital twin from reference cards."

## Appendix (slides or attached files)
- A1 Transcripts S1–S11: `runs/final/eval/gemma4_12b-it-q4_K_M/EVAL_REPORT.md` and the per-scenario
  `events.jsonl`, `llm_calls.jsonl`.
- A2 Token table: `docs/results/REPORT.md` §8–§9 (`runs/final/final_llm/token_summary.json`).
- A3 Assumptions register: `config/assumptions.yaml` (ids cited in every Decision Record).
- A4 Calculations: `runs/final/final_llm/tool_results/TR-001…TR-023.json`, annual CSVs,
  `optimisation_C2.json`, `uncertainty_C2-opt.json`, `sensitivity_C2-opt.json`.
- A5 Decision Records DR-01…DR-11: `docs/results/REPORT.md` §7.
- A6 References: `data/knowledge/reference_cards/RC-01…RC-10` and spec §19.
- A7 S7 evidence: `docs/results/s7_demo/`.
- A8 Limitations and API placeholders: `README.md`.

## Video script (≤ 5:00)

| Time | Scene | Say |
|---|---|---|
| 0:00–0:30 | Slide 1 | "This is GeoAgent. It designed a deep-geothermal heating and cooling system for the challenge: two doublets, a heat pump, CHP, free cooling — verified, accepted, approved. 44.6 and 26.3 dollars per megawatt-hour, 72 % less CO₂, no unmet day." |
| 0:30–1:15 | Slide 2 | "The brief fixes the reservoir, the loads and the limits. Four facts drive the design: 85 °C brine needs a lift to 100 °C; Dutch wet-bulb temperatures make most cooling free; each doublet is down 35 days a year; the associated gas is a fuel." |
| 1:15–2:00 | Slide 3 | "The workflow is deterministic code, P0 to P10. At every phase a local open-weight model chooses among options the tools computed. Only the verifier can accept a design, and a human approves it. Every call is logged with its tokens." |
| 2:00–3:15 | Slide 4, live clips | Clip 1: `cli run --engine llm --input-mode ask` pauses at the DB-30 question (S2 transcript). Clip 2: `python -m gemini_agent.agents.model_builder` output: "free_cooling: FreeCoolingRc07 registered at runtime after 2 draft(s); 2 tests passed". Clip 3: the P9 verifier table and the approval prompt answered `yes`. |
| 3:15–4:00 | Slides 6–8 | "Nine alternatives. Deeper wells fail the pump's NPSH check; absorption cooling is too expensive; shallow wells need too much heat pump. C2 wins, the optimiser trims the flow and sizes the backups. Here is the year, day by day." |
| 4:00–4:30 | Slide 9–10 | "200 Monte Carlo futures: the P90 boiler raises the probability of meeting demand from 46 to 90 % for two dollars per megawatt-hour. 34 LLM calls, 99 thousand tokens, on a laptop." |
| 4:30–5:00 | Slide 10, repo | "Everything is open source on top of GEMINI: seven new models with reference cards and tests, an agent layer, an evaluation suite. One command reproduces the design without a GPU." |
