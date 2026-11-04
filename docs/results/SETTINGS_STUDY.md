# §4.3 runtime-settings study — S11 on gemma4:12b-it-q4_K_M (thinking, context length)

Spec §4.3 fixes the Ollama runtime settings (`num_ctx` / `OLLAMA_CONTEXT_LENGTH` ≥ 32K, 64K
preferred; thinking off for schema-constrained calls, see `config/llm.yaml` `calls`). This study
runs the S11 scenario (the full P0–P10 design task, `scripts/run_eval.py --engine llm --model
gemma4:12b-it-q4_K_M --scenarios S11`, default quick tool profile, assume input mode) three
times on the laptop of the submitted runs (operator's note: MacBook Pro M3, 36 GB, Ollama) and
changes one setting at a time (the settings are the operator's record of each run; the run files
log the model tag, not the Ollama options):

1. **submitted settings** — `calls.thinking: false`, `OLLAMA_CONTEXT_LENGTH=65536` (the S11 line
   of the submitted S1–S11 eval, `runs/final/eval/gemma4_12b-it-q4_K_M/eval_results.json`);
2. **thinking on** — `calls.thinking: true` (`config/llm.yaml` `calls`, passed as a config copy
   via `--config-dir`), everything else as (1), `--eval-root runs/study/thinking_on`;
3. **32K context** — `OLLAMA_CONTEXT_LENGTH=32768` on `ollama serve`, everything else as (1),
   `--eval-root runs/study/ctx32k`.

The three `eval_results.json` are copied to [`settings_study/`](settings_study/)
(`submitted/`, `thinking_on/`, `ctx32k/`); the full run folders of (2) and (3) (`llm_calls.jsonl`,
`events.jsonl`, `token_summary.json`, tool results, Decision Records) are on the branch
`origin/study-evidence` under `runs/study/` (evidence only, not merged; `runs/` is git-ignored on
`main`). Every number below is read from those files; nothing is typed.

| Setting | Completed? | Verdict (selected) | LLM calls | Tool-call validity | Retries | Fallbacks | Overruns | Input / output tokens | Wall time | Phase where it stopped |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| (1) submitted: thinking off, `num_ctx` 65536 | yes (completed) | ACCEPTED (C2-opt-robust) | 33 | 32/33 (97 %) | 7 | 1 | 0 | 83 558 / 4 966 (88 524) | 373 s | P10 (RUN_END) |
| (2) thinking on (`calls.thinking: true`), `num_ctx` 65536 | yes (completed) | ACCEPTED (C2-opt-robust) | 34 | 23/34 (68 %) | 1 | 11 | 0 | 70 716 / 38 533 (109 249) | 1006 s | P10 (RUN_END) |
| (3) thinking off, `OLLAMA_CONTEXT_LENGTH=32768` | yes (completed) | ACCEPTED (C2-opt-robust) | 32 | 32/32 (100 %) | 4 | 0 | 0 | 74 673 / 4 554 (79 227) | 331 s | P10 (RUN_END) |

Columns: `metrics` of the S11 entry in each `eval_results.json` (`llm_calls`, `llm_valid_calls` =
LLM calls used without a policy fallback, `retries`, `fallbacks`, `token_budget_overruns`,
`input_tokens`, `output_tokens`, `runtime_s`); "Completed?" = `status`, "Verdict" = `verdict` /
`selected_candidate`, "Phase" = `phase` of the last event. Wall time is `runtime_s`, which equals
the `RUN_START` → `RUN_END` span of each run's `events.jsonl` (372.5 s, 1006.4 s, 331.2 s). The
harness outcome of S11 is `pending` in all three files by design (the 16 GB vs 36 GB comparison is
not automated); both S11 checks (run completes; validity, retries, fallbacks and tokens recorded)
pass in all three.

## Interpretation

1. **Same design under all three settings.** Every run completed P0–P10, selected C2-opt-robust, was ACCEPTED by the verifier and wrote 11 Decision Records with identical choices (DR-01…DR-11, `run_summary.json`); the settings changed the path and the cost, not the result.
2. **Thinking on breaks the structured calls.** 11 of 34 calls ended with `Model token limit (2048) exceeded before any response was generated` (`llm_calls.jsonl` `error`): the thinking span counts against `max_output_tokens` (Ollama `num_predict`, `config/llm.yaml`), so the model spent the whole 2 048-token cap reasoning and returned no JSON. Each of those calls fell back to the policy (fallbacks 11, validity 68 %); 10 of the 11 Decision Records were decided by the policy (`decision_source: policy_fallback`), only DR-07 by the model. The design is therefore the policy's, not the model's.
3. **Cost of thinking.** Output tokens 38 533 vs 4 966 (× 7.8), total tokens + 23 % (109 249 vs 88 524) and wall time × 2.7 (1 006 s vs 373 s; P5 alone 349 s vs 87 s, the six P5 choices each ran ≈ 45 s to the cap). The 11 failed calls carry `token_source: messages` (tokens estimated from the messages because Ollama returned no usage), so the thinking-on total is a lower bound.
4. **Retries are not a quality signal here.** Thinking on shows 1 retry against 7 and 4 because the validator never saw a malformed answer: the calls failed before any output. The step-rejection and kind-coercion counts (0 / 2 vs 1 / 6 and 2 / 7) fall for the same reason.
5. **The run still completes only because of the designed safety net** (policy fallback per call, AG-04; the verifier, not the model, accepts the design). A fair thinking-on test would raise `calls.max_output_tokens` (the Model Builder coder uses 8 192) and the request timeout; it was not run, and §4.3/`llm.yaml` keep thinking off because Ollama places the thinking span outside the JSON grammar (`ThinkingClose`), so even a finished answer can carry free text before the JSON.
6. **32K context changes nothing measurable.** 32/32 valid calls, 0 fallbacks, 4 retries, 79 227 tokens, 331 s against 97 %, 1, 7, 88 524 and 373 s at 64K: the differences are within the run-to-run spread of the same task (the eight completed single full-pipeline runs of the S1–S11 eval (S10 excluded, it sums two runs) span 88 524–120 985 tokens and 364–448 s).
7. **Why 32K is enough:** the largest single prompt was 5 703 tokens at 64K and 4 608 at 32K (compacted MEM-06 context, `llm_calls.jsonl` `input_tokens`), more than 5 × below 32 768; even the S10 naive run (full history in every prompt) peaked at 13 395 tokens. 64K is kept as the spec's preference and for headroom (the Model Builder's 8 192-token drafts, the review and what-if loops), not because the design task needs it.
8. **The one fallback at the submitted settings** (P6 DR-06: the model twice named a criterion that was not among the offered ids, `max_output_retries` 2) is the known criterion-id failure mode of these models (README, Limitations), independent of context length and absent in run (3) by chance, not by setting.
9. **Limits of the study:** one run per setting, one model, one laptop; the wall time includes the tools (55–60 s of the 331–373 s runs: model latency 311.7 s and 275.7 s in `token_summary.json`); the submitted line comes from a different `run_eval` call (the S11 line of the S1–S11 eval, `RUN_START` 03:38 UTC on 2026-11-04) than (2) and (3) (`RUN_START` 09:59 and 10:26 UTC), same code (`main` after #39).
10. **Decision:** keep the submitted settings — thinking off (required for the schema-constrained calls at the 2 048-token cap) and `OLLAMA_CONTEXT_LENGTH=65536` (spec §4.3 "64K preferred"; 32K is the verified lower bound for the design task). Recorded in `docs/hackathon/PROGRESS.md`.
