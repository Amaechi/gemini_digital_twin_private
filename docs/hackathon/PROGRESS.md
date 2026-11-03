# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p`.

## Status board (final, 2026-11-03)
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | doublet example 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3), [#5](https://github.com/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](https://github.com/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | merged (6fa9667) | [#11](https://github.com/Amaechi/gemini_digital_twin_private/pull/11) | closed-form HP (capped 3.71 @ design); e-boiler; storage |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](https://github.com/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](https://github.com/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | merged (9eb7beb) | [#13](https://github.com/Amaechi/gemini_digital_twin_private/pull/13) | TL-09/10/11; 9 CFG-*; `DayResult.flags` |
| WP-07 | Annual simulation & dispatch | merged (a68c2c9) | [#17](https://github.com/Amaechi/gemini_digital_twin_private/pull/17) | TL-12 |
| WP-08 | Economics, CO₂, exergy, scaling | merged (66acde7) | [#12](https://github.com/Amaechi/gemini_digital_twin_private/pull/12) | TL-14/22/23; C0 LCOH 137 $/MWh ≈ ACM cap |
| WP-09 | Verifier, memory, logging | merged (901e110) | [#9](https://github.com/Amaechi/gemini_digital_twin_private/pull/9) | 41-check DesignVerifier (46 with WP-14) |
| WP-10 | Agents, runner, CLI | merged (2285460; review a9083b7) | [#4](https://github.com/Amaechi/gemini_digital_twin_private/pull/4), [#6](https://github.com/Amaechi/gemini_digital_twin_private/pull/6) | policy P0–P10 on stubs |
| WP-10 L-1 | LLM-path hardening from L-1 | merged (3304f48) | [#19](https://github.com/Amaechi/gemini_digital_twin_private/pull/19) | kinds, call limits, dup guard, DR criteria |
| WP-10 P0 | P0 intake cannot escalate an in-scope request | merged (81787f1) | [#22](https://github.com/Amaechi/gemini_digital_twin_private/pull/22) | finalise/refuse only; `INTAKE_CLOSED` |
| WP-11 | Optimisation & uncertainty | merged (78491fe) | [#18](https://github.com/Amaechi/gemini_digital_twin_private/pull/18) | TL-13/15 |
| WP-12 | Model gap & Model Builder | merged (b81cfa9) | [#15](https://github.com/Amaechi/gemini_digital_twin_private/pull/15) | TL-17/18; S7 demo |
| WP-12 follow-up | Builder uses llm.yaml call settings, retries on timeout | merged (5ba4ad5) | [#23](https://github.com/Amaechi/gemini_digital_twin_private/pull/23) | `calls.model_builder` 1200 s / 8192 tok; local S7 closed |
| WP-13 | Eval suite & submission checks | merged (a622551) | [#16](https://github.com/Amaechi/gemini_digital_twin_private/pull/16) | S1–S11; SUB-01…24 |
| WP-14 | Integration run & figures | merged (c694720) | [#20](https://github.com/Amaechi/gemini_digital_twin_private/pull/20) | real tools; `runs/final_policy/` |
| Pre-L-2 | Robust backup (DR-11), final-run instructions | merged (a1d7f40) | [#21](https://github.com/Amaechi/gemini_digital_twin_private/pull/21) | OPT-ROBUST-BACKUP; CLOUD_SETUP §7 |
| L-1 | Local: pick LLM via S11 eval | done | [#24](https://github.com/Amaechi/gemini_digital_twin_private/pull/24) | gemma4:12b-it-q4_K_M chosen |
| L-2 | Local: final LLM run + eval transcripts | done (1c5fb18) | [#24](https://github.com/Amaechi/gemini_digital_twin_private/pull/24) | `runs/final/final_llm`, `runs/final/eval` |
| S7 local | Model Builder demo after #23 | done (evidence `origin/s7-evidence`, copied to `docs/results/s7_demo/`) | | gap detected and closed, 2 drafts, 2 tests passed |
| WP-15 | Final review, documentation, licensing, packaging | in review | | README, NOTICE, REPORT.md, DECK_OUTLINE.md, clean LLM shutdown |
| Feature B | What-if on a finished run (`cli whatif`) | in review | | `tools/whatif.py`; 9 levers; `runs/<id>/whatif/<n>/` + DR-W<n> |

Status: todo · in progress · in review · merged · blocked.
Feature B (11-03): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **1341 passed in 617 s** (working-tree sentinel tripped on edits to this file during the run; green alone); `tools/test_whatif.py` alone (smoke run + 3 what-ifs): **7 passed in 89 s**.
Latest full suite (WP-15, 11-03): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **1334 passed, 7 warnings in 475 s (7 min 56 s)**. `validate_submission --pytest` → **READY, 22 pass / 0 fail / WARN SUB-14 / MANUAL SUB-23; SUB-24 PASS (`docs/results/submission_check.md`). CI smoke run: 22.8 s.**

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**Feature B — what-if** (11-03, FINAL_SPRINT §B)
- `cli whatif --run-id <id> --runs-dir runs/final --set LEVER=v [--set …] [--budget quick|full|smoke]` = `tools.whatif.whatif(run_dir, levers, budget) -> WhatIfResult`. Reads the run's `events.sqlite` state, `session/sized_designs.json`, `uncertainty_<analysed>.json`; evaluates before (selected design) and after (levers applied) in `runs/<id>/whatif/<n>/` with its own `ToolContext`/`ToolSession` (own `tool_results/`, cache `runs/<root>/cache`); the run's files are never written (test: byte-identical). `.gitignore`: `runs/final/**/whatif/`, `runs/final_policy/whatif/`.
- Levers (TL-15 case keys / register ids), applied as TL-15 does: DB-31/DB-32 candidate fields (plant regenerated by TL-09 at the selected sizes), DB-30 A|B (`db30_answer`; B drops the outage schedule), ECO-CO2-GAS / CHP-EL-FRAC register edits in a scenario copy of `config/` (`write_scenario_config`), OPT-ROBUST-BACKUP on|off (the run's TL-15 `robust_backup_mw`), design_flow_m3h / doublets (DesignGuard DB-10/11), concept (re-pick from the run; guard/verifier exclusions kept). Bounds: register value + `sensitivity` list, `limits.yaml` via DesignGuard, the run; refusals write nothing.
- Per side: guard → TL-12 fast → TL-12 full (+ TL-14, CO₂ vs C0, exergy) → DesignVerifier; `--budget full` adds TL-15 on the changed design (P(demand met)); before's P = the run's TL-15 (robust mapping). `DR-W<n>` uses the DR option/criterion schemas: recommended by the optimiser's rule (LCOH + LCOC among demand-meeting, not REJECTED), choice = the user's lever, rationale number-free (AG-02); a note when they differ.
- Findings on `runs/final/final_llm` (quick, 39 s each): DB-31=65 ⇒ ACCEPTED, 0 unmet days, LCOH 44.6 → 58.4 USD/MWh, CO₂ +3 249 t/yr, boiler margin 5.93 → 0.20 MW (the DR-11 P90 boiler absorbs it; on the smoke fixture C1-opt, without that reserve, DB-31=65 is REJECTED with 3 unmet days — pinned by the test, a deliberate deviation from §B's "keeps the verdict"; DB-31=45 keeps ACCEPTED); OPT-ROBUST-BACKUP=off reproduces DR-11 (ΔCAPEX −2.21 MUSD, ΔLCOH −2). `smoke` cannot run DB-30=B (day subset outside B's window ⇒ infeasible). A what-if decides nothing; the run's DRs stand.

**WP-15** (11-03)
- Top-level `README.md` is GeoAgent's (SUB-22 needs the word "placeholder"); GEMINI's README kept as `README_GEMINI.md`.
- `docs/results/REPORT.md` is generated by `scripts/build_report_pack.py` from run artefacts only (each table cites its file); LLM-run figures copied to `docs/results/final_llm/`; `fig_tokens_by_phase.png` drawn from `token_summary.json` because the run's `fig_tokens.png` is a placeholder (defect, see follow-ups).
- S7 evidence stored as `.py.txt` + JSON in `docs/results/s7_demo/` (nothing collectable); `runs/final/eval` untouched (SUB-17).
- S11 table uses each model's S11 row from `eval_results.json`; gemma4's L-1 S11-only line (79 k tokens, 100 %, 0 fallbacks, 328 s) was overwritten by the S1–S11 eval and survives only here; `model_comparison.json` sums all 12 gemma4 runs.
- Clean shutdown: `LLMEngine.close()` → `close_model_clients()` closes the `AsyncOpenAI`/httpx clients on pydantic-ai's loop, then the loop; `ResilientEngine`/`DesignRunner.close()` delegate; model-builder `main` closes in `finally`. Exit codes unchanged.
- No tool, model, config value or run artefact changed; defects recorded under README Limitations and follow-ups.

**WP-12 follow-up** (11-03)
- One settings path `engine.model_settings(llm_config, calls, temperature)`; coder = `calls.model_builder` (thinking off, 8192 tokens, 1200 s). `CoderError.timed_out` ⇒ failed `BuildAttempt`, feedback, next draft up to `--max-attempts`; CLI prints `failure_report`. Local S7 before: every draft timed out; after: closed in 2 drafts.
**WP-10 P0 follow-up** (11-02; evidence `origin/l1-p0-evidence`, never merged)
- P0 = pure intake: `P0_STEP_KINDS` {finalise, refuse}; other kinds/tools ⇒ STEP_REJECTED; budget used up ⇒ `INTAKE_CLOSED` and the deterministic intake decides (in scope → P1, off-topic → REFUSE). Cause: #19's STEP_KINDS line named `delegate` first and three models answered P0 with it.
**WP-10 L-1 follow-up** (11-02)
- kind coerced from tool before validation (`KIND_COERCED`); `llm.yaml calls` (`reasoning_effort` none, `max_tokens`, `request_timeout`); dup guard on tool + args + context hash (2nd repeat closes the phase); DR = criteria + `evidence_keys`, choice ∈ best.
**Pre-L-2** (11-02, user-approved)
- OPT-ROBUST-BACKUP [A]: recommend TL-15 `robust_backup_mw` (P90) if it adds MW, raises P(demand met), passes guard, meets demand. `PhaseSpec.workflow_tools` run at close before DRs; P8 `robust_backup` → DR-11 → `<id>-robust` SizedDesign (fast + full year + TL-14; deltas); P9 verifies it. Variant has no own MC (`analysed_id`).
**WP-14**
- `runner/real_tools.py`: `default_toolbox()` = real tools via a per-run `ToolSession`; P3 guard + `depth_check`; P5 fast year + TL-14; P6 TL-13 → `<concept>-opt`; P7 full year; P8 TL-15 + `sensitivity`; P10 figures + report. rank = LCOH + LCOC. Verifier PH-25…30, PD-11/12; PH-19 no NPSH_R ⇒ NPSH_A ≥ 15 m; depth ≤ 2.5 km. RUN-BUDGET full / quick / smoke; SIGTERM → SystemExit.
**WP-11**
- TL-13: doublets × depth screen → per `top_k` seeded DE (sizes, then outages); boiler/e-chiller right-sized and priced; rule 4 guard before/after TL-09; objective LCOH + LCOC (30 yr) + OPT-OBJ. TL-15: `Scenario` = config copy + overrides + year scale/shift + CostBook factors; tornado; `robust_backup_mw` = P90 need. [A]: VER-HP-ETA via `max_carnot_fraction`; ±6 K = gradient ± 6/depth; II 0.65–1.0. Touches: `injectivity_factor`, chp `electrical_fraction` (1/3).
**WP-07**
- TL-12 fast = TL-06 map + `PlantSpec` models (SIM-FAST); full = fast plan via `simulate_plant_day`. Merit order HEX → CHP → HTHP → boiler; DSP-FLOW; cooling free → absorption → electric; DB-30 SIM-OUTAGE lowest-load; C6 discharge before boiler.
**WP-13**
- `src/gemini_agent/eval/` (harness, scenarios, submission) + thin scripts; `evals/scenarios.yaml` checks decide, `pending` = needs + reason; engines policy / testmodel / llm; `--compare` ⇒ `model_comparison.{json,md}`; S10 naive = `DesignRunner(history_events=None, compact_history=False)`; SUB-01…24, only FAIL blocks.
**WP-12**
- TL-17 needs = WP-06 `concept_layout` types; 15 LIM-*; sandbox outside the repo, 5 files of a NEW `gemini_model/<name>/`, `screen_python` allow-list; `run_pytest` isolated (120 s); `register_model` in-memory only; coder ≤ 3 drafts, 40 k tokens; policy P4 = gap analysis.
**WP-06**
- generated HPs = WP-03 closed form, Lorenz; VER-HP-ETA cap binds (C2 COP 3.86); TL-09 `PlantSpec` → `build_plant_project`; CFG-LAYOUT degasser → filter → [absorption] → HEX-1 → [HP evap] → injector; district HEX → CHP → HPs → boiler; CFG-HEX UA RC-03 eq 1; CFG-CHP 0.96; CFG-BOILER 0.90; TL-11 HEX share bisected for DB-12; TL-10 limits.yaml + DB-40.
**WP-08**
- costs only via `CostBook` (EUR × 1.13, DEA × 1.168, AUD × 0.63, CEPCI 2023); LCOH/LCOC Gippsland Eq. 1, 8 % real, 30 + 20 yr; ECO-REAL +2.44 %/yr; ECO-OM 1.5 %; HTHP 15 yr; ECO-ALLOC by brine heat; capex 2 wells/doublet (NREL); ECO-CHILLER RC-05 × 2.51 (VERIFY); exergy T₀ daily dry bulb; TL-22 no chemistry ⇒ "insufficient data"; TL-23 NET-LOSS-FRAC 10 %.
**WP-03**
- HP `solver: "closed_form"` (default NM unchanged); COP cap VER-HP-ETA × Carnot (HP-APPROACH 4 K): design 3.71; HTHP limits None = MISSING; EB-ETA 0.99; STO-REC-HT 0.63 / -COLD 0.96; CHP-EL-FRAC 1/3.
**WP-05**
- verifier gets bare p_b bar(g); VER-ESP-MARGIN = SUB-PBUB margin; SUB-PBUB Henry 46.05 bar(a), setpoint 55.26, ESP 840 m; TL-05 `brentq` 30–70 Hz; P_el = QΔp/0.70/(0.93·0.97); maps cached `runs/cache/doublet_maps/`; TL-07 t_b 33.9 yr @ 1.2 km; TL-08 1141 m (2) / 1787 m (3).
**WP-09**
- REJECTED iff any fail; UNVERIFIED if a required check is `not_evaluated` or stub; margin ≥ 0 passes; PH-22 first law; runner: real UNVERIFIED never loops, any stub ⇒ not ACCEPTED; append-only events + `replay`; cache key SHA-256; knowledge = durable `USER_ANSWER`s.
**WP-02**
- COOL-ELC η 0.508 [D] (COP 4.39 @ 7.2/32.2 °C), cap 8; cooling = `StaticModel`s, `power_th` = useful cooling; COOL-ABS-B 1.15 (85 °C → COP 0.64); COOL-ABS-DRV ≈ 13.9 m³/h per MW; COOL-FC T_db + 8 K; HEX per-side ρ/c_p.
**WP-10** / review
- typed `Decision`s; runner executes tools; `NativeOutput`, 2 retries, `policy_fallback`; DR-09 P2, DR-01…05/07/08 P5, DR-06 P6, DR-10 end P9; P7/P9 REJECTED → P6 (≤ 3); approval after P10; CLI exits 0 ok, 1 config, 2 usage, 3 paused, 4 escalated, 5 refused, 6 rejected; AG-02 refuses digits outside an id.
**WP-01** / review
- `"pvt"` on wells (ρ, μ, c_p; DB-08/DB-33 1030, 0.40e-3, 4100); `kpi.py` ρ/c_p from producer `pvt`, else 1050/4200; separator gas CH₄ + CO₂ = 1, LHV 35.8 MJ/Nm³; ESP head/power × pvt SG; registered `heatpump`, `compressor`, `co2_corrosion`.
**WP-04**
- tool signature `(request=None, *, config_dir=None)`; demand warns if CSV ≠ DB-18/19; DB-25 per card (PI 12.2 → 24.5 bar); lowest-load window = min rolling 35-day sum; Stull T_wb.
**WP-00**
- ECO-04 native units/currency/year, USD in code; `StrictModel` schemas; day-average MW; hard limits in DesignGuard; Pydantic AI 2.64.0; cloud = policy / TestModel only.

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30…DB-38, DB-34a–d, DB-34-RH, DB-40 (no DB-39), ECO-CUR. WP-01/12/13/14/15, follow-ups, Feature B: none.
- WP-04: SUB-RHOC, SUB-II, DEM-COP-HD. WP-02: COOL-ABS-B/CAL/CAP/LB/AUX/PL/DRV, COOL-ELC, COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB, VER-ESP-MARGIN, VER-HP-ETA, VER-HP-LIFT. WP-05: SUB-WELL, SUB-PWH, SUB-PBUB, SUB-NPSH, SUB-ESP, SUB-PSUC, SUB-SPACING.
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC. WP-08: ECO-CO2-GAS/AG/SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT/SURFACE/HEX/HP/CHP/BOILER/GRID-GAS/ASSOC-GAS/SIZING. WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE. WP-11: OPT-SPACE, OPT-OBJ, OPT-DE, UQ-RANGES, UQ-CASES, UQ-MC. Pre-L-2: OPT-ROBUST-BACKUP.

## Open follow-ups (after submission; grouped by owner)
**Defects found in WP-15 (recorded, not fixed; README Limitations)**
- `plots.plot_tokens` runs in P10 before `token_summary.json` exists ⇒ the run's `fig_tokens.png` is the placeholder even for LLM runs (report pack draws `docs/results/final_llm/fig_tokens_by_phase.png`). Fix: write the summary before P10 plots.
- `run_eval.py` rewrites `eval_results.json` per call (S11-only line lost); `--compare` sums every scenario. Fix: per-scenario results, compare S11 only.
- P5 DR-08 on gemma4:26b-a4b escalated on `UsageLimitExceeded` (765 tokens left vs 1 900) instead of falling back; consider a per-call floor. Final run DR-10 and P7 were policy fallbacks (criterion not offered; `call_tool` without a tool): add to the few-shot examples. Eval harness: add an `intake_closed` metric.
**WP-07** — DSP-FLOW alternatives unpriced; DSP-HP-MIN `min_part_load_frac` 0 cheaper on C2 (register undecided); fast year 7–25 s at ≥ 3 km; C6 `storage` fails PH-20/PH-22; treat `simulate_day._flag_text/_heat_pump_*`, `Mixer._MIN_TOTAL_FLOW` as API.
**WP-11** — DR-06 cites TL-13, not the outage days; TL-13 `rejected` ↛ `guard_failed`; no HT-ATES capex (C6 unpriced); WACC view (ECO-06) missing; saturation data > 100 °C re-opens ≥ 3 km (`depth_check`); treat `FastPlant._model/.carries/.run` as API.
**WP-12** — LLM P4 calls tools directly (no `check_draft`), `register_model` mutates the live registry; promotion = copy folders + registry + `.rst` via PR (S7 draft needs the card's partial free-cooling rule and tests first, `docs/results/s7_demo/README.md`).
**WP-13** — S5 confirm-basis request; S2 missing-input listing; S8 cross-run re-size; CI ruff skips `scripts/`.
**WP-14** — No `AbsorptionPoint` ⇒ C3/C4 UNVERIFIED; `HeatPumpPoint` lacks max sink T; ESP intake +0.86 bar over 1.2·p_b; free cooling 87 % on smoothed normals; HEX rated at 300 m³/h; CLI `resume` hint omits `--runs-dir`; budget not stored in the run; DR-11 variant priced on the base year only, no own MC; `robust_backup` never escalates.
**Spec/card nits** — GM-05 η ≈ 0.40 (code follows RC-06); DB-25 ≈ 22 bar (card 24.5); RC-09 "0.55 K/km" not reproducible; RC-10 lacks ESP/injection-pump capex; negative `gas_water_ratio` accepted; kpi defaults 1050 kg/m³ unsuffixed; dissolved-gas NPSH margin unquantified; `resume <unknown id>` creates `runs/<id>/`; `--run-id` unsanitised; `heat_pump_1987_usd_per_kw` unescalated.
**All WPs** — GEMINI's `run_linter.sh` (black, isort, flake8 + docstrings, 100 cols): 42 pre-existing flake8 findings (WP-04 + tests); many GEMINI files are CRLF (keep line endings). Act/format split (spec §5.4.3) not implemented.

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off: submission checklist (WP-15, 2026-11-03)
Spec §18 with evidence:
- [x] ACCEPTED design, 0 unmet days — `runs/final/final_llm/run_summary.json` (C2-opt-robust, ACCEPTED, approved), `report_draft.md`.
- [x] DB-10…17 every day — `runs/final/final_llm/figures/verification.md` (46/46), SUB-08 pass.
- [x] C0–C7 (+C8) compared, DR-01…DR-11 — `figures/candidates.md`, `decision_records/`, `docs/results/REPORT.md` §2, §7.
- [x] Cooling models with cards, tests, registry, docs — `src/gemini_model/{absorption_chiller,electric_chiller,heat_rejection,free_cooling}`, SUB-18 pass.
- [x] Brine PVT configurable — SUB-19 pass.
- [x] LCOH/LCOC, CO₂ vs C0, exergy, sensitivity, MC P10/P50/P90 — `REPORT.md` §1, §5 (SUB-10/11 pass).
- [x] `llm_calls.jsonl` + `token_summary.json`; S10 (−45 %), S11 — `runs/final/final_llm/`, `runs/final/eval/` (SUB-12/15/16 pass).
- [x] S1–S11 transcripts — `runs/final/eval/gemma4_12b-it-q4_K_M/` (SUB-17 pass; S7 "fail" predates #23, see `docs/results/s7_demo/`).
- [x] Apache-2.0 notices — `NOTICE`, SUB-20/21 pass; LICENSE unchanged; no secrets; `.env.template` untouched.
- [x] README with API placeholders (SUB-22 pass), `docs/results/REPORT.md`, `DECK_OUTLINE.md`.
- [ ] 10 slides + appendix, ≤ 5-min video (SUB-23, user records from `DECK_OUTLINE.md`).
- [ ] Code pushed: merge, then CLOUD_SETUP §8: fork the public GEMINI repo, `git push submission main:hackathon/<team-name>`, submit branch URL + deck + video.

WP-15 touched: `README.md` (new; GEMINI's → `README_GEMINI.md`), `NOTICE`, `docs/results/`, `scripts/build_report_pack.py`, `agents/engine.py`, `agents/model_builder.py`, `runner/design_runner.py` (`close`), tests, this file. wp-reviewer approved (5 findings fixed). Next: user records deck + video, merges, pushes the fork branch.

Feature B touched (new): `tools/whatif.py`, `tools/test_whatif.py`; edited: `cli.py` (`whatif`), `README.md` (What-if), `CLOUD_SETUP.md` §7.9, `.gitignore`, this file. Nothing in models, other tools, config or `runs/`.
