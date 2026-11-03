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
| Post-sub C | `--speak`, CHP 0.50 case, fig_tokens | merged (cf6ec2a) | [#31](https://github.com/Amaechi/gemini_digital_twin_private/pull/31) | `speech.py`; CHP case already in UQ-CASES (tests + README); token chart real in LLM runs; 11 `fig_tokens.png` redrawn |
| Runner J | Decision Record calls are not starved by the phase token budget | in review | | DR choice usage-limit floor; `TOKEN_BUDGET_OVERRUN`; P5 90 k tokens; L3 rerun after merge |

Status: todo · in progress · in review · merged · blocked.
Latest full suite (Runner J after merging main with Post-sub C, 11-03): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **1376 passed, 8 warnings in 605 s** (Runner J alone 1365 passed / 580 s; Post-sub C alone 1370 / 340 s). `validate_submission --pytest` → READY, 22 pass / 0 fail / WARN SUB-14 / MANUAL SUB-23; SUB-24 PASS (`docs/results/submission_check.md`).

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**Post-submission C — `--speak`, CHP 0.50 case, fig_tokens** (11-03, FINAL_SPRINT §C)
- `--speak` (`run`/`resume`/`whatif`): `speech.Speaker` runs macOS `say -- <text>` via `subprocess` (absent ⇒ one note, no-op; errors/timeouts caught). Speaks each InputRequest + default (ask mode wraps `ask_terminal`; pause/resume: the pending question), the P10 summary with the verdict (`RunResult.summary`), DR-11 from `decision_records/DR-11.json` (nothing recomputed), what-if verdicts + `DR-W<n>` rationale. Printed output unchanged; assume mode asks nothing. Tests monkeypatch `speech.subprocess.run`/`shutil.which`.
- CHP-EL-FRAC 0.50 was already in UQ-CASES (WP-11) and in the final TL-15 (`uncertainty_C2-opt.json`: LCOH 41.4, LCOC 25.8, CO₂ −245 t/yr, 23 MWh unmet heat on 2 days, DB-16 −0.87 K, C2-opt base sizes). Added the physics check that it reaches `chp.py` (CHP electricity/heat = f/(1−f): 0.5 base, 1.0 case), README Results row + Limitations bullet. Smoke budget keeps `uq_cases: false`; final runs untouched.
- fig_tokens: runner writes an interim `token_summary.json` before `plot_figures` and redraws `figures/fig_tokens.png` from the final summary in `_write_outputs` when it exists (`plots.render_tokens_figure`; non-fatal). FunctionModel-path test: in-run chart has bars, run-end chart differs; stub runs draw nothing. **Regenerated (figures only)** with `scripts/regenerate_fig_tokens.py`: `runs/final/final_llm` + 10 eval figures (gemma4_12b S1/S4/S5/S6/S8/S9/S10-naive/S10-optimised/S11, qwen3.6_27b S11); S2/S3/S7 and the other models' S11 never reached P10 (skipped). `docs/results/final_llm/fig_tokens.png` copied, REPORT.md §13 caption updated (only that line). Nothing else under `runs/` changed.
**Runner J — DR calls not starved by the phase token budget** (11-03, FINAL_SPRINT §J, PR #32)
- Cause (evidence `origin/qwen27b-evidence` and `runs/final/eval/gemma4_26b-a4b/S11`): every call's `total_tokens_limit` = tokens left in the phase; each P5 DR choice costs 8–9 k tokens with its 2 retries on verbose models, so ≈ 56–60 k of P5's 60 k were used when the mandatory DR-08 came → `UsageLimitExceeded` → escalation mid-DR.
- `LLMEngine.choose` floors the usage limit at `call_token_ceiling` = n·(p + overhead) + o·n(n+1)/2 (n = `max_output_retries` + 2 requests; p = prompt + instructions at `CHARS_PER_TOKEN` 3, measured 2.9–3.7 chars per logged input token on the evidence runs; `REQUEST_OVERHEAD_TOKENS` 1024 for the output schema and retry feedback; o = `calls.max_output_tokens`) ≈ 36 k for a 3 k-token prompt, one request of surplus. `decide()` unchanged (AG-04: 0 tokens left ⇒ escalate before any call).
- Runner `_decision_record`: a DR past the phase `max_tokens` records `TOKEN_BUDGET_OVERRUN` (dr_id, tokens_used, max_tokens, overrun, error) instead of escalating; past even its own ceiling the policy chooses (`policy_fallback`, counted) with the same event — no DR escalates any more. `token_budget_overruns(run_id)`, run-summary count, harness `RunMetrics.token_budget_overruns` + "Overruns" column (stored `runs/final/eval` md not regenerated). "request_input normalisation" (§J) has no LLM call (`_coerce_answer`), nothing to floor.
- `phases.py`: P5 `max_tokens` 90 000 (2 tools + 7 DRs ≈ 80 k on verbose models); other phases keep 60 k; no other config value; `runs/final*` untouched.
- Tests (FunctionModel, set `RequestUsage`): P5 at 59 k used writes DR-08 by the LLM, 1 overrun, COMPLETED, harness metric 1; a 200 k-token DR-08 call → policy, fallback 1, overrun with error; a P2 decision after the whole budget escalates "no tokens left" with no second call; engine floor/ceiling tests; P5 budget largest. wp-reviewer: 5 findings, all applied.
**Merged WPs** (one line each; the full decisions are in the PR descriptions and `git log -p` of this file)
- Eval F (#29): P0 lists missing challenge inputs (`missing_request_inputs` → `MISSING_INPUTS`); P1 opens with CFG-BASIS-CONFIRM (`keep`/`change`, change ⇒ escalate) then one CFG-INPUT-DEFAULTS question; `cross_run_sizing` check (S8, baseline S1 in the same eval dir); S2/S5/S8 pass on real tools; keyword-based missing-input detection.
- Feature B (#28): `cli whatif --run-id --set LEVER=v` = `tools.whatif`; 9 levers (DB-31/32, DB-30, ECO-CO2-GAS, CHP-EL-FRAC, OPT-ROBUST-BACKUP, flow, doublets, concept) applied as TL-15 does; guard → TL-12 → verifier per side in `runs/<id>/whatif/<n>/`, the run's files never written; DR-W<n>; DB-31=65 on the final run stays ACCEPTED (LCOH 44.6 → 58.4).
- Eval F (#29): P0 lists missing request inputs (`scope.missing_request_inputs`, keyword rules); P1 asks CFG-BASIS-CONFIRM (keep/change; change escalates) then one CFG-INPUT-DEFAULTS question; S8 `cross_run_sizing` vs S1 (PI, wells, ESP, peak ESP power); S2/S5/S8 closed in code, eval rerun pending (L2).
- Feature B (#28): `cli whatif --run-id --set LEVER=v [--budget]` → `tools.whatif`: before/after on the selected design in `runs/<id>/whatif/<n>/` (run files byte-identical); levers DB-31/32, DB-30, ECO-CO2-GAS, CHP-EL-FRAC, OPT-ROBUST-BACKUP, design_flow_m3h, doublets, concept; `DR-W<n>`; on final_llm DB-31=65 stays ACCEPTED (LCOH 44.6 → 58.4), OPT-ROBUST-BACKUP=off reproduces DR-11.
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
- ~~`plots.plot_tokens` runs in P10 before `token_summary.json` exists~~ fixed in Post-sub C (interim summary + run-end redraw; committed figures regenerated).
- `run_eval.py` rewrites `eval_results.json` per call (S11-only line lost); `--compare` sums every scenario. Fix: per-scenario results, compare S11 only.
- ~~P5 DR-08 on gemma4:26b-a4b escalated on `UsageLimitExceeded` instead of falling back~~ fixed by Runner J (#32); the submitted runs predate it; L3 (qwen3.6:27b) rerun after the merge. Final run DR-10 and P7 were policy fallbacks (criterion not offered; `call_tool` without a tool): add to the few-shot examples. Eval harness: add an `intake_closed` metric.
**WP-07** — DSP-FLOW alternatives unpriced; DSP-HP-MIN `min_part_load_frac` 0 cheaper on C2 (register undecided); fast year 7–25 s at ≥ 3 km; C6 `storage` fails PH-20/PH-22; treat `simulate_day._flag_text/_heat_pump_*`, `Mixer._MIN_TOTAL_FLOW` as API.
**WP-11** — DR-06 cites TL-13, not the outage days; TL-13 `rejected` ↛ `guard_failed`; no HT-ATES capex (C6 unpriced); WACC view (ECO-06) missing; saturation data > 100 °C re-opens ≥ 3 km (`depth_check`); treat `FastPlant._model/.carries/.run` as API.
**WP-12** — LLM P4 calls tools directly (no `check_draft`), `register_model` mutates the live registry; promotion = copy folders + registry + `.rst` via PR (S7 draft needs the card's partial free-cooling rule and tests first, `docs/results/s7_demo/README.md`).
**WP-13** — CI ruff skips `scripts/`. Eval F closed S2/S5/S8 in code; the full S1–S11 eval rerun (L2, laptop) is pending (expected S2, S5, S8 → pass; `cross_run_sizing` needs S1 in the same `run_eval` call). Missing-input detection is keyword-based, so every non-brief prompt records one extra assumed `CFG-INPUT-DEFAULTS` answer in assume mode; the `change` answer escalates rather than re-basing the run.
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

Post-sub C touched (new): `speech.py`, `scripts/regenerate_fig_tokens.py`, `test_speech.py`; edited: `cli.py` (`--speak`), `runner/design_runner.py` (interim/final token summary + figure), `tools/plots.py` (`plot_token_summary`, `render_tokens_figure`), `scripts/build_report_pack.py` (caption), tests (`test_cli.py`, `tools/test_plots.py`, `tools/test_whatif.py`, `tools/test_uncertainty.py`, `runner/test_design_runner.py`), `README.md`, `docs/results/REPORT.md` (§13 caption) + `docs/results/final_llm/fig_tokens.png`, 11 × `runs/final/**/figures/fig_tokens.png`, this file. No model, tool physics, config value or other run file changed.

Runner J touched: `agents/engine.py` (`CHARS_PER_TOKEN`, `call_token_ceiling`, `_run(floor_tokens)`, `choose`), `runner/design_runner.py` (`TOKEN_BUDGET_OVERRUN`, `token_budget_overruns`, summary line), `runner/phases.py` (P5 budget), `eval/harness.py` (metric + column), tests (`agents/test_engine.py`, `runner/test_design_runner.py`, `runner/test_phases.py`, `eval/test_harness.py`), `README.md` (Limitations bullet), this file. Nothing in tools, models, `cli.py`, other config values or `runs/`.
