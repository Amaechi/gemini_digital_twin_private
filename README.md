# GeoAgent — agentic design of a deep-geothermal district heating and cooling system

SPE GeoHackathon 2026 entry built on the [GEMINI geothermal digital twin](README_GEMINI.md)
(this repository is a fork; GEMINI's own README is kept as `README_GEMINI.md`).

GeoAgent is an agentic AI application that selects, sizes and validates a deep-geothermal
district **heating (100 °C supply)** and **cooling (20 °C supply)** system for the challenge brief:
it compares nine alternatives (C0–C8), explains every decision in a Decision Record, runs the
GEMINI simulator for a full year, verifies the design against 46 checks, quantifies cost, CO₂,
exergy and uncertainty, and logs every prompt, answer and token. The LLM is an open-weight model
served locally by Ollama; nothing leaves the laptop.

## Design principle: agents propose, tools compute, the verifier accepts, the human approves

```
 USER (CLI: --input-mode ask | assume | pause/resume)
   │
 WORKFLOW RUNNER  deterministic phases P0–P10, SQLite event log, budgets, pause/resume
   │              (adapted from Plant Debugger's incident runner)
   ▼
 AGENTS           Pydantic AI role agents on a local Ollama model (Lead Engineer, Requirements,
                  Subsurface, System Architect, Model Builder, Economics, Reviewer, Reporter);
                  structured outputs, ≤ 2 retries, then a deterministic PolicyEngine fallback
   │  typed tool calls (3–6 tools per role)
   ▼
 TOOLS            deterministic, unit-tested Python (TL-01…TL-23): design basis, climate, resource,
                  doublet hydraulics/ESP, config generator, DesignGuard, annual simulation and
                  dispatch, optimiser, Monte Carlo, economics/CO₂/exergy, scaling, plots, report
   ▼
 ENGINE           GEMINI gemini_model + gemini_simulator (+ 7 new and 4 extended models, registry)
   ▼
 VERIFICATION     DesignGuard (hard limits) · DesignVerifier (46 checks, 4 layers) · Reviewer
                  agent · human approval — only DesignVerifier can mark a design ACCEPTED
   ▼
 EVIDENCE         events.jsonl/sqlite · tool_results/TR-*.json · llm_calls.jsonl ·
                  token_summary.json · decision_records/ · figures · report_draft.md
```

- **The LLM never computes a number.** Every physics, sizing, cost and KPI value comes from a
  deterministic tool; report numbers are copied from tool outputs.
- **No invented coefficients.** Equations and values come from the reference cards in
  `data/knowledge/reference_cards/RC-*.md`; every assumption is an entry in
  `config/assumptions.yaml` with its source and tag.
- **Hard limits** (1–3 doublets, 50–300 m³/h per doublet, brine cooling ≤ 40 K, pump efficiency
  70 %, Q_inj = Q_prod, 100 °C / 20 °C supply) are enforced by `DesignGuard` before any
  simulation and re-checked by `DesignVerifier`.
- **Models are choosers.** Each phase offers bounded options with evidence ids; the model picks
  and must name the decisive criterion; a typed validator rejects anything else.

## Results of the final run (gemma4:12b-it-q4_K_M, laptop, `runs/final/final_llm/`)

Selected design **C2-opt-robust**: two doublets at 2500 m, plate HEX + high-temperature heat
pump in series, CHP on the associated gas, peak boiler, free cooling + electric chillers, no
seasonal storage; verifier verdict **ACCEPTED** (46/46 checks), human approval **approved**.
Full tables with their source files: [`docs/results/REPORT.md`](docs/results/REPORT.md).

| Item | Value | Source |
|---|---|---|
| Doublets · depth · flow per doublet | 2 · 2500 m · 296 m³/h | `runs/final/final_llm/figures/sizing.md` |
| Installed heating · cooling | 36.3 MW · 16.8 MW | same |
| HTHP · electric chiller · peak boiler | 4.10 · 8.20 · 10.90 MW (C2-opt had 5.00 MW boiler) | same |
| Wellhead T · ESP · injection pump (per doublet) | 85.2 °C · 58.1 Hz, 0.446 MW_e · 0.339 MW_e | same |
| Thermal breakthrough (design flow) · well spacing | 34.4 yr · 1200 m | same |
| Unmet demand (365 d) | heating 0 d / 0 MWh, cooling 0 d / 0 MWh | `report_draft.md` → Answer |
| LCOH · LCOC (30 yr) · CAPEX | 44.6 · 26.3 USD/MWh · 30.1 MUSD | same |
| CO₂ | 7 360 t/yr, 72 % below the C0 counterfactual (26 332 t/yr) | same; `figures/candidates.md` |
| Exergy efficiency | 0.44 (T₀ = daily dry bulb, De Bilt) | same |
| Robustness before → after DR-11 | P(demand met) 0.46 → 0.90 (TL-15, 200 samples) | same; `uncertainty_C2-opt.json` |
| Cost of the robust backups (DR-11) | ΔLCOH +2.05, ΔLCOC +0.00 USD/MWh, ΔCAPEX +2.21 MUSD | same; `tool_results/TR-019_robust_backup.json` |
| Monte Carlo LCOH P10 / P50 / P90 | 33.8 / 47.3 / 66.1 USD/MWh (C2-opt) | `figures/uncertainty.md` |

Alternatives (fast-year screening points, TL-12 + TL-14; `runs/final/final_llm/figures/candidates.md`):

| Candidate | Concept | LCOH | LCOC | CO₂ cut vs C0 | Note |
|---|---|---|---|---|---|
| C0-0d | gas boilers + electric chillers (counterfactual) | 137.0 | 36.0 | – | |
| C1-3d | 3 doublets at 2.5 km, plate HEX + gas boiler/CHP top-up, electric chillers | 54.2 | 23.6 | 47 % | |
| C2-2d | 2 doublets at 2.5 km, HEX + HTHP in series + CHP + peak boiler, free cooling + electric chillers | 50.3 | 26.3 | 75 % | optimised → C2-opt 42.5 / 26.3 |
| C3-2d | as C2, free cooling + absorption chillers + electric peak | 50.0 | 40.4 | 75 % | |
| C4-2d | 2 doublets at 3.2 km, direct plate HEX (no HTHP), absorption cooling | 29.4 | 38.7 | 83 % | excluded: PH-19 NPSH (ESP) |
| C5-3d | 3 doublets at 1.5 km, large heat pumps, free cooling + electric | 104.5 | 26.1 | 46 % | |
| C6-2d | C2 + seasonal HT-ATES | 50.3 | 26.3 | 75 % | storage unpriced (no RC-10 capex) |
| C7-2d | C2 with a pressurised loop, gas reinjected (no degassing) | 62.1 | 27.3 | 76 % | |
| C8-3d | C2 + 1 doublet as N-1 redundancy | 57.0 | 26.3 | 75 % | |
| **C2-opt-robust** | C2-opt + TL-15 P90 backups | **44.6** | **26.3** | **72 %** | selected, ACCEPTED |

Figures: `docs/results/final_llm/` (copies of `runs/final/final_llm/figures/`), captions in
`docs/results/REPORT.md` §13.

## Agent and LLM evidence

**Final run** (`runs/final/final_llm/token_summary.json`, `run_summary.json`): 34 LLM calls,
99 301 tokens (93 699 in / 5 602 out), 299 s of model latency, 2 policy fallbacks, 0 of 23 tool
calls were stubs, 11 Decision Records (10 decided by the model, DR-10 by the policy fallback
after the model twice named a criterion that was not offered), approval `approved` in ask mode.
Hardware (operator's note, not recorded by the run): MacBook Pro M3, 36 GB, Ollama with
`OLLAMA_CONTEXT_LENGTH=65536`.

**S1–S11 evaluation** (`runs/final/eval/gemma4_12b-it-q4_K_M/EVAL_REPORT.md`, quick tool
profile): 5 pass (S1, S3, S4, S6, S9), 5 pending (S2, S5, S8, S10, S11: the scenario check that
is not implemented yet is named in the report), 1 fail (S7). The S7 "fail" predates the Model
Builder fix [#23](https://github.com/Amaechi/gemini_digital_twin_private/pull/23): every coder
draft timed out at the SDK default deadline. Re-run locally after #23, the Model Builder detected
the gap and closed it in 2 drafts with 2 passing tests: evidence and review notes in
[`docs/results/s7_demo/`](docs/results/s7_demo/README.md). `runs/final/eval` is left unchanged
because the transcripts check (SUB-17) reads it. S2/S5/S8 closed on 2026-11-03 (P1 intake
questions `CFG-INPUT-DEFAULTS` / `CFG-BASIS-CONFIRM`, `cross_run_sizing` check against S1):
eval rerun pending — the table above is the eval before the change; the new S8 check passes on
its stored evidence (ESP 49 stages at 1560 m, 1.43 MW peak at 50 mD vs 16 stages at 840 m,
0.46 MW in S1).

**S10 naive vs optimised context** (`runs/final/eval/gemma4_12b-it-q4_K_M/S10-token_comparison.json`):
183 874 → 101 042 tokens for the same 34 calls, a **45 % saving** from the compacted history and
tool-result summaries.

**S11 model comparison** (one S11 run per model, `runs/final/eval/<model>/eval_results.json`):

| Model (Ollama tag) | Class | Outcome | LLM calls | Tool-call validity | Fallbacks | Tokens | Runtime |
|---|---|---|---|---|---|---|---|
| gemma4:12b-it-q4_K_M (chosen) | 16 GB | completed, ACCEPTED | 35 | 89 % | 4 | 115 928 | 400 s |
| qwen3.6:27b | 36 GB | completed, ACCEPTED | 38 | 82 % | 7 | 126 855 | 673 s |
| qwen3.6:35b-a3b | 36 GB | escalated in P6 (tool-call budget) | 29 | 97 % | 1 | 79 061 | 167 s |
| gemma4:26b-a4b | 24–36 GB | escalated in P5 (DR-08 token limit) | 27 | 70 % | 7 | 101 871 | 176 s |
| qwen3.5:9b-q4_K_M | 16 GB | escalated in P5 (tool-call budget) | 22 | 68 % | 7 | 83 724 | 203 s |

The gemma4:12b row is the S11 of its S1–S11 eval. Its earlier S11-only run (L-1, the basis of the
model choice: completed, 100 % validity, 0 fallbacks, 79 k tokens, 328 s, recorded in
`docs/hackathon/PROGRESS.md`) was overwritten by the full eval because `run_eval.py` rewrites
`eval_results.json` on every call; `runs/final/eval/model_comparison.json` was generated after
that and therefore sums all 12 gemma4 runs. See Limitations.

## Reproduce

Python 3.12, no GPU, no network and no LLM are needed for the policy engine. Install:

```bash
uv venv -p 3.12 .venv && source .venv/bin/activate && uv pip install -r requirements-hackathon.txt
```

One command, deterministic (same decisions as the final run, ≈ 9 min with the full budget on a
2–4-core machine; `--budget smoke` gives the CI-sized run, ≈ 23 s):

```bash
PYTHONPATH=src python -m gemini_agent.cli run --engine policy
```

Outputs land in `runs/<run-id>/` (`run_summary.json`, `report_draft.md`, `decision_records/`,
`figures/`, `tool_results/`, `events.jsonl`). The committed policy run is `runs/final_policy/`.
Other commands:

```bash
PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x
```

```bash
PYTHONPATH=src python scripts/validate_submission.py --pytest
```

```bash
PYTHONPATH=src python scripts/build_results.py --run-id final_policy
```

```bash
PYTHONPATH=src python scripts/build_report_pack.py
```

**Laptop LLM run** (needs [Ollama](https://ollama.com); full detail and timings in
`docs/hackathon/CLOUD_SETUP.md` §7). Models per hardware are in `config/llm.yaml`: profile
`16gb` (qwen3.5:9b orchestrator, gemma4:12b coder) and `36gb` (qwen3.6:35b-a3b, qwen3.6:27b);
`--model <tag>` uses one tag for every role. The settings below are **server** settings:

```bash
OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_CONTEXT_LENGTH=65536 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

```bash
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <model-a> --tool-profile quick --scenarios S11 --eval-root runs/final/eval
```

```bash
PYTHONPATH=src python scripts/run_eval.py --compare runs/final/eval/<dir-a> runs/final/eval/<dir-b> --eval-root runs/final/eval
```

```bash
PYTHONPATH=src python -m gemini_agent.cli run --engine llm --model <model> --budget full --runs-dir runs/final --run-id final_llm --input-mode ask
```

```bash
PYTHONPATH=src python scripts/run_eval.py --engine llm --model <model> --tool-profile quick --eval-root runs/final/eval
```

```bash
PYTHONPATH=src python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo --model <model> --max-attempts 3
```

## Where the evidence is

| Path | Content |
|---|---|
| `runs/final/final_llm/` | the submitted LLM run: `llm_calls.jsonl` (every prompt, answer, tokens, latency), `token_summary.json`, `events.jsonl`/`events.sqlite`, `tool_results/TR-001…023.json`, `decision_records/DR-01…11.{json,md}`, `report_draft.md`, figures, annual CSVs, optimisation/uncertainty/sensitivity JSON |
| `runs/final/eval/` | S1–S11 transcripts of the chosen model, S11 of four other models, `model_comparison.{json,md}`, `S10-token_comparison.json` |
| `runs/final_policy/` | the same workflow on the deterministic policy engine (reference run, built by `scripts/build_results.py`) |
| `docs/results/` | `REPORT.md` (report pack, generated), `DECK_OUTLINE.md` (slides + video script), figures (`final_llm/`), `s7_demo/` (Model Builder evidence), `submission_check.md`, `eval_policy_quick/`, WP-14 policy-run tables |
| `docs/hackathon/` | `GEOAGENT_DESIGN_SPEC.md` (requirements), `WORK_PACKAGES.md`, `PROGRESS.md` (decisions, follow-ups, submission checklist), `CLOUD_SETUP.md` (laptop steps, submission) |
| `config/` | `design_basis.yaml`, `climate.yaml`, `limits.yaml`, `cost_assumptions.yaml`, `assumptions.yaml` (register of every assumed value), `llm.yaml` |
| `data/knowledge/reference_cards/` | RC-01…RC-10: the only engineering source for equations and values (books are not redistributed) |

## Repository map (what we added to GEMINI)

- `src/gemini_agent/` — the application: `agents/` (engine, roles, scope, model builder),
  `runner/` (phases, design runner, toolbox, real tools, Decision Records), `tools/` (TL-01…23),
  `verify/` (DesignGuard, DesignVerifier), `memory/` (event store, project knowledge, design
  cache), `logging/` (LLM call log), `eval/` (S1–S11 harness, submission checks), `schemas/`,
  `prompts/`, `cli.py`.
- `src/gemini_model/` — new GEMINI models with reference cards, tests and `.rst` pages:
  `absorption_chiller`, `electric_chiller`, `heat_rejection`, `free_cooling`, `electric_boiler`,
  `thermal_storage`, `fluid` (configurable brine PVT), plus extensions of `heatpump`
  (closed-form HTHP), `separator` (gas composition), `well` (brine PVT in pressure drop) and
  `chp`. Modified GEMINI files carry a `# Modified by …` notice (Apache-2.0 §4(b)).
- `src/gemini_simulator/` — registry entries, brine PVT in `kpi.py`, well components and nodal
  solver fixes (each file marked).
- `unit_test/` mirrors `src/` (1 334 tests, command above; the result is recorded in
  `docs/hackathon/PROGRESS.md`).
- `scripts/` — `build_results.py`, `build_report_pack.py`, `run_eval.py`, `run_optimisation.py`,
  `validate_submission.py`. `evals/scenarios.yaml` defines S1–S11.
- `third_party/plantdebugger/` — unmodified Apache-2.0 reference copies (never imported).
- CI: `.github/workflows/hackathon_tests.yaml` (ruff, the test suite, a smoke CLI run).

## API placeholders and external interfaces

Everything external is either a local endpoint from a config file or not wired at all; no
credential exists anywhere in the repository (`.env.template` is GEMINI's and unchanged).

| Interface | State |
|---|---|
| Ollama endpoint | `config/llm.yaml` → `ollama.base_url` (`http://localhost:11434/v1`, OpenAI-compatible). Placeholder in the sense of DL-03: the only external call the application makes, local, no key. Cloud sessions, CI and tests never call it (`--engine policy`, `TestModel`/`FunctionModel`). |
| Commercial / provider models | not wired. `--model` takes Ollama tags only; there is no OpenAI, Anthropic, Google or Ollama Cloud client, key or config entry. A cloud tag (`*-cloud`) is detected only to switch off native JSON-schema output. |
| GEMINI platform APIs | not used. GeoAgent drives `gemini_model` + `gemini_simulator` in-process; GEMINI's Flask app, Celery, MySQL/InfluxDB/Mongo/Chroma/Redis and the AI-chat assistant are untouched placeholders from the upstream repository (`docker-compose.yml`, `.env.template`). |
| Model Builder sandbox | `python -m gemini_agent.agents.model_builder` writes LLM-generated model code into a sandbox folder outside `src/` and registers it in memory only; promotion into the registry is a reviewed PR, never automatic. |
| Web UI ("Design Commander") | not built (spec MAY); the CLI is the user interface. |
| Weather / price feeds | none; climate normals and prices are config and reference-card values (`config/climate.yaml`, `config/cost_assumptions.yaml`). |

## Limitations (known, recorded, not hidden)

- **RC-05 chiller cost functions are `VERIFY`-tagged** (SUB-14 warns): the absorption and
  electric chiller plant costs are decoded 1986 Means $ functions from the Guidebook, escalated
  by CEPCI 1986→2023 (× 2.51), inside the RC-10 benchmark bands but not confirmed from a second
  source (`config/cost_assumptions.yaml`, `*_chiller_plant_cost_function_usd`). LCOC carries
  that uncertainty; the Monte Carlo range covers ± 30 %.
- **S2, S5 and S8 are "pending" in `runs/final/eval`**: that eval predates the P1 intake
  questions (S2 lists the inputs a request lacks and offers the repository defaults; S5 asks to
  confirm a DB-16/DB-17 change of basis, default keep) and the S8 cross-run sizing check against
  S1; closed on 2026-11-03, eval rerun pending. The missing-input detection is keyword-based on
  the request text (`agents/scope.missing_request_inputs`); a change of basis stops the run
  (the limits are changed in `config/` and a new run started), it is never designed for; every
  prompt other than the brief records one assumed `CFG-INPUT-DEFAULTS` answer in assume mode.
- **P5 DR-08 token-limit escalation on gemma4:26b-a4b**: the per-call usage limit for the
  Decision Record choice was exceeded (`total_tokens_limit` 765 vs 1 900 used) and the run
  escalated instead of falling back; that limit is the phase's remaining token budget (AG-04). Two
  other models escalated on the tool-call budget (P5/P6). The chosen model never hit a budget.
- **Heat-exchanger fluid properties**: the GEMINI plate HEX takes one ρ/c_p per side (brine
  1030 kg/m³, 4100 J/kgK from DB-33; district water 1000 kg/m³, 4217 J/kgK), constant with
  temperature; `kpi.py` reads the producer PVT, GEMINI's boiler and CHP keep their water defaults
  (`fluid_consistency_issues` lists them). The verifier's brine-side first-law check (PH-22)
  passes within 1 %.
- **RC-09 network loss "0.55 K/km"** could not be reproduced from the card's own pipe data
  (25 mm PUR needs R_total ≈ 1.24 < R_ins 1.59 m·K/W); the network tool uses the register
  fraction NET-LOSS-FRAC (10 %) instead and says so.
- **Alternative Ollama tags** in `config/llm.yaml` (`gpt-oss:20b`, `gemma4:26b-a4b`,
  `qwen3.8:27b`, `gemma4:31b`) were not verified on ollama.com; `gemma4:26b-a4b` was pulled and
  run in S11. The five tags that were run are the ones in the S11 table.
- **`coverage = "7.17.1"` is pinned in `pyproject.toml`** (GEMINI's Poetry dev group): CI runs
  `poetry lock` fresh and resolved 7.17.1 while its wheels were mid-upload on PyPI, which broke
  GEMINI's own Lint/Test/Docs workflows; the exact pin stops the next release racing CI. Bump it
  deliberately. The hackathon workflow uses `requirements-hackathon.txt`, not Poetry.
- **Token figure of the run is a placeholder** (`runs/final/final_llm/figures/fig_tokens.png`):
  P10 plots before `token_summary.json` is written at run end. The report pack draws
  `docs/results/final_llm/fig_tokens_by_phase.png` from the summary instead.
- **S11-only result of the chosen model overwritten**: `run_eval.py` rewrites `eval_results.json`
  on each call, so the L-1 S11-only line of gemma4:12b (79 k tokens, 100 % validity, 0 fallbacks)
  survives only in `docs/hackathon/PROGRESS.md`; the S11 row above is from the S1–S11 eval.
- **Two policy fallbacks in the final run** (P7: a `call_tool` step without a tool; P9 DR-10: a
  criterion that was not offered); both are logged with the model's raw answer in
  `llm_calls.jsonl`, and the policy decided from the same evidence ids.
- **Not priced / not modelled**: HT-ATES capex (C6 unpriced, RC-10 has none); ESP and
  injection-pump capex and their replacement (ECO-PUMP-CAPEX is null, the economics tool warns
  "MISSING INPUT"); TL-17 lists 15 model limitations (LIM-*) in the report.
- Unit tests and the submission checker: `validate_submission --pytest` is the SUB-24 evidence
  (see `docs/hackathon/PROGRESS.md`, WP-15 hand-off, for the exact output).

## Licensing and credits

Apache-2.0 throughout (`LICENSE`, unchanged from GEMINI; `NOTICE` lists every reuse).

- **GEMINI Geothermal Digital Twin** (TNO, Gaia Energy, HVC, WEP, Helin; NWN innovatie) — the
  simulation engine; modified files carry `# Modified by the GeoAgent team for SPE GeoHackathon
  2026, <date>: <what changed>`.
- **Plant Debugger** (marcosanyo/PlantDebugger, Apache-2.0, Copyright 2026 Plant Debugger
  contributors) — 14 files adapted (decision engine, schemas, runner, action guard, verifier,
  runbook search, SQLite store, site knowledge, JSON logging, eval and submission scripts), each
  with the required header; reference copies in `third_party/plantdebugger/`.
- **Ideas cited, no code copied**: Warrant ("do not let a model do arithmetic"), the
  Plant-as-Code paper (verification layers, Design-Ops), the reference books and papers listed
  in spec §19 and on each reference card.
- Python dependencies from PyPI (numpy, scipy, pandas, matplotlib, pydantic, pydantic-ai,
  PyYAML, pytest, ruff) are not redistributed.

## Submission (CLOUD_SETUP §8)

1. Fork `GEMINI-Digital-Twin/gemini-geothermal-suite-open` on GitHub to your account (public).
2. Push this repository's `main` as the team branch of the fork:

```bash
git remote add submission https://github.com/<you>/gemini-geothermal-suite-open.git
```

```bash
git push submission main:hackathon/<team-name>
```

3. On the pushed branch check: no secrets, no PDFs or books, `NOTICE` and
   `third_party/plantdebugger/LICENSE` present, `runs/final/`, `runs/final_policy/` and
   `docs/results/` present, `README.md` renders.
4. Submit as instructed by the organisers: the branch URL `hackathon/<team-name>`, the 10-slide
   deck + appendix (`docs/results/DECK_OUTLINE.md`), the ≤ 5-min video, and point to
   `runs/final/final_llm/` for interactions (prompts, answers, tokens), configuration and
   calculations (DL-04).
