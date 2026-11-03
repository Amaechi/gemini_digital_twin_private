# GeoAgent evaluation report (spec §12, S1–S11)

Generated: 2026-11-02T23:50:27.490608+00:00  
Engine: `llm` · model: `gemma4:12b-it-q4_K_M` · scenarios: `evals/scenarios.yaml`

Outcomes: pass 5, pending 5, fail 1, skipped 0, error 0

| Scenario | Expected | Success | Status | Verdict | Tool calls (stub) | LLM calls | Valid | Retries | Fallbacks | Tokens | Runtime s |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| S1 | Complete design, ACCEPTED | pass | completed | ACCEPTED | 27 (0) | 34 | 94 % | 10 | 2 | 103302 | 379.42 |
| S2 | Lists missing inputs, asks, does not invent | pending | paused | - | 3 (0) | 3 | 100 % | 1 | 0 | 6796 | 26.52 |
| S3 | Polite refusal/redirect | pass | refused | - | 0 (0) | 1 | 100 % | 0 | 0 | 1128 | 4.34 |
| S4 | Flags DB-10/11 violation, proposes compliant option | pass | completed | ACCEPTED | 25 (0) | 32 | 91 % | 12 | 3 | 99202 | 352.73 |
| S5 | Notes conflict with DB-16, asks to confirm change of basis | pending | completed | ACCEPTED | 28 (0) | 34 | 97 % | 7 | 1 | 92830 | 372.15 |
| S6 | Asks or records DB-31 assumption + sensitivity | pass | completed | ACCEPTED | 27 (0) | 33 | 91 % | 12 | 3 | 105299 | 350.88 |
| S7 | Gap detected; Model Builder writes model + passes reference-example test | fail | escalated | - | 16 (0) | 15 | 93 % | 6 | 1 | 46743 | 143.09 |
| S8 | Re-sizes and explains | pending | completed | ACCEPTED | 26 (0) | 38 | 92 % | 11 | 3 | 106820 | 395.94 |
| S9 | Detects inconsistency | pass | completed | ACCEPTED | 23 (0) | 32 | 84 % | 16 | 5 | 110382 | 354.20 |
| S10 | Token comparison chart | pending | completed | ACCEPTED | 54 (0) | 68 | 94 % | 24 | 4 | 284916 | 948.32 |
| S11 | Tool-call validity, retries, fallbacks, tokens, quality | pending | completed | ACCEPTED | 29 (0) | 35 | 89 % | 15 | 4 | 115928 | 400.35 |

## Checks and pending items

### S1 — Full brief + all files (pass)
- [x] runs P0–P10 to completion — status completed
- [x] ends in P10 (report and approval) — phase P10
- [x] every phase P0–P10 runs, in order (verifier loop-backs allowed) — first starts ['P0', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10']; 11 starts
- [x] verifier loop-backs stay within the limit (spec §5.2: ≤ 3) — 0 loop-back(s) (limit 3)
- [x] a candidate is selected that was neither guard-failed nor REJECTED — selected C2-opt-robust
- [x] DR-01…DR-11 are written — 11 DRs; missing none
- [x] run artefacts are written — missing none
- [x] the verifier ACCEPTs the selected design (promoted WP-14) — verdict ACCEPTED
- [x] 0 stub tool calls (promoted WP-14: real tools bound) — 0/27 stub calls
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S2 — "Design a geothermal heating system for 30 MW" (no CSVs/cooling/reservoir) (pending)
- [x] stops and asks instead of designing — status paused
- [x] asks during P1 (design basis and missing inputs) — phase P1
- [x] a missing-input question is pending — asks [DB-30]: How should '330 daily steps' be read: A = each doublet available 330 of 365 days (35-day outage, dem
- [x] nothing is assumed before the answer (does not invent) — 0 assumption(s)/answer(s) recorded
- PENDING (WP-14): the question lists the inputs the request lacks (demand CSVs, cooling, reservoir) — P1 gaps come from the assumptions register (DB-30/31/32); the runner always loads config/ and data/demand/, so inputs missing from the request itself are not detected yet; currently: question misses ['cooling', 'reservoir']

### S3 — "What's the best pizza in Delft?" (pass)
- [x] the request is refused — status refused
- [x] stops in P0 intake — phases ['P0']
- [x] the refusal redirects to the design task — summary: GeoAgent only designs deep-geothermal district heating (100 degC) and cooling (20 degC) systems with the GEMINI digital 
- [x] no tool is called — 0 tool call(s)

### S4 — "Use 5 doublets at 500 m³/h" (pass)
- [x] flags the doublet-count limit DB-10 — 2 conflict(s); matching: ['Requested 5 doublets conflicts with the hard limit 1–3 doublets (DB-10); candidates stay within the limit.']
- [x] flags the per-doublet flow limit DB-11 — 2 conflict(s); matching: ['Requested 500 m3/h conflicts with the per-doublet flow limit 50–300 m3/h (DB-11); flows stay within the limit.']
- [x] continues within the limits — status completed
- [x] every candidate respects DB-10 doublets and DB-11 flow (limits.yaml) — 12 candidates; doublets ≤ 3, flows ≤ 300 m3/h; outside limits: none
- [x] proposes a compliant candidate — selected C2-opt-robust
- conflict noted: Requested 5 doublets conflicts with the hard limit 1–3 doublets (DB-10); candidates stay within the limit.
- conflict noted: Requested 500 m3/h conflicts with the per-doublet flow limit 50–300 m3/h (DB-11); flows stay within the limit.
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S5 — "Supply heating at 120 °C" (pending)
- [x] notes the conflict with the 100 °C heating supply DB-16 — 1 conflict(s); matching: ['Requested heating supply 120 degC conflicts with the design basis 100 degC (DB-16); the design keeps 100 degC until a change of basis is confirmed.']
- [x] keeps the basis until the change is confirmed — 1 conflict(s); matching: ['Requested heating supply 120 degC conflicts with the design basis 100 degC (DB-16); the design keeps 100 degC until a change of basis is confirmed.']
- PENDING (WP-14): asks the user to confirm the change of basis — the runner records the conflict (CONFLICT event) but has no confirm-basis InputRequest yet; it needs an assumptions-register id for the question; currently: no pending question
- conflict noted: Requested heating supply 120 degC conflicts with the design basis 100 degC (DB-16); the design keeps 100 degC until a change of basis is confirmed.
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S6 — Return temperature not given (pass)
- [x] asks for, or records, the DB-31 heating return temperature — recorded: DB-31 = 55.0 degC (assumed: register default)
- [x] continues on the labelled assumption — status completed
- [x] runs a sensitivity on DB-31 (TL-15 cases, promoted WP-14) — uncertainty.data.cases misses nothing
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S7 — Cooling model removed from registry (fail)
- [x] runs the model-gap analysis in P4 — model_gap_analysis called 1x in P4
- [ ] the run completes — status escalated
- [x] the missing absorption chiller is reported as a gap (promoted WP-14) — gaps reported: ['absorption_chiller']
- PENDING (L-2): the Model Builder writes the model from its reference card and it passes the reference-example test — the policy engine's P4 runs the gap analysis only (WP-12 decision); the LLM Model Builder writes and tests the model in the local Ollama run (python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo)
- VERIFY-tagged cost items (RC-05): electric_chiller_plant_cost_function_usd

### S8 — Permeability 50 mD (pending)
- [x] the design basis carries 50 mD — reservoir.permeability_md = 50.0
- [x] the run completes on the changed basis — status completed
- [x] explains the change in the Decision Records: DR-01 quotes the permeability and TL-04 (promoted WP-14) — DR-01 misses nothing
- PENDING (WP-15): re-sizes the doublets for the lower productivity — the real TL-05…TL-13 now size on the 50 mD basis (lower PI → ESP limits the flow), but no scenario check compares the S8 design with S1's yet (needs a cross-run check type)
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S9 — Gradient 30 °C/km but "reservoir 150 °C at 2.5 km" (pass)
- [x] detects that 150 °C at 2.5 km contradicts the 30 °C/km gradient — 1 conflict(s); matching: ['Stated 150 degC at 2500 m is inconsistent with the design basis: 12 degC surface temperature (DB-04) and 30 K/km gradient (DB-03) give 87.0 degC there (RC-01); the design uses the design basis until the data are confirmed.']
- [x] names the gradient it checked against (DB-03) — 1 conflict(s); matching: ['Stated 150 degC at 2500 m is inconsistent with the design basis: 12 degC surface temperature (DB-04) and 30 K/km gradient (DB-03) give 87.0 degC there (RC-01); the design uses the design basis until the data are confirmed.']
- [x] continues on the design basis — status completed
- conflict noted: Stated 150 degC at 2500 m is inconsistent with the design basis: 12 degC surface temperature (DB-04) and 30 K/km gradient (DB-03) give 87.0 degC there (RC-01); the design uses the design basis until the data are confirmed.
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S10 — Same task, naive vs optimised token settings (pending)
- [x] the optimised run completes — status completed
- [x] the optimised settings use no more tokens than the naive ones — naive 183874 vs optimised 101042 tokens
- [x] LLM calls and tokens are logged — 33/34 LLM calls used without fallback
- PENDING (WP-14): token comparison chart (figure) — the report tables the comparison (S10-token_comparison.json); WP-14 draws the figures
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

### S11 — Same task on 16 GB-class vs 36 GB-class model (pending)
- [x] the run completes — status completed
- [x] tool-call validity, retries, fallbacks and tokens are recorded — 31/35 LLM calls used without fallback
- PENDING (L-1): the 16 GB-class and 36 GB-class models are compared — needs two local Ollama runs (run_eval.py --engine llm --model <tag> for each), then run_eval.py --compare
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

## S10 naive vs optimised tokens (MEM-06)

naive = full event history with full payloads in every context; optimised = the last 12 compacted events (MEM-06 compact context); no other setting differs.

| Phase | Naive tokens | Optimised tokens | Saved |
|---|---:|---:|---:|
| total | 183874 | 101042 | 45 % |
| P0 | 1158 | 1159 | -0 % |
| P1 | 9057 | 7939 | 12 % |
| P2 | 12775 | 9762 | 24 % |
| P3 | 21214 | 16436 | 23 % |
| P4 | 13351 | 2349 | 82 % |
| P5 | 37469 | 26202 | 30 % |
| P6 | 13126 | 8219 | 37 % |
| P7 | 18049 | 6150 | 66 % |
| P8 | 15669 | 9439 | 40 % |
| P9 | 26351 | 9888 | 62 % |
| P10 | 15655 | 3499 | 78 % |

> Stub tools are labelled in each run (`stub_tool_calls`); a run on stubs can never be ACCEPTED. TestModel output never validates, so its runs show the retry/fallback path.
