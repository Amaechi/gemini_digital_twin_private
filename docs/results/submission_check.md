# GeoAgent submission check (spec §18)

Generated: 2026-11-05T21:50:33.850392+00:00
Final run: `./runs/final/final_llm`  
Eval root: `./runs/final/eval`

**READY** — pass 21, fail 0, warn 1, manual 2

| ID | Item | Status | Detail |
|---|---|---|---|
| SUB-01 | DL-04: final run artefacts (run_summary.json, events.sqlite, events.jsonl) | PASS | ./runs/final/final_llm; missing none |
| SUB-02 | Run completed (P0–P10) and the recommended design was approved (P10) | PASS | completed, approved |
| SUB-03 | §18: ACCEPTED design (final verifier verdict) | PASS | selected C2-opt-robust, verdict ACCEPTED |
| SUB-04 | No stub tool calls (run_summary.json stub_tool_calls = 0) | PASS | 0/23 tool calls were stubs |
| SUB-05 | No final Decision Record with stub_evidence | PASS | stub evidence in none |
| SUB-06 | §18: DR-01…DR-10 (+ DR-11) written | PASS | missing none |
| SUB-07 | §18: 0 unmet heating/cooling days over 365 days (or justified) | PASS | unmet heating days 0, cooling days 0 |
| SUB-08 | §18: DB-10…17 satisfied every day (verifier report) | PASS | DesignVerifier on C2-opt-robust: no violations |
| SUB-09 | §18: C0–C7 compared with KPIs | PASS | concepts without KPIs: none |
| SUB-10 | §18: LCOH/LCOC, CO2 vs C0, exergy (selected design) | PASS | missing none |
| SUB-11 | §18: sensitivity and Monte Carlo P10/P50/P90 | PASS | sensitivity ['sensitivity']; percentiles ['lcoh_per_mwh', 'lcoc_per_mwh', 'lcoh_plus_lcoc_per_mwh', 'co2_t_per_yr', 'capex_million', 'unmet_heating_days', 'unmet_cooling_days', 'unmet_heating_mwh', 'unmet_cooling_mwh', 'thermal_breakthrough_yr', 'required_peak_boiler_mw', 'required_electric_chiller_mw'] (TL-15 on C2-opt) |
| SUB-12 | §18: llm_calls.jsonl + token_summary.json (final run) | PASS | 34 LLM calls; ok |
| SUB-13 | Cost placeholders set (CostAssumptions.unset_placeholders() empty) | PASS | unset placeholders: none |
| SUB-14 | VERIFY-tagged cost items flagged (cost_items[].verify, RC-05) | WARN | economics used VERIFY-tagged items ['absorption_chiller_plant_cost_function_usd', 'electric_chiller_plant_cost_function_usd']: state the RC-05 caveat in the report |
| SUB-15 | §18: naive-vs-optimised token comparison (S10, --engine llm) | PASS | S10 comparison in models ['gemma4:12b-it-q4_K_M'] |
| SUB-16 | §18: model-size comparison (S11, two --engine llm models) | PASS | LLM models compared: ['gemma4:12b-it-q4_K_M', 'qwen3.6:27b', 'qwen3.6:35b-a3b', 'gemma4:26b-a4b', 'qwen3.5:9b-q4_K_M'] |
| SUB-17 | §18: S1–S11 transcripts (prompts, answers, tokens) | PASS | model gemma4:12b-it-q4_K_M: missing none |
| SUB-18 | §18: absorption, electric chiller, heat rejection, free cooling models | PASS | problems: none |
| SUB-19 | §18: brine PVT configurable (DB-08, DB-33) | PASS | {'density_kg_m3': 1030.0, 'viscosity_pa_s': 0.0004, 'specific_heat_j_kgk (DB-33)': 4100.0}; consistency across components is part of the verifier verdict (SUB-03) |
| SUB-20 | §18: Apache-2.0 notices for Plant Debugger reuse | PASS | 14 adapted file(s); problems: none |
| SUB-21 | §18: Apache-2.0 notices on modified GEMINI files | PASS | 11 GEMINI .py file(s) modified since d86e8bb; no notice: none; other modified files (check by hand): none |
| SUB-22 | §18/DL-03: code pushed with README (API placeholders documented) | PASS | README.md present, placeholders documented |
| SUB-23 | §18: 10 slides + appendix, ≤ 5-min video | MANUAL | check the deck and the video by hand |
| SUB-24 | EV-02: unit tests green (GEMINI + gemini_agent) | MANUAL | run with --pytest to include the suite |

Only FAIL blocks the submission. WARN items need a note in the report; MANUAL items are checked by hand.
