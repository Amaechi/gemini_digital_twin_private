# GeoAgent evaluation report (spec §12, S1–S11)

Generated: 2026-11-02T13:05:14.219667+00:00  
Engine: `policy` · model: `policy` · scenarios: `evals/scenarios.yaml`

Outcomes: pass 2, pending 2, fail 0, skipped 0, error 0

| Scenario | Expected | Success | Status | Verdict | Tool calls (stub) | LLM calls | Valid | Retries | Fallbacks | Tokens | Runtime s |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| S1 | Complete design, ACCEPTED | pass | completed | ACCEPTED | 22 (0) | 0 | - | 0 | 0 | 0 | 182.22 |
| S6 | Asks or records DB-31 assumption + sensitivity | pass | completed | ACCEPTED | 22 (0) | 0 | - | 0 | 0 | 0 | 181.39 |
| S7 | Gap detected; Model Builder writes model + passes reference-example test | pending | completed | ACCEPTED | 22 (0) | 0 | - | 0 | 0 | 0 | 181.37 |
| S8 | Re-sizes and explains | pending | completed | ACCEPTED | 22 (0) | 0 | - | 0 | 0 | 0 | 227.87 |

## Checks and pending items

### S1 — Full brief + all files (pass)
- [x] runs P0–P10 to completion — status completed
- [x] ends in P10 (report and approval) — phase P10
- [x] every phase P0–P10 runs, in order (verifier loop-backs allowed) — first starts ['P0', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10']; 11 starts
- [x] verifier loop-backs stay within the limit (spec §5.2: ≤ 3) — 0 loop-back(s) (limit 3)
- [x] a candidate is selected that was neither guard-failed nor REJECTED — selected C2-opt
- [x] DR-01…DR-10 are written — 10 DRs; missing none
- [x] run artefacts are written — missing none
- [x] the verifier ACCEPTs the selected design (promoted WP-14) — verdict ACCEPTED
- [x] 0 stub tool calls (promoted WP-14: real tools bound) — 0/22 stub calls
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S6 — Return temperature not given (pass)
- [x] asks for, or records, the DB-31 heating return temperature — recorded: DB-31 = 55.0 degC (assumed: register default)
- [x] continues on the labelled assumption — status completed
- [x] runs a sensitivity on DB-31 (TL-15 cases, promoted WP-14) — uncertainty.data.cases misses nothing
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S7 — Cooling model removed from registry (pending)
- [x] runs the model-gap analysis in P4 — model_gap_analysis called 1x in P4
- [x] the run completes — status completed
- [x] the missing absorption chiller is reported as a gap (promoted WP-14) — gaps reported: ['absorption_chiller']
- PENDING (L-2): the Model Builder writes the model from its reference card and it passes the reference-example test — the policy engine's P4 runs the gap analysis only (WP-12 decision); the LLM Model Builder writes and tests the model in the local Ollama run (python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo)
- VERIFY-tagged cost items (RC-05): electric_chiller_plant_cost_function_usd

### S8 — Permeability 50 mD (pending)
- [x] the design basis carries 50 mD — reservoir.permeability_md = 50.0
- [x] the run completes on the changed basis — status completed
- [x] explains the change in the Decision Records: DR-01 quotes the permeability and TL-04 (promoted WP-14) — DR-01 misses nothing
- PENDING (WP-15): re-sizes the doublets for the lower productivity — the real TL-05…TL-13 now size on the 50 mD basis (lower PI → ESP limits the flow), but no scenario check compares the S8 design with S1's yet (needs a cross-run check type)
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

> Stub tools are labelled in each run (`stub_tool_calls`); a run on stubs can never be ACCEPTED. TestModel output never validates, so its runs show the retry/fallback path.
