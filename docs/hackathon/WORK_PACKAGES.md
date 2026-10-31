# Work packages — one cloud session per WP

Each WP is sized for one Claude Code cloud session (Opus). Read only your WP. Spec = `docs/hackathon/GEOAGENT_DESIGN_SPEC.md` (§ numbers). Cards = `data/knowledge/reference_cards/`.

## How to start a session (paste this prompt, replacing XX)
```
You are implementing WP-XX of the GeoAgent hackathon project in this repository.
Follow CLAUDE.md strictly. First read docs/hackathon/PROGRESS.md, then ONLY the WP-XX section of
docs/hackathon/WORK_PACKAGES.md, then only the spec sections and reference cards it lists.
Plan briefly, then implement with tests. Keep the context small (grep/sed, quiet pytest).
When done: all tests green, run the wp-reviewer subagent (and physics-checker if the WP lists it),
fix findings, update PROGRESS.md, commit, and open a PR titled "WP-XX: <title>".
Do not start other work packages.
```

## Dependency graph and parallel lanes
```
WP-00 ─┬─► WP-01 ─┬─► WP-02 ─┐
       │          ├─► WP-03  ├─► WP-06 ─► WP-07 ─► WP-11 ─┐
       │          └─► WP-05 ─┘                              │
       ├─► WP-04 ───────────────────────────► (WP-07)       ├─► WP-14 ─► WP-15
       ├─► WP-08 ───────────────────────────► (WP-11)       │
       ├─► WP-09 ───────────────────────────────────────────┤
       └─► WP-10 ─┬─► WP-12 (needs WP-02)                   │
                  └─► WP-13 ────────────────────────────────┘
```
Run at most 3 sessions at once (shared rate limits; fewer merge conflicts). Merge PRs in dependency order; rebase open branches on `main` after each merge.

Suggested order: **Batch 1:** WP-00 alone → **Batch 2:** WP-01, WP-04, WP-10 → **Batch 3:** WP-02, WP-05, WP-09 → **Batch 4:** WP-03, WP-06, WP-08 → **Batch 5:** WP-07, WP-12, WP-13 → **Batch 6:** WP-11 → **Batch 7:** WP-14 → local LLM runs (CLOUD_SETUP.md §7) → **WP-15**.

Shared files: `src/gemini_agent/schemas/*.py` (created in WP-00) may be EXTENDED (add fields/classes) by later WPs — never rename/remove; note changes in PROGRESS.md. `src/gemini_simulator/registry.py` — each model WP appends its own entries only.

---

## WP-00 Bootstrap, environment, CI, skeleton  (run first, alone)
- **Spec:** §4.1, §4.2, §14, §15, §11 (licensing), §2 (values for YAML). **Cards:** RC-07 (climate table), RC-10 (cost placeholders).
- **Owns:** `requirements-hackathon.txt`, `.github/workflows/hackathon_tests.yaml`, `config/*.yaml`, `src/gemini_agent/**` skeleton, `src/gemini_agent/schemas/{candidate.py,results.py,common.py}`, `unit_test/gemini_agent/`, `NOTICE`, `.gitignore` additions, `pyproject.toml` (pytest section only).
- **Tasks:**
  1. `requirements-hackathon.txt` pinned: numpy==2.2.6, scipy==1.13.1, pandas==2.3.3, matplotlib==3.10.7, pydantic (2.x), pydantic-ai==2.64.0, pyyaml, pytest, pytest-cov, ruff. Verify it installs on Python 3.12.
  2. CI `.github/workflows/hackathon_tests.yaml`: on push + pull_request; Python 3.12; install requirements; run the CLAUDE.md test command; `ruff check src/gemini_agent unit_test/gemini_agent`. Do not modify GEMINI's existing workflows.
  3. Package skeleton per spec §14 (`agents/ runner/ tools/ verify/ memory/ logging/ schemas/ prompts/ cli.py`) with `__init__.py`; `python -m gemini_agent.cli --help` works.
  4. Shared schemas (Pydantic v2): `DesignBasis`, `Candidate` (id, concept C0–C8, doublets, depth_m, design_flow_m3h, equipment sizes dict, gas_handling, return temps), `DayResult` (per-day flows, temps, heat/cool by source, electricity, gas, CO₂, unmet, margins, ambient), `AnnualResult`, `KPISet`, `ToolResult[T]` (data + `summary` ≤ 300 chars).
  5. Config YAML: `design_basis.yaml` (DB-01…19), `limits.yaml` (hard limits), `assumptions.yaml` (register seeded with DB-30…40 defaults, each with id, value, justification, source, sensitivity), `climate_nl.yaml` (RC-07 table + design point), `cost_assumptions.yaml` (RC-10 items; every entry has value, unit, range, source, tag), `llm.yaml` (engine: policy|llm; ollama base_url `http://localhost:11434/v1`; model tags for 16 GB and 36 GB profiles from spec §4.3; num_ctx 65536; temperatures).
  6. `.gitignore`: `runs/*`, `!runs/final/`, `.venv/`. `pyproject.toml` pytest: add `norecursedirs = ["third_party", "runs"]` (keep `pythonpath = "src"`).
  7. `NOTICE`: credits (GEMINI/TNO, Plant Debugger Apache-2.0 with commit, Warrant ideas, Plant-as-Code paper).
  8. Run existing GEMINI tests and the doublet example; record results (pass counts; primary flow ≈ 129 m³/h) in PROGRESS.md.
- **Acceptance:** CI workflow valid; all tests green; example runs; YAML loads into schemas in a test; `cli --help` works.

## WP-01 GEMINI core fixes and registry  (physics-checker: yes)
- **Spec:** §7.2 GM-01, GM-02, GM-10, GM-11, GM-12; §7.2 "GEMINI behaviours". **Cards:** RC-01, RC-08.
- **Owns:** `src/gemini_model/fluid/`, `src/gemini_model/well/pressure_drop.py`, `src/gemini_model/separator/`, `src/gemini_simulator/{registry.py,kpi.py,well_flow.py,well_components.py}` + their tests.
- **Tasks:** configurable PVT (ρ, μ, c_p) settable from project JSON for `well`, `producer_well`, `injector_well`, defaults unchanged; propagate to `kpi.py`; generalise `kpi.py` (no hard-coded names/ρ/c_p, backward compatible); fix `us_g` at `pressure_drop.py:105`; separator gas composition params (ch4/co2 fraction, gas density, fuel LHV) and outputs (fuel power W, inherent CO₂ kg/s); register `heatpump` and `compressor`; add "Modified by" headers.
- **Acceptance:** all existing tests unchanged and green; new tests: brine PVT (1030 kg/m³) raises hydrostatic head by ≈ 7.4 bar over 2.5 km vs water; separator gas outputs match RC-08 (fuel 1.91 MW at 300 m³/h, 0.8 GLR); registry test instantiates new types.

## WP-02 Cooling models  (physics-checker: yes)
- **Spec:** §3.3, §7.1, §7.2 GM-04…GM-07. **Cards:** RC-05, RC-06, RC-07.
- **Depends:** WP-01 merged (registry).
- **Owns:** `src/gemini_model/{absorption_chiller,electric_chiller,heat_rejection,free_cooling}/` (+ `REFERENCE.md` each), tests in `unit_test/gemini_model/...`, `src/gemini_documentation/source/model_cooling.rst`, registry entries for these four.
- **Tasks:** implement per cards. AbsorptionChiller with `method: "ghc1989" | "characteristic"`; characteristic method calibrated to Method A at rating points by a provided helper (document coefficients); validity warnings outside ranges; outputs incl. Q_reject, power_el, drive outlet T. ElectricChiller COP model with caps. HeatRejection (wet tower / dry cooler). FreeCooling active-condition logic and partial pre-cooling.
- **Acceptance:** all card validation tests pass (incl. Method A F and COP values; the OIT case documented as a comparison); energy balances exact; registry builds a JSON project containing each new type.

## WP-03 Heat pump robustness, electric boiler, storage  (physics-checker: yes)
- **Spec:** §7.2 GM-03, GM-08, GM-09. **Cards:** RC-04, RC-09.
- **Depends:** WP-01 merged.
- **Owns:** `src/gemini_model/heatpump/` (add closed-form path, keep Nelder-Mead path), `src/gemini_model/{electric_boiler,thermal_storage}/`, tests, `.rst`, registry entries.
- **Acceptance:** RC-04 validation tests (COP 4.41 reproduction; closed-form vs Nelder-Mead within 2 %); HTHP limits (lift, max T) are parameters flagged `MISSING` until a datasheet is supplied; storage energy balance closes; electric boiler trivial tests.

## WP-04 Design basis, demand and climate tools
- **Spec:** §2, §3.2, §6 TL-01, TL-02, TL-02b. **Cards:** RC-07.
- **Owns:** `src/gemini_agent/tools/{design_basis.py,demand.py,climate.py}` + tests.
- **Acceptance:** DB-20…29 derived values reproduced (tolerances in spec); demand: peaks 30.0/10.0 MW on 28 Jan/19 Jul, totals 111.67/23.74 GWh, monthly table §3.2 (±0.1), lowest 35-day windows (heat-only 9 Jul–12 Aug; heat + cooling/0.75 → 6 May–9 Jun), days above thresholds; climate: 365-day series, wet-bulb table RC-07 (±0.1 °C), Stull test case; every tool returns `ToolResult` with summary ≤ 300 chars.

## WP-05 Subsurface and doublet tools  (physics-checker: yes)
- **Spec:** §6 TL-03…TL-08, §7.2 behaviours. **Cards:** RC-01, RC-02.
- **Depends:** WP-01 merged (brine PVT).
- **Owns:** `src/gemini_agent/tools/{resource.py,doublet.py,thermal.py}` + tests.
- **Tasks:** reservoir conditions vs depth; PI/II; `doublet_operating_point(Q_m3h, depth, design)` that solves ESP frequency (brentq) for the target flow using GEMINI ProducerWell, computes wellhead T via DPDT "up" from reservoir T, ESP power both ways (GEMINI curve and QΔp/0.7), NPSH/intake-pressure check, injector WHP and pump power with brine PVT; performance map over 50…300 m³/h with caching; thermal breakthrough; interference.
- **Acceptance:** card tests; operating point hits target Q within 1 % across the range or returns an explicit infeasible flag with reason; injection = production; map cached and reloaded; runtime of a full map < 60 s.

## WP-06 Config generator, DesignGuard, simulate_day
- **Spec:** §3.4, §3.5, §6 TL-09…TL-11, §10.1 syntactic/semantic. **Cards:** RC-03, RC-08.
- **Depends:** WP-02, WP-05 merged.
- **Owns:** `src/gemini_agent/tools/{config_generator.py,simulate_day.py}`, `src/gemini_agent/verify/design_guard.py` (adapted from `third_party/plantdebugger/.../safety/action_guard.py`, with header) + tests.
- **Acceptance:** generates GEMINI JSON for C0–C7 with 1–3 doublets, unique names, topologically ordered connections, `boundary_inputs` key; every generated config loads with `build_project` and runs one day; DesignGuard rejects each hard-limit violation with a specific reason and detects duplicate configs by hash; doublet example JSON reproduced by the generator for a 1-doublet C1-like case within 1 % of example outputs.

## WP-07 Annual simulation and dispatch  (physics-checker: yes)
- **Spec:** §8 SIM-01…07, §2.4 DB-30, §3.3. **Cards:** RC-05, RC-06, RC-07, RC-09.
- **Depends:** WP-04, WP-06 merged.
- **Owns:** `src/gemini_agent/tools/{dispatch.py,simulate_annual.py}` + tests.
- **Acceptance:** 365 `DayResult`s; availability calendar with non-overlapping 35-day outages (optimiser-ready parameter); merit order per SIM-03 (free cooling → absorption → electric; HEX → HTHP → CHP/boiler); unmet demand recorded; fast vs full mode agree within 2 % daily heat on a 14-day sample; a hand-sized C2 (2 doublets, HTHP 14 MW, boiler 6 MW, chillers 10 MW) gives 0 unmet days; CSV/Parquet + summary written to `runs/<id>/`; fast mode 365 days < 10 s.

## WP-08 Economics, CO₂, exergy, scaling, network
- **Spec:** §9.2, §10.1 exergy row, §6 TL-14, TL-22, TL-23. **Cards:** RC-08, RC-09, RC-10, RC-11, RC-12.
- **Owns:** `src/gemini_agent/tools/{economics.py,exergy.py,scaling.py,network.py}`, updates to `config/cost_assumptions.yaml` (sources) + tests.
- **Acceptance:** card validation tests; LCOH and LCOC from an `AnnualResult` fixture; CO₂ with stated factors; exergy KPI with T₀ from climate; scaling tool returns "insufficient data" + mitigation list when chemistry missing; tornado-ready sensitivity function.

## WP-09 Verifier, memory, logging
- **Spec:** §10.1, §10.2, §10.3, §11 (adaptation table). **Cards:** RC-03 (HEX limits), RC-12.
- **Owns:** `src/gemini_agent/verify/design_verifier.py`, `src/gemini_agent/memory/{event_store.py,design_cache.py,project_knowledge.py}`, `src/gemini_agent/logging/{llm_logger.py,json_formatter.py}` (adapted from Plant Debugger with headers) + tests.
- **Acceptance:** 4-layer verifier with pass/fail + margin + ≤ 300-char summary; each physical check has a failing-case test; event store append/list/replay; design cache by config hash; project knowledge accepts only explicit user assertions; LLM logger writes JSONL (agent, phase, model, messages, response, tool calls, input/output tokens, latency, retries, decision source) and `token_summary.json` — tested with Pydantic AI `FunctionModel`.

## WP-10 Agent layer, workflow runner, CLI
- **Spec:** §4.1, §5 (all), §6 TL-19, TL-20, §10.2. **Cards:** none (no physics).
- **Owns:** `src/gemini_agent/{agents/,runner/,prompts/,schemas/decision.py,tools/knowledge.py,cli.py}` + tests.
- **Tasks:** adapt `decision_engine.py`, `schemas.py`, `incident_runner.py`, `runbook.py` (headers). Deterministic phase machine P0–P10 with budgets; PolicyEngine for every phase; Pydantic AI agents per role (≤ 6 tools each, `NativeOutput`/validators, ≤ 2 retries then policy fallback); `request_missing_input` pause/resume persisted in SQLite (works in CLI interactive and non-interactive "assume" mode); Decision Records DR-01…10 written as JSON+MD; Ollama model via OpenAI-compatible provider from `config/llm.yaml`; knowledge search over `data/knowledge/` and GEMINI `.rst`. Until WP-04…09 land, call tool stubs with the shared schemas.
- **Acceptance:** `cli run --engine policy` completes P0–P10 on stubs/real tools offline; `--engine llm` path tested with `TestModel`/`FunctionModel` (no network); pause/resume test; budget-exceeded → escalate test; off-topic prompt → refusal test; all LLM calls pass through the logger.

## WP-11 Optimisation and uncertainty
- **Spec:** §9.1, §9.3, §6 TL-13, TL-15.
- **Depends:** WP-07, WP-08 merged.
- **Owns:** `src/gemini_agent/tools/{optimise.py,uncertainty.py}` + tests.
- **Acceptance:** enumerates integer choices and runs seeded `differential_evolution` for continuous sizes; objective LCOH+LCOC with unmet = 0 penalty; maintenance windows optimised; reproducible with seed; Monte Carlo ≥ 200 fast samples with P10/P50/P90 and driver ranking; runtime for C2 optimisation < 15 min on 4 vCPU (document actual).

## WP-12 Model-gap analysis and Model Builder
- **Spec:** §5.1 Model Builder, §6 TL-17, TL-18, §7.1 REF-01…03, §12 S7.
- **Depends:** WP-02, WP-10 merged.
- **Owns:** `src/gemini_agent/agents/model_builder.py`, `src/gemini_agent/tools/model_builder_tools.py` + tests.
- **Acceptance:** gap analysis lists missing types vs registry and known limitations; sandboxed writes only to a new model folder + its test folder (path traversal test); runs pytest in a subprocess with timeout; registers only if tests pass; S7 demo test using `FunctionModel` that returns a scripted model file built from RC-07 FreeCooling (real-LLM demo is done locally).

## WP-13 Evaluation suite and submission checks
- **Spec:** §12 (S1–S11), §13, §18. **Depends:** WP-10 merged (S1 needs WP-07/08/09).
- **Owns:** `scripts/run_eval.py`, `scripts/validate_submission.py` (adapted, headers), `evals/scenarios.yaml`, `unit_test/gemini_agent/eval/` + tests.
- **Acceptance:** each scenario has an expected-behaviour assertion; runs with `--engine policy` and with `TestModel` in CI; with `--engine llm --model <tag>` it records transcripts + tokens to `runs/eval/<model>/` (used locally); Markdown report table (success, tool calls, retries, fallbacks, tokens, runtime); `validate_submission.py` checks every §18 item that can be checked automatically.

## WP-14 Integration run, figures, Decision Records, results pack
- **Spec:** §3.4 KPIs, §13 slides, §6 TL-21. **Depends:** WP-07…WP-11, WP-13.
- **Owns:** `src/gemini_agent/tools/plots.py`, `docs/results/` (generated figures/tables), `runs/final_policy/`.
- **Acceptance:** policy-engine full pipeline produces: candidate comparison table (C0–C7), final design sizing table, daily stacked heat and cooling charts, load-duration, Sankey (or stacked bar), uncertainty fan, tornado, token chart placeholder, Decision Records MD, verification report — all from tool outputs; one command regenerates everything. Hand off to local LLM run (CLOUD_SETUP.md §7).

## WP-15 Final review, documentation, licensing, packaging
- **Spec:** §11, §13, §18, §20. **Depends:** everything, local LLM run committed to `runs/final/`.
- **Owns:** `README_HACKATHON.md`, `NOTICE`, `docs/hackathon/SUBMISSION.md`, header audit fixes.
- **Acceptance:** README (what, how to run with policy and with Ollama, models per hardware, results summary, architecture, credits); every adapted/modified file has the right header; `validate_submission.py` passes; run `/code-review` on the branch and fix high-severity findings; checklist §18 ticked with evidence links.
