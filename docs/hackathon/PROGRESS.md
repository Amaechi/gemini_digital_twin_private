# PROGRESS — read first, update last

Keep this file short and current. It is the hand-off between sessions (chat history is not).

## Status board
| WP | Title | Status | Branch / PR | Last test command → result | Notes |
|---|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | `claude/peaceful-tesla-xdrfyy` / [PR #1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | full suite → 143 passed (91 GEMINI + 52 agent); ruff, black, isort, flake8 clean | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | in review | `claude/youthful-hamilton-9yqqmh` / [PR #3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3) | full suite → 200 passed (91 original GEMINI + 57 new GEMINI-side + 52 agent); black 25.11.0, isort 7.0.0, flake8 7.3.0 + docstrings, ruff clean | both examples print identical output; Q = 129.02 m³/h |
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
- 2026-11-01 — WP-01: project JSON `"pvt"` block on `well`/`producer_well`/`injector_well` (`gemini_model.fluid.pvt_config`). Accepts native `PVTConstantSTP` keys or unit-suffixed aliases (`liquid_density_kg_m3`, `liquid_viscosity_pa_s`, `liquid_specific_heat_j_kgk`, …); `_`-prefixed keys are comments; unknown keys, conflicts, non-finite or non-positive values raise. Keys a block leaves out keep the PVT defaults (water: 1000 kg/m³, 1e-3 Pa·s, 4217.4 J/kg·K), so **always give ρ, μ and c_p together** for brine (1030, 0.40e-3 [P DB-08], 4100 [A DB-33]).
- 2026-11-01 — WP-01: `kpi.py` takes primary-loop ρ/c_p from the producer well's `pvt` block; without one it keeps the legacy 1050 kg/m³ / 4200 J/kg·K (≠ PVT water defaults 1000/4217.4 — GEMINI inconsistency kept for backward compatibility). Component names are keyword parameters (defaults = `doublet_plant.json` names); a non-default `pvt_component` that does not exist raises. GEMINI HEX/boiler/CHP are **not** fed automatically: set their `fluid_density`/`specific_heat` with `pvt_config.heat_exchanger_fluid_parameters(pvt)`; `kpi.fluid_consistency_issues` reports and `doublet_thermal_power_w` warns on a mismatch (energy balance would not close).
- 2026-11-01 — WP-01: separator gas composition (`ch4_fraction` + `co2_fraction` = 1, ideal gas 22.414 Nm³/kmol, CH₄ LHV 35.8 MJ/Nm³, RC-08). Explicit `gas_density`/`fuel_lhv_j_kg` override the derived values; species flows = composition mass fractions × total gas mass flow, so they always add up. Separator `emission` stays 0; `inherent_co2_kg_s` and `co2_if_burned_kg_s` are reported for the burner/WP-08. Ideal-gas inherent CO₂ is 0.314 kg/m³ brine (RC-08's 0.32 uses the real-gas CO₂ density; 0.965 kg/Nm³ and 68.6 kg/GJ are ideal-gas).
- 2026-11-01 — WP-01: registered `heatpump` (HeatpumpBasic), `compressor`, `co2_corrosion`. Compressor gained `power_el` (= `compressor_power`), `power_th` = 0, `emission` = 0 (file not in WP-01's list; minimal additive change). `co2_corrosion` is a non-energy component (no power/emission outputs).
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

- WP-01: none added (all values are [P] DB-08/DB-09, [A] DB-33 already registered, or [D] RC-08).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
**WP-01 done (in review, [PR #3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3)).** Branch `claude/youthful-hamilton-9yqqmh` (based on `main` @ 529bc4b = merged WP-00). Test command: `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **200 passed** (91 original GEMINI tests untouched and green, 57 new GEMINI-side, 52 agent). Lint with GEMINI's pinned versions via `uvx`: `black==25.11.0 --check`, `isort==7.0.0`, `flake8==7.3.0` + `flake8-docstrings==1.7.0` (`--max-line-length=100`, all of `src/gemini_model src/gemini_simulator unit_test/gemini_model unit_test/gemini_simulator`) → clean; `ruff check src/gemini_agent unit_test/gemini_agent` → clean. Sphinx 7.4.7 build: no new warnings (the 76 local warnings are pre-existing autosummary import failures for gemini_framework/interface deps not installed here). `run_doublet_plant_example.py` and `run_parallel_hex_plant_example.py` print byte-identical output before/after (Q = 129.02 m³/h). Reviews: wp-reviewer and physics-checker run; findings fixed or listed below.

What exists now:
- `gemini_model/fluid/pvt_config.py`: `normalise_pvt_parameters`, `build_pvt`, `liquid_properties`, `heat_exchanger_fluid_parameters`, `PVT_PARAMETER_ALIASES`. `DPDT.update_parameters({"pvt": …})` builds its PVT; `well_flow` passes `well_config["pvt"]` to every DPDT (producer top/bottom, ESP pressures, injector); `ProducerWell`/`InjectorWell` validate the block when parameters are set.
- `pressure_drop.py:115` GM-11 fix: `us_g = mg / rho_g / A` (only affects two-phase, GMF > 0).
- `separator.py`: `associated_gas_properties(ch4, co2)`; new outputs `gas_density_kg_nm3`, `fuel_lhv_j_kg`, `fuel_power_w`, `ch4_mass_flow_kg_s`, `inherent_co2_kg_s`, `co2_if_burned_kg_s` (RC-08: 0.965 kg/Nm³, 29.67 MJ/kg, 1.909 MW at 300 m³/h, 68.56 kg/GJ).
- `kpi.py`: keyword component names, `fluid_properties`, `fluid_consistency_issues`, `sum_component_outputs`, `generators`/`consumers`/`emitters` lists.
- `REFERENCE.md` in `fluid/`, `well/`, `separator/`; rst pages `model_fluid_pvt`, `model_well_hydraulics`, `model_surface_equipment` extended. Files outside WP-01's list touched (minimal): `compressor/compressor.py` (standard outputs + docstring caveats), the three `.rst` pages. No `compressor/REFERENCE.md` (trivial output addition; the WP that sizes a compressor should add one).

Follow-ups (from the reviews; not fixed here because they change GEMINI default behaviour or are outside WP-01):
- **WP-05 (important): injector friction sign.** `nodal_solver._well_bottomhole_pressure_injection` walks DPDT with `direction="down"`, which computes `P_bh = P_wh + ρgH + ΔP_fric`; for downward injection flow friction must be a loss (RC-01 eq. 12: `P_wh = P_bh,req − ρgH + ΔP_fric`). Today friction *lowers* the required injector WHP (vertical 2500 m, D 0.2 m, ε 0.01 m, P_res 260 bar, water, 300 m³/h: code 13.2 bar vs 76.3 bar correct). The temperature change has the same sign issue (harmless now: T_ambient = T_wh ⇒ dT = 0). GEMINI's `injectionwell_monitoring.py:207` has the correct sign. Add an explicit flow-down mode (subtract friction) behind a parameter and use it in TL-05.
- **WP-05: default well roughness 0.01 m** (`well_flow.py`, `roughness_m` default) vs steel 0.0457 mm (RC-01 eq. 5): at 300 m³/h brine, friction 32.4 vs 6.7 bar. Always pass `roughness_m` explicitly (assumption register) instead of relying on the default.
- **WP-05/WP-06:** set `"pvt"` (ρ 1030, μ 0.40e-3, c_p 4100) on **both** wells, and copy `heat_exchanger_fluid_parameters(pvt)` into each brine-side HEX. Caveat: GEMINI `HeatExchanger` uses one ρ/c_p for both sides (brine and district water) — WP-02/WP-06 must decide how to handle the secondary side (e.g. a per-side model or accept ≈ 0.5 % error and record it). `kpi.plant_thermal_power_w` still defaults to 1050 kg/m³ for district water (legacy); pass explicit values.
- **WP-06:** the `well` (DPDT) type from plain JSON needs numpy arrays for `diameter/length/angle/roughness` (lists fail in `calculate_output`, existing GEMINI behaviour); prefer `producer_well`/`injector_well`.
- **WP-06/WP-08: separator gas → boiler/CHP.** `doublet_plant.json` uses `caloric_value` 50e6 J/kg and 56.6 kg/GJ (pure CH₄ / grid gas). For associated gas set the burner `caloric_value` = separator `fuel_lhv_j_kg` (29.67e6) and `gas_emission_factor` = 68.56 kg/GJ (or use `co2_if_burned_kg_s`); otherwise fuel power is 1.69× too high and CO₂ too low. Configure the separator with `gas_water_ratio` 0.8, `ch4_fraction` 0.8, `co2_fraction` 0.2 (DB-09).
- **WP using the compressor (DR-07 gas reinjection, if any):** GEMINI `Compressor` omits the factor k of steady-flow isentropic work (power low by k ≈ 1.3) and `power_el` has no motor/VFD efficiency (RC-01 eq. 6: 0.93 × 0.97 [A]). Existing test `test_compressor.py:42` pins the current formula, so fix it behind a parameter, add `compressor/REFERENCE.md`, and record the efficiency assumption.
- **Model note:** DPDT treats `flowrate` as the in-situ mixture flow with constant `RHOG` (STP); a GLR 0.8 two-phase run would overstate gas volume. Keep GMF = 0 (pressurised single-phase loop) unless a WP adds in-situ gas density.
- From WP-00, still open: **WP-08** cost accessor / CO₂ factors (choose 56.6 vs 51.53 kg/GJ for grid gas; associated gas 68.6 kg/GJ); **WP-13** fail if `CostAssumptions.unset_placeholders()` is non-empty; **WP-03/WP-08** CHP electrical fraction 1/3 vs DEA ≈ 0.50 (state it); **L-1** confirm Ollama tags with `ollama show`; **all WPs** run pinned black/isort (`uvx black==25.11.0`, `uvx isort==7.0.0`) — the venv's black is 26.x. Many GEMINI files are **CRLF**: keep their line endings when editing (a plain Python `read_text`/`write_text` round-trip converts them to LF and rewrites the whole file in the diff).
- Next sessions: Batch 2 continues (WP-04, WP-10), then Batch 3 (WP-02, WP-05, WP-09).
