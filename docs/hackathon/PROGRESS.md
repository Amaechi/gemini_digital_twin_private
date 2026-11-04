# PROGRESS — read first, update last

Keep it ≤ 20 KB (the hand-off between sessions; old detail: merged PRs, `git log -p`).

## Status board
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](/Amaechi/gemini_digital_twin_private/pull/1) | doublet example 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](/Amaechi/gemini_digital_twin_private/pull/3), [#5](/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | merged (6fa9667) | [#11](/Amaechi/gemini_digital_twin_private/pull/11) | closed-form HP (capped 3.71 @ design); e-boiler; storage |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | merged (9eb7beb) | [#13](/Amaechi/gemini_digital_twin_private/pull/13) | TL-09/10/11; 9 CFG-*; `DayResult.flags` |
| WP-07 | Annual simulation & dispatch | merged (a68c2c9) | [#17](/Amaechi/gemini_digital_twin_private/pull/17) | TL-12 |
| WP-08 | Economics, CO₂, exergy, scaling | merged (66acde7) | [#12](/Amaechi/gemini_digital_twin_private/pull/12) | TL-14/22/23; C0 LCOH 137 $/MWh ≈ ACM cap |
| WP-09 | Verifier, memory, logging | merged (901e110) | [#9](/Amaechi/gemini_digital_twin_private/pull/9) | 41-check DesignVerifier (46 with WP-14) |
| WP-10 | Agents, runner, CLI | merged (2285460; review a9083b7) | [#4](/Amaechi/gemini_digital_twin_private/pull/4), [#6](/Amaechi/gemini_digital_twin_private/pull/6) | policy P0–P10 on stubs |
| WP-10 L-1 | LLM-path hardening from L-1 | merged (3304f48) | [#19](/Amaechi/gemini_digital_twin_private/pull/19) | kinds, call limits, dup guard, DR criteria |
| WP-10 P0 | P0 intake cannot escalate an in-scope request | merged (81787f1) | [#22](/Amaechi/gemini_digital_twin_private/pull/22) | finalise/refuse only; `INTAKE_CLOSED` |
| WP-11 | Optimisation & uncertainty | merged (78491fe) | [#18](/Amaechi/gemini_digital_twin_private/pull/18) | TL-13/15 |
| WP-12 | Model gap & Model Builder | merged (b81cfa9) | [#15](/Amaechi/gemini_digital_twin_private/pull/15) | TL-17/18; S7 demo |
| WP-12 follow-up | Builder uses llm.yaml call settings, retries on timeout | merged (5ba4ad5) | [#23](/Amaechi/gemini_digital_twin_private/pull/23) | `calls.model_builder` 1200 s / 8192 tok; local S7 closed |
| WP-13 | Eval suite & submission checks | merged (a622551) | [#16](/Amaechi/gemini_digital_twin_private/pull/16) | S1–S11; SUB-01…24 |
| WP-14 | Integration run & figures | merged (c694720) | [#20](/Amaechi/gemini_digital_twin_private/pull/20) | real tools; `runs/final_policy/` |
| Pre-L-2 | Robust backup (DR-11), final-run instructions | merged (a1d7f40) | [#21](/Amaechi/gemini_digital_twin_private/pull/21) | OPT-ROBUST-BACKUP; CLOUD_SETUP §7 |
| L-1 | Local: pick LLM via S11 eval | done | [#24](/Amaechi/gemini_digital_twin_private/pull/24) | gemma4:12b-it-q4_K_M chosen |
| L-2 | Local: final LLM run + eval transcripts | done (1c5fb18) | [#24](/Amaechi/gemini_digital_twin_private/pull/24) | `runs/final/final_llm`, `runs/final/eval` |
| S7 local | Model Builder demo after #23 | done (evidence `origin/s7-evidence`, copied to `docs/results/s7_demo/`) | | gap detected and closed, 2 drafts, 2 tests passed |
| WP-15 | Final review, documentation, licensing, packaging | merged (7b75c62) | [#25](/Amaechi/gemini_digital_twin_private/pull/25) | README, NOTICE, REPORT.md, DECK_OUTLINE.md, clean LLM shutdown |
| Feature B | What-if on a finished run (`cli whatif`) | merged (a8d9aa5) | [#28](/Amaechi/gemini_digital_twin_private/pull/28) | `tools/whatif.py`; 9 levers; `runs/<id>/whatif/<n>/` + DR-W<n> |
| Eval F | Close S2 (request inputs), S5 (confirm basis), S8 (cross-run sizing) | merged (da5c64d) | [#29](/Amaechi/gemini_digital_twin_private/pull/29) | P1 intake questions CFG-INPUT-DEFAULTS / CFG-BASIS-CONFIRM; `cross_run_sizing`; eval rerun pending (L2) |
| Post-sub C | `--speak`, CHP 0.50 case, fig_tokens | merged (cf6ec2a) | [#31](/Amaechi/gemini_digital_twin_private/pull/31) | `speech.py`; CHP case already in UQ-CASES (tests + README); token chart real in LLM runs; 11 `fig_tokens.png` redrawn |
| Runner J | Decision Record calls are not starved by the phase token budget | merged (f0e0823) | [#32](/Amaechi/gemini_digital_twin_private/pull/32) | DR choice usage-limit floor; `TOKEN_BUDGET_OVERRUN`; P5 90 k tokens; L3 rerun after merge |
| Feature G | Web Design Commander view (`cli serve`) | merged (c1e9783) | [#33](/Amaechi/gemini_digital_twin_private/pull/33) | `ui/run_view.py`, `ui/server.py`, `ui/static/` (Plant Debugger shell, Apache header); read-only JSON API; answer/approve via `resume` |
| Feature D | Push-to-talk voice front-end (`cli talk`) | merged (7a5dcac) | [#35](/Amaechi/gemini_digital_twin_private/pull/35) | `ui/voice.py`; faster-whisper STT, `say` TTS, one logged normaliser call per answer, keyboard confirmation; `requirements-voice.txt`; §I hook |
| Feature E | Interactive design review loop (DR-12) | merged (fbf763f) | [#36](/Amaechi/gemini_digital_twin_private/pull/36) | `cli run --review`; P9b objection → one what-if lever → `whatif` → adopt (P6–P9 rerun, TL-13 pinned) or keep; DR-12 |
| Feature I | Voice dialogue loop (what-if by voice) | in review | PR pending | `ui/dialogue.py`; after the approval or `talk --run-id <completed>`: change → Lead Engineer lever → `whatif` → 3-sentence summary → adopt (`DesignRunner.adopt_whatif`, DR-12) / another / stop; max 5 rounds |

Latest full suite (Feature I, 11-04): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **1501 passed, 8 warnings in 601 s** (incl. 34 dialogue tests; PROGRESS.md text edited after the run, no code change). Previous (Feature E, 11-04): 1467 passed, 8 warnings in 498 s. `validate_submission --pytest` READY (22 pass, 0 fail, WARN SUB-14, MANUAL SUB-23, SUB-24 PASS).

## Decisions log (newest first)
One line per decision; the why is in `config/assumptions.yaml` or the module docstring.

**Feature I — voice dialogue loop, what-if by voice** (11-04, §I)
- `ui/dialogue.py` `DialogueLoop` = `VoiceSession.dialogue_hook` installed by `cli talk` (default on; `--no-dialogue`): after the P10 approval, or at once when `talk --run-id <id>` names a COMPLETED run (other states refused with the `resume` hint), ≤ `MAX_ROUNDS` 5 rounds: "What would you like to change?" → `hear()` → echo + `[Y/n]` → `LeverMapper` → "I understood: change <lever> to <value>" + `[Y/n]` → `review.run_whatif` → `spoken_summary` → "Adopt it, try another, or stop?" → echo + `[Y/n]`. A round = one confirmed request (unmatched / refused / declined count).
- Mapping = the review loop's schema and call: `review.build_review_question` + `dialogue_context` (`DecisionContext` P9 / Lead Engineer, no tools, `tokens` 0 → the engine's review floor applies) → `DecisionEngine.map_objection` (LLM logged as `lead_engineer`; policy keywords); budget overrun or invalid `LeverChoice` → keyword fallback. `more`/`fewer` via `review.resolve_value`.
- Summary = three sentences read from `WhatIfResult` (`applied[].base_value` → value; LCOH, LCOC, CAPEX, CO2 rows; unmet days; P(demand met) "before, not recomputed with the <budget> budget" unless `full`; guard + verifier verdict); `spoken_number` rounding is formatting only (days/tonnes whole, fractions 2 d.p., else 1); a design without KPIs says why. Printed = spoken + the Markdown table.
- Decision words: `map_decision` (adopt/accept/apply/take · another/again/next/other/try · stop/quit/done/…; "yes"/"no" ambiguous → asked again); the LLM normaliser first sees the question as a choice (`voice.DIALOGUE_DECISION_ID`, no default, one logged call). `is_stop` ends on a short stop utterance.
- Adopt = `DesignRunner.adopt_whatif` (preconditions: COMPLETED, own what-if, `ADOPTABLE`, usable design, a loop left — else `ValueError`, nothing written) = the review loop's path after P10: `REVIEW_*` events + what-if `ToolRecord` → `_adopt` (DR-12, TL-13 pin) → `_restart_from_p6` → `_loop` P6–P10 with `approval` reset, `input_mode` ask (the new approval by voice). "Adopt" is offered only when adoptable and never on the repository's `runs/final*` (`submitted_run_reason` → `DialogueLoop.adopt_block`, FINAL_SPRINT rule 1); the dialogue continues until "stop" or the limit.
- Writes without adoption: only `runs/<id>/whatif/` (what-if folders, `dialogue.jsonl`; on a completed run also the dialogue's `llm_calls.jsonl`, `_runner(log_dir=)`) — fixture copy byte-identical (tested). `DesignRunner.result(run_id)` added. `tools/whatif.py` (Feature B file, minimal): a changed `doublets` count drops the outage schedule so TL-12 re-places it (SIM-OUTAGE); before, `doublets=3` on a 2-doublet design was "infeasible" (2 outage starts for 3 doublets). Shared `unit_test/gemini_agent/whatif_fakes.py`.
- Tests `ui/test_dialogue.py` (34): words, submitted-run guard, decision spec, summary, mapper (LLM / fallback / policy), adopt → rerun + DR-12 + `dialogue.jsonl`, unmatched/refused/declined, round limit, silence, not-adoptable, `adopt_whatif` preconditions, scripted 3-turn CLI dialogue on the fixture copy with the real what-if (2 records, run byte-identical, LLM logs under `whatif/`), paused run refused, `--no-dialogue`, help; `test_voice.py` end-to-end says "stop".

**Merged WPs** (one line each; the full decisions are in the PR descriptions and `git log -p` of this file)
- Feature E (#36): `cli run --review` (default off; `RunState.review_enabled`): P9b `DesignRunner._review` state machine (pause/resume via `REVIEW-*` ids) — objection → `DecisionEngine.map_objection` (Lead Engineer `review` kind, `LeverChoice`, `check_lever_choice`; policy = `agents/objections` keywords; unmatched/refused → asked again, 3 rounds) → `tools.whatif` on the live run as a `whatif` `ToolRecord` → adopt (`_adopt`: candidate joins the run, DB-30/31/32 as user answers, `review_pin` → TL-13 `OptimiseArgs.candidate_id`, `_restart_from_p6`, `OPT-ROBUST-BACKUP=off` disables the DR-11 robust option) or keep; DR-12 `review.review_record` (criterion `user-review`, evidence what-if TR + P9 TR); not adoptable: `design_flow_m3h`, `ECO-CO2-GAS`, `CHP-EL-FRAC`.
- Feature D (#35): `cli talk` = `ui/voice.py` around the ask-mode run: push-to-talk `Microphone` (sounddevice) → faster-whisper `small` → `answer_spec` (mirrors `_coerce_answer`) + ONE logged NativeOutput `LLMNormaliser` call per transcript (`agent=voice_normaliser`; keyword `PolicyNormaliser` fallback / `--engine policy`) → canonical value echoed, keyboard-confirmed, validated by the runner as typed; P10 speaks the stored verifier summary; `requirements-voice.txt`; `--typed`, `--mute`; §I hook.
- Feature G (#33): `cli serve` = `ui.server` on `http.server` + Plant Debugger Commander shell (`ui/static/`); `ui/run_view.py` read-only, display only; `POST answer|approve` → `cli.resume_runner` + `DesignRunner.resume` (400/409/404/501); non-loopback warning.
- Runner J (#32): `LLMEngine.choose` floors the usage limit at `call_token_ceiling` (`CHARS_PER_TOKEN` 3, `REQUEST_OVERHEAD_TOKENS` 1024); a DR past the phase budget records `TOKEN_BUDGET_OVERRUN` instead of escalating; P5 `max_tokens` 90 k; harness `token_budget_overruns` metric.
- Post-sub C (#31): `--speak` (`speech.Speaker`, macOS `say`, no-op elsewhere; questions, P10 summary, DR-11, what-if verdicts); CHP-EL-FRAC 0.50 in UQ-CASES; interim `token_summary.json` before `plot_figures` + run-end `fig_tokens.png` redraw; 11 committed `fig_tokens.png` regenerated.
- Eval F (#29): P0 lists missing challenge inputs (`MISSING_INPUTS`); P1 opens with CFG-BASIS-CONFIRM (`keep`/`change`, change ⇒ escalate) then one CFG-INPUT-DEFAULTS question; `cross_run_sizing` check (S8); S2/S5/S8 pass on real tools.
- Feature B (#28): `cli whatif --run-id --set LEVER=v` = `tools.whatif`; 9 levers (DB-31/32, DB-30, ECO-CO2-GAS, CHP-EL-FRAC, OPT-ROBUST-BACKUP, flow, doublets, concept) applied as TL-15 does; guard → TL-12 → verifier per side in `runs/<id>/whatif/<n>/`, the run's files never written; DR-W<n>.
- WP-15: README/NOTICE/REPORT pack generated from run artefacts; `DecisionEngine.close()` chain; WP-15 defects recorded under README Limitations, not fixed.
- WP-12 follow-up: coder uses `calls.model_builder` (thinking off, 8192 tok, 1200 s); a timeout ⇒ feedback + next draft.
- WP-10 P0 follow-up: P0 = finalise/refuse only; step budget out ⇒ `INTAKE_CLOSED`, the deterministic intake decides.
- WP-10 L-1 follow-up: `KIND_COERCED`; `llm.yaml calls`; duplicate-call guard; DR = criteria + `evidence_keys`.
- Pre-L-2: OPT-ROBUST-BACKUP (P90) via P8 `robust_backup` → DR-11 → `<id>-robust`; P9 verifies it.
- WP-14: real tools per run (`ToolSession`); P3 guard + `depth_check`; P5 fast year; P6 TL-13 `<concept>-opt`; P7 full year; P8 TL-15; rank LCOH + LCOC; RUN-BUDGET full/quick/smoke.
- WP-11: TL-13 doublets × depth screen → seeded DE; TL-15 scenarios, tornado, `robust_backup_mw` P90.
- WP-07: TL-12 fast/full year; merit order HEX → CHP → HTHP → boiler, cooling free → absorption → electric; DB-30 lowest-load outage.
- WP-13: eval harness (policy / testmodel / llm), `pending` items with needs + reason, SUB-01…24.
- WP-12: TL-17/18 gap analysis + Model Builder sandbox (≤ 3 drafts, 40 k tokens); policy P4 = gap analysis only.
- WP-06: TL-09/10/11 config generator (CFG-LAYOUT, CFG-HEX UA, CFG-CHP 0.96, CFG-BOILER 0.90), guard, day sim.
- WP-08: `CostBook` (USD 2023); LCOH/LCOC Gippsland Eq. 1, 8 % real, 30 + 20 yr; ECO-05 CO₂; exergy T₀ daily.
- WP-03: closed-form HP capped VER-HP-ETA × Carnot (3.71 at design); e-boiler EB-ETA 0.99; storage.
- WP-05: TL-03…08; SUB-PBUB Henry 46.05 bar(a); TL-05 `brentq` 30–70 Hz; SUB-ESP HC27000 × 1.642; maps cached.
- WP-09: REJECTED iff any fail; UNVERIFIED if `not_evaluated`/stub; append-only events; knowledge = durable `USER_ANSWER`s.
- WP-02: 4 cooling `StaticModel`s; COOL-ELC η 0.508; COOL-ABS-B 1.15; HEX per-side ρ/c_p.
- WP-10 / review: typed `Decision`s; runner executes tools; DR-09 P2, DR-01…05/07/08 P5, DR-06 P6, DR-10 P9; exit codes 0–6.
- WP-01 / review: `pvt` on wells; kpi ρ/c_p from producer `pvt`; registered `heatpump`, `compressor`, `co2_corrosion`.
- WP-04: tool signature `(request=None, *, config_dir=None)`; DB-25 per card; lowest-load window = min 35-day sum.
- WP-00: ECO-04 USD in code; `StrictModel` schemas; day-average MW; cloud = policy / TestModel only.

## Assumptions added during build
(Each id has a `config/assumptions.yaml` entry.)
- WP-00: DB-30…DB-38, DB-34a–d, DB-34-RH, DB-40 (no DB-39), ECO-CUR. WP-01/12/13/14/15, follow-ups, Feature B, Feature E, Feature G: none. Eval F (WP-13 follow-up): CFG-BASIS-CONFIRM, CFG-INPUT-DEFAULTS (`added_by: WP-13 follow-up`; no design value depends on them).
- WP-04: SUB-RHOC, SUB-II, DEM-COP-HD. WP-02: COOL-ABS-B/CAL/CAP/LB/AUX/PL/DRV, COOL-ELC, COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB, VER-ESP-MARGIN, VER-HP-ETA, VER-HP-LIFT. WP-05: SUB-WELL, SUB-PWH, SUB-PBUB, SUB-NPSH, SUB-ESP, SUB-PSUC, SUB-SPACING.
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC. WP-08: ECO-CO2-GAS/AG/SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT/SURFACE/HEX/HP/CHP/BOILER/GRID-GAS/ASSOC-GAS/SIZING. WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE. WP-11: OPT-SPACE, OPT-OBJ, OPT-DE, UQ-RANGES, UQ-CASES, UQ-MC. Pre-L-2: OPT-ROBUST-BACKUP.

## Open follow-ups (after submission; grouped by owner)
**Defects found in WP-15 (recorded, not fixed; README Limitations)**
- `run_eval.py` rewrites `eval_results.json` per call (S11-only line lost); `--compare` sums every scenario. Fix: per-scenario results, compare S11 only.
- Final run DR-10 and P7 were policy fallbacks (criterion not offered; `call_tool` without a tool): add to the few-shot examples. Eval harness: add an `intake_closed` metric. (P5 DR-08 starvation and the P10 `plot_tokens` placeholder were fixed by #32 / #31; the submitted runs predate them; L3 rerun after the merge.)
**WP-07** — DSP-FLOW alternatives unpriced; DSP-HP-MIN `min_part_load_frac` 0 cheaper on C2 (register undecided); fast year 7–25 s at ≥ 3 km; C6 `storage` fails PH-20/PH-22; `simulate_day._flag_text/_heat_pump_*`, `Mixer._MIN_TOTAL_FLOW` are API.
**WP-11** — DR-06 cites TL-13, not the outage days; TL-13 `rejected` ↛ `guard_failed`; no HT-ATES capex (C6 unpriced); WACC view (ECO-06) missing; saturation data > 100 °C re-opens ≥ 3 km (`depth_check`); `FastPlant._model/.carries/.run` are API.
**WP-12** — LLM P4 calls tools directly (no `check_draft`), `register_model` mutates the live registry; promotion = copy folders + registry + `.rst` via PR (S7 draft first needs the card's partial free-cooling rule and tests; `docs/results/s7_demo/README.md`).
**WP-13** — CI ruff skips `scripts/`. Eval F closed S2/S5/S8 in code; the full S1–S11 eval rerun (L2, laptop) is pending (`cross_run_sizing` needs S1 in the same `run_eval` call). Missing-input detection is keyword-based, so every non-brief prompt records one extra assumed `CFG-INPUT-DEFAULTS` answer in assume mode; the `change` answer escalates rather than re-basing the run.
**WP-14** — No `AbsorptionPoint` ⇒ C3/C4 UNVERIFIED; `HeatPumpPoint` lacks max sink T; ESP intake +0.86 bar over 1.2·p_b; free cooling 87 % on smoothed normals; HEX rated at 300 m³/h; `resume` hint omits `--runs-dir`; budget not stored in the run; DR-11 variant priced on the base year only; `robust_backup` never escalates.
**Feature E** — DR-12 is one record per run (last pass wins; history in rationale/events); after a verifier REJECT of the re-sized adopted design the pin is dropped but the adopted DB-31/32 answer and `review_levers` stay (the free TL-13 rerun uses each candidate's own return temperatures); `design_flow_m3h` needs a TL-13 pin (`fixed_sizes` request field, WP-11 file); keyword mapper English-only and literal; the web view shows review questions as pending questions without the table; voice (D/I) hooks in at `ask_fn`.
**Feature G** — no live refresh (REFRESH button); resuming an LLM run from the browser needs the local Ollama model (or `--engine policy`); no authentication — keep the 127.0.0.1 bind.
**Feature D** — not tried on a Mac yet (CLOUD_SETUP §7.10); English only; policy normaliser needs digits; no barge-in; normaliser tokens count in `token_summary.json`.
**Feature I** — not tried on a Mac yet; English keyword vocabulary (stop/adopt/another) is literal; after an adoption on a completed run the rerun's LLM calls are logged under `whatif/llm_calls.jsonl` (the run's `token_summary.json` misses them); DR-12 of an adoption keeps the review loop's one-record-per-run rule; the web view shows a dialogue adoption as `REVIEW_*` events only (no `dialogue.jsonl` panel); smoke-budget what-ifs read absurd LCOH (13-day year) — CI only; `probability_demand_met` is never recomputed below `--budget full`.
**Spec/card nits** — GM-05 η ≈ 0.40 (code follows RC-06); DB-25 ≈ 22 bar (card 24.5); RC-09 "0.55 K/km" not reproducible; RC-10 lacks ESP/injection-pump capex; negative `gas_water_ratio` accepted; kpi defaults 1050 kg/m³ unsuffixed; dissolved-gas NPSH margin unquantified; `resume <unknown id>` creates `runs/<id>/`; `--run-id` unsanitised; `heat_pump_1987_usd_per_kw` unescalated.
**All WPs** — GEMINI's `run_linter.sh` (black, isort, flake8 + docstrings, 100 cols): 42 pre-existing flake8 findings (WP-04 + tests); many GEMINI files are CRLF (keep line endings). Act/format split (spec §5.4.3) not implemented.

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off: submission checklist (WP-15)
Spec §18, evidence:
- [x] ACCEPTED design, 0 unmet days — `runs/final/final_llm/run_summary.json` (C2-opt-robust, approved), `report_draft.md`.
- [x] DB-10…17 every day — `runs/final/final_llm/figures/verification.md` (46/46), SUB-08.
- [x] C0–C7 (+C8) compared, DR-01…DR-11 — `figures/candidates.md`, `decision_records/`, `REPORT.md` §2, §7.
- [x] Cooling models with cards, tests, registry, docs — `src/gemini_model/{absorption_chiller,electric_chiller,heat_rejection,free_cooling}`, SUB-18.
- [x] Brine PVT configurable — SUB-19 pass.
- [x] LCOH/LCOC, CO₂ vs C0, exergy, sensitivity, MC P10/P50/P90 — `REPORT.md` §1, §5 (SUB-10/11).
- [x] `llm_calls.jsonl` + `token_summary.json`; S10 (−45 %), S11 — `runs/final/final_llm/`, `runs/final/eval/` (SUB-12/15/16).
- [x] S1–S11 transcripts — `runs/final/eval/gemma4_12b-it-q4_K_M/` (SUB-17; S7 "fail" predates #23, `docs/results/s7_demo/`).
- [x] Apache-2.0 notices — `NOTICE`, SUB-20/21; LICENSE unchanged; no secrets; `.env.template` untouched.
- [x] README with API placeholders (SUB-22), `docs/results/REPORT.md`, `DECK_OUTLINE.md`.
- [ ] 10 slides + appendix, ≤ 5-min video (SUB-23, user records from `DECK_OUTLINE.md`).
- [ ] Code pushed: merge, then CLOUD_SETUP §8 (fork the public GEMINI repo, `git push submission main:hackathon/<team-name>`, submit branch URL + deck + video).

Feature E touched (new): `runner/review.py`, `agents/objections.py`, `unit_test/gemini_agent/runner/test_review.py`; edited: `schemas/decision.py`, `runner/state.py`, `runner/design_runner.py`, `agents/engine.py` (`map_objection`), `prompts/system.py`, `runner/toolbox.py` (`OptimiseArgs.candidate_id`), `runner/real_tools.py`, `runner/tool_stubs.py`, `runner/decision_records.py`, `cli.py` (`--review`), `README.md` (Review loop), `CLOUD_SETUP.md` §7.11, this file. No model/tool physics, no config value, nothing in `runs/`.

Feature D touched (new): `ui/voice.py`, `unit_test/gemini_agent/ui/test_voice.py`, `requirements-voice.txt`; edited: `cli.py` (`talk`, `_runner(ask_fn=, model=)`), `README.md` (Voice), `CLOUD_SETUP.md` §7.10, this file. Nothing in models, tools, runner, verifier, config, CI or `runs/`.

Feature I touched (new): `ui/dialogue.py`, `unit_test/gemini_agent/ui/test_dialogue.py`, `unit_test/gemini_agent/whatif_fakes.py`; edited: `ui/voice.py` (`DIALOGUE_DECISION_ID` spec, public `hear()`, hook docs), `runner/design_runner.py` (`result()`, `adopt_whatif()`), `cli.py` (`talk --run-id <completed>`, `--no-dialogue`, `_runner(log_dir=)`), `tools/whatif.py` (doublets lever re-places the outage schedule — Feature B file, minimal), `unit_test/gemini_agent/runner/test_review.py` (imports the shared fake), `unit_test/gemini_agent/ui/test_voice.py` ("stop" after the approval), `README.md` (Voice: dialogue + transcript), `CLOUD_SETUP.md` §7.10, this file. No model physics, no config value, nothing in `runs/`.
