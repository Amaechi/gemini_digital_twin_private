# GeoAgent evaluation report (spec §12, S1–S11)

Generated: 2026-11-04T10:16:09.490332+00:00  
Engine: `llm` · model: `gemma4:12b-it-q4_K_M` · scenarios: `evals/scenarios.yaml`

Outcomes: pass 0, pending 1, fail 0, skipped 0, error 0

| Scenario | Expected | Success | Status | Verdict | Tool calls (stub) | LLM calls | Valid | Retries | Fallbacks | Overruns | Tokens | Runtime s |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S11 | Tool-call validity, retries, fallbacks, tokens, quality | pending | completed | ACCEPTED | 24 (0) | 34 | 68 % | 1 | 11 | 0 | 109249 | 1006.43 |

## Checks and pending items

### S11 — Same task on 16 GB-class vs 36 GB-class model (pending)
- [x] the run completes — status completed
- [x] tool-call validity, retries, fallbacks and tokens are recorded — 23/34 LLM calls used without fallback
- PENDING (L-1): the 16 GB-class and 36 GB-class models are compared — needs two local Ollama runs (run_eval.py --engine llm --model <tag> for each), then run_eval.py --compare
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

> Stub tools are labelled in each run (`stub_tool_calls`); a run on stubs can never be ACCEPTED. TestModel output never validates, so its runs show the retry/fallback path.
