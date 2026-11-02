# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p` of this file.

## Status board
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | doublet example 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3), [#5](https://github.com/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](https://github.com/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | merged (6fa9667) | [#11](https://github.com/Amaechi/gemini_digital_twin_private/pull/11) | closed-form HP (capped 3.71 @ design); e-boiler; storage |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](https://github.com/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](https://github.com/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | merged (9eb7beb) | [#13](https://github.com/Amaechi/gemini_digital_twin_private/pull/13) | TL-09/10/11; 9 CFG-*; `DayResult.flags` |
| WP-07 | Annual simulation & dispatch | merged (a68c2c9) | [#17](https://github.com/Amaechi/gemini_digital_twin_private/pull/17) | TL-12; 1211 passed (merged main) |
| WP-08 | Economics, CO₂, exergy, scaling | merged (66acde7) | [#12](https://github.com/Amaechi/gemini_digital_twin_private/pull/12) | TL-14/22/23; C0 LCOH 137 $/MWh ≈ ACM cap |
| WP-09 | Verifier, memory, logging | merged (901e110) | [#9](https://github.com/Amaechi/gemini_digital_twin_private/pull/9) | 41-check DesignVerifier |
| WP-10 | Agents, runner, CLI | merged (2285460; review a9083b7) | [#4](https://github.com/Amaechi/gemini_digital_twin_private/pull/4), [#6](https://github.com/Amaechi/gemini_digital_twin_private/pull/6) | policy P0–P10 on stubs |
| WP-10 L-1 | LLM-path hardening from L-1 | in review | [#19](https://github.com/Amaechi/gemini_digital_twin_private/pull/19) | kinds, call limits, dup guard, DR criteria |
| WP-11 | Optimisation & uncertainty | merged (78491fe) | [#18](https://github.com/Amaechi/gemini_digital_twin_private/pull/18) | TL-13/15 |
| WP-12 | Model gap & Model Builder | merged (b81cfa9) | [#15](https://github.com/Amaechi/gemini_digital_twin_private/pull/15) | TL-17/18; S7 demo |
| WP-13 | Eval suite & submission checks | merged (a622551) | [#16](https://github.com/Amaechi/gemini_digital_twin_private/pull/16) | S1–S11; SUB-01…24 |
| WP-14 | Integration run & figures | todo | | Batch 7 |
| L-1 | Local: pick LLM via S11 eval | todo | | user, local Ollama |
| L-2 | Local: final LLM run + eval transcripts | todo | | user, local Ollama |
| WP-15 | Final review & packaging | todo | | |

Status: todo · in progress · in review · merged · blocked (say why).
Latest full suite (L-1 follow-up, 11-02): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **1273 passed**.
Next: Batch 7 (WP-14).

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**WP-11**
- TL-13: screen doublets × depth → per `top_k` combo seeded DE (`deferred` ⇒ serial = pool): sizes (flow, HTHP, absorption) then outages (sorted-gap, x0 = SIM-OUTAGE, plant reused + memoised).
- boiler/e-chiller cap-only ⇒ right-sized (largest daily output; chiller also the DB-19 peak at DB-34b ambient), priced, optimum re-run with them (`best` = None if that fails); free-cooling tower beyond the chiller plant's priced (`cooling_tower_mw`).
- rule 4: guard before and after TL-09; year margins DB-11/12/14 < 0 rejected; DB-16/17 < 0 = unmet demand (penalised), rejected only without unmet; barrier 1e6 + reason; objective LCOH + LCOC (30 yr) + OPT-OBJ.
- then: SIM-OUTAGE default, DSP-* bounds (report), Pareto ≤ 3, C6 scan unpriced.
- TL-15: fixed design; `Scenario` = config copy + GEMINI overrides + year scale/shift (warm on cooling days) + `DoubletDesign` + CostBook factors; world cases keep base capex, DB-31/CHP-EL-FRAC re-derive; tornado = LCOH+LCOC swing; `robust_backup_mw` = P90 need.
- [A]: VER-HP-ETA via `max_carnot_fraction` + `HP_DATASHEET_LABEL`; ±6 K = gradient ± 6/depth; II 0.65–1.0; not varied: SUB-RHOC/WELL/ESP/NPSH/PWH, HP-ETA-L.
- touches: `DoubletDesign.injectivity_factor` (0.8 ⇒ +6.1 bar) + test; chp.py `electrical_fraction` (default 1/3) + test; TL-09 writes it only if CHP-EL-FRAC ≠ 1/3, no longer raises (test edited); CHP-EL-FRAC/SUB-RHOC text.
**WP-07**
- TL-12 fast = TL-06 map + design `PlantSpec` models direct (SIM-FAST); full = fast plan via `simulate_plant_day`.
- merit order = topology HEX → CHP → HTHP → boiler; DSP-FLOW min equal flow w/o boiler, off < 50, ≤ design.
- cooling free → absorption (DSP-ABS-MIN; drive ≥ COOL-ABS-DRV × 1.01, ΔT ≤ 40 K) → electric; DSP-HP-MIN off if ≤ +10 % brine.
- DB-30: SIM-OUTAGE lowest-load; B drops lowest heat + cool window; availability = calendar; C7 suction = WHP.
- C6: power required, discharge before boiler; SIM-06 via TL-07; full year 0 invalid d, ≤ 0.36 %.
**WP-13**
- `src/gemini_agent/eval/` (harness, scenarios, submission) + thin `scripts/run_eval.py`, `validate_submission.py`; `evals/scenarios.yaml` checks decide, `pending` = needs + reason.
- engines policy / testmodel / llm (`runs/eval/<tag>/`); metrics incl. tokens; `--compare` ⇒ `model_comparison.{json,md}`; S10 naive = `DesignRunner(history_events=None, compact_history=False)`.
- today: pass S3, S4, S9; pending S1, S2, S5–S8; S10/S11 skip on policy. WP-10 touch: `constraint_conflicts(prompt, limits, basis=None)` (S5, S9).
- `validate_submission`: SUB-01…24; only FAIL blocks; SUB-08 needs `verify_design`; SUB-07 unmet > 0 ⇒ MANUAL; eval root `runs/final/eval` (tracked).
**WP-12**
- TL-17 needs = WP-06 `concept_layout` types; 15 LIM-* entries; sandbox outside the repo or `runs/<id>/`, 5 files of a NEW `gemini_model/<name>/`, `screen_python` allow-list.
- `run_pytest` isolated child (120 s), pass = rc 0 + ≥ 1 test + files unchanged; `register_model` in-memory only; coder ≤ 3 drafts, 40 k tokens; policy P4 = gap analysis.
**WP-06**
- generated HPs = WP-03 closed form, Lorenz; VER-HP-ETA cap binds everywhere (C2 design COP 3.86), η_L inert above ≈ 0.41; HP flags → `DayResult.flags`, margins.
- TL-09 `PlantSpec` → `build_plant_project` (Kahn order); `generate_plant(candidate, loads, PlantDispatch)`; CFG-LAYOUT brine degasser → filter → [absorption, Ti] → HEX-1 → [HP evap] → injector; district HEX → CHP → HPs → boiler.
- C6 = parent plant; C7 WHP 54.25 bar(g) flagged; CFG-SURFACE letdown to SUB-PSUC; CFG-HEX UA from RC-03 eq 1 (85 °C brine → 82.2/58 °C, 3.19 MW/K); CFG-HP floor max(T_wh − 40, 100 − VER-HP-LIFT).
- CFG-CHP 0.96; CFG-BOILER 0.90; grid gas CO₂ = ECO-CO2-GAS; CFG-SIZING = peaks; TL-11 HEX share bisected for DB-12; TL-10 limits.yaml + DB-40, duplicate = hash {project, design}.
**WP-08**
- costs only via `CostBook.usd`/`usd_value` (EUR × 1.13, DEA × 1.168, AUD × 0.63, CEPCI 2023); sensitivity max(±30 %, range); `economics`/`cost_sensitivity` use `book.fresh()`.
- LCOH/LCOC Gippsland Eq. 1, 8 % real, 30 + 20 yr; ECO-REAL +2.44 %/yr; ECO-OM 1.5 %; HTHP 15 yr; ECO-ALLOC by brine heat; ECO-ELEC-NET no credit; degas_use ⇒ associated gas first.
- ECO-05: ECO-CO2-GAS 56.6 (51.53 case), ECO-CO2-AG 68.56, DB-37 0.3 kg/kWh, direct only; capex 2 wells/doublet (NREL), ECO-CHILLER RC-05 × 2.51 (VERIFY), boiler 374 $/kW, dry cooler 3× tower, Ti × 1.125.
- ECO-07 benchmarks (ACM cap 137.7, Gippsland 43.5, WVU 41.5); exergy T₀ daily dry bulb; TL-22 no chemistry ⇒ "insufficient data"; TL-23 else NET-LOSS-FRAC 10 %.
**WP-03**
- HP `solver: "closed_form"` (default NM unchanged); idle on zero flow / no need / COP ≤ 1; COP cap VER-HP-ETA × Carnot (HP-APPROACH 4 K): design 3.71, brine out 47.1 °C.
- HTHP limits None = MISSING (uncited raise), else VER-HP-LIFT 60 K; EB-ETA 0.99; STO-REC-HT 0.63 / -COLD 0.96 on discharge (`net_heat_to_plant_w`); CHP-EL-FRAC 1/3.
**WP-05**
- verifier gets bare p_b bar(g); VER-ESP-MARGIN = SUB-PBUB margin (enforced); SUB-PBUB Henry 46.05 bar(a) (pure water 52.17), setpoint 55.26, ESP 840 m.
- TL-05 `brentq` 30–70 Hz; P_el = QΔp/0.70/(0.93·0.97); SUB-ESP HC27000 × 1.642, 16 stages; 300 m³/h: 58.9 Hz, 0.81 MW_e, T_wh 85.2 °C, inj. WHP 26.5 bar(g).
- maps 50–300 m³/h cached `runs/cache/doublet_maps/`; TL-07 ±6 K: t_b 33.9 yr @ 1.2 km; TL-08 ≤ 1 bar: 1141 m (2) / 1787 m (3).
**WP-09**
- REJECTED iff any fail; UNVERIFIED if a required check is `not_evaluated` or stub; 41 checks; margin ≥ 0 passes; no p_b ⇒ VER-ESP-PB 60 bar(a).
- PH-22 via `brine_heat_drawn_mw` + first law; runner: real UNVERIFIED never loops, any stub ⇒ not ACCEPTED; `verify_design_spec` not in `default_toolbox`.
- append-only events + `replay`; cache key SHA-256 + `<tool>:<mode>:v<N>`; knowledge = durable `USER_ANSWER`s; one `LLMCallRecord.from_messages`.
**WP-02**
- COOL-ELC η 0.508 [D] (COP 4.39 @ 7.2/32.2 °C), COP cap 8; COOL-HR `include_heat_rejection_auxiliaries`; cooling = `StaticModel`s, `power_th` = useful cooling, 20/26 °C.
- COOL-ABS-B 1.15; 85 °C drive → COP 0.64; COOL-ABS-DRV ≈ 13.9 m³/h per MW; PL 0.65 → 0.47 at 10 %; AUX 2.2 %; COOL-FC T_db + 8 K; HEX per-side ρ/c_p.
**WP-10 L-1 follow-up** (11-02)
- kind set from tool before validation (`validation_context` hook, `KIND_COERCED`); prompt STEP_KINDS + 2nd example. `llm.yaml calls`: `reasoning_effort` "none", `max_tokens` (profile flag), `request_timeout`; Ollama's grammar skips thinking.
- dup guard: engine steps; tool + normalised args + `ToolContext` hash, same loop; open required tool may repeat once; 2nd repeat closes phase; reviewer sees its rejections. DR: criteria + `evidence_keys`; choice ∈ best, rationale names key. Touched `schemas/config.py`, `llm.yaml`, `eval/harness.py`.
**WP-10 review**
- AG-02 refuses digits outside an id; AG-06 needs a design word; DB-30 'B' = no outage; DR-10 never offers REJECTED.

**WP-10**
- typed `Decision`s; runner executes tools; role lists ≤ 6; `NativeOutput`, 2 retries, `policy_fallback`; `UsageLimitExceeded` escalates.
- DR-09 P2, DR-01…05/07/08 P5, DR-06 P6, DR-10 end P9; P7/P9 REJECTED → P6 (≤ 3, 4th escalates); approval after P10.
- recommended = top `rank_candidates` (unmet last, lowest LCOH), DR-06 DB-30, DR-08 DB-31, DR-09 Ti; guard-failed/REJECTED ineligible.
- P1 gaps DB-30/31/32; assume/ask/pause; CLI exits 0 ok, 1 config, 2 usage, 3 paused, 4 escalated, 5 refused, 6 rejected.
- scope check both ways; LLM engine needs `LLMLogger`; DR rationales have no quantities; P9 reviewer sees tool results only.

**WP-01 review**
- ESP head/power × pvt SG; separator raises on `fuel_lhv_j_kg` without gas density.

**WP-01**
- `"pvt"` on `well`/`producer_well`/`injector_well`; give ρ, μ, c_p together (DB-08/DB-33: 1030, 0.40e-3, 4100); GM-11 `us_g` fix.
- `kpi.py` ρ/c_p from producer `pvt`, else 1050/4200; HEX/boiler/CHP not fed (`fluid_consistency_issues`).
- separator gas CH₄ + CO₂ = 1, LHV 35.8 MJ/Nm³, `emission` 0 (reports inherent/if-burned CO₂); registered `heatpump`, `compressor`, `co2_corrosion`.

**WP-04**
- tool signature `(request=None, *, config_dir=None)`; DB-20…29 helpers public; demand warns if CSV ≠ DB-18/19.
- DB-25 per card (PI 12.2 → 24.5 bar); base case needs ESP and injection pump.
- lowest-load window = min rolling 35-day sum (no wrap, earliest); days-above k × DB-23; daily climate, Stull T_wb.

**WP-00**
- ECO-04 native units/currency/year, USD in code (1.13, 1.168, CEPCI); ECO-CUR USD, money fields + `currency`.
- `StrictModel` schemas; day-average MW, MWh = Σ MW × 24; `Candidate` structural; hard limits in DesignGuard.
- Ollama tags verified 31 Oct; Pydantic AI 2.64.0; cloud = policy / TestModel only (spec v2 §4).

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30, DB-31, DB-32, DB-33, DB-34a, DB-34b, DB-34c, DB-34d, DB-34-RH, DB-35, DB-36, DB-37, DB-38, DB-40 (the spec has no DB-39), ECO-CUR.
- WP-01: none.
- WP-04: SUB-RHOC (ρC)_a 2.5e6 J/m³K (DB-26); SUB-II II = PI (DB-21); DEM-COP-HD 0.75 (demand profile only; cases 0.60/0.70).
- WP-02: COOL-ABS-B, COOL-ABS-CAL, COOL-ABS-CAP, COOL-ABS-LB, COOL-ABS-AUX, COOL-ABS-PL, COOL-ABS-DRV, COOL-ELC (η 0.508 [D]), COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB (60 bar(a)), VER-ESP-MARGIN (1.2), VER-HP-ETA (0.6 × Carnot), VER-HP-LIFT (60 K).
- WP-05: SUB-WELL (ID 0.216 m, roughness 0.0457 mm), SUB-PWH (WHP 3.0 bar(g)), SUB-PBUB (Henry's law, margin 1.2), SUB-NPSH (≥ 15 m), SUB-ESP, SUB-PSUC (0 bar(g)), SUB-SPACING (1200 m).
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC.
- WP-08: ECO-CO2-GAS, ECO-CO2-AG, ECO-CO2-SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT, CFG-SURFACE, CFG-HEX, CFG-HP, CFG-CHP, CFG-BOILER, CFG-GRID-GAS, CFG-ASSOC-GAS, CFG-SIZING.
- WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE.
- WP-13: none.
- WP-12: none (limitations are gap-analysis entries, not register values).
- WP-11: OPT-SPACE, OPT-OBJ, OPT-DE, UQ-RANGES, UQ-CASES, UQ-MC.
- WP-10 L-1 follow-up: none.

## Open follow-ups (grouped by the WP that must act)
**WP-07** (old items done)
- WP-14: bind TL-12 (`.data.annual.kpis`, `service_by_key` → WP-08, `verification_evidence(sim, c, **extra)`); KPIs cached by `config_hash` (put only).
- WP-11 levers done. Open: DSP-FLOW alternatives (spread at min flow / max geothermal) not implemented ⇒ unpriced; DSP-HP-MIN `min_part_load_frac` 0 is cheaper on the C2 optimum (TL-13 `dispatch_variants`; register unchanged: decide).
- Speed: a fast year takes 7–25 s at ≥ 3 km (nested HEX/HTHP bisections) vs 2 s at 2.5 km.
- WP-09/14: C6 `storage` fails PH-20 (charge < 0) and PH-22 (no input key). WP-12: ATES outlet T; HP share bisection.
- Treat as API: `simulate_day._flag_text/_heat_pump_margins/_heat_pump_screening`, `Mixer._MIN_TOTAL_FLOW`

**WP-11**
- WP-14: bind `optimise_design` / `uncertainty` (stub names); SUB-11 needs a `*sensitivity*` tool (WP-08 `cost_sensitivity`); S6 from `UncertaintyReport.cases` (`DB-31=…`); DR-06 = `best.outage_start_indices`; TL-13 `rejected` → `ToolContext.guard_failed`.
- WP-08/15: no HT-ATES capex on RC-10 ⇒ C6 unpriced; WACC view (ECO-06) missing.
- WP-14: 3.2 km optimum has T_wh ≈ 105 °C: ≈ 1 % flashes in the 0 bar(g) degasser, NPSH unchecked ⇒ separator p ≥ p_sat + margin or take 3000 m. Install TL-15 `robust_backup_mw` (DR). C2 at ≥ 3 km = C1 + inert HTHP ⇒ optimise C1/C3/C4 too.
- C2 optimum/MC numbers: [#18](https://github.com/Amaechi/gemini_digital_twin_private/pull/18) hand-off, `runs/wp11-c2/`.
- Treat as API: `FastPlant._model`, `.carries`, `.run` (TL-13/15 `ScenarioPlant` hooks: overrides + memo).
**WP-12**
- WP-14: report `limitations` from `TR-*_model_gap_analysis.json`; LLM P4 calls the tools directly (no `check_draft`) and `register_model` mutates the live registry; use `ModelBuilder.close_gaps` if a gap appears.
- L-2: `python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo [--model TAG]` runs generated tests locally (review first). WP-15: promotion = copy folders + registry + `.rst` via PR.

**WP-13**
- WP-14: promote pending checks once they pass (S1 ACCEPTED + 0 stubs; S7 gap via WP-12; S8 re-size + DR explains; S6 DB-31 sensitivity via WP-11); S5 confirm-basis InputRequest; S2 detect inputs missing from the request (DB-30 question names demand, not cooling/reservoir).
- L-1/L-2: `run_eval.py --engine llm --model <tag> --eval-root runs/final/eval` per model, then `--compare runs/final/eval/<a> runs/final/eval/<b> --eval-root runs/final/eval`; final run in `runs/final/<id>/` in ask mode (SUB-02 approval).
- WP-15: README must document API placeholders (SUB-22 warns); CI ruff skips `scripts/`; GEMINI non-.py edits (e.g. `pyproject.toml` notice ≈ line 90) checked by hand (SUB-21 warns if any).

**WP-14**
- Bind real tools under the same names with `stub=False`; keep the `runner/toolbox.py` reducer contract.
- WP-04: pass `context.config_dir`; `climate` → `profile_climate`; adapter adds DB-30…32 (no `open_gaps`).
- WP-05: `reservoir_conditions` (DepthArgs), `productivity_index` (NoArgs), `doublet_operating_point` (FlowArgs → `OperatingPointRequest`), `thermal_breakthrough`, `pressure_interference`; add TL-07/08 argument models (else TL-08 uses 2 doublets).
- WP-06: `generate_gemini_config`, `design_guard` (`failed_ids` → `ToolContext.guard_failed`), `simulate_day`; `CapexRequest` via WP-11 `capex_request(FastPlant, annual)`.
- WP-08: `economics(EconomicsRequest, annual)`, `co2_emissions(Co2Request, annual)`, `exergy_kpi(annual, climate=, base_kpis=)`, `cost_sensitivity`, `scaling_check(ScalingRequest)` (C7 `co2_release_pressure_barg` from TL-05), `pipe_heat_loss`; C0 CO₂ first (`c0_co2_t_per_yr`).
- Register `verify_design_spec(collector)` before stubs go off (else P9 escalates); collector = `verification_evidence(candidate, days)` + `EspPoint(**point.esp_evidence.model_dump())`, `bubble_point.release_pressure_barg` (bare, gauge), `AbsorptionPoint`, subsurface, scaling, `stub_evidence`.
- Verifier gaps: unchecked TL-05 `feasible`, `flow_error_frac ≤ 0.01`, `intake_margin_bar ≥ 0`, TL-08 `within_limit`, TL-07 `breakthrough_after_horizon` (ENG-06); unread cooling `validity_warnings`, `DayResult.flags` (margins carry them, PH-21); `HeatPumpPoint` lacks max sink T.
- DR KPI criteria need real KPIs; keep result field names (`lcoh_per_mwh`, `verdict`: `data_keys`).
- Wire `DesignCache` (versioned), `JsonProjectKnowledgeStore(runs_root).ingest_events(...)` (may pre-fill P1 gaps). WP-12 gaps = report limitations.

**L-1 / L-2**
- Confirm tags with `ollama show <tag>` (must list `tools`). Set `OLLAMA_CONTEXT_LENGTH=65536` before `cli run --engine llm` (`num_ctx` ignored); `--model TAG` overrides orchestrator and coder.
- Act/format split (spec §5.4.3) not implemented: add it if L-1 shows tool-call problems. Paused LLM runs from before a9083b7 may not reload.
- **Re-run L-1** (S11, 5 models) after [#19](https://github.com/Amaechi/gemini_digital_twin_private/pull/19): fallbacks, `kind_coercions`, `step_rejections`, tokens/phase; no "Thinking Process" in content, no call > `request_timeout_s`. gpt-oss can't disable reasoning; `thinking: true` needs more `max_output_tokens`.

**WP-15**
- Header audit of adapted Plant Debugger files (`NOTICE`).
- Spec/card: GM-05 says η ≈ 0.40, no 5 K range (code follows RC-06); DB-25 says ≈ 22 bar (card 24.5); RC-09 "0.55 K/km" not reproducible (needs R_total ≈ 1.24 < R_ins 1.59 m·K/W of 25 mm PUR); RC-10 lacks ESP/injection-pump capex.
- Nits: negative `gas_water_ratio` accepted; DPDT stores params before a bad `pvt` raises; kpi `plant_thermal_power_w` default 1050 kg/m³, unsuffixed `fluid_density`/`specific_heat`; PH-17 checks pump triples, not daily power; dissolved-gas NPSH margin unquantified; DB-30 fact stores the coerced value; SY-01, SE-02 both build the project; `resume <unknown id>` creates `runs/<id>/`; `--run-id` unsanitised; `resource.py:235` reads `costs.items[...].value`; `heat_pump_1987_usd_per_kw` unescalated.

**All WPs**
- Lint with GEMINI's pins (black 25.11.0, isort 7.0.0, flake8 7.3.0 + flake8-docstrings 1.7.0, `--max-line-length=100`): `uv pip install --python /opt/geo-venv/bin/python isort==7.0.0 black==25.11.0 flake8==7.3.0 flake8-docstrings==1.7.0`. WP-15: 42 pre-existing flake8 findings (WP-04 climate/demand/design_basis + tests) fail `run_linter.sh`.
- Many GEMINI files are CRLF: keep line endings (no `read_text`/`write_text` round trip).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
2026-11-02, WP-10 L-1 follow-up (wire body tested via MockTransport). Next: L-1 re-run; WP-14.
