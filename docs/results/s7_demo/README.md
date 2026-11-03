# S7 evidence — model gap detected and closed by the LLM Model Builder

Scenario S7 (spec §12): a component type the candidate plants need is missing from the GEMINI
registry; the Model Builder must write the model from its reference card, test it against the
card's example and register it. This folder holds the evidence of the local run made after the
WP-12 follow-up ([#23](https://github.com/Amaechi/gemini_digital_twin_private/pull/23)):

```bash
PYTHONPATH=src python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo --model gemma4:12b-it-q4_K_M --max-attempts 3
```

Terminal output (operator's note: MacBook Pro M3, 36 GB, Ollama `OLLAMA_CONTEXT_LENGTH=65536`; the hardware is not recorded by the run):

```
free_cooling: FreeCoolingRc07 registered at runtime after 2 draft(s); 2 tests passed.
S7 gap detected: True; closed: True
```

## Result (from `s7_report.json`)

| Field | Value | Source |
|---|---|---|
| Removed registry type | `free_cooling` | `s7_report.json` → `removed_type` |
| Gap detected / closed | `true` / `true` | `s7_report.json` → `gap_detected`, `gap_closed` |
| Registered class | `FreeCoolingRc07` (reference card RC-07) | `s7_report.json` → `registered_class` |
| Draft 1 | 1 907 in / 3 068 out tokens, 69.6 s; pytest 2 tests, 1 failure | `s7_report.json` → `run.results[0].attempts[0]` |
| Draft 2 | 2 520 in / 3 086 out tokens, 68.8 s; pytest 2 tests, 0 failures | `s7_report.json` → `run.results[0].attempts[1]` |
| Isolated pytest of draft 2 | `passed: true`, 2 tests in 0.21 s, files unchanged (sha256 `4d0fce…`) | `pytest_result.json` |
| Registration | runtime only (`runtime_only: true`); promotion needs a reviewed PR | `registration.json` |
| Types still missing after the run | none | `s7_report.json` → `run.still_missing` |

The eval table in `runs/final/eval/gemma4_12b-it-q4_K_M/EVAL_REPORT.md` still shows S7 as
**fail** (status `escalated`): that eval ran on 2 Nov, before #23, when every coder draft timed
out at the SDK default deadline (`llm_calls.jsonl` records 1 and 2 here are those timeouts from the
same sandbox; records 3 and 4 are the two drafts of the run above). `runs/final/eval` is left as it
was because SUB-17 (S1–S11 transcripts) reads it.

## Files

| File | What it is |
|---|---|
| `s7_report.json` | the `S7DemoReport` written by the demo (registry before/after, every draft with tokens, elapsed time and the isolated pytest result) |
| `free_cooling_rc07.py.txt` | the model source the LLM wrote (draft 2), renamed so no test runner imports it |
| `test_free_cooling_rc07.py.txt` | the test the LLM wrote (draft 2), renamed so pytest does not collect it |
| `REFERENCE.md` | the card copy the LLM wrote into the model folder (RC-07 content) |
| `llm_calls.jsonl` | the four logged coder calls (prompt, answer, tokens, latency; 2 timeouts before #23, then 2 drafts) |
| `pytest_result.json`, `registration.json` | the sandbox's isolated test result and in-memory registration record |

The original files live on the branch `origin/s7-evidence` under `runs/s7_demo/` (not merged: a
generated model must never enter `src/` without review).

## Review notes (why this is runtime-only evidence, not a promoted model)

- The model reproduces the RC-07 wet-bulb formula (Stull 2011) and the heat-rejection relation
  `Q_reject = Q_cool (1 + 1/COP)` from the card, and reports `power_th`, `power_el`, `emission`
  as a GEMINI `StaticModel`.
- The partial free-cooling branch returns 50 % of the cooling duty: that fraction is not on the
  card (the LLM added it to satisfy its own draft-1 test, as its code comment says). A promoted
  model would replace it with the card's rule (pre-cooling of the chiller return) and the
  card's validation tests (T_wb 13.7 °C at 20 °C / 50 % RH; monthly table).
- The tests check the model's own outputs; the card's example values are referenced in comments
  only. This is the honest state of a 2-draft, 6 k-token build on a 12 B model.
