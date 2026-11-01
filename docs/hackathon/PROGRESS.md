# PROGRESS — read first, update last

Keep this file short and current. It is the hand-off between sessions (chat history is not).

## Status board
| WP | Title | Status | Branch / PR | Last test command → result | Notes |
|---|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged | `claude/peaceful-tesla-xdrfyy` / [PR #1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | full suite → 143 passed (91 GEMINI + 52 agent); ruff, black, isort, flake8 clean | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | todo | | | |
| WP-02 | Cooling models | todo | | | |
| WP-03 | Heat pump, e-boiler, storage | todo | | | |
| WP-04 | Design basis, demand, climate | in review | `claude/peaceful-mayer-9onzgj` / PR (WP-04) | full suite → 208 passed (143 + 65 tool tests); ruff, black 25.11.0, isort 7.0.0, flake8 clean | TL-01/02/02b; 3 assumptions added |
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
- 2026-11-01 — WP-04: tool I/O models live in the tool modules (`tools/design_basis.py`, `demand.py`, `climate.py`), not in `schemas/`; import them from there. Tools are `load_design_basis(request=None, *, config_dir=None)`, `profile_demand(request=None, *, config_dir=None, data_root=None)`, `profile_climate(*, config_dir=None)` (TL-02b "climate"), each returning `ToolResult[...]`. Every tool also has a config-object variant (`derive_quantities`, `build_demand_profile`, `build_climate_profile`) for sensitivity runs without YAML.
- 2026-11-01 — WP-04: the DB-20…29 physics helpers are public in `tools/design_basis.py` (`temperature_at_depth_degc`, `hydrostatic_pressure_barg`, `mass_flow_kg_s`, `brine_heat_mw`, `productivity_index_m3h_per_bar`, `breakthrough_time_yr`, `gas_mix_properties`); WP-05 should reuse them rather than re-implement. Constants are cited to RC-01/RC-08 (g 9.81, 1 mD = 9.869e-16 m², 22.414 Nm³/kmol, M 16.04/44.01, CH₄ LHV 35.8 MJ/Nm³).
- 2026-11-01 — WP-04: DB-25 drawdown — spec says ≈ 22 bar, but the RC-01-validated PI 12.2 m³/h/bar gives 24.5 bar at 300 m³/h (22 bar would need PI ≈ 13.6). We follow the card; the test checks the RC-01 envelope 20–25 bar. Spec inconsistency, not a code issue.
- 2026-11-01 — WP-04: `esp_required` = flowing producer WHP at Q_max (P_res − Q_max/PI − ρgz, friction neglected) ≤ 0; `injection_pump_required` = injector WHP at Q_min (P_res + Q_min/II − ρgz) > 0. Both true for the base case (−27.2 / +1.5 bar(g)).
- 2026-11-01 — WP-04: lowest-load window = min rolling sum over `DB-30.planned_outage_days_per_doublet` (35 d) consecutive days inside the year (no Dec→Jan wrap), earliest on ties. Default "days above" thresholds are k × DB-23 (14.08 / 28.15 / 42.23 MW → 161 / 4 / 0 days); the spec's 28.2 / 15.7 MW → 4 / 145 days are passed explicitly in a test.
- 2026-11-01 — WP-04: climate daily series interpolates both T_db and RH (mid-month anchors, wrap) and computes T_wb with Stull on the daily values (mean T_db 10.5 °C; monthly Stull vs table max 0.05 K).
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
- WP-04 added (appended to `config/assumptions.yaml`, a WP-00 file — append-only): **SUB-RHOC** aquifer (ρC)_a = 2.5e6 J/(m³ K) (RC-02 [A], DB-26); **SUB-II** injectivity index II = PI (RC-01 item 4 [A], DB-21 injection pump); **DEM-COP-HD** screening COP 0.75 of heat-driven cooling for the demand profile only (spec §3.2 [A]; sensitivity cases 0.60/0.70 from RC-05 — like DB-31 the list holds the alternative cases, not a range around the default).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
**WP-04 done (in review).** Test command: `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **208 passed** (143 before + 65 in `unit_test/gemini_agent/tools/`). `ruff check src/gemini_agent unit_test/gemini_agent` clean; black 25.11.0, isort 7.0.0, `flake8 --max-line-length=100` clean. wp-reviewer: one blocking finding (summary could exceed 300 chars for long requests) fixed, plus the ESP criterion, request validation, `data_root`, and the climate annual-mean warning. Physics-checker not run (not listed for WP-04); all values reproduce the RC-01/02/07/08 validation tests.

What exists now (WP-04):
- `tools/design_basis.py` TL-01 `load_design_basis` → `ToolResult[DesignBasisReport]` (`fixed` = DesignBasis, [A] inputs used, `derived` DB-20…29). Base case: T_res 87.0 °C; P_hyd 252.6 bar(g); 85.83 kg/s; 14.08 / 2.35 MW per doublet; 3 doublets for the brine-side peak; PI 12.22 m³/h/bar, drawdown 24.5 bar; t_b 23.6 yr @ 1.0 km, 33.9 yr @ 1.2 km; 240 Nm³/h → 1.91 MW; 0.458 kg CH₄ + 0.314 kg CO₂ per m³; 0.965 kg/Nm³, 29.7 MJ/kg, 68.6 kg/GJ; DB-29 57/72/102/108/117 °C.
- `tools/demand.py` TL-02 `profile_demand` → `ToolResult[DemandProfile]` with `heating`, `cooling`, `combined_heat_driven` (heat + cooling/COP) `DemandStats` (daily, peak, totals, monthly mean/energy, load factor, load-duration, days above, lowest window). Warns if the CSV disagrees with DB-18/19.
- `tools/climate.py` TL-02b `profile_climate` → `ToolResult[ClimateProfile]`: 365-day T_db, RH, Stull T_wb; `stull_wet_bulb_degc` (vectorised, validity-checked) for WP-02; design days (DB-34b) and extremes.

Follow-ups from WP-04:
- **WP-05:** reuse the helpers in `tools/design_basis.py`; SUB-II (II = PI) and SUB-RHOC are registered. Maybe add a cold-injection II factor.
- **WP-02 / WP-07:** use `stull_wet_bulb_degc` and the `ClimateProfile` daily series (free-cooling eligibility, heat-rejection temperatures); do not re-interpolate.
- **WP-07 / WP-11:** the lowest 35-day windows (heat-only 9 Jul–12 Aug; heat + cooling/0.75 6 May–9 Jun) are the outage candidates for DB-30; `DemandStats.lowest_window` gives one window, and the optimiser places non-overlapping ones.
- **Ops:** `/opt/geo-venv` has no isort and `black` on PATH is 26.x (`~/.local/bin`). Install the pinned linters first: `uv pip install --python /opt/geo-venv/bin/python isort==7.0.0 black==25.11.0 flake8-pyproject==1.2.3`, then call `/opt/geo-venv/bin/black` explicitly.

Follow-ups from WP-00 (still open):
- **WP-08 (no blocker):** RC-10 now has a "Sourced values" table for all eight former placeholders, and `config/cost_assumptions.yaml` already holds them in native units. WP-08 converts them in code (`eur_to_usd`, `dea_eur2020_to_eur2025`, `cepci_index` where `escalation_index: cepci`) and applies max(±30 %, stated range) in sensitivity. **Never read `.value` of the eight sourced items directly**: the key names say USD (fixed by RC-10), but `.value` is native (e.g. `hthp_capex_usd_per_kw_th.value` = 1.20 M EUR/MW_th). Go through one `usd_value(name)`-style accessor, and don't convert `usd_range` again (it is already in USD, escalated). Also add the RC-05 chiller cost functions. Add the ECO-05 CO₂ factors and state the grid-gas choice (56.6 vs 51.53 kg/GJ) in the assumptions register; associated gas is 68.6 kg/GJ. Optionally add the RC-10 sanity benchmarks (ECO-07).
- **WP-13:** fail submission checks if `CostAssumptions.unset_placeholders()` is ever non-empty (it is empty now).
- **WP-03 / WP-08:** GEMINI `chp.py` uses an electrical fraction of 1/3, but the DEA gas engine on RC-10 gives ≈ 0.50 of useful output as electricity. The WP that uses CHP must state the choice in the assumptions register. Heat-pump COP for our ≈ 15–25 K lift comes from the RC Lorenz/Carnot method, not the DEA table, which starts at 40 K.
- **L-1:** the tags were verified on ollama.com; still confirm locally with `ollama show <tag>`, whose output must list `tools`.
- **All WPs:** run `black` and `isort` on new or changed files before committing (CLAUDE.md); GEMINI's `Lint` workflow fails on black.
- **Ops:** the cloud setup script can now use `uv pip install --python /opt/geo-venv/bin/python -r requirements-hackathon.txt` (CLOUD_SETUP.md §3 note).
- Next sessions: Batch 2 (WP-01, WP-10 still open), then Batch 3 (WP-02, WP-05, WP-09).
