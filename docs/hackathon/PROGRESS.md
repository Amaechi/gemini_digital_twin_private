# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p` of this file.

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
| Eval F | Close S2 (request inputs), S5 (confirm basis), S8 (cross-run sizing) | in review | | P1 intake questions CFG-INPUT-DEFAULTS / CFG-BASIS-CONFIRM; `cross_run_sizing`; eval rerun pending (L2) |

Status: todo · in progress · in review · merged · blocked (say why).
Latest full suite (Eval F, 11-03): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **full suite running at commit time; count recorded in the follow-up commit**. Before (WP-15, 11-03): 1334 passed, 7 warnings in 475 s. `validate_submission --pytest` → **READY, 22 pass / 0 fail / WARN SUB-14 / MANUAL SUB-23; SUB-24 PASS "1334 passed, 7 warnings in 472.38s" (`docs/results/submission_check.md`). CI smoke `--budget smoke`: 22.8 s (WP-12 follow-up measurement). After the reviewer's guard fix in `close_model_clients` only `agents/test_engine.py` + `test_cli.py` were re-run**.

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**Eval F — S2/S5/S8** (11-03)
- P0 intake lists the challenge inputs a user-written request lacks (`agents/scope.missing_request_inputs`: demand CSVs, cooling demand, reservoir data; keyword rules; the empty prompt = full brief is never scanned) → `RunState.missing_inputs`, `MISSING_INPUTS` event.
- P1 opens with the runner's own questions (`_intake_requests`: before any tool or register gap, asked once, idempotent over resume): CFG-BASIS-CONFIRM when a CONFLICT names (DB-16)/(DB-17) — `keep` (default, assumed in `--input-mode assume`) continues on the basis, `change` escalates (new limits in config/ + a new run; never designed for) — then ONE CFG-INPUT-DEFAULTS question naming the missing inputs, default "repository defaults" (`defaults`; other text is an InvalidAnswer saying where the inputs go). S1 unaffected: DB-30/31/32 + approval (fixture `runner/fixtures/s1_policy_smoke.json`, recorded before the change).
- Check `cross_run_sizing` (`baseline: S1`, same eval dir): PI (TL-04), wells (doublets, depth, flow), ESP stages / setting depth (TL-05 `doublet.esp`), peak `elec_esp_*_mw` of the selected design's annual CSV; passes at a lower PI when the wells differ or the ESP is larger (> 5 % peak); fails naming the reason without S1 or without a PI (stubs). Stored gemma4 S8 evidence passes (PI 3.06 vs 12.22; 49 stages @ 1560 m, 1.43 MW vs 16 @ 840 m, 0.46 MW).
- scenarios.yaml: S2 `pending_question id CFG-INPUT-DEFAULTS`, `question_mentions`, `tool_calls_max 0`; S5 `assumption_or_question CFG-BASIS-CONFIRM`, `status completed`; S8 `cross_run_sizing`; no pending left on S2/S5/S8. Stubs: S2/S5 pass, S8 fails `dr_mentions` + `cross_run_sizing`. `cli.py` untouched.

**WP-15** (11-03)
- Top-level `README.md` is GeoAgent's (organisers' entry point; SUB-22 needs the word "placeholder"); GEMINI's README kept verbatim as `README_GEMINI.md`.
- `docs/results/REPORT.md` is generated by `scripts/build_report_pack.py` from run artefacts only (each table cites its file); LLM-run figures copied to `docs/results/final_llm/`; `fig_tokens_by_phase.png` drawn from `token_summary.json` because the run's `fig_tokens.png` is a placeholder (defect, see follow-ups).
- S7 evidence stored as `.py.txt` + JSON in `docs/results/s7_demo/` (nothing collectable); `runs/final/eval` untouched (SUB-17).
- S11 table uses each model's S11 row from `eval_results.json`; gemma4's L-1 S11-only line (79 k tokens, 100 %, 0 fallbacks, 328 s) was overwritten by the S1–S11 eval (known `run_eval` behaviour) and survives only here; `model_comparison.json` sums all 12 gemma4 runs.
- Clean shutdown: `DecisionEngine.close()` (no-op), `LLMEngine.close()` → `close_model_clients()` closes the `AsyncOpenAI`/httpx clients on the loop pydantic-ai used, then the loop; `ResilientEngine.close()` delegates; `DesignRunner.close()` calls it; model-builder `main` closes in `finally`. Exit codes unchanged (tests).
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
- `runner/real_tools.py`: `default_toolbox()` = real tools via a per-run `ToolSession` (`verify_design` first); P3 guard + `depth_check`; P5 fast year + TL-14; P6 TL-13 → `<concept>-opt`; P7 full year; P8 TL-15 + `sensitivity`; P10 figures + report. rank = LCOH + LCOC. Verifier PH-25…30, PD-11/12; PH-19 no NPSH_R ⇒ NPSH_A ≥ 15 m; depth ≤ 2.5 km. RUN-BUDGET full / quick / smoke (CI); SIGTERM → SystemExit.
**WP-11**
- TL-13: doublets × depth screen → per `top_k` seeded DE (sizes, then outages); boiler/e-chiller right-sized and priced; rule 4 guard before/after TL-09; objective LCOH + LCOC (30 yr) + OPT-OBJ. TL-15: `Scenario` = config copy + overrides + year scale/shift + CostBook factors; tornado; `robust_backup_mw` = P90 need. [A]: VER-HP-ETA via `max_carnot_fraction`; ±6 K = gradient ± 6/depth; II 0.65–1.0. Touches: `injectivity_factor`, chp `electrical_fraction` (1/3).
**WP-07**
- TL-12 fast = TL-06 map + design `PlantSpec` models (SIM-FAST); full = fast plan via `simulate_plant_day`. Merit order HEX → CHP → HTHP → boiler; DSP-FLOW; cooling free → absorption → electric; DB-30 SIM-OUTAGE lowest-load; C6 discharge before boiler; full year 0 invalid d.
**WP-13**
- `src/gemini_agent/eval/` (harness, scenarios, submission) + thin scripts; `evals/scenarios.yaml` checks decide, `pending` = needs + reason; engines policy / testmodel / llm; `--compare` ⇒ `model_comparison.{json,md}`; S10 naive = `DesignRunner(history_events=None, compact_history=False)`; SUB-01…24, only FAIL blocks.
**WP-12**
- TL-17 needs = WP-06 `concept_layout` types; 15 LIM-*; sandbox outside the repo, 5 files of a NEW `gemini_model/<name>/`, `screen_python` allow-list; `run_pytest` isolated (120 s); `register_model` in-memory only; coder ≤ 3 drafts, 40 k tokens; policy P4 = gap analysis.
**WP-06**
- generated HPs = WP-03 closed form, Lorenz; VER-HP-ETA cap binds (C2 design COP 3.86); TL-09 `PlantSpec` → `build_plant_project` (Kahn); CFG-LAYOUT degasser → filter → [absorption, Ti] → HEX-1 → [HP evap] → injector; district HEX → CHP → HPs → boiler; CFG-HEX UA from RC-03 eq 1; CFG-CHP 0.96; CFG-BOILER 0.90; TL-11 HEX share bisected for DB-12; TL-10 limits.yaml + DB-40.
**WP-08**
- costs only via `CostBook` (EUR × 1.13, DEA × 1.168, AUD × 0.63, CEPCI 2023); LCOH/LCOC Gippsland Eq. 1, 8 % real, 30 + 20 yr; ECO-REAL +2.44 %/yr; ECO-OM 1.5 %; HTHP 15 yr; ECO-ALLOC by brine heat; ECO-05 CO₂ factors; capex 2 wells/doublet (NREL); ECO-CHILLER RC-05 × 2.51 (VERIFY); ECO-07 benchmarks; exergy T₀ daily dry bulb; TL-22 no chemistry ⇒ "insufficient data"; TL-23 NET-LOSS-FRAC 10 %.
**WP-03**
- HP `solver: "closed_form"` (default NM unchanged); COP cap VER-HP-ETA × Carnot (HP-APPROACH 4 K): design 3.71; HTHP limits None = MISSING; EB-ETA 0.99; STO-REC-HT 0.63 / -COLD 0.96; CHP-EL-FRAC 1/3.
**WP-05**
- verifier gets bare p_b bar(g); VER-ESP-MARGIN = SUB-PBUB margin; SUB-PBUB Henry 46.05 bar(a), setpoint 55.26, ESP 840 m; TL-05 `brentq` 30–70 Hz; P_el = QΔp/0.70/(0.93·0.97); SUB-ESP HC27000 × 1.642; maps cached `runs/cache/doublet_maps/`; TL-07 t_b 33.9 yr @ 1.2 km; TL-08 1141 m (2) / 1787 m (3).
**WP-09**
- REJECTED iff any fail; UNVERIFIED if a required check is `not_evaluated` or stub; margin ≥ 0 passes; PH-22 first law; runner: real UNVERIFIED never loops, any stub ⇒ not ACCEPTED; append-only events + `replay`; cache key SHA-256; knowledge = durable `USER_ANSWER`s.
**WP-02**
- COOL-ELC η 0.508 [D] (COP 4.39 @ 7.2/32.2 °C), cap 8; cooling = `StaticModel`s, `power_th` = useful cooling; COOL-ABS-B 1.15 (85 °C → COP 0.64); COOL-ABS-DRV ≈ 13.9 m³/h per MW; COOL-FC T_db + 8 K; HEX per-side ρ/c_p.
**WP-10** / review
- typed `Decision`s; runner executes tools; role lists ≤ 6; `NativeOutput`, 2 retries, `policy_fallback`; DR-09 P2, DR-01…05/07/08 P5, DR-06 P6, DR-10 end P9; P7/P9 REJECTED → P6 (≤ 3); approval after P10; CLI exits 0 ok, 1 config, 2 usage, 3 paused, 4 escalated, 5 refused, 6 rejected; AG-02 refuses digits outside an id; DR-10 never offers REJECTED.
**WP-01** / review
- `"pvt"` on wells (ρ, μ, c_p together; DB-08/DB-33 1030, 0.40e-3, 4100); `kpi.py` ρ/c_p from producer `pvt`, else 1050/4200; separator gas CH₄ + CO₂ = 1, LHV 35.8 MJ/Nm³; ESP head/power × pvt SG; registered `heatpump`, `compressor`, `co2_corrosion`.
**WP-04**
- tool signature `(request=None, *, config_dir=None)`; demand warns if CSV ≠ DB-18/19; DB-25 per card (PI 12.2 → 24.5 bar); lowest-load window = min rolling 35-day sum; daily climate, Stull T_wb.
**WP-00**
- ECO-04 native units/currency/year, USD in code; `StrictModel` schemas; day-average MW; hard limits in DesignGuard; Ollama tags verified 31 Oct; Pydantic AI 2.64.0; cloud = policy / TestModel only.

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30…DB-38, DB-34a–d, DB-34-RH, DB-40 (no DB-39), ECO-CUR. WP-01, WP-13, WP-12, WP-14, follow-ups, WP-15: none. Eval F (WP-13 follow-up): CFG-BASIS-CONFIRM, CFG-INPUT-DEFAULTS (both `added_by: WP-13 follow-up`; no design value depends on them).
- WP-04: SUB-RHOC, SUB-II, DEM-COP-HD. WP-02: COOL-ABS-B/CAL/CAP/LB/AUX/PL/DRV, COOL-ELC, COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB, VER-ESP-MARGIN, VER-HP-ETA, VER-HP-LIFT. WP-05: SUB-WELL, SUB-PWH, SUB-PBUB, SUB-NPSH, SUB-ESP, SUB-PSUC, SUB-SPACING.
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC. WP-08: ECO-CO2-GAS/AG/SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT/SURFACE/HEX/HP/CHP/BOILER/GRID-GAS/ASSOC-GAS/SIZING. WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE. WP-11: OPT-SPACE, OPT-OBJ, OPT-DE, UQ-RANGES, UQ-CASES, UQ-MC. Pre-L-2: OPT-ROBUST-BACKUP.

## Open follow-ups (after submission; grouped by owner)
**Defects found in WP-15 (recorded, not fixed; README Limitations)**
- `plots.plot_tokens` runs in P10 before `token_summary.json` is written at run end ⇒ the run's `fig_tokens.png` is the policy placeholder even for LLM runs. Fix: write the summary before P10 plots or draw from the logger. Report pack draws `docs/results/final_llm/fig_tokens_by_phase.png` meanwhile.
- `run_eval.py` rewrites `eval_results.json` per call (S11-only line lost after S1–S11); `--compare` sums every scenario in a folder. Fix: keep per-scenario results, compare S11 only.
- P5 DR-08 choice on gemma4:26b-a4b escalated on `UsageLimitExceeded` (phase token budget 765 left vs 1 900 needed) instead of falling back; consider a minimum per-call floor or a fallback for the last call of a phase.
- Final run DR-10 was a policy fallback (model named a criterion not offered, twice); P7 fallback (`call_tool` step without a tool, twice). Add these to the few-shot examples.
- Eval harness: add an `intake_closed` metric (grep `INTAKE_CLOSED` meanwhile).
**WP-07** — DSP-FLOW alternatives unpriced; DSP-HP-MIN `min_part_load_frac` 0 cheaper on C2 (register undecided); fast year 7–25 s at ≥ 3 km; C6 `storage` fails PH-20/PH-22; treat `simulate_day._flag_text/_heat_pump_margins/_heat_pump_screening`, `Mixer._MIN_TOTAL_FLOW` as API.
**WP-11** — DR-06 cites TL-13, not the outage days; TL-13 `rejected` ↛ `guard_failed`; no HT-ATES capex (C6 unpriced); WACC view (ECO-06) missing; saturation data > 100 °C or a pressurised degasser re-opens ≥ 3 km (`depth_check`); treat `FastPlant._model/.carries/.run` as API.
**WP-12** — LLM P4 calls tools directly (no `check_draft`), `register_model` mutates the live registry; promotion = copy folders + registry + `.rst` via PR (S7 draft needs the card's partial free-cooling rule and validation tests first, see `docs/results/s7_demo/README.md`).
**WP-13** — CI ruff skips `scripts/`. Eval F closed S2/S5/S8 in code; the full S1–S11 eval rerun (L2, laptop) is pending — expected S2, S5, S8 → pass; `cross_run_sizing` needs S1 in the same `run_eval` call (a `--scenarios S8` run alone reports "baseline S1 not found"). Missing-input detection is keyword-based (a request that names a "profile" or "aquifer" counts as providing it); the `change` answer escalates rather than re-basing the run.
**WP-14** — No `AbsorptionPoint` ⇒ C3/C4 UNVERIFIED; `HeatPumpPoint` lacks max sink T; ESP intake +0.86 bar over 1.2·p_b (pure water fails: ESP ≈ 65 m deeper); free cooling 87 % on smoothed normals; HEX rated at 300 m³/h; boiler spare 0.03 MW; CLI `resume` hint omits `--runs-dir`; budget not stored in the run; DR-11 variant priced on the base year only, no own MC (≈ +2 USD/MWh LCOH); `robust_backup` never escalates.
**Spec/card nits** — GM-05 η ≈ 0.40 (code follows RC-06); DB-25 ≈ 22 bar (card 24.5); RC-09 "0.55 K/km" not reproducible; RC-10 lacks ESP/injection-pump capex; negative `gas_water_ratio` accepted; DPDT stores params before a bad `pvt` raises; kpi defaults 1050 kg/m³ unsuffixed; PH-17 checks pump triples; dissolved-gas NPSH margin unquantified; `resume <unknown id>` creates `runs/<id>/`; `--run-id` unsanitised; `heat_pump_1987_usd_per_kw` unescalated.
**All WPs** — GEMINI's `run_linter.sh` (black 25.11.0, isort 7.0.0, flake8 7.3.0 + docstrings, 100 cols): 42 pre-existing flake8 findings (WP-04 climate/demand/design_basis + tests); many GEMINI files are CRLF (keep line endings). Act/format split (spec §5.4.3) not implemented.

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
- [x] `llm_calls.jsonl` + `token_summary.json`; S10 (−45 %) and S11 — `runs/final/final_llm/`, `runs/final/eval/` (SUB-12/15/16 pass).
- [x] S1–S11 transcripts — `runs/final/eval/gemma4_12b-it-q4_K_M/` (SUB-17 pass; S7 "fail" predates #23, evidence `docs/results/s7_demo/`).
- [x] Apache-2.0 notices — `NOTICE` (audited lists), SUB-20/21 pass; LICENSE unchanged; no secrets (grep); `.env.template` untouched.
- [x] README with API placeholders (SUB-22 pass), `docs/results/REPORT.md`, `DECK_OUTLINE.md`.
- [ ] 10 slides + appendix, ≤ 5-min video (SUB-23, user records from `DECK_OUTLINE.md`).
- [ ] Code pushed: merge this PR, then CLOUD_SETUP §8 / README "Submission": fork the public GEMINI repo, `git push submission main:hackathon/<team-name>`, submit branch URL + deck + video.

Eval F touched: `agents/scope.py`, `runner/design_runner.py`, `runner/state.py`, `eval/scenarios.py`, `evals/scenarios.yaml`, `config/assumptions.yaml` (2 entries appended, no value changed), tests (`agents/test_scope.py`, `runner/test_design_runner.py`, `runner/test_real_tools.py` + fixture, `eval/test_scenarios.py`, `eval/test_harness.py`), `README.md`, this file. Nothing in tools, models, `cli.py`, config values or `runs/`.

WP-15 touched: `README.md` (new; GEMINI's → `README_GEMINI.md`), `NOTICE`, `docs/results/{REPORT.md,DECK_OUTLINE.md,final_llm/,s7_demo/}`, `scripts/build_report_pack.py`, `agents/engine.py` (`close`), `agents/model_builder.py` (main `finally`), `runner/design_runner.py` (`close` → engine), tests `agents/test_engine.py`, `test_cli.py`, this file. Nothing in tools, models, config or `runs/`. wp-reviewer: approved; its 5 findings (README traceability ×3, guard around client close, public loop assertion) fixed. Next: user records deck + video, merges, pushes the fork branch.
