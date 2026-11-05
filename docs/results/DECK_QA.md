# GeoAgent deck: glossary, questions and answers

Companion to the 12-slide deck and to `SPEAKER_NOTES.md`. Every statement below was checked
against the code, the configuration or the run files named in brackets. Numbers are from the
final run `runs/final/final_llm/` unless stated otherwise. Where the deck slide numbers are
quoted: 1 answer first, 2 design basis, 3 architecture, 4 the agent at work, 5 GEMINI
contributions, 6 alternatives, 7 final design, 8 annual performance, 9 verification and
uncertainty, 10 tokens and novelty, 11 talking to a finished run, 12 appendix.

Contents

1. Glossary of abbreviations and identifier families
2. How a user interacts with the application, and the gotchas
3. Voice: how to phrase a sentence, and the gotchas
4. The candidates on the slide-1 chart (C4-2d, C2-opt, C2-opt-robust, …)
5. What "−72 % CO₂ vs gas boilers + electric chillers" means, with the arithmetic
6. The load-duration chart on slide 2
7. The phases P0 to P10 in detail
8. What the policy fallback is
9. The 23 tools and the 12 reference cards
10. The DesignVerifier: 4 layers, 46 checks
11. The hard limits enforced in code
12. Decision Record DR-03 and why each option was rejected (plus DR-01 to DR-11)
13. Is internal pipe friction taken into account?
14. Ten tangible improvements over the original GEMINI application
15. The diagrams on slides 7 and 8
16. What free cooling entails
17. The costings behind the deck, with calculations
18. The uncertainty analysis on slide 9, with calculations

---

## 1. Glossary of abbreviations and identifier families

### Engineering and economics terms

| Term | Meaning |
|---|---|
| LCOH | Levelised cost of heat, USD per MWh of heat delivered over 30 years (RC-10, Gippsland equation 1). |
| LCOC | Levelised cost of cooling, USD per MWh of cooling delivered. |
| CAPEX / OPEX | Capital expenditure (one-off, million USD) / operating expenditure (per year). |
| HEX | Heat exchanger; in this project a titanium plate heat exchanger between brine and the district water. |
| HTHP | High-temperature heat pump: lifts heat from about 58 °C brine to the 100 °C supply. |
| CHP | Combined heat and power: a gas engine burning the associated gas, producing electricity and heat. |
| ESP | Electric submersible pump, the pump inside the production well. |
| NPSH (NPSH_A, NPSH_R) | Net positive suction head, available and required, the margin that keeps the pump from cavitating. |
| COP | Coefficient of performance: heat (or cold) delivered per unit of electricity. |
| P10 / P50 / P90 | The 10th, 50th and 90th percentiles of a Monte Carlo result (not to be confused with phase P10). |
| MC | Monte Carlo: repeated simulation with randomly sampled inputs (200 samples here). |
| PI / II | Productivity index / injectivity index, m³/h per bar of drawdown. |
| WHP | Wellhead pressure. |
| LHV / GCV | Lower heating value / gross calorific value of a fuel. |
| CEPCI | Chemical Engineering Plant Cost Index, used to escalate old equipment prices to 2023. |
| CRF | Capital recovery factor, the annuity factor that converts a capital sum into equal yearly payments. |
| t_b | Thermal breakthrough time: the years before the cold injected front reaches the producer. |
| T₀ | Dead-state temperature for exergy (the daily ambient dry-bulb here). |
| RI / RS | Ryznar index (calcite scaling) / relative saturation of gypsum. |
| p_b | Bubble point: the pressure below which gas breaks out of the brine. |
| DE | Differential evolution, the optimiser's search algorithm. |
| ATES / HT-ATES | (High-temperature) aquifer thermal energy storage, concept C6. |
| VFD | Variable-frequency drive, which sets the ESP speed. |
| GLR | Gas-to-liquid ratio (0.8 Nm³ of gas per m³ of brine). |
| OAT | One-at-a-time sensitivity (the tornado chart). |
| N-1 | Redundancy with one spare unit, concept C8. |
| GEMINI | TNO's open-source geothermal digital twin (not Google's model). |
| Ollama | The local server that runs open-weight language models on the laptop. |
| Pydantic AI | The Python agent framework used for the role agents, with typed outputs. |

### Identifier families

| Family | What it is | Where defined |
|---|---|---|
| DB-01 … DB-19 | Fixed design-basis inputs from the brief (depth, gradient, flows, limits, demand). | `config/design_basis.yaml`, spec §2.1 |
| DB-20 … DB-29 | Derived quantities (reservoir temperature, gas fuel power, …). | spec §2.2 |
| DB-30 … DB-40 | Ambiguities and defaults: DB-30 outage reading, DB-31 heating return 55 °C, DB-32 cooling return 26 °C, DB-33 brine c_p, DB-34 climate, DB-36 no venting, DB-37 grid CO₂ 0.3 kg/kWh. | `config/assumptions.yaml`, spec §2.4 |
| TL-01 … TL-23 | The deterministic tools (section 9). | spec §6, `src/gemini_agent/tools/` |
| RC-01 … RC-12 | Reference cards, the only engineering source for equations and values (section 9). | `data/knowledge/reference_cards/` |
| DR-01 … DR-11 (DR-12, DR-W n) | Decision Records written by the agents (DR-12 user review; DR-W what-if records). | `runs/<id>/decision_records/` |
| TR-001 … TR-023 | Tool results, numbered in the order the tools ran in a run. | `runs/<id>/tool_results/` |
| C0 … C8 | Candidate plant concepts (section 4). | spec §3.4, TL-09 |
| P0 … P10 | Workflow phases (section 7). | `src/gemini_agent/runner/phases.py` |
| SY / SE / PH / PD / EX | Verifier check layers: syntactic, semantic, physical, performance and design, exergy (section 10). | `src/gemini_agent/verify/design_verifier.py` |
| ENG, AG, SIM, OPT, ECO, UQ, VR, MEM (numbered) | Requirement ids in the design spec: engineering, agent, simulation, optimisation, economics, uncertainty, verification, memory. | `docs/hackathon/GEOAGENT_DESIGN_SPEC.md` |
| ECO-CUR, ECO-05, OPT-ROBUST-BACKUP, UQ-MC, CFG-LAYOUT, COOL-FC, VER-HP-ETA, SUB-WELL, … (named) | Entries in the assumptions register: every value the brief did not give, with justification, source and sensitivity. | `config/assumptions.yaml` |
| SUB-01 … SUB-24 | Submission checks run by `scripts/validate_submission.py`. | `src/gemini_agent/eval/submission.py` |
| S1 … S11 | Evaluation scenarios (section 2). | `evals/scenarios.yaml` |
| WP-00 … WP-15 | Work packages of the build (internal planning notes, not in the public repository). | commit history |
| L-1, L-2, L-3 | The laptop LLM runs: model selection, the final run, the second-model run. | README, `runs/final*` |
| [P] [D] [L] [W] [A] | Provenance tags: provided, derived, literature, web, assumption. | spec §0 |

---

## 2. How a user interacts with the application, and the gotchas

The entry point is one command with sub-commands (`src/gemini_agent/cli.py`):

```bash
PYTHONPATH=src python -m gemini_agent.cli <command> [flags]
```

| Command | What it does |
|---|---|
| `run` | Runs the design workflow P0 to P10. Flags: `--engine` (policy or llm), `--model <ollama tag>`, `--budget` (full, quick or smoke), `--input-mode` (ask, assume or pause), `--review`, `--speak`, `--runs-dir`, `--run-id`, `--brief`, `--prompt`. |
| `resume --run-id X --answer "…"` | Answers a paused question (a missing input, or the final approval) and continues. |
| `validate-config` | Loads every `config/*.yaml` into its schema and prints ok or FAIL. |
| `whatif --run-id X --set LEVER=value` | Changes one or more levers on a finished run, reruns the year, economics and verifier, prints a before and after table, writes a DR-W record; never modifies the run's own files. |
| `serve` | Starts the web Design Commander on localhost: runs, phase timeline, tools, tokens, Decision Records, verifier report, figures, and the Answer and Approve buttons. |
| `talk` | Push-to-talk voice front-end (section 3). |

A typical session on the laptop:

1. Start the Ollama server with the required environment variables (gotcha 1 below).
2. `run --engine llm --model gemma4:12b-it-q4_K_M --budget full --runs-dir runs/final --run-id final_llm --input-mode ask`.
3. The run prints each phase. In P1 it asks the open questions: DB-30 (outage reading A or B), DB-31 (heating return, default 55), DB-32 (cooling return, default 26). Enter keeps the default.
4. At P10 it prints the verifier summary and asks "Approve the recommended design? yes/no". Type `yes`.
5. Outputs land in `runs/final/final_llm/`: `run_summary.json`, `events.jsonl`, `llm_calls.jsonl`, `token_summary.json`, `decision_records/`, `tool_results/`, `figures/`, `report_draft.md`, annual CSVs.
6. `whatif`, `serve` or `talk --run-id final_llm` to explore the finished run.

Input modes: `ask` (terminal questions, the default when a terminal is attached), `assume` (records the register default as a labelled assumption, the default when input is piped), `pause` (saves the run and exits with code 3; answer later with `resume`). Exit codes: 0 completed, 1 config error, 2 usage error, 3 paused, 4 escalated, 5 refused, 6 the user rejected the design.

Gotchas, each one met during the build:

1. **Ollama settings are server settings.** `OLLAMA_CONTEXT_LENGTH=65536` and the other variables must be in the environment of `ollama serve`, not of the client shell; the desktop app starts its own server without them. The OpenAI-compatible endpoint ignores per-request context size, so a too-small server context silently truncates prompts. The CLI prints a reminder but cannot check it.
2. **Enter at the final approval means "pending".** The approval question has the default `pending`; an empty answer is recorded as an assumption, the run completes with approval pending, and `resume` then refuses ("completed, not paused"). Type `yes` or `no`. This happened to the qwen3.6:27b run.
3. **Repeat `--runs-dir` and `--budget` on `resume`.** The printed resume hint omits `--runs-dir`; without it the run is "unknown". The budget is not stored either.
4. **Piped input does not answer questions.** `printf 'yes' | … run` falls back to assume mode because stdin is not a terminal.
5. **A run id cannot be reused.** Keep one run folder in `runs/final/`, or the submission validator stops with "several runs".
6. **`scripts/run_eval.py` rewrites `eval_results.json` every time.** Running a subset of scenarios overwrites the full S1 to S11 file. Run the whole suite, or copy the file first.
7. **Model Builder calls are long.** `config/llm.yaml` gives the coder 1 200 s and 8 192 output tokens; the first S7 attempts timed out at the SDK default before this was set.
8. **Choose a model that completes.** Of five models tried, gemma4:12b-it-q4_K_M (chosen) and qwen3.6:27b completed; the 35b-a3b, 26b-a4b and 9b models escalated on phase budgets. Thinking mode on costs 2.7 times the wall time and drops validity to 68 % because the reasoning consumes the output budget.
9. **The web view has no lever controls.** Levers are changed with `whatif` or by voice; the web page answers questions and approves. Its routes have no authentication, so bind it to localhost only.
10. **Resuming an LLM run needs the same model running**, or pass `--engine policy`.

The evaluation scenarios, so the names mean something: S1 full brief; S2 missing inputs (the agent must ask); S3 off-topic request (refuse); S4 five doublets at 500 m³/h (outside the limits); S5 120 °C supply (conflicts with the basis); S6 return temperature not given; S7 cooling model removed (Model Builder writes it); S8 permeability 50 mD; S9 inconsistent 150 °C; S10 naive versus compacted context tokens; S11 the same task on different models.

---

## 3. Voice: how to phrase a sentence, and the gotchas

Files: `src/gemini_agent/ui/voice.py` (capture, speech-to-text, answer normaliser), `ui/dialogue.py` (the what-if dialogue), `speech.py` (text-to-speech with macOS `say`), `agents/objections.py` (keyword lever mapper). Dependencies: `faster-whisper` and `sounddevice` from `requirements-voice.txt`.

How it works: press Enter to start recording, Enter to stop; Whisper (model `small`, CPU, English) transcribes in a few seconds; the assistant prints and speaks "I heard …" and asks "apply? [Y/n]". Each question has an expected form (a number, A or B, yes or no, keep or change). After P10, or at once with `talk --run-id <completed run>`, it asks "What would you like to change?", maps the sentence to one lever, runs the what-if, speaks a three-sentence before-to-after summary and asks "Adopt it, try another, or stop?" (up to 5 rounds). With `--typed` you type instead of speaking; with `--mute` nothing is spoken.

**Sentence structure that works: one lever keyword, one value or direction, nothing else.**

| Say | Understood as |
|---|---|
| "65" or "65 degrees" (to the return-temperature question) | 65 |
| "option B" / "I'd say interpretation A" (to DB-30) | B / A |
| "use the defaults" | default |
| "yes please" / "no, reject it" (at the approval) | yes / no |
| "change the return temperature to 65" | DB-31 = 65 |
| "cooling return 26" | DB-32 = 26 |
| "I'd prefer 3 doublets" / "one more doublet please" | doublets = 3 |
| "the boiler is too big" / "I want more backup capacity" | OPT-ROBUST-BACKUP off / on |
| "drill deeper" / "depth 3 km" | concept C4-2d (the screened candidate nearest that depth) |
| "try C3 instead" / "use absorption chillers" | concept C3-2d |
| "flow 200 per doublet" | design_flow_m3h = 200 |
| "use DB-30 interpretation B" | DB-30 = B |
| "let's accept it" / "try another" / "I'm done, stop" | adopt / another / stop |

The lever keywords the mapper looks for, in order: backup, boiler or chiller capacity with a direction word (too big, smaller, remove; or more, bigger, add); "return" or DB-31 or DB-32 with a number ("cool" selects DB-32); "flow" with a number; "doublet" with a digit or more, another, fewer, less; depth, deep, shallow (a number below 20 is read as km); DB-30, availability, interpretation with A or B; C1 to C8, absorption, storage.

**Sentences that fail, and why**

| Say | Result | Why |
|---|---|---|
| "sixty-five" | unclear | the policy normaliser reads digits only (the LLM normaliser can read number words, but never converts units) |
| "150 F" | refused | units are never converted; 150 is outside the DB-31 range 45 to 75 |
| "change the return temperature" | no lever | no value given |
| "the boiler" | no lever | no direction given |
| "make it cheaper" | no lever | no keyword matches; the levers are listed in the reply |
| "5 doublets" | refused by DesignGuard | DB-10 allows 1 to 3 |
| "yes" after "adopt it, try another, or stop?" | asked again | deliberately ambiguous; say adopt, another or stop |
| "adopt it and then stop" | asked again | two decisions in one sentence |
| "do not approve" | asked again | a negated yes-word is treated as unclear rather than as no; say "no" |
| "default" at the approval | asked again | the approval has no default |

Gotchas:

- **Digits, not words; Celsius and m³/h, not other units.** Nothing is converted.
- **Only one lever per sentence.**
- **Enter on "[Y/n]" means yes**; anything but n or no confirms.
- **Noise.** Whisper runs with voice-activity filtering, and every understood utterance is echoed and confirmed before it is applied, so a mis-hearing is caught at the confirmation, not after. Speak after the assistant has finished; there is no barge-in. English only.
- **Enter at the final approval records "pending"**, exactly as in the typed CLI.
- **A paused or escalated run cannot start a dialogue**; answer it with `resume` first. Adoption of a what-if is blocked on the submitted runs under `runs/final*`; copy the run first.
- **Three levers are shown but not adoptable**: design flow, the gas CO₂ factor and the CHP electrical share (they are register values or optimiser variables, not design choices).
- **The model never computes.** The language model only rewrites your words into a lever id and a value, or picks a decision word. The numbers in the spoken summary come from the what-if tool; DesignGuard and the verifier validate the result like any other design.

---

## 4. The candidates on the slide-1 chart

The chart `fig_candidates.png` ranks every candidate that reached economics by LCOH + LCOC (left, stacked blue and orange bars, the sum printed at the end) and shows the annual CO₂ (right). The ids have three parts: the concept (C0 to C8), the number of doublets (`-2d`, `-3d`, `-0d`), and a suffix for sized variants (`-opt`, `-opt-robust`).

| Id | Concept | What it is | LCOH / LCOC USD/MWh | CO₂ t/yr | Outcome |
|---|---|---|---|---|---|
| C0-0d | C0 | No geothermal: gas boilers for heat, electric chillers for cooling. The counterfactual all CO₂ cuts are measured against. | 137.0 / 36.0 | 26 332 | baseline only |
| C1-3d | C1 | Three doublets, plate HEX only; the lift from 85 to 100 °C is done by gas boiler and CHP. | 54.2 / 23.6 | 13 963 | gas does the lift, CO₂ cut only 47 % |
| C2-2d | C2 | Two doublets, plate HEX then HTHP in series, CHP on the gas, peak boiler, free cooling then electric chillers. | 50.3 / 26.3 | 6 522 | best LCOH + LCOC of the nine |
| C3-2d | C3 | C2 with absorption chillers driven by brine heat instead of electric chillers. | 50.0 / 40.4 | 6 621 | cooling 54 % dearer |
| C4-2d | C4 | Two doublets drilled to 3 200 m (108 °C brine), direct plate HEX, no heat pump. | 29.4 / 38.7 | 4 597 | excluded by the verifier (PH-19 NPSH) |
| C5-3d | C5 | Three shallow doublets at 1 500 m (57 °C brine) with large heat pumps. | 104.5 / 26.1 | 14 300 | too much heat-pump lift |
| C6-2d | C6 | C2 plus seasonal high-temperature aquifer storage. | 50.3 / 26.3 | 6 522 | identical to C2, storage adds nothing |
| C7-2d | C7 | C2 with a pressurised brine loop: no degassing, the gas is reinjected, no CHP. | 62.1 / 27.3 | 6 218 | loses the free fuel |
| C8-3d | C8 | C2 plus a spare third doublet (N-1 redundancy). | 57.0 / 26.3 | 6 522 | pays for an idle well |
| C1-opt, C2-opt, C6-opt | the three best families after the optimiser (TL-13) | Flow trimmed to 296 m³/h, heat pump, boiler and chillers right-sized. | 53.8 / 23.5, 42.5 / 26.3, 42.6 / 26.3 | 7 361 for C2-opt | C2-opt is cheapest |
| C2-opt-robust (selected) | C2-opt plus the P90 backup from DR-11 | Boiler 10.9 MW instead of 5.0 MW. | 44.6 / 26.3 | 7 360 | ACCEPTED, approved |

Two points the chart raises:

- **Why C4 is at the top of the chart but not selected.** The ranking is by cost, and C4 is cheapest at 68 USD/MWh combined. It is excluded because at 3 200 m the ESP intake pressure cannot stay above 1.2 times the brine bubble point, so gas would break out and the pump would cavitate (checks PH-18 and PH-19). The verifier, not the model, removed it.
- **Why C2-opt has more CO₂ than C2-2d (7 361 against 6 522 t/yr).** The optimiser trimmed the flow from 300 to 296 m³/h per doublet and the boiler to the base-year need, which shifts a little energy from the HEX to the heat pump and boiler. Cost went down 16 %; CO₂ went up 13 % but is still 72 % below C0.

---

## 5. What "−72 % CO₂ vs gas boilers + electric chillers" means

It is the annual CO₂ of the selected design compared with C0, the plant that would meet exactly the same 111.7 GWh of heat and 23.7 GWh of cooling with gas boilers and electric chillers only. Both are computed by the same tool (`tools/economics.py`, register entry ECO-05) with the same factors:

| Factor | Value | Source |
|---|---|---|
| Grid gas | 56.6 kg CO₂ per GJ of fuel (LHV) = 0.2038 t/MWh | ECO-CO2-GAS, RC-08 |
| Associated gas | 68.56 kg/GJ = 0.2468 t/MWh (includes the 20 % CO₂ already in the gas) | DB-28, RC-08 |
| Grid electricity | 0.3 kg/kWh = 0.3 t/MWh, applied to bought electricity only | DB-37 |

Selected design (from `annual_C2-opt-robust_full_summary.json`):

```
grid gas in the boiler:      4 523.7 MWh × 0.2038 =   921.8 t
associated gas in the CHP:  16 416.2 MWh × 0.2468 = 4 051.7 t
bought electricity:  (13 207.7 − 5 253.2) = 7 954.5 MWh × 0.3 = 2 386.4 t
total                                                 7 359.8 t/yr   (reported 7 360)
```

C0 (from TR-012 and TR-013): heat 111 667.7 MWh at boiler efficiency 0.9 = 124 075 MWh of gas, cooling by chillers using 3 502 MWh of electricity.

```
gas:          124 075.2 MWh × 0.2038 = 25 281.6 t
electricity:    3 502.0 MWh × 0.3    =  1 050.6 t
total                                  26 332.2 t/yr
```

Reduction: (26 332 − 7 360) / 26 332 = 0.7205, so 72 %.

What the figure does and does not include: it counts the gas burnt (grid and associated) and the electricity bought from the grid. The CHP's own electricity offsets purchases day by day, which is why only 7 954 MWh are bought; there is no export credit because there is no surplus. The CO₂ already in the associated gas is counted when the gas is burnt. Drilling and construction emissions are not included on either side.

---

## 6. The load-duration chart on slide 2

A load-duration curve sorts the 365 daily demands from the highest to the lowest and plots them against the number of days. The area under the curve is the annual energy. The chart (`fig_load_duration.png`) also stacks, for each sorted day, which source supplied the energy in the selected design, so it is a supply picture, not only a demand picture.

Heating (left):

- The black line is demand: 30 MW on the single peak day, 25 MW for about 10 days, 20 MW by day 100, 10 MW by day 180, and a floor of about 2.5 MW (hot water) for the last 100 days.
- Blue, geothermal plate HEX: the base layer, 17 to 18 MW on the coldest days, falling with demand. It is the direct heat from the brine between 85 and about 58 °C, two doublets. It carries 69.7 of the 111.7 GWh.
- Orange, high-temperature heat pump: the next layer, up to about 4 MW, present on almost every day because the last 15 K to 100 °C always need it. 27.3 GWh.
- Green, CHP on associated gas: about 2.4 MW whenever the doublets run, since the gas comes with the brine. 10.5 GWh.
- Yellow, peak boiler: only above about 22 MW of demand, which is the first 80 days, and for the days inside an outage window where one doublet is missing. 4.1 GWh, 3.6 % of the heat.
- The small dips in the blue layer near day 40 and day 230 are the two 35-day outage windows in sorted order: with one doublet out the HEX supplies half, and the heat pump and boiler fill the gap.

Cooling (right):

- Demand runs from 10 MW on the peak day to zero after day 219; 146 days have no cooling demand.
- Blue, free cooling: whenever the ambient wet-bulb is low enough the cooling tower alone brings the return from 26 to 20 °C; it carries 20.6 of 23.7 GWh.
- Green, electric chillers: only the hottest 120 days, when the wet-bulb is above about 13.5 °C and the tower cannot reach 20 °C on its own. The ragged edge is the day-to-day weather. 3.2 GWh.

The practical reading: the heating peak is short, the shoulder is long, so a geothermal base load sized at about 18 MW direct heat covers 62 % of the energy, and the expensive backups run for few hours.

---

## 7. The phases P0 to P10 in detail

The runner (`src/gemini_agent/runner/phases.py`, `design_runner.py`) executes the phases in a fixed order. In each phase one role agent proposes steps (which tool to call, which option to choose), the runner calls the tools, and at the end of the phase any required tool the agent skipped is called by the runner itself, then the Decision Records of that phase are taken. Each phase has a budget (default 12 steps, 10 tool calls, 60 000 tokens); exceeding it escalates the run to a human. State is saved after every step, so a run can pause and resume.

| Phase | Role | What happens | Records |
|---|---|---|---|
| P0 Intake | Lead Engineer | A deterministic scope check decides whether the request is a geothermal heating and cooling design. In scope: continue. Off topic (S3, a pizza order): refuse. The model may only finalise or refuse. | – |
| P1 Design basis | Requirements and Data | Loads DB-01 to DB-19 from the yaml (TL-01), profiles the demand CSVs (TL-02) and the De Bilt climate with Stull wet-bulb (TL-02b). First it confirms the basis if the request contradicts it (S5, 120 °C supply) and offers the defaults if inputs are missing (S2). Open questions DB-30, DB-31, DB-32 are asked, assumed or paused on. | USER_ANSWER or ASSUMPTION events |
| P2 Resource | Subsurface | Reservoir temperature and pressure at depth (TL-03), productivity index with skin (TL-04), the doublet operating point: ESP frequency, wellhead temperature, intake pressure, bubble point, injection pressure (TL-05), thermal breakthrough (TL-07), interference between doublets (TL-08). | DR-09 materials (titanium, no copper) |
| P3 Concepts | System Architect | Generates the GEMINI project JSON for C0 to C8 (TL-09) and runs DesignGuard on each (TL-10): hard limits and gas handling before anything is simulated. | – |
| P4 Model gaps | Model Builder | Compares the component types the candidates need with the GEMINI registry (TL-17). If a type is missing it reads the reference card, writes the model and a test in a sandbox, runs pytest, and registers the class at runtime (TL-18). This is scenario S7. | – |
| P5 Screening | System Architect | A fast 365-day dispatch for every candidate (TL-12 fast mode, using the doublet performance map) and economics for each (TL-14). Ranks by unmet demand, then LCOH + LCOC. | DR-01 doublets, DR-02 depth, DR-03 top-up technology, DR-04 cooling mix, DR-05 storage, DR-07 gas handling, DR-08 return temperature |
| P6 Optimisation | System Architect | TL-13 sizes the three best families: integer choices enumerated, continuous sizes (flow, heat pump, boiler, chillers) by seeded differential evolution, then the outage schedule placed in the lowest-load windows. | DR-06 maintenance windows |
| P7 Verification | Lead Engineer | The selected design is simulated in full mode (GEMINI solved every day, TL-12 full) and verified (TL-16). REJECTED loops back to P6, at most 3 times, then escalates. | – |
| P8 Uncertainty | Economics | Monte Carlo over 200 sampled futures and the tornado (TL-15), then the robust-backup workflow tool. | DR-11 backup capacity (switches to the -robust variant) |
| P9 Review | Independent Reviewer | Reads the results and runs the verifier again on the robust design. The same gate as P7. With `--review`, P9b asks the user for an objection, maps it to a lever, computes a what-if and offers to adopt it (DR-12). | DR-10 final selection |
| P10 Report | Reporter | Figures (TL-21), the report draft, the token summary, and the final question "Approve the recommended design? yes/no". | approval event |

What can stop a run: a missing-input request in pause mode (status PAUSED), a budget overrun, a failed required tool, the agent choosing to escalate, or a refusal in P0 (status ESCALATED or REFUSED).

---

## 8. What the policy fallback is

`PolicyEngine` (`src/gemini_agent/agents/engine.py`) is a deterministic, rule-based decision maker that needs no language model. For a phase step it proposes the next required tool that has not run yet with default arguments, then a missing-input request for the first open gap, then "finalise". For a Decision Record it picks the option the tools marked as recommended (the top-ranked candidate, or the register default), else the first eligible option, and writes a rationale from the first offered criterion under which that option is best, with its evidence ids.

With `--engine policy` the whole pipeline runs on it: that is the offline reproduction (`runs/final_policy/`), same design, same verdict, no GPU.

With `--engine llm` the engine is `ResilientEngine(LLMEngine, PolicyEngine)`:

1. The LLM is asked for a typed answer (a Pydantic AI native output). Validators check it: the tool exists for the role, the option is one of those offered, the criterion is one of those offered, the evidence ids exist, the rationale contains no digits.
2. An invalid answer is sent back with the error, at most 2 retries (`max_output_retries` in `config/llm.yaml`).
3. If the retries are exhausted, or the call times out (300 s per call, 1 200 s for the Model Builder), or any other error occurs, the `PolicyEngine` takes that one decision and the result is marked `decision_source = policy_fallback`.
4. Budget overruns are not masked: a phase that exceeds its token or step budget escalates. The exception is a Decision Record call, which gets a floor budget and, if even that is exceeded, is taken by the policy and logged as TOKEN_BUDGET_OVERRUN (this was the DR-08 fix for the larger models).

Everything is logged: each LLM call in `llm_calls.jsonl` with its source (llm, policy_fallback, escalated), tokens, retries and error; runner events DECISION, DECISION_RECORD, STEP_REJECTED, TOKEN_BUDGET_OVERRUN; and the final summary line "LLM fallbacks to policy: N". The final run had 2 fallbacks in 34 calls: one in P7 and DR-10 in P9, where the model twice named a criterion that was not offered.

Why it matters: the fallback keeps the run deterministic and complete even with a weak model, and makes every decision auditable (you can see exactly which ones the model took). It is also the reason the eval can compare models fairly: the validity rate and the fallback count are the measure of a model, not whether the run finished.

---

## 9. The 23 tools and the 12 reference cards

Every tool is deterministic Python under `src/gemini_agent/tools/` with Pydantic input and output models, a unit test under `unit_test/gemini_agent/tools/`, and a docstring that cites the card it implements. The agents call them by name; the runner records each result as `TR-nnn`.

| Id | Tool | What it computes | Card |
|---|---|---|---|
| TL-01 | load_design_basis | Loads DB-01 to DB-19, derives DB-20 to DB-29 (reservoir temperature, hydrostatic pressure, gas fuel power), lists the open gaps DB-30 to DB-32. | RC-01, RC-02, RC-08 |
| TL-02 | profile_demand | Peaks, monthly means, load-duration curve, the lowest-load 35-day windows. | brief §3.2 |
| TL-02b | climate | Daily dry-bulb, humidity and Stull wet-bulb for De Bilt 2027, design days. | RC-07 |
| TL-03 | reservoir_conditions | Temperature and pressure at a given depth. | RC-01 |
| TL-04 | productivity_index | Radial-Darcy productivity and injectivity index with skin. | RC-01 |
| TL-05 | doublet_operating_point | For a target flow: ESP frequency by root finding (30 to 70 Hz), wellhead temperature by GEMINI's wellbore model, ESP pressure rise and power, NPSH, bubble point by Henry's law, injection wellhead pressure and pump power. | RC-01, RC-08, RC-12 |
| TL-06 | doublet_performance_map | TL-05 evaluated from 50 to 300 m³/h and cached, used by the fast dispatch. | RC-01 |
| TL-07 | thermal_breakthrough | Gringarten and Sauty breakthrough time and the production-temperature decline. | RC-02 |
| TL-08 | pressure_interference | Line-source superposition between doublets, limit 1 bar. | RC-02 |
| TL-09 | generate_gemini_config | The GEMINI project JSON (components, boundary inputs, connections) for C0 to C8. | RC-03, 04, 05, 07, 08 |
| TL-10 | design_guard | Hard limits before simulation, gas handling, duplicate detection (section 11). | limits.yaml |
| TL-11 | simulate_day | Builds and solves one day in GEMINI; bisects the HEX bypass to respect the 40 K and 100 °C limits. | RC-01, 03, 04, 08 |
| TL-12 | simulate_annual | 365-day dispatch in fast mode (performance maps) or full mode (GEMINI every day); checks the two agree within 2 % (warn) and 5 % (fail). | RC-02, 05, 06, 07, 09 |
| TL-13 | optimise_design | Outer sizing: integers enumerated, continuous sizes by seeded differential evolution, outage placement. | RC-05, 06, 07, 10 |
| TL-14 | economics, co2, exergy, sensitivity | CAPEX, OPEX, LCOH and LCOC, CO₂ against C0, exergy efficiency, cost tornado. | RC-10, RC-11, RC-08 |
| TL-15 | uncertainty | Latin-hypercube Monte Carlo (200), percentiles, one-at-a-time tornado, named cases, robust backup sizing. | RC-02, 04, 05, 06, 10 |
| TL-16 | verify_design | The four-layer DesignVerifier (section 10). | RC-01, 03, 04, 12 |
| TL-17 | model_gap_analysis | Component types needed versus the GEMINI registry, plus known model limitations. | registry |
| TL-18 | read_reference_card, write_model_file, run_pytest, register_model | The sandboxed Model Builder tools: it may only write into a new model folder and its test folder. | RC-xx |
| TL-19 | knowledge_search | Keyword search over the cards, notes and GEMINI docs, returns cited snippets. | all |
| TL-20 | request_missing_input | Pauses and asks the user, or records a labelled assumption. | – |
| TL-21 | plot_figures, build_report | The eight figures and the report draft, from tool outputs only. | – |
| TL-22 | scaling_check | Ryznar index, gypsum saturation, CO₂ release margin; "insufficient data" plus mitigations when no brine chemistry is given. | RC-12 |
| TL-23 | pipe_heat_loss | Temperature drop along a buried pre-insulated pipe. | RC-09 |

Also registered: `robust_backup` (the P8 workflow step behind DR-11), `read_results` (read-only access for the Reviewer and Reporter), and `whatif` (post-run lever changes, not numbered).

The reference cards are the only engineering source in the repository; the books are not in the repo, the cards paraphrase and cite them with page numbers. Each new GEMINI model carries a copy of its card as `REFERENCE.md`.

| Card | Title | What it gives | Main sources |
|---|---|---|---|
| RC-01 | Reservoir, well hydraulics and pumps | T = 12 + 0.030 z; hydrostatic pressure; radial-Darcy productivity index (about 12.2 m³/h per bar here); Darcy-Weisbach friction with the Swamee-Jain factor and 0.0457 mm roughness; pump shaft power Q Δp / 0.70 and motor and drive efficiencies 0.93 and 0.97; ESP head and frequency; NPSH margin 15 m; Henry's-law bubble point (about 46 bar absolute) and the 1.2 × margin; injection pressure. | DiPippo chapter 4; Geo-Heat Center Guidebook p.167-190, 256-263; Sander 2023 |
| RC-02 | Thermal breakthrough, spacing, interference | t_b = π h D² (ρC)_a / (3 (ρC)_w Q): 34 yr at 1 200 m and 300 m³/h; production-temperature decline; line-source interference ≤ 1 bar; D ≥ 1 200 m. | Gringarten and Sauty 1975; Paris Basin and Dogger papers; DiPippo |
| RC-03 | Plate heat exchanger | ε-NTU and LMTD methods; U 3 to 4 kW/m²K; approach ≥ 1.1 K (default 3 K); 24 kPa per NTU; 0.6 to 4 NTU per pass; ratings 150 °C / 20 bar gasketed; titanium plates +50 %. | Guidebook chapters 11-12; DiPippo chapter 8 |
| RC-04 | Heat pump including HTHP | Carnot and Lorenz COP; COP = 0.50 × Lorenz (0.40 to 0.55); approach 3 to 5 K; HTHP cap COP ≤ 0.6 × Carnot and lift ≤ 60 K in the absence of a datasheet. | Guidebook chapter 13 |
| RC-05 | Absorption chiller (single-effect LiBr) | Two methods: Geo-Heat Center temperature factors and the characteristic-equation method; auxiliaries 2.2 %; 1986 cost functions escalated by CEPCI (tagged VERIFY). | Guidebook chapter 14 |
| RC-06 | Electric chiller | COP = η T_evap / (T_cond − T_evap) with η about 0.5, evaporator 4 K below supply, condenser 3 K above tower water; −2.6 % per K. | Guidebook p.257-267 |
| RC-07 | Heat rejection, free cooling, Dutch climate | De Bilt monthly normals; Stull 2011 wet-bulb; design point 25.0 / 18.4 °C; tower approach 4 to 5 K; fan and pump 2 %; free cooling when wet-bulb ≤ about 13.5 °C. | KNMI; NOAA; Stull 2011; Guidebook p.262-269 |
| RC-08 | Associated gas, boiler, CHP, emissions | Gas mix 0.965 kg/Nm³, LHV 28.6 MJ/Nm³, 240 Nm³/h ≈ 1.91 MW per doublet at 300 m³/h; 68.6 kg CO₂/GJ including the inherent CO₂; no venting; boiler efficiency 0.9; CHP electrical share 1/3; grid factors 56.6 kg/GJ gas and 0.3 kg/kWh electricity. | GEMINI boiler and CHP; Gippsland; NREL; DiPippo |
| RC-09 | District network and thermal storage | 100/55 °C and 20/26 °C networks; pipe temperature drop ΔT = q' L / (ṁ c_p); PUR insulation 0.026 W/mK; network losses 10 to 17 % (10 % used); storage energy balance; HT-ATES recovery about 63 %. | Guidebook chapters 10-12; Danfoss; TU Delft |
| RC-10 | Economics | The LCOH definition (Gippsland equation 1) and the fixed-charge-rate form; 8 % real discount rate, 30 and 20 years; NREL 2025 well-cost table (3.45 MUSD per 2 500 m well); plate-HEX cost 2 000 + 48 ṁ ΔT AUD; CEPCI escalation; O&M 1.5 % of CAPEX; DEA heat-pump, CHP and electric-boiler costs; Eurostat electricity and gas, EU allowance 79.42 EUR/t; ECB 1.13 USD/EUR; ACM heat price cap about 138 USD/MWh. | Gippsland; NREL; ESMAP; Guidebook chapter 18; DEA; Eurostat; ECB |
| RC-11 | Exergy | Dead state T₀ = daily dry-bulb; flow exergy; exergy of heat and of cold; utilisation efficiency η = (W + E_heat + E_cold) / (ṁ e_res). | DiPippo chapters 9-10 |
| RC-12 | Scaling, corrosion, materials | Keep the brine above its CO₂ release pressure; inhibitor dosing; Ryznar index classes; gypsum saturation; oxygen limits; no copper alloys; titanium plates for saline brine; the "insufficient data" rule. | Guidebook chapters 8-11; DiPippo chapters 4, 6, 16 |

---

## 10. The DesignVerifier: 4 layers, 46 checks

`src/gemini_agent/verify/design_verifier.py`. The verdict rule: any failed check gives REJECTED; otherwise any required check that could not be evaluated, or any evidence that came from a stub tool, gives UNVERIFIED; otherwise ACCEPTED. Only this class can write ACCEPTED; no agent can. The report has 50 rows; three are "not applicable" to this design (the absorption-chiller checks PH-15, PH-16 and the pressurised-loop check PD-10) and one is information only (EX-01), which leaves 46 evaluated checks, all pass. The full table with values is in `docs/results/verification.md`.

**Layer 1, syntactic (3):** SY-01 the GEMINI project JSON loads; SY-02 schemas valid and consistent; SY-03 unit suffixes present on every field.

**Layer 2, semantic (4):** SE-01 unique component names; SE-02 every connection references an existing component and key; SE-03 splitter fractions sum to 1; SE-04 each producer is paired with an injector.

**Layer 3, physical (30, of which 28 evaluated):**

| Check | Limit | Value in the final design |
|---|---|---|
| PH-01 daily energy balance (supplied + unmet = demand) | within 1 % | 0 |
| PH-02 brine cooling | 0 to 40 K (DB-12) | 40 K, margin 0 |
| PH-03 flow per active doublet, zero on outage | 50 to 300 m³/h (DB-11) | minimum 50 |
| PH-04 injection equals production | difference 0 (DB-14) | 0 |
| PH-05 heating supply met | 100 °C (DB-16) | 100 |
| PH-06 cooling supply met | 20 °C (DB-17) | 20 |
| PH-07 plate-HEX approach | ≥ 1.1 K | 3 K |
| PH-08 plate-HEX temperature within rating | 150 °C gasketed | 85.2 °C |
| PH-09 plate-HEX pressure within rating | 20 bar | pass |
| PH-10 plate-HEX pressure drop per NTU | ≤ 41 kPa | 24.1 |
| PH-11 NTU per pass | 0.6 to 4.0 | 3.02 |
| PH-12 one unit within 1 360 m³/h and 1 208 m² | fraction ≤ 1 | 0.65 |
| PH-13 heat-pump COP ≤ Carnot | Carnot | 3.33, margin 2.22 |
| PH-14 heat-pump lift and COP within RC-04 defaults | 0.6 × Carnot, lift ≤ 60 K | pass |
| PH-15, PH-16 absorption chiller COP, capacity and pumping | GM-04 bounds, ≤ 50 % | not applicable |
| PH-17 pump shaft power = Q Δp / 0.7 | 1 % | pass |
| PH-18 ESP intake ≥ 1.2 × bubble point | 1.2 × p_b | 55.1 bar, margin 0.86 |
| PH-19 NPSH available ≥ required + 15 m | +15 m | 545 m |
| PH-20 no negative flows or powers | ≥ 0 | pass |
| PH-21 simulation constraint margins | ≥ 0 | pass |
| PH-22 brine-side energy balance (ρ Q c_p ΔT) | within 1 % | 0 |
| PH-23 HEX NTU consistent with the terminal temperatures | F ≥ 0.85 | pass |
| PH-24 number of doublets matches the results | ≤ 3 (DB-10) | 2 |
| PH-25 TL-05 design point feasible | 0 infeasible | 0 |
| PH-26 ESP flow solve error | ≤ 1 % | 0 |
| PH-27 ESP intake margin at the design point | ≥ 0 | 0.86 bar |
| PH-28 cooling and heat-pump models within validity (RC-04 to RC-07) | 0 invalid days | 0 |
| PH-29 daily result flags | 0 failing | 0 |
| PH-30 degassed brine below its boiling point at letdown | < 100 °C | 85.2 |

**Layer 4, performance and design (12, of which 11 evaluated):**

| Check | Limit | Value |
|---|---|---|
| PD-01 unmet demand | 0 MWh | 0 |
| PD-02 planned outages do not overlap | none | pass |
| PD-03 well spacing | ≥ 1 200 m | 1 200, margin 0 |
| PD-04 inter-doublet interference | ≤ 1 bar | 1 bar, margin 0 |
| PD-05 thermal breakthrough | ≥ 30 yr | 34.4 |
| PD-06 fast and full simulation agree | ≤ 5 % (warn 2 %) | 0.3 % |
| PD-07 scaling screen passed or mitigated | 0 open issues | 0 |
| PD-08 materials compatible (no copper, titanium plates) | 0 issues | 0 |
| PD-09 production temperature ≤ reservoir temperature | 87 °C | 85.2 |
| PD-10 pressurised loop above the CO₂ release setpoint | setpoint | not applicable |
| PD-11 TL-08 interference flag at the design separation | pass | pass |
| PD-12 TL-07 breakthrough beyond the analysis horizon | pass | pass |

**Exergy (reported, never pass/fail):** EX-01 exergy utilisation 0.44 with the dead state stated.

The two zero margins are deliberate: PH-02 because the optimiser uses the whole 40 K the brief allows (more cooling of the brine means more heat per m³ pumped), and PD-03/PD-04 because the wells sit at the minimum spacing that keeps interference at 1 bar (closer wells mean shorter pipes and earlier breakthrough).

---

## 11. The hard limits enforced in code

`config/limits.yaml` (schema `HardLimits` in `src/gemini_agent/schemas/config.py`):

| Limit | Value | Id |
|---|---|---|
| Doublets | 1 to 3 (0 only for C0) | DB-10 |
| Flow per active doublet | 50 to 300 m³/h | DB-11 |
| Brine cooling | at most 40 K | DB-12 |
| Pump hydraulic efficiency | 70 % | DB-13 |
| Injection flow equals production flow | always (the schema only accepts true) | DB-14 |
| Heating supply temperature | 100 °C | DB-16 |
| Cooling supply temperature | 20 °C | DB-17 |
| Methane venting | never (the schema only accepts false) | DB-36 |

Where they are enforced, in order of appearance:

1. **At intake** (`agents/scope.py`, `design_runner.py`): a request outside the limits (S4, five doublets at 500 m³/h) or in conflict with the supply temperatures (S5, 120 °C) is flagged and the user is asked to confirm the basis; "change" escalates with the instruction to edit the yaml files, because the limits are not changeable from a prompt. Answers for DB-31 must be below 100 °C and for DB-32 above 20 °C.
2. **Before simulation, DesignGuard (TL-10)** (`verify/design_guard.py`): every candidate is checked for DB-10 (count, and the project's producer count must match), DB-11 (design and dispatch flows; a doublet that is off has zero flow), DB-12 (HEX design temperature drop, heat-pump source floor), DB-13 (pump efficiency including motor and drive), DB-14 (each injector receives exactly one producer's flow, traced through splitters and mixers), DB-16 and DB-17 (targets of the heat pump and the chillers), DB-36 (separated gas must reach a burner; a pressurised-loop candidate must not separate gas; every candidate with doublets states its gas handling), and duplicates (SHA-256 of the canonical configuration). One reason per violation, REJECTED designs never reach the simulator.
3. **Inside the tools**: `simulate_day` bisects the HEX bypass so no day cools the brine by more than 40 K or supplies below 100 °C; `dispatch.py` and `optimise.py` bound flows to 50 to 300; the flow argument schema itself refuses values outside 50 to 300.
4. **After simulation, the verifier** re-reads the same limits in PH-02, PH-03, PH-04, PH-05, PH-06, PH-17 and PH-24 on the simulated results, not on the inputs.
5. **In the what-if and voice levers**: `doublets` is bounded 1 to 3 and `design_flow_m3h` 50 to 300; an out-of-range value is refused with the limit quoted.

Related verifier thresholds that are not in the brief but come from the cards: HEX approach ≥ 1.1 K, NPSH margin 15 m, ESP intake ≥ 1.2 × bubble point, well spacing ≥ 1 200 m, interference ≤ 1 bar, breakthrough ≥ 30 years, heat-pump COP ≤ 0.6 × Carnot and lift ≤ 60 K, fast-versus-full agreement 5 %.

---

## 12. Decision Record DR-03 and why each option was rejected

Every Decision Record lists the question, the options with their eligibility, the chosen option, the decisive criterion, the evidence ids and the rejected options. The language model's rejection text is short ("higher LCOH", "ineligible"); the numbers behind it are in the candidate table (TR-013) and are given here.

**DR-03, heat top-up technology (phase P5, System Architect, decided by the LLM).** Question: which technology lifts the heat to the 100 °C supply? Decisive criterion: lowest LCOH + LCOC among guard-passed candidates without unmet demand. Evidence TR-009, TR-010, TR-012, TR-013.

| Option | Candidate | Eligible | Why it lost |
|---|---|---|---|
| hthp-series: plate HEX, then HTHP in series, CHP, peak boiler | C2 | yes | **chosen**: 50.3 + 26.3 = 76.6 USD/MWh, the lowest sum; CO₂ 6 522 t/yr. |
| gas-boiler-chp-topup: plate HEX, the lift by gas boiler and CHP | C1 (three doublets) | yes | LCOH 54.2 against 50.3, sum 77.8 against 76.6. The lift by gas also doubles the CO₂ (13 963 t/yr, a 47 % cut against 75 %). A third doublet adds 3.45 MUSD per well but the HEX can only use brine heat down to 55 °C, so the extra doublet adds little direct heat. |
| deeper-direct-hex: drill to 3 200 m, direct HEX, small peak boiler | C4 | no | Excluded by the verifier. At 3 200 m the brine is 108 °C and the bubble point rises with temperature and depth; the ESP intake pressure cannot stay 1.2 × above it (PH-18) and the NPSH margin fails (PH-19). Depths 3 000, 3 200 and 3 500 m were all tested and excluded. Its 29.4 USD/MWh is not reachable with the pump the brief implies. |
| shallow-large-hp: three doublets at 1 500 m with large heat pumps | C5 | yes | LCOH 104.5. The brine is only 57 °C, so the heat pump must lift 43 K: COP about 2, 14 MW of heat pump, 56 MUSD CAPEX, 14 300 t/yr CO₂ from the electricity. |

The other Decision Records, in the same format:

- **DR-01 number of doublets** (P5): 2 chosen. 3-doublet candidates lose on cost: C1-3d sum 77.8, C8-3d (spare doublet) 83.3, C5-3d 130.6, against C2-2d 76.6. Evidence TR-005 (productivity index 12.2 m³/h per bar, drawdown 24.5 bar at 300 m³/h).
- **DR-02 well depth** (P5): 2 500 m chosen. 3 200 m ineligible (C4 excluded, PH-19); 1 500 m eligible but C5 costs 104.5 and 56 MUSD.
- **DR-04 cooling mix** (P5): free cooling then electric chillers chosen. Electric only (C1) sum 77.8; free cooling then absorption (C3) LCOC 40.4 because single-stage lithium-bromide at an 85 °C drive has a COP of 0.6 to 0.7 and consumes expensive brine heat; C4 excluded.
- **DR-05 storage** (P5): no storage chosen. C6 (seasonal HT-ATES) has exactly the KPIs of C2 (50.3 / 26.3; C6-opt 42.6 against C2-opt 42.5): with two doublets there is no summer surplus to store, and the storage cost is unpriced, so the tie goes to the simpler design.
- **DR-06 maintenance windows** (P6): optimiser-placed, lowest-load, non-overlapping windows (days 119 and 238) chosen. A common summer block is ineligible: both doublets out together would violate DB-30 and leave summer hot water unmet. Evidence TR-014.
- **DR-07 gas handling** (P5): degas and burn in a CHP chosen. The pressurised loop (C7) that reinjects the gas keeps the whole brine above the bubble point but loses 1.9 MW of free fuel per doublet: LCOH 62.1, sum 89.4, for a CO₂ gain of only 300 t/yr.
- **DR-08 return temperature** (P5): the register default 55 °C kept; 45, 65 and 75 °C were offered. Criterion: keep the register default unless the user changes it (it is a network property, not a plant choice). For scale: 45 °C would give LCOH 36.5, 65 °C 54.7 with unmet demand.
- **DR-09 materials** (P2): titanium plates, no copper alloys in contact with brine, chosen. Stainless plates ineligible: the saline, CO₂-bearing brine fails the chloride limits of the §10.1 materials check. Evidence TR-001.
- **DR-10 final selection** (P9, Lead Engineer, taken by the policy fallback after the model twice named an unoffered criterion): C2-opt-robust, the only option whose latest verifier verdict is ACCEPTED. C2-opt, C6-opt and C1-opt are ineligible because only the verified design can be selected. Evidence TR-021 (the verifier report).
- **DR-11 backup capacity** (P8, Economics): the P90 robust backup chosen (boiler 10.9 MW, chillers unchanged at 8.2 MW). The installed 5.0 MW boiler lost because demand was met in only 46 % of the Monte Carlo futures against 90 %, for +2.05 USD/MWh and +2.21 MUSD. Criterion: the register rule OPT-ROBUST-BACKUP. Evidence TR-017, TR-019.

---

## 13. Is internal pipe friction taken into account?

Yes in the wells, which is where it matters for the pumps; no explicit hydraulic model for the surface and district piping, which is a stated limitation.

**Wellbore friction.** GEMINI's well model (`src/gemini_model/well/pressure_drop.py`) walks the well in cells. For single-phase brine it uses `correlation/darcyweisbach.py`: pressure drop = f ρ (L/D) v² / 2 plus the gravity term, with the Swamee-Jain explicit friction factor f = 0.25 / [log10(ε / 3.7 D + 5.74 / Re^0.9)]² (RC-01 item 5; validation Re = 10⁶, ε/D = 0.0003 gives f = 0.0158). With free gas it switches to Beggs-Brill two-phase flow. The roughness and diameter are register entries SUB-WELL: steel roughness 0.0457 mm and an inner diameter of 0.216 m, chosen as conservative for friction; the tools always pass these explicitly.

**Where friction enters the pump sizing (TL-05, `tools/doublet.py`).** The ESP intake pressure is found by walking up from the bottom-hole flowing pressure to the pump depth (friction lowers it); the required discharge pressure by walking down from the wellhead pressure to the pump (friction raises it). The ESP pressure rise is the difference, and ESP power = Q Δp / 0.70 / (0.93 × 0.97). The injector is walked downward with friction opposing the flow: the GEMINI nodal solver was corrected for this (it added friction to the injection bottom-hole pressure; the fix is behind a parameter and noted with a "Modified by" header). In the final run at 150 m³/h per well the producer friction is 1.18 bar and the injector friction 1.18 bar, against an ESP rise of 19.1 bar and a drawdown of 12.3 bar (TR-006); at the 296 m³/h design flow friction scales roughly with the square of the velocity.

**Plate heat exchanger.** The pressure drop is 24 kPa per NTU (RC-03), checked by PH-10, but it is not fed into a pump-power line.

**Surface and district pipes.** There is no surface-pipe pressure-drop or distribution-pumping model; `tools/network.py` (TL-23, RC-09) handles heat loss only, and the economics has no trunk-pipe line because the brief gives no route length. The README lists this, and the one card figure that could not be reproduced (the 0.55 K/km loss) is replaced by the 10 % network-loss fraction from the register. The screening-level wellhead pressures in the design-basis tool also neglect friction, by design, because the doublet tool computes it properly.

So the honest answer to a judge: well friction yes, with the standard correlation and conservative roughness; district-network hydraulics no, flagged as a limitation with the register value that stands in for it.

---

## 14. Ten tangible improvements over the original GEMINI application

The baseline is upstream GEMINI at commit `d86e8bbc` (29 October 2026): an operations digital twin with a Flask web interface that needs Docker (MySQL, InfluxDB, Redis, MongoDB, Chroma, Grafana), 18 component models on the heating side only, an offline simulator that solves one hand-written plant JSON at one operating point, nine instantaneous KPIs (powers in W, emission rates in kg/s), 92 tests, and a document chat pop-up that runs no tools. The fork adds 38 000 lines of agent code, 3 700 lines of GEMINI models and simulator, 18 000 lines of tests and six configuration files, leaves `gemini_application`, `gemini_interface` and `gemini_framework` untouched, and keeps every original test green.

1. **A design in fifteen minutes instead of weeks.** One command takes a request to a verified, costed design: nine alternatives, a full year each, optimisation, Monte Carlo and a report, in about 14.5 minutes on a laptop (34 LLM calls, 99 301 tokens), or 9 minutes offline on the policy engine. In the original every plant JSON was written by hand and gave one operating point. (`cli.py`, `tools/config_generator.py`, REPORT §8)
2. **Nine alternatives compared on the same basis.** The config generator writes GEMINI projects for C0 to C8 automatically and the screening ranks them by unmet demand then cost, so the comparison is complete and repeatable rather than whichever two cases someone had time to build. (`tools/config_generator.py`, `docs/results/candidates.md`)
3. **Cost optimisation that found 16 %.** The optimiser trimmed C2 from 50.3 to 42.5 USD/MWh by right-sizing flow, heat pump, boiler and chillers. The selected design costs a third of the gas counterfactual (44.6 against 137.0). The original had no sizing at all. (`tools/optimise.py`)
4. **Economics where there were none.** CAPEX, OPEX, LCOH and LCOC over 30 years with every cost item sourced, escalated and currency-converted in `config/cost_assumptions.yaml`; the final CAPEX is 30.1 MUSD. (`tools/economics.py`, RC-10)
5. **Environmental accounting.** Annual CO₂ against a defined counterfactual (7 360 against 26 332 t/yr, −72 %), associated gas burnt rather than vented (never allowed), exergy efficiency 0.44 with the dead state stated, and emissions tracked per source per day. The original gave only an instantaneous kg/s. (ECO-05, `tools/exergy.py`, RC-11)
6. **A full year of operations in view.** A 365-day dispatch with the two 35-day outage windows placed in the lowest-load weeks, daily supply by source, unmet energy per day (0), load-duration and energy-balance figures: the overview an operator needs, where the original showed one steady state. (`tools/simulate_annual.py`, `tools/dispatch.py`, the eight figures)
7. **Cooling added to the twin.** Six new model packages (absorption and electric chillers, free cooling, heat rejection, electric boiler, thermal storage) and a closed-form high-temperature heat pump, each with its reference card, a textbook test and a documentation page; the original had no cooling model. (`src/gemini_model/*`)
8. **Verification that catches unsafe designs automatically.** DesignGuard blocks limit violations before simulation; the 46-check verifier excluded the cheapest candidate (C4, 3 200 m) on pump NPSH and reports a margin for every check. In the original, nothing stopped an infeasible plant from being simulated and reported. (`verify/design_guard.py`, `verify/design_verifier.py`)
9. **Risk quantified and acted on.** 200 Monte Carlo futures gave P10 to P90 cost bands and showed the base design meeting demand in only 46 % of them; the P90 backup raised that to 90 % for 2 USD/MWh. A decision the original could not even pose. (`tools/uncertainty.py`, DR-11)
10. **Every decision traceable and reproducible.** Eleven Decision Records with options, criterion and evidence ids; an 87-entry assumptions register; every tool result and LLM call logged with tokens; a deterministic engine that reproduces the design with no model, GPU or network; 1 500 tests, an 11-scenario eval suite and a submission validator in CI. (`runner/decision_records.py`, `config/assumptions.yaml`, `logging/llm_logger.py`, `runs/final_policy/`)

Two more if asked: the Model Builder extends the twin itself from a reference card with tests as the gate (S7), and the what-if, web and voice front-ends let an engineer interrogate a finished design in minutes without rerunning it.

---

## 15. The diagrams on slides 7 and 8

**Slide 7, the process diagram.** Three rows, each read left to right in the direction of flow.

- Brine side, per doublet: the producer brings 296 m³/h of 85.2 °C brine to surface with the ESP at 58.1 Hz (0.446 MW). The degasser drops the pressure in a controlled way so the associated gas separates (0.8 Nm³ per m³ of brine) and goes to the CHP engine; the brine stays above its boiling point (PH-30). The titanium plate heat exchanger cools the brine from 85 to about 58 °C against the district return. The heat-pump evaporator takes it down to about 45 °C, so the total brine cooling is 40 K, the limit. The injector returns it to the reservoir at 26 bar(g) wellhead with a 0.339 MW injection pump.
- Heating network, in dispatch order: the return water at 55 °C passes first through the plate HEX (free geothermal heat, 69.7 GWh/yr), then collects the CHP's jacket and exhaust heat (10.5 GWh), then goes through the heat-pump condenser to reach 100 °C (27.3 GWh, 4.1 MW), and the 10.9 MW gas boiler adds the rest only on peak days and during outages (4.1 GWh).
- Cooling network, in dispatch order: the return at 26 °C goes first through the free-cooling heat exchanger against tower water (20.6 GWh), and the 8.2 MW electric chillers only cool the residual when the air is too warm (3.2 GWh), to the 20 °C supply.

**Slide 8, the two daily charts.** The upper chart is the heat supplied each day of 2027, stacked by source in dispatch order with the black demand line on top: blue plate HEX, orange heat pump, green CHP, yellow boiler. Demand falls from about 25 MW in January to the 2.5 MW hot-water floor in July and back. The blue layer tracks demand because the doublet flow is dispatched each day to the smallest value that meets the load without the boiler (DSP-FLOW). The orange layer is nearly constant in proportion: every unit of heat to 100 °C needs the heat pump for the last 15 K. The yellow boiler appears only in January and December and in the two outage windows (late April and late August, where the blue layer halves for 35 days). Where the black line touches the stack exactly there is no unmet demand, which is true on all 365 days. The lower chart is cooling: blue free cooling carries everything from April to June and in September and October; green chillers appear in July and August when the wet-bulb exceeds about 13.5 °C.

---

## 16. What free cooling entails

Free cooling means producing the 20 °C chilled-water supply with the cooling tower alone, without running the chiller compressors, whenever the outside air is cool and dry enough. Only the tower fans and the circulation pumps run.

How the model decides (`src/gemini_model/free_cooling/free_cooling.py`, register COOL-FC, RC-07):

- The coldest water a wet cooling tower can make is the ambient wet-bulb temperature plus the tower approach (4.5 K). That water cools the chilled-water loop through a plate heat exchanger with its own approach (2 K). So the economiser water temperature is T_fc = T_wb + 4.5 + 2.
- Full free cooling when T_fc ≤ 20 °C, which means T_wb ≤ 13.5 °C: the tower meets the whole load.
- Partial free cooling when 20 °C < T_fc < 26 °C (T_wb between 13.5 and 19.5 °C): the tower pre-cools the 26 °C return by the fraction (26 − T_fc) / (26 − 20), and the chillers finish the job on water that is already cooler, which also raises their COP.
- Off when T_fc ≥ 26 °C: the chillers carry the whole load.
- Auxiliary power is about 2 % of the cooling delivered (fans and pumps); in the generated plant the tower's own fan is counted once in the heat-rejection model. A warning is raised when the tower water could freeze; freeze protection itself is not modelled.

Why it matters in the Netherlands: the De Bilt monthly wet-bulb runs from 2.4 °C in January to 15.4 °C in July, so free cooling covers all of April, May, September and October, most of June, and part of July and August. In the final run it delivered 20.6 of 23.7 GWh (87 %), with the chillers running 3.2 GWh. That is why LCOC is 26.3 USD/MWh and why the C3 absorption alternative, which spends valuable brine heat on cooling the tower could do for nothing, lost.

The dispatch order in the simulator (SIM-03, `tools/dispatch.py`): cooling is free cooling first, absorption chiller second if present, electric chiller last; heating is plate HEX, then CHP, then heat pump up to its nameplate, then peak boiler, and whatever is left is recorded as unmet.

---

## 17. The costings behind the deck, with calculations

All from `tools/economics.py` and `config/cost_assumptions.yaml` (RC-10); the tool stores totals per candidate in TR-013, and the per-line rebuild below reproduces them to four significant figures.

**Method.** The levelised cost is the present value of all costs divided by the present value of all energy delivered over N = 30 years at a real discount rate r = 8 %:

```
LCOH = Σ_t (CAPEX_t + O&M_t + energy_t) / (1+r)^t  ÷  Σ_t heat_t / (1+r)^t
```

CAPEX falls in year 0 (plus a heat-pump replacement at year 15); O&M is 1.5 % of CAPEX per year; energy costs rise 2.44 % per year in real terms (5 % nominal escalation less 2.5 % inflation). With constant heat this is the same as (CRF × CAPEX + O&M + levelised energy) / heat, where the capital recovery factor is CRF = r (1+r)^N / ((1+r)^N − 1) = 0.0888 and the energy levelisation factor is 1.270 (escalating annuity 14.30 over plain annuity 11.26).

Shared costs (wells, ESPs, injection pumps) are split between heat and cooling by the brine heat each draws; nothing on the cooling side draws brine, so 100 % of the well cost goes to LCOH.

**Unit prices used (converted to USD at 1.13 USD/EUR, escalated to 2023 by CEPCI where old):**

| Item | Price | Source |
|---|---|---|
| Well to 2 500 m | 3.45 MUSD each (NREL 2025 table, 10 % contingency included) | RC-10 |
| HTHP | 1 356 USD per kW of heat (1.20 MEUR/MW, DEA), replaced at year 15 | RC-10 |
| CHP | 1 333 USD per kW electric | DEA |
| Gas boiler | 374 USD/kW (270 USD-2014 escalated) | RC-10 |
| Electric chiller | 107 000 + 267 × (tons − 100) USD-1986 per unit, × 2.506 CEPCI | RC-05 (VERIFY) |
| Free-cooling HEX | 296 USD/kW (147 USD-2002 escalated) | RC-07 |
| Cooling tower | 87 USD/kW | RC-07 |
| Titanium plate HEX | (2 000 + 48 ṁ ΔT) AUD × 0.63 × 1.5 | RC-10, RC-03 |
| Electricity | 133.6 USD/MWh (118.2 EUR) | Eurostat |
| Gas | 71.4 USD/MWh of fuel (15.8 EUR/GJ gross, converted to LHV) | Eurostat |
| CO₂ allowance | 89.7 USD/t on direct emissions | EU ETS 79.42 EUR |
| Not costed | ESPs, injection pumps, trunk pipe (no length given), contingency outside the wells | register ECO-PUMP-CAPEX "missing input" |

**CAPEX of C2-opt-robust, 30.07 MUSD:**

| Line | Service | Calculation | MUSD |
|---|---|---|---|
| 4 wells | shared | 4 × 3.45 | 13.80 |
| HTHP 4.10 MW | heat | 4 100 × 1 356 | 5.56 |
| CHP, 1.21 MW electric (2.41 MW heat at 1/3 electrical share) | heat | 1 205 × 1 333 | 1.61 |
| Peak boiler 10.90 MW | heat | 10 900 × 374 | 4.08 |
| Two titanium plate HEX | heat | about 2 300 kg/s·K each | 0.21 |
| Electric chillers 8.2 MW, three units of 777 tons | cool | 3 × (107 000 + 267 × 677) × 2.506 | 2.16 |
| Free-cooling HEX 8.59 MW | cool | 8 591 × 296 | 2.55 |
| Extra tower capacity 1.17 MW | cool | 1 173 × 87 | 0.10 |
| Total | | heat 11.46 + cool 4.81 + shared 13.80 | 30.07 |

**Operating costs, year 1, 2.28 MUSD:** O&M 0.015 × 30.07 = 0.451; bought electricity 7 954 MWh × 133.6 = 1.062; grid gas 4 524 MWh × 71.4 = 0.323; CO₂ 4 973 t × 89.7 = 0.446.

**LCOH, 44.6 USD/MWh**, heat side CAPEX 11.46 + 13.80 = 25.26 MUSD, heat 111 668 MWh/yr:

| Component | Calculation | USD/MWh |
|---|---|---|
| Capital | (25.26 + 5.56 × 1.08⁻¹⁵ for the heat-pump replacement = 1.75) × 0.0888 = 2.399 MUSD/yr ÷ 111 668 | 21.48 |
| O&M | 0.015 × 25.26 = 0.379 ÷ 111 668 | 3.39 |
| Energy | (heat-pump electricity 0.580 + gas 0.323 + CO₂ 0.446 + well-pump electricity 0.384) = 1.733 × 1.270 ÷ 111 668 | 19.72 |
| LCOH | | 44.59 (tool: 44.59) |

**LCOC, 26.3 USD/MWh**, cooling CAPEX 4.81 MUSD, cooling 23 743 MWh/yr: capital 4.81 × 0.0888 ÷ 23 743 = 18.01; O&M 0.072 ÷ 23 743 = 3.04; chiller electricity 737 MWh × 133.6 × 1.270 ÷ 23 743 = 5.27; total 26.31 (tool: 26.31).

**C0 for comparison:** CAPEX 13.7 MUSD (30 MW boiler 11.2 + 10 MW of chillers 2.5); year-1 costs 11.8 MUSD (gas 124 075 MWh × 71.4 = 8.86, CO₂ 25 282 t × 89.7 = 2.27, electricity 0.47, O&M 0.21). LCOH = capital 8.92 + O&M 1.51 + energy 126.60 = 137.0; LCOC = 9.38 + 1.58 + 25.03 = 36.0. The gas plant is cheap to build and very expensive to run; the geothermal plant is the opposite, and at 8 % real over 30 years the geothermal plant wins by a factor of three on heat.

**The cost of robustness (DR-11):** the extra 5.9 MW of boiler costs 5 900 × 374 = 2.21 MUSD. ΔLCOH = 2.21 × (0.0888 + 0.015) ÷ 111 668 × 10⁶ = 2.05 USD/MWh (1.75 capital + 0.30 O&M). LCOC and CO₂ do not change because the base-year dispatch never needs more than 4.97 MW of boiler.

**Where the 30-year figure is most sensitive (cost tornado, TR-018):** energy escalation (LCOH 39.1 to 47.3 across its range), discount rate 5.6 to 11.2 % (39.4 to 47.3), well cost ±30 % (38.7 to 46.4), electricity price (39.3 to 45.8), heat-pump cost, gas price, CO₂ price, O&M. For LCOC the free-cooling HEX price (17.2 to 29.7) and the discount rate dominate.

---

## 18. The uncertainty analysis on slide 9, with calculations

Tool TL-15 (`tools/uncertainty.py`), register entries UQ-MC, UQ-RANGES, UQ-CASES; results in `runs/final/final_llm/uncertainty_C2-opt.json`, TR-017, TR-018, TR-019.

**What is sampled.** 200 samples (seed 2026) by Latin hypercube over 17 physical inputs and 15 cost multipliers:

| Input | Distribution, range (base) |
|---|---|
| Permeability | lognormal, P10 100 to P90 400 mD (200) |
| Geothermal gradient | uniform 28 to 32 °C/km (30) |
| Skin | uniform −1 to +5 (0) |
| Heating and cooling demand scale | uniform 0.9 to 1.1 (1) |
| Heating return temperature DB-31 | uniform 45 to 65 °C (55) |
| Warm-climate fraction (towards +5 K dry-bulb, +2.6 K wet-bulb) | uniform 0 to 1 (0) |
| Production temperature shift | uniform −6 to +6 K (0) |
| Heat-pump Carnot fraction | uniform 0.50 to 0.65 (0.60), replaces the spec's COP ±15 % |
| Heat-pump approach | uniform 3 to 5 K (4) |
| Chiller efficiency, absorption slope, ESP margin factor, injection suction, injectivity factor 0.65 to 1, well spacing 1 000 to 1 500 m | uniform |
| Real energy escalation | uniform 0.5 to 4.4 % (2.44 %) |
| Cost multipliers on wells, heat pump, CHP, boiler, chillers, prices, O&M, discount rate | uniform ±30 % or the item's own range if wider |

**What is computed per sample.** The design is fixed (C2-opt: two doublets, 296 m³/h, heat pump 4.1 MW, boiler 5.0 MW, chillers 8.2 MW; CAPEX recomputed with the cost multipliers), and for each sample the guard, a fast year and the economics run. A sample "meets demand" when it passes the guard, breaches no hard limit, and has zero unmet heating and cooling. The required boiler of a sample is the installed 5.0 MW plus the largest daily shortfall.

**Percentiles** (numpy linear interpolation; P10 is the favourable end for costs):

| KPI | P10 | P50 | P90 |
|---|---|---|---|
| LCOH USD/MWh | 33.8 | 47.3 | 66.1 |
| LCOC USD/MWh | 20.9 | 26.7 | 34.5 |
| CAPEX MUSD | 23.6 | 27.0 | 30.5 |
| CO₂ t/yr | 5 312 | 8 329 | 11 920 |
| Unmet heating days | 0 | 2 | 71 |
| Unmet heating MWh | 0 | 13 | 2 772 |
| Required boiler MW | 5.00 | 5.38 | 10.85 |
| Thermal breakthrough yr | 48 | 71 | 101 |

The spread in LCOH (33.8 to 66.1 around the base 42.5) is wide because three geological inputs each move it by 10 to 20 USD/MWh and they combine: a 6 K lower production temperature, a 65 °C return or a 28 °C/km gradient each take away direct HEX heat that the heat pump and boiler must then make from electricity and gas.

**P(demand met) = 0.46.** 92 of 200 samples meet demand; the 108 others breach the 100 °C supply check on at least one day because the 5 MW boiler is too small when the return is warm or the brine is cooler than designed. All 92 that pass have zero unmet days, so with the base-year backup the design is either fine or short, never slightly short.

**The tornado (one at a time).** Each input is set to the ends of its range with the others at base, and the swing in LCOH + LCOC is recorded:

| Input | LCOH at low / high end | Swing |
|---|---|---|
| Production temperature −6 / +6 K | 54.5 / 34.1 | 20.8 |
| Return temperature 45 / 65 °C | 36.4 / 54.8 (2 605 MWh unmet at 65) | 18.8 |
| Gradient 28 / 32 °C/km | 52.6 / 35.0 | 17.8 |
| Energy escalation | 39.1 / 47.3 | 10.4 |
| Permeability 100 / 400 mD | 49.2 / 39.2 | 10.0 |
| Warm climate (on LCOC) | 26.3 / 33.0 | 6.8 |
| Skin, cooling demand, Carnot fraction, injectivity, heating demand, approach, suction | | 0.5 to 4.4 |

Rank correlations with LCOH across the 200 samples agree: production temperature shift −0.53, return temperature +0.42, gradient −0.41, permeability −0.32, well cost +0.27, heat-pump cost +0.21, electricity price +0.20.

**DR-11, the robust backup.** The register rule OPT-ROBUST-BACKUP says: install the P90 required backup if it is larger than the installed one, raises P(demand met), passes the guard and meets every base-year day.

```
P90 of required boiler over the 200 samples = 10.853 MW
rounded up to the 0.05 MW sizing step            = 10.90 MW
samples with required boiler ≤ 10.90 MW and no other breach = 180 of 200 → P(demand met) = 0.90
P90 of required chiller = 8.2 MW = installed → chillers unchanged
cost: 5.9 MW × 374 USD/kW = 2.21 MUSD; ΔLCOH = 2.21 × (0.0888 + 0.015) / 111 668 MWh = +2.05 USD/MWh
```

So the robust design costs 44.6 instead of 42.5 USD/MWh and 30.1 instead of 27.9 MUSD, and meets demand in nine of ten futures instead of fewer than one in two. The remaining 10 % are samples where the breach is not a boiler-size problem (for instance a return temperature so high that the 100 °C supply cannot be reached with the installed heat pump); they would need a larger heat pump, which the rule does not add. Two caveats worth stating: the Monte Carlo was run on the 5 MW design and the robust variant was not re-sampled; and the 10.9 MW boiler is idle in the base year above 5 MW, which is the price of the insurance.

**Named sensitivity cases** (`REPORT.md` §5), each a single change on the full design:

| Case | LCOH | LCOC | CO₂ t/yr | Unmet |
|---|---|---|---|---|
| Base C2-opt | 42.5 | 26.3 | 7 361 | 0 |
| Return 45 °C | 36.5 | 26.1 | 5 774 | 0 |
| Return 65 °C | 54.7 | 26.5 | 10 021 | 2 605 MWh, 61 days |
| Return 75 °C | 74.9 | 26.5 | 12 406 | 17 727 MWh, 137 days |
| CHP electrical share 0.5 | 41.4 | 25.8 | 7 116 | 23 MWh |
| Warm climate (+5 K) | 42.7 | 32.9 | 7 662 | 0 |
| DB-30 reading B (no outages) | 43.4 | 30.4 | 7 148 | 0 |
| Gas CO₂ factor 51.53 kg/GJ | 42.5 | 26.3 | 7 279 | 0 |

The single most valuable thing the network operator can do for this plant is to lower the return temperature: every 10 K of return is worth about 6 to 12 USD/MWh and 2 000 to 4 000 t of CO₂ a year.
