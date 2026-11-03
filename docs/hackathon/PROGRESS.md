# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs, `git log -p`.

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
| WP-15 | Final review, documentation, licensing, packaging | merged (7b75c62) | [#25](https://github.com/Amaechi/gemini_digital_twin_private/pull/25) | README, NOTICE, REPORT.md, DECK_OUTLINE.md, clean LLM shutdown |
| Feature B | What-if on a finished run (`cli whatif`) | merged (a8d9aa5) | [#28](https://github.com/Amaechi/gemini_digital_twin_private/pull/28) | `tools/whatif.py`; 9 levers; `runs/<id>/whatif/<n>/` + DR-W<n> |
| Eval F | Close S2 (request inputs), S5 (confirm basis), S8 (cross-run sizing) | merged (da5c64d) | [#29](https://github.com/Amaechi/gemini_digital_twin_private/pull/29) | P1 intake questions CFG-INPUT-DEFAULTS / CFG-BASIS-CONFIRM; `cross_run_sizing`; eval rerun pending (L2) |
| Runner J | Decision Record calls are not starved by the phase token budget | in review | | DR choice usage-limit floor; `TOKEN_BUDGET_OVERRUN`; P5 90 k tokens; L3 rerun after merge |

Status: todo · in progress · in review · merged · blocked.
Latest full suite (Runner J, 11-03): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **RUNNER_J_SUITE_RESULT**. `validate_submission --pytest` → **READY, 22 pass / 0 fail / WARN SUB-14 / MANUAL SUB-23; SUB-24 PASS "1334 passed, 7 warnings in 472.38s" (`docs/results/submission_check.md`). CI smoke `--budget smoke`: 22.8 s (WP-12 follow-up measurement). After the reviewer's guard fix in `close_model_clients` only `agents/test_engine.py` + `test_cli.py` were re-run**.

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**Runner J — DR calls not starved by the phase token budget** (11-03, FINAL_SPRINT §J)
- Cause (evidence `origin/qwen27b-evidence` `runs/final_qwen27b/final_llm` and `runs/final/eval/gemma4_26b-a4b/S11`): every call's `total_tokens_limit` = tokens left in the phase; on verbose models each P5 DR choice costs 8–9 k tokens with its 2 retries (prompt ≈ 2.9 k × 3 requests), so ≈ 56–60 k of P5's 60 k were used when DR-08 (a phase-close step the workflow requires) came → `UsageLimitExceeded` → escalation mid-DR.
- `LLMEngine.choose` floors the usage limit at `call_token_ceiling(prompt, role)` = n·p + o·n(n+1)/2 (n = `max_output_retries` + 2 requests, p = prompt + instructions at `CHARS_PER_TOKEN` 3, o = `calls.max_output_tokens`): a call within its configured limits cannot exceed it (≈ 32 k for a 3 k-token prompt), while a runaway past the ceiling still raises `EngineBudgetExceeded` → escalation. `decide()` unchanged (AG-04: 0 tokens left ⇒ escalate before any call; a usage overrun ⇒ escalate).
- Runner `_decision_record`: a DR that leaves the phase over `max_tokens` records `TOKEN_BUDGET_OVERRUN` (dr_id, tokens_used, max_tokens, overrun) instead of escalating; `DesignRunner.token_budget_overruns(run_id)`; the run summary lists the count; eval harness `RunMetrics.token_budget_overruns` + "Overruns" column in both eval tables (`runs/final/eval` md files not regenerated).
- `phases.py`: P5 `max_tokens` 90 000 (2 tools + 7 DRs ≈ 80 k on verbose models); every other phase keeps the 60 k default. No other config value changed; `runs/final*` untouched.
- "request_input normalisation" named in §J has no LLM call (answers are coerced deterministically in `_coerce_answer`), so nothing to floor there.
- Tests (FunctionModel with set `RequestUsage`): P5 at 59 k used still writes DR-08 by the LLM, 1 overrun event, run COMPLETED, harness metric 1; a P2 decision after the whole budget is used escalates with "no tokens left" and no second call; engine-level: choice at 0 tokens left runs, choice past its ceiling escalates, decisions keep the phase limit; P5 budget is the largest.
**Eval F — S2/S5/S8** (11-03)
- P0 intake lists the challenge inputs a user-written request lacks (`agents/scope.missing_request_inputs`: demand CSVs, cooling demand, reservoir data; keyword rules; the empty prompt = full brief is never scanned) → `RunState.missing_inputs`, `MISSING_INPUTS` event.
- P1 opens with the runner's own questions (`_intake_requests`: before any tool or register gap, asked once, idempotent over resume): CFG-BASIS-CONFIRM when a CONFLICT names (DB-16)/(DB-17) — `keep` (default, assumed in `--input-mode assume`) continues on the basis, `change` escalates (new limits in config/ + a new run; never designed for) — then ONE CFG-INPUT-DEFAULTS question naming the missing inputs, default "repository defaults" (`defaults`; other text is an InvalidAnswer saying where the inputs go). S1 unaffected: DB-30/31/32 + approval (fixture `runner/fixtures/s1_policy_smoke.json`, recorded before the change).
- Check `cross_run_sizing` (`baseline: S1`, same eval dir): PI (TL-04), wells (doublets, depth, flow), ESP stages / setting depth (TL-05 `doublet.esp`, latest call naming the selected design), peak `elec_esp_*_mw` of the selected design's annual CSV (same year mode in both runs); passes at a lower PI when the wells are re-sized (more doublets, lower flow per doublet, other depth) or the ESP is larger (> 5 % peak); fails naming the reason without S1 or without a PI (stubs). Policy smoke eval S1,S8,S2,S5 on real tools: all pass (S8 "wells re-sized": 3 × 199 m³/h vs 3 × 274 m³/h, ESP 49 stages @ 1560 m vs 16 @ 840 m). Stored gemma4 S8 evidence passes (PI 3.06 vs 12.22; 49 stages @ 1560 m, 1.43 MW vs 16 @ 840 m, 0.46 MW).
- scenarios.yaml: S2 `pending_question id CFG-INPUT-DEFAULTS`, `question_mentions`, `tool_calls_max 0`; S5 `assumption_or_question CFG-BASIS-CONFIRM`, `status completed`; S8 `cross_run_sizing`; no pending left on S2/S5/S8. Stubs: S2/S5 pass, S8 fails `dr_mentions` + `cross_run_sizing`. `cli.py` untouched.
**Feature B — what-if** (11-03, FINAL_SPRINT §B)
- `cli whatif --run-id <id> --runs-dir runs/final --set LEVER=v [--set …] [--budget quick|full|smoke]` = `tools.whatif.whatif(run_dir, levers, budget) -> WhatIfResult`. Reads the run's `events.sqlite` state, `session/sized_designs.json`, `uncertainty_<analysed>.json`; evaluates before (selected design) and after (levers applied) in `runs/<id>/whatif/<n>/` with its own `ToolContext`/`ToolSession` (own `tool_results/`, cache `runs/<root>/cache`); the run's files are never written (test: byte-identical). `.gitignore`: `runs/final/**/whatif/`, `runs/final_policy/whatif/`.
- Levers (TL-15 case keys / register ids), applied as TL-15 does: DB-31/DB-32 candidate fields (plant regenerated by TL-09 at the selected sizes), DB-30 A|B (`db30_answer`; B drops the outage schedule), ECO-CO2-GAS / CHP-EL-FRAC register edits in a scenario copy of `config/` (`write_scenario_config`), OPT-ROBUST-BACKUP on|off (the run's TL-15 `robust_backup_mw`), design_flow_m3h / doublets (DesignGuard DB-10/11), concept (re-pick from the run; guard/verifier exclusions kept). Bounds: register value + `sensitivity` list, `limits.yaml` via DesignGuard, the run; refusals write nothing.
- Per side: guard → TL-12 fast → TL-12 full (+ TL-14, CO₂ vs C0, exergy) → DesignVerifier; `--budget full` adds TL-15 on the changed design (P(demand met)); before's P = the run's TL-15 (robust mapping). `DR-W<n>` uses the DR option/criterion schemas: recommended by the optimiser's rule (LCOH + LCOC among demand-meeting, not REJECTED), choice = the user's lever, rationale number-free (AG-02); a note when they differ.
- Findings on `runs/final/final_llm` (quick, 39 s each): DB-31=65 ⇒ ACCEPTED, 0 unmet days, LCOH 44.6 → 58.4 USD/MWh, CO₂ +3 249 t/yr, boiler margin 5.93 → 0.20 MW (the DR-11 P90 boiler absorbs it; on the smoke fixture C1-opt, without that reserve, DB-31=65 is REJECTED with 3 unmet days — pinned by the test, a deliberate deviation from §B's "keeps the verdict"; DB-31=45 keeps ACCEPTED); OPT-ROBUST-BACKUP=off reproduces DR-11 (ΔCAPEX −2.21 MUSD, ΔLCOH −2). `smoke` cannot run DB-30=B (day subset outside B's window ⇒ infeasible). A what-if decides nothing; the run's DRs stand.
**Post-WP-15 README polish** (11-03, FINAL_SPRINT §1)
- README: "At a glance" block (numbers copied from the Results section), `config/climate_nl.yaml` path, demand claim in words, S7/DR-08/token-placeholder/S11-pending wording in Limitations, policy-engine reproduce wording, wall time ≈ 14.5 min from `events.jsonl`. `build_report_pack.py` now rewrites the DR-xx index links relative to `docs/results/` (they pointed at non-existent `docs/results/DR-xx.md`); only `REPORT.md` §7 changed on regeneration. Docs only; `runs/final*` untouched. Tests: 1334 passed.

**Merged WPs** (one line each; the full decisions are in the PR descriptions and `git log -p` of this file)
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
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30…DB-38, DB-34a–d, DB-34-RH, DB-40 (no DB-39), ECO-CUR. WP-01/12/13/14/15, follow-ups, Feature B: none. Eval F (WP-13 follow-up): CFG-BASIS-CONFIRM, CFG-INPUT-DEFAULTS (`added_by: WP-13 follow-up`; no design value depends on them).
- WP-04: SUB-RHOC, SUB-II, DEM-COP-HD. WP-02: COOL-ABS-B/CAL/CAP/LB/AUX/PL/DRV, COOL-ELC, COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB, VER-ESP-MARGIN, VER-HP-ETA, VER-HP-LIFT. WP-05: SUB-WELL, SUB-PWH, SUB-PBUB, SUB-NPSH, SUB-ESP, SUB-PSUC, SUB-SPACING.
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC. WP-08: ECO-CO2-GAS/AG/SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT/SURFACE/HEX/HP/CHP/BOILER/GRID-GAS/ASSOC-GAS/SIZING. WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE. WP-11: OPT-SPACE, OPT-OBJ, OPT-DE, UQ-RANGES, UQ-CASES, UQ-MC. Pre-L-2: OPT-ROBUST-BACKUP.

## Open follow-ups (after submission; grouped by owner)
**Defects found in WP-15 (recorded, not fixed; README Limitations)**
- `plots.plot_tokens` runs in P10 before `token_summary.json` exists ⇒ the run's `fig_tokens.png` is the placeholder even for LLM runs (report pack draws `docs/results/final_llm/fig_tokens_by_phase.png`). Fix: write the summary before P10 plots.
- `run_eval.py` rewrites `eval_results.json` per call (S11-only line lost); `--compare` sums every scenario. Fix: per-scenario results, compare S11 only.
- ~~P5 DR-08 on gemma4:26b-a4b escalated on `UsageLimitExceeded` (765 tokens left vs 1 900) instead of falling back~~ fixed by Runner J (DR usage-limit floor, `TOKEN_BUDGET_OVERRUN`, P5 90 k); the submitted runs predate it; L3 (qwen3.6:27b) is rerun after the merge. Final run DR-10 and P7 were policy fallbacks (criterion not offered; `call_tool` without a tool): add to the few-shot examples. Eval harness: add an `intake_closed` metric.
**WP-07** — DSP-FLOW alternatives unpriced; DSP-HP-MIN `min_part_load_frac` 0 cheaper on C2 (register undecided); fast year 7–25 s at ≥ 3 km; C6 `storage` fails PH-20/PH-22; treat `simulate_day._flag_text/_heat_pump_*`, `Mixer._MIN_TOTAL_FLOW` as API.
**WP-11** — DR-06 cites TL-13, not the outage days; TL-13 `rejected` ↛ `guard_failed`; no HT-ATES capex (C6 unpriced); WACC view (ECO-06) missing; saturation data > 100 °C re-opens ≥ 3 km (`depth_check`); treat `FastPlant._model/.carries/.run` as API.
**WP-12** — LLM P4 calls tools directly (no `check_draft`), `register_model` mutates the live registry; promotion = copy folders + registry + `.rst` via PR (S7 draft needs the card's partial free-cooling rule and tests first, `docs/results/s7_demo/README.md`).
**WP-13** — CI ruff skips `scripts/`. Eval F closed S2/S5/S8 in code; the full S1–S11 eval rerun (L2, laptop) is pending — expected S2, S5, S8 → pass; `cross_run_sizing` needs S1 in the same `run_eval` call (a `--scenarios S8` run alone reports "baseline S1 not found"). Missing-input detection is keyword-based (a request that names a "profile" or "aquifer" counts as providing it), so every non-brief prompt (S4–S9, user prompts) records one extra assumed `CFG-INPUT-DEFAULTS` answer in assume mode; the `change` answer escalates rather than re-basing the run; `cross_run_sizing` looks for the baseline run dir by scenario id (a baseline with `token_profiles` would be `S1-<profile>`); wp-reviewer (Eval F): yes/no removed from CFG-BASIS-CONFIRM (ambiguous for "confirm the change?"), check made candidate-scoped, same-year-mode and directional.
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

Runner J touched: `agents/engine.py` (`CHARS_PER_TOKEN`, `call_token_ceiling`, `_run(floor_tokens)`, `choose`), `runner/design_runner.py` (`TOKEN_BUDGET_OVERRUN`, `token_budget_overruns`, summary line), `runner/phases.py` (P5 budget), `eval/harness.py` (metric + column), tests (`agents/test_engine.py`, `runner/test_design_runner.py`, `runner/test_phases.py`, `eval/test_harness.py`), `README.md` (Limitations bullet), this file. Nothing in tools, models, `cli.py`, other config values or `runs/`.

Eval F touched: `agents/scope.py`, `runner/design_runner.py`, `runner/state.py`, `eval/scenarios.py`, `evals/scenarios.yaml`, `config/assumptions.yaml` (2 entries appended, no value changed), tests (`agents/test_scope.py`, `runner/test_design_runner.py`, `runner/test_real_tools.py` + fixture, `eval/test_scenarios.py`, `eval/test_harness.py`), `README.md`, this file. Nothing in tools, models, `cli.py`, config values or `runs/`.

WP-15 touched: `README.md` (new; GEMINI's → `README_GEMINI.md`), `NOTICE`, `docs/results/`, `scripts/build_report_pack.py`, `agents/engine.py`, `agents/model_builder.py`, `runner/design_runner.py` (`close`), tests, this file. wp-reviewer approved (5 findings fixed). Next: user records deck + video, merges, pushes the fork branch.

Feature B touched (new): `tools/whatif.py`, `tools/test_whatif.py`; edited: `cli.py` (`whatif`), `README.md` (What-if), `CLOUD_SETUP.md` §7.9, `.gitignore`, this file. Nothing in models, other tools, config or `runs/`.
