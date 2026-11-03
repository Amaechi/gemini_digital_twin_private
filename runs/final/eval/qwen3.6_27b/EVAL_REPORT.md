# GeoAgent evaluation report (spec §12, S1–S11)

Generated: 2026-11-02T21:49:47.901509+00:00  
Engine: `llm` · model: `qwen3.6:27b` · scenarios: `evals/scenarios.yaml`

Outcomes: pass 0, pending 1, fail 0, skipped 0, error 0

| Scenario | Expected | Success | Status | Verdict | Tool calls (stub) | LLM calls | Valid | Retries | Fallbacks | Tokens | Runtime s |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| S11 | Tool-call validity, retries, fallbacks, tokens, quality | pending | completed | ACCEPTED | 26 (0) | 38 | 82 % | 17 | 7 | 126855 | 672.54 |

## Checks and pending items

### S11 — Same task on 16 GB-class vs 36 GB-class model (pending)
- [x] the run completes — status completed
- [x] tool-call validity, retries, fallbacks and tokens are recorded — 31/38 LLM calls used without fallback
- PENDING (L-1): the 16 GB-class and 36 GB-class models are compared — needs two local Ollama runs (run_eval.py --engine llm --model <tag> for each), then run_eval.py --compare
- VERIFY-tagged cost items (RC-05): absorption_chiller_plant_cost_function_usd, electric_chiller_plant_cost_function_usd

> Stub tools are labelled in each run (`stub_tool_calls`); a run on stubs can never be ACCEPTED. TestModel output never validates, so its runs show the retry/fallback path.
