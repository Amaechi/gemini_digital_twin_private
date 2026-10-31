# PROGRESS — read first, update last

Keep this file short and current. It is the hand-off between sessions (chat history is not).

## Status board
| WP | Title | Status | Branch / PR | Last test command → result | Notes |
|---|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | in review | `claude/peaceful-tesla-xdrfyy` / [PR #1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | full suite → 142 passed (91 GEMINI + 51 agent); ruff, black, isort, flake8 clean | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | todo | | | |
| WP-02 | Cooling models | todo | | | |
| WP-03 | Heat pump, e-boiler, storage | todo | | | |
| WP-04 | Design basis, demand, climate | todo | | | |
| WP-05 | Subsurface & doublet tools | todo | | | |
| WP-06 | Config generator, guard, day sim | todo | | | |
| WP-07 | Annual simulation & dispatch | todo | | | |
| WP-08 | Economics, CO₂, exergy, scaling | todo | | | |
| WP-09 | Verifier, memory, logging | todo | | | |
| WP-10 | Agents, runner, CLI | todo | | | |
| WP-11 | Optimisation & uncertainty | todo | | | |
| WP-12 | Model gap & Model Builder | todo | | | |
| WP-13 | Eval suite & submission checks | todo | | | |
| WP-14 | Integration run & figures | todo | | | |
| L-1 | Local: pick LLM via S11 eval | todo | | | user, local Ollama |
| L-2 | Local: final LLM run + eval transcripts | todo | | | user, local Ollama |
| WP-15 | Final review & packaging | todo | | | |

Status values: todo · in progress · in review · merged · blocked (say why).

## Decisions log (newest first)
- 2026-10-31 — WP-00 (after rebase on 0db8087): the eight former ECO-04 placeholders (HTHP, CHP, cooling tower, e-boiler, free-cooling HEX capex; electricity, gas, CO₂ prices) are filled from RC-10 "Sourced values". They are stored in **native** units, currency and price year (`CostItem.currency`, `price_year`, `usd_range`, `escalation_index`); USD is derived in code with `eur_to_usd` (1.13), `dea_eur2020_to_eur2025` (1.168) and `cepci_index`. `CostAssumptions.unset_placeholders()` is empty, and a test reproduces the RC-10 conversion checks. Sensitivity = ±30 % (`min_sensitivity_frac`) or the stated range, whichever is wider. Supersedes the earlier "leave them null" decision.
- 2026-10-31 — WP-00: Ollama tags in `config/llm.yaml` (`qwen3.5:9b`, `gemma4:12b`, `qwen3.6:35b-a3b`, `qwen3.6:27b`) were verified on ollama.com on 31 Oct 2026 (CLOUD_SETUP.md §7). The alternative tags are not verified.
- 2026-10-31 — WP-00: new code is formatted with black 25.11.0 and isort 7.0.0 (GEMINI's pinned versions; line length 100) and is flake8-clean, so the upstream `Lint` workflow passes.
- 2026-10-31 — WP-00: reporting currency USD (assumption ECO-CUR) because spec §17 Q5 is open; KPI money fields are currency-neutral (`capex_million`, `lcoh_per_mwh`) plus a `currency` field.
- 2026-10-31 — WP-00: all schemas derive from `StrictModel` (`extra="forbid"`, `allow_inf_nan=False`). Results use day-average MW (like the demand CSVs); annual MWh = Σ MW × 24. Totals are plain properties, not serialised, so dump → validate round-trips stay strict.
- 2026-10-31 — WP-00: `Candidate` checks structure only (C0 ⇔ 0 doublets; well fields required iff doublets > 0; equipment keys need a unit suffix). Hard limits are enforced by DesignGuard from `config/limits.yaml`. Return temperatures have no defaults; they come from the assumptions register (DB-31/32).
- 2026-10-31 — Framework Pydantic AI 2.64.0; Ollama local models; deterministic phase skeleton; cloud sessions use policy engine / TestModel only. (spec v2 §4)

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00 seeded: DB-30, DB-31, DB-32, DB-33, DB-34a, DB-34b, DB-34c, DB-34d, DB-34-RH, DB-35, DB-36, DB-37, DB-38, DB-40 (the spec has no DB-39), plus ECO-CUR (reporting currency USD; native values converted at ECB 1.13 USD/EUR; price-year mismatch accepted as ± a few %).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
**WP-00 done (in review).** Test command: `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **142 passed** (baseline before WP-00: 91 GEMINI). `ruff check src/gemini_agent unit_test/gemini_agent` → clean. black 25.11.0 / isort 7.0.0 / flake8 7.3.0 (`--max-line-length=100`) on the new files → clean. Branch rebased on `main` @ 0db8087. `PYTHONPATH=src python simulator_examples/run_doublet_plant_example.py` → primary flow **129.02 m³/h**, power_plant 5.825 MW. `requirements-hackathon.txt` installs into a fresh Python 3.12.13 venv, and the suite passes there.

What exists now:
- `config/`: `design_basis.yaml` (DB-01…19, checked against the CSVs in a test), `limits.yaml` (kept in step with design_basis by a test), `assumptions.yaml`, `climate_nl.yaml` (RC-07; the Stull T_wb column is checked to ±0.1), `cost_assumptions.yaml` (RC-10, including all eight sourced values; no null items), `llm.yaml` (tags verified on ollama.com).
- Schemas in `src/gemini_agent/schemas/`:
  - `common.py`: `StrictModel`, `ToolResult[T]`, `SourceTag`, `GasHandling`, `ConceptId`.
  - `candidate.py`: `Candidate`.
  - `results.py`: `DayResult`, `AnnualResult`, `KPISet`.
  - `config.py` (WP-00-owned): one model per YAML, plus `load_config(name)`, `load_all_configs()` and the `GEOAGENT_CONFIG_DIR` override.
- CLI skeleton `python -m gemini_agent.cli {run,validate-config}`. `run` is a stub that returns exit code 2. WP-10 owns `cli.py` from here on.
- CI: `.github/workflows/hackathon_tests.yaml` runs ruff, the test suite and a CLI smoke test.

Follow-ups:
- **WP-08 (no blocker):** RC-10 now has a "Sourced values" table for all eight former placeholders, and `config/cost_assumptions.yaml` already holds them in native units. WP-08 converts them in code (`eur_to_usd`, `dea_eur2020_to_eur2025`, `cepci_index` where `escalation_index: cepci`) and applies max(±30 %, stated range) in sensitivity. Also add the RC-05 chiller cost functions. Add the ECO-05 CO₂ factors and state the grid-gas choice (56.6 vs 51.53 kg/GJ) in the assumptions register; associated gas is 68.6 kg/GJ. Optionally add the RC-10 sanity benchmarks (ECO-07).
- **WP-13:** fail submission checks if `CostAssumptions.unset_placeholders()` is ever non-empty (it is empty now).
- **WP-03 / WP-08:** GEMINI `chp.py` uses an electrical fraction of 1/3, but the DEA gas engine on RC-10 gives ≈ 0.50 of useful output as electricity. The WP that uses CHP must state the choice in the assumptions register. Heat-pump COP for our ≈ 15–25 K lift comes from the RC Lorenz/Carnot method, not the DEA table, which starts at 40 K.
- **L-1:** the tags were verified on ollama.com; still confirm locally with `ollama show <tag>`, whose output must list `tools`.
- **All WPs:** run `black` and `isort` on new or changed files before committing (CLAUDE.md); GEMINI's `Lint` workflow fails on black.
- **Ops:** the cloud setup script can now use `uv pip install --python /opt/geo-venv/bin/python -r requirements-hackathon.txt` (CLOUD_SETUP.md §3 note).
- Next sessions: Batch 2 (WP-01, WP-04, WP-10).
