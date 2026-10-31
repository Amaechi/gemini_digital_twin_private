# PROGRESS — read first, update last

Keep this file short and current. It is the hand-off between sessions (chat history is not).

## Status board
| WP | Title | Status | Branch / PR | Last test command → result | Notes |
|---|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | in review | `claude/peaceful-tesla-xdrfyy` / [PR #1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | full suite → 138 passed (91 GEMINI + 47 agent); ruff clean | doublet example Q = 129.02 m³/h |
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
- 2026-10-31 — WP-00: cost items RC-10 lists as `[A] placeholder` with no number (HTHP, CHP, cooling tower, e-boiler, free-cooling HEX capex; electricity, gas, CO₂ prices) are `value: null, placeholder: true` in `config/cost_assumptions.yaml` — no invented numbers (golden rule 2). `CostAssumptions.unset_placeholders()` lists them.
- 2026-10-31 — WP-00: reporting currency USD (assumption ECO-CUR) because spec §17 Q5 is open; KPI money fields are currency-neutral (`capex_million`, `lcoh_per_mwh`) plus a `currency` field.
- 2026-10-31 — WP-00: all schemas derive from `StrictModel` (`extra="forbid"`, `allow_inf_nan=False`). Results use day-average MW (like the demand CSVs); annual MWh = Σ MW × 24. Totals are plain properties, not serialised, so dump → validate round-trips stay strict.
- 2026-10-31 — WP-00: `Candidate` checks structure only (C0 ⇔ 0 doublets; well fields required iff doublets > 0; equipment keys need a unit suffix). Hard limits are enforced by DesignGuard from `config/limits.yaml`. Return temperatures have no defaults; they come from the assumptions register (DB-31/32).
- 2026-10-31 — Framework Pydantic AI 2.64.0; Ollama local models; deterministic phase skeleton; cloud sessions use policy engine / TestModel only. (spec v2 §4)

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00 seeded: DB-30, DB-31, DB-32, DB-33, DB-34a, DB-34b, DB-34c, DB-34d, DB-34-RH, DB-35, DB-36, DB-37, DB-38, DB-40 (the spec has no DB-39), plus ECO-CUR (reporting currency USD).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
**WP-00 done (in review).** Test command: `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **138 passed** (baseline before WP-00: 91 GEMINI). `ruff check src/gemini_agent unit_test/gemini_agent` → clean. `PYTHONPATH=src python simulator_examples/run_doublet_plant_example.py` → primary flow **129.02 m³/h**, power_plant 5.825 MW. `requirements-hackathon.txt` installs into a fresh Python 3.12.13 venv, and the suite passes there.

What exists now:
- `config/`: `design_basis.yaml` (DB-01…19, checked against the CSVs in a test), `limits.yaml` (kept in step with design_basis by a test), `assumptions.yaml`, `climate_nl.yaml` (RC-07; the Stull T_wb column is checked to ±0.1), `cost_assumptions.yaml` (RC-10), `llm.yaml`.
- Schemas in `src/gemini_agent/schemas/`:
  - `common.py`: `StrictModel`, `ToolResult[T]`, `SourceTag`, `GasHandling`, `ConceptId`.
  - `candidate.py`: `Candidate`.
  - `results.py`: `DayResult`, `AnnualResult`, `KPISet`.
  - `config.py` (WP-00-owned): one model per YAML, plus `load_config(name)`, `load_all_configs()` and the `GEOAGENT_CONFIG_DIR` override.
- CLI skeleton `python -m gemini_agent.cli {run,validate-config}`. `run` is a stub that returns exit code 2. WP-10 owns `cli.py` from here on.
- CI: `.github/workflows/hackathon_tests.yaml` runs ruff, the test suite and a CLI smoke test.

Follow-ups:
- **WP-08 blocker:** 8 unset cost placeholders must be sourced before LCOH/LCOC or the OPT objective can be computed: `hthp_capex_usd_per_kw_th`, `chp_capex_usd_per_kw_e`, `cooling_tower_capex_usd_per_kw_th`, `electric_boiler_capex_usd_per_kw_th`, `free_cooling_hex_capex_usd_per_kw_th`, `electricity_price_usd_per_mwh`, `gas_price_usd_per_mwh`, `co2_price_usd_per_t`.
- **WP-08:** add the RC-05 chiller cost functions. Add the ECO-05 CO₂ factors and state the grid-gas choice (56.6 vs 51.53 kg/GJ) in the assumptions register; associated gas is 68.6 kg/GJ. Optionally add the RC-10 sanity benchmarks (ECO-07).
- **WP-13:** fail submission checks while `CostAssumptions.unset_placeholders()` is non-empty.
- **L-1:** confirm the Ollama tags in `config/llm.yaml` with `ollama show <tag>`. The output must list `tools`.
- **Ops:** the cloud setup script can now use `uv pip install --python /opt/geo-venv/bin/python -r requirements-hackathon.txt` (CLOUD_SETUP.md §3 note).
- Next sessions: Batch 2 (WP-01, WP-04, WP-10).
