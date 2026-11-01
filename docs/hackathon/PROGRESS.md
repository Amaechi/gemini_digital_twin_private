# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p` of this file.

## Status board
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3), [#5](https://github.com/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](https://github.com/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | merged (6fa9667) | [#11](https://github.com/Amaechi/gemini_digital_twin_private/pull/11) | closed-form HP (capped 3.71 @ design); e-boiler; storage |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](https://github.com/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](https://github.com/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | merged (9eb7beb) | [#13](https://github.com/Amaechi/gemini_digital_twin_private/pull/13) | TL-09/10/11; 9 CFG-*; `DayResult.flags` |
| WP-07 | Annual simulation & dispatch | in review | [#17](https://github.com/Amaechi/gemini_digital_twin_private/pull/17) | TL-12; 1043 passed |
| WP-08 | Economics, CO₂, exergy, scaling | merged (66acde7) | [#12](https://github.com/Amaechi/gemini_digital_twin_private/pull/12) | TL-14/22/23; C0 LCOH 137 $/MWh ≈ ACM cap |
| WP-09 | Verifier, memory, logging | merged (901e110) | [#9](https://github.com/Amaechi/gemini_digital_twin_private/pull/9) | 41-check DesignVerifier |
| WP-10 | Agents, runner, CLI | merged (2285460; review a9083b7) | [#4](https://github.com/Amaechi/gemini_digital_twin_private/pull/4), [#6](https://github.com/Amaechi/gemini_digital_twin_private/pull/6) | policy run P0–P10 on stubs (UNVERIFIED) |
| WP-11 | Optimisation & uncertainty | todo | | Batch 6 |
| WP-12 | Model gap & Model Builder | todo | | Batch 5 |
| WP-13 | Eval suite & submission checks | todo | | Batch 5 |
| WP-14 | Integration run & figures | todo | | Batch 7 |
| L-1 | Local: pick LLM via S11 eval | todo | | user, local Ollama |
| L-2 | Local: final LLM run + eval transcripts | todo | | user, local Ollama |
| WP-15 | Final review & packaging | todo | | |

Status values: todo · in progress · in review · merged · blocked (say why).
Latest full suite (`main` @ 9eb7beb, 2026-11-01): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **993 passed**.
Next: Batch 5 (WP-07, WP-12, WP-13).

## Decisions log (newest first)
One line per decision, grouped by WP; dated 11-01 (WP-00: 10-31). Why: the `config/assumptions.yaml` entry or the module docstring.

**WP-07** (full-suite command above)
- TL-12 fast = TL-06 map + design `PlantSpec` models direct (SIM-FAST); full = fast plan via `simulate_plant_day`.
- merit order = topology HEX → CHP → HTHP → boiler; DSP-FLOW min equal flow w/o boiler, off < 50, ≤ design.
- cooling free → absorption (DSP-ABS-MIN; drive ≥ COOL-ABS-DRV × 1.01, ΔT ≤ 40 K) → electric; DSP-HP-MIN off if ≤ +10 % brine.
- DB-30: SIM-OUTAGE lowest-load; B drops lowest heat + cool window; availability = calendar; C7 suction = WHP.
- C6: power required, discharge before boiler; SIM-06 via TL-07; full year 0 invalid d, ≤ 0.36 %.

**WP-06**
- generated HPs = WP-03 closed form, Lorenz, η_L = HP-ETA-L (GEMINI NM default kept); no validity bypass / "HPs off" rebuild.
- VER-HP-ETA cap binds everywhere (C2 design COP 3.86): η_L inert above ≈ 0.41.
- HP `within_limits` False / MISSING datasheet → new optional `DayResult.flags`, `heat_pump_flags`, margins (`hthp_i_rc04_limits_margin_count`, lift, sink T); `heat_pump_point` → PH-13/14.
- one owner each: CHP share = CHP-EL-FRAC; grid-gas CO₂ = ECO-CO2-GAS.
- TL-09 `PlantSpec` → `build_plant_project` (unique names, Kahn order); `generate_plant(candidate, loads, PlantDispatch)`; example within 1 %.
- wells via doublet.py `producer_/injector_well_parameters` (now public); return T = candidate's; degassing = `gas_handling`.
- CFG-LAYOUT brine: degasser → filter → [absorption, Ti] → HEX-1 → [HP evap] → injector; district: [bypass] HEX-1s → CHP → [bypass] HPs → peak boiler.
- C6 = parent plant (no storage model); C7 WHP 54.25 bar(g), flagged (PH-09 fails, PD-10 zero margin).
- CFG-SURFACE letdown to SUB-PSUC in the degasser, adiabatic; brine > 100 °C flagged.
- CFG-HEX UA = RC-03 eq 1 inverted; 85 °C brine → 82.2/58 °C, UA 3.19 MW/K.
- CFG-HP floor max(T_wh − 40, 100 − VER-HP-LIFT), approaches HP-APPROACH.
- CFG-CHP total 0.96; CFG-BOILER η 0.90; CFG-ASSOC-GAS 29.67 MJ/kg, 68.56 kg/GJ; CFG-GRID-GAS 50.03 MJ/kg; CFG-SIZING = peaks.
- TL-11 HEX share bisected for DB-12 and ≤ 100 °C before HPs; HP share cut only to `hthp_heating_mw`; DB-12 breach ⇒ invalid day.
- TL-11 ESP power by rule; `co2_by_source_kg` = burners only; `verification_evidence()` uses HEX design points.
- TL-10 limits.yaml + DB-40, Q_inj and venting traces; duplicate = hash {project, design}; ratings warn.

**WP-08**
- costs only via `CostBook.usd(name)`/`usd_value` (EUR × 1.13, DEA EUR2020 × 1.168, AUD × 0.63, CEPCI 2023; EUR/GJ_GCV → USD/MWh_LHV × 3.6 × 1.111); `usd_range` as is.
- sensitivity max(±30 %, range), ECO-SENS-MULT; `economics`/`cost_sensitivity` use `book.fresh()`.
- LCOH/LCOC Gippsland Eq. 1, 8 % real, 30 + 20 yr; FCR form and allocation-free cost reported.
- ECO-REAL +2.44 %/yr; ECO-OM 1.5 %; HTHP replaced 15 yr; pumps 7 yr if capex given (ECO-PUMP-CAPEX MISSING); no salvage.
- ECO-ALLOC by `brine_heat_drawn_mw` per service (absorption = cool), else delivered energy.
- ECO-ELEC-NET daily max(0, consumed − generated), no credit.
- degas_use ⇒ associated gas first, then grid gas, surplus flared; no `gas_handling` + associated gas raises.
- ECO-05: ECO-CO2-GAS 56.6 kg/GJ (51.53 sensitivity, VERIFY); ECO-CO2-AG 68.56 (DB-09, DB-28); DB-37 0.3 kg/kWh; ECO-CO2-SCOPE direct only.
- capex: 2 wells/doublet (NREL, no extrapolation); ECO-CHILLER RC-05 × CEPCI 2.51 (VERIFY, ≤ 1000 ton, absorption nominal = duty/F).
- capex: gas boiler 374 $/kW; e-boiler 175 (≤ 10 MW) / 79; dry cooler 3× tower; Ti × 1.125; CHP from `chp_electric_mw`/`chp_thermal_mw` + CHP-EL-FRAC.
- ECO-07 benchmarks: ACM cap 137.7 $/MWh, Gippsland 43.5, WVU 41.5 (2012 $).
- exergy T₀ = daily dry-bulb (DB-34d); EXG-KPI, EXG-FUEL 1.04; `exergy_utilisation_frac` = spec η_u; `base_kpis` merges.
- TL-22 SCL-RULES: no chemistry ⇒ "insufficient data" + PD-07 mitigations; fail RI < 5.5, RS > 1.2, loop < release, missing mitigation.
- TL-23 needs geometry + soil; surface DB-34c; else NET-LOSS-FRAC 10 %, NET-TRUNK-M 5 km.

**WP-03**
- HP `solver: "closed_form"` (default `nelder_mead`: 1920 cases, 0 diffs); ≤ 1.5e-5 from converged NM.
- idle on zero flow / no need / source ≤ min / COP ≤ 1; negative or NaN raise; `within_limits` None when off.
- COP cap VER-HP-ETA × Carnot (HP-APPROACH 4 K): design 3.71 (uncapped 4.79), brine out 47.1 °C; VER-HP-ETA 0.50/0.55/0.65 → 3.14/3.43/4.00; HP-APPROACH 3/5 K → 3.82/3.61.
- NM evidence uncapped (fails PH-14), withheld if not converged; closed form passes PH-14 at margin 0.
- HTHP limits default None = MISSING; uncited limits raise; else VER-HP-LIFT 60 K; DEA only as `dea_reference_cop`.
- EB-ETA 0.99; STO-REC-HT 0.63 / STO-REC-COLD 0.96 on discharge; use `net_heat_to_plant_w`.
- CHP-EL-FRAC 1/3 (chp.py), DEA 0.50 sensitivity; VER-HP-ETA/-LIFT text edited, values unchanged.

**WP-05**
- verifier gets a bare p_b bar(g); `esp_evidence` = `EspPoint`; VER-ESP-MARGIN = `intake_setpoint_barg` = SUB-PBUB margin (enforced).
- SUB-PBUB Henry's law 46.05 bar(a) (pure water 52.17 @ 87 °C); setpoint 55.26 bar(a); ESP depth 840 m.
- injector friction sign behind `friction_opposes_flow=False` (76.26 vs 13.24 bar); WP-05 tools pass `True`.
- TL-05 `brentq` 30–70 Hz (> 1 % infeasible, < 30 Hz choked); P_shaft = QΔp/0.70, P_el = P_shaft/(0.93·0.97) (DB-13/DB-40).
- SUB-ESP HC27000 × 1.642 (BEP 300 m³/h), 16 stages, shallowest depth meeting setpoint.
- 300 m³/h: 58.9 Hz, Δp 34.7 bar, 0.413 MW shaft, intake 54.7 bar(g), T_wh 85.2 °C, inj. WHP 26.5 bar(g), 0.81 MW_e; 50 m³/h: 30 Hz, 0.025 MW_e, 77.1 °C.
- maps 50–300 m³/h cached in `runs/cache/doublet_maps/`; never interpolated across infeasible points.
- TL-07 ±6 K: t_b 33.9 yr @ 1.2 km, 23.6 @ 1.0 km, D_min 1128 m; TL-08 ≤ 1 bar: 1141 m (2) / 1787 m (3); refuse > DB-11/DB-12.
- TL-03 keeps DB-07 (2.6 bar step, warned); notes flag inj. WHP < p_b, T_wh > 100 °C, injection < DB-04; 0.001 bar floor ⇒ signed required WHP.

**WP-09**
- given p_b is bare; PH-18/PD-10 apply VER-ESP-MARGIN; per-ESP p_b wins; VER-ESP-PB does not follow VER-ESP-MARGIN.
- no p_b ⇒ VER-ESP-PB 60 bar(a) = 58.99 bar(g); pressurised loop (DB-36 b) behind a plate HEX fails PD-10.
- REJECTED iff any fail; UNVERIFIED if a required check is `not_evaluated` or stub; else ACCEPTED; 41 checks (SY, SE, PH-01…24, PD-01…10; EX-01 info); margin ≥ 0 passes; thresholds only from limits/cards/spec/register.
- PH-22 via `DayResult.brine_heat_drawn_mw` (1 %/day) + first law; `heat_by_source_mw` = network heat; lift outlet to outlet, COP ≥ 1; PH-16 ≤ 50 %.
- runner: real UNVERIFIED never loops; P9 escalates only without stubs; P7 UNVERIFIED continues; any stub ⇒ not ACCEPTED.
- `VerificationEvidence`; real verifier not in `default_toolbox` (`verify_design_spec`); `context_evidence` never ACCEPTED.
- append-only events + `replay`; cache key SHA-256 + `<tool>:<mode>:v<N>`, no stubs; knowledge = durable `USER_ANSWER`s; one `LLMCallRecord.from_messages`; MEM-06.

**WP-02**
- COOL-ELC η 0.508 [D] refrigerant basis (COP 4.39 @ 7.2/32.2 °C) supersedes 0.40; COP cap 8.
- COOL-HR `include_heat_rejection_auxiliaries` supersedes zeroing `aux_power_fraction`.
- cooling = registered `StaticModel`s; `power_th` = useful cooling; chilled side by load or flow; 20/26 °C (DB-32).
- COOL-ABS-B 1.15 (COOL-ABS-CAL); 85 °C drive → COP 0.64 (warned); COOL-ABS-CAP nameplate; COOL-ABS-LB `ghc1989`.
- COOL-ABS-DRV ≈ 13.9 m³/h per MW else off; Method A drive out ≥ 71.4 °C; COOL-ABS-PL 0.65 → 0.47 at 10 %; COOL-ABS-AUX 2.2 %; COOL-ELC-APPROACH; COOL-FC T_db + 8 K.
- `HeatExchanger` optional `secondary_fluid_density`/`secondary_specific_heat` (default = primary).

**WP-10 review**
- AG-02 refuses digits outside an id; AG-06 needs a design word; DB-30 'B' = no outage; DR-10 never offers REJECTED.

**WP-10**
- typed `Decision`s; runner executes tools; role lists ≤ 6; `NativeOutput`, 2 retries, `policy_fallback`; `UsageLimitExceeded` escalates.
- DR-09 P2, DR-01…05/07/08 P5, DR-06 P6, DR-10 end P9; P7/P9 REJECTED → P6 (≤ 3, 4th escalates); approval after P10.
- recommended = top `rank_candidates` (unmet last, lowest LCOH), DR-06 DB-30, DR-08 DB-31, DR-09 Ti; guard-failed/REJECTED ineligible.
- P1 gaps DB-30/31/32; assume/ask/pause; CLI exits 0 ok, 1 config, 2 usage, 3 paused, 4 escalated, 5 refused, 6 rejected.
- scope check both ways; LLM engine needs `LLMLogger`; DR rationales have no quantities; P9 reviewer sees tool results only.

**WP-01 review**
- ESP head/power × pvt SG; separator raises on `fuel_lhv_j_kg` without gas density.

**WP-01**
- `"pvt"` on `well`/`producer_well`/`injector_well`; give ρ, μ, c_p together (DB-08/DB-33: 1030, 0.40e-3, 4100); GM-11 `us_g` fix.
- `kpi.py` ρ/c_p from producer `pvt`, else 1050/4200; HEX/boiler/CHP not fed (`fluid_consistency_issues`).
- separator gas CH₄ + CO₂ = 1, LHV 35.8 MJ/Nm³, `emission` 0 (reports inherent/if-burned CO₂); registered `heatpump`, `compressor`, `co2_corrosion`.

**WP-04**
- tool signature `(request=None, *, config_dir=None)`; DB-20…29 helpers public; demand warns if CSV ≠ DB-18/19.
- DB-25 per card (PI 12.2 → 24.5 bar); base case needs ESP and injection pump.
- lowest-load window = min rolling 35-day sum (no wrap, earliest); days-above k × DB-23; daily climate, Stull T_wb.

**WP-00**
- ECO-04 native units/currency/year, USD in code (1.13, 1.168, CEPCI); ECO-CUR USD, money fields + `currency`.
- `StrictModel` schemas; day-average MW, MWh = Σ MW × 24; `Candidate` structural; hard limits in DesignGuard.
- Ollama tags verified 31 Oct; Pydantic AI 2.64.0; cloud = policy / TestModel only (spec v2 §4).

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30, DB-31, DB-32, DB-33, DB-34a, DB-34b, DB-34c, DB-34d, DB-34-RH, DB-35, DB-36, DB-37, DB-38, DB-40 (the spec has no DB-39), ECO-CUR.
- WP-01: none.
- WP-04: SUB-RHOC (ρC)_a 2.5e6 J/m³K (DB-26); SUB-II II = PI (DB-21); DEM-COP-HD 0.75 (demand profile only; cases 0.60/0.70).
- WP-02: COOL-ABS-B, COOL-ABS-CAL, COOL-ABS-CAP, COOL-ABS-LB, COOL-ABS-AUX, COOL-ABS-PL, COOL-ABS-DRV, COOL-ELC (η 0.508 [D]), COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB (60 bar(a)), VER-ESP-MARGIN (1.2), VER-HP-ETA (0.6 × Carnot), VER-HP-LIFT (60 K).
- WP-05: SUB-WELL (ID 0.216 m, roughness 0.0457 mm), SUB-PWH (WHP 3.0 bar(g)), SUB-PBUB (Henry's law, margin 1.2), SUB-NPSH (≥ 15 m), SUB-ESP, SUB-PSUC (0 bar(g)), SUB-SPACING (1200 m).
- WP-03: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC.
- WP-08: ECO-CO2-GAS, ECO-CO2-AG, ECO-CO2-SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES.
- WP-06: CFG-LAYOUT, CFG-SURFACE, CFG-HEX, CFG-HP, CFG-CHP, CFG-BOILER, CFG-GRID-GAS, CFG-ASSOC-GAS, CFG-SIZING.
- WP-07: SIM-OUTAGE, SIM-DB30B, SIM-FAST, DSP-FLOW, DSP-HP-MIN, DSP-ABS-MIN, DSP-STORAGE.

## Open follow-ups (grouped by the WP that must act)
**WP-07** (old items done)
- WP-14: bind TL-12 (`.data.annual.kpis`, `service_by_key` → WP-08, `verification_evidence(sim, c, **extra)`); KPIs cached by `config_hash` (put only).
- WP-11: lever `outage_start_indices`; DSP-FLOW not cost-optimal (priced flow search); vary DSP-*; C6 sizes.
- WP-09/14: PH-22 fails C6 `storage` heat (no input key). WP-12: ATES outlet T; HP share bisection.
- Treat as API: `simulate_day._flag_text/_heat_pump_margins/_heat_pump_screening`, `Mixer._MIN_TOTAL_FLOW`

**WP-11**
- Sensitivities: SUB-* ranges; SUB-PBUB (52.2 bar(a); margin 1.1–1.3 with VER-ESP-MARGIN and VER-ESP-PB); SUB-PSUC; SUB-II (cold injection ≈ +6 bar on 26.5 bar WHP); TL-07 ±6 K; COOL-ELC η 0.44–0.513.
- HP: vary VER-HP-ETA, HP-APPROACH (not HP-ETA-L). CHP-EL-FRAC 0.50 needs a chp.py parameter. CFG-SIZING = peaks.
- `cost_sensitivity` (tornado, top 8); `Co2Request(grid_gas_kg_per_gj=51.53)` (cross-check warns ≈ 9 %, expected); WACC view (ECO-06) not implemented.
- `ToolContext` has `guard_failed`, `rejected_ids`, `violations`; outages with WP-07.

**WP-12**
- Yours: `agents/model_builder.py`; role + 5 tools in `agents/roles.py`, stubs in `runner/tool_stubs.py`; P4 only calls `model_gap_analysis`. `tools/doublet.py` uses private `well_flow._build_dpdt`/`_build_esp`: treat as API.
- Subsurface gaps: `ESP` no flow affinity; constant PVT (no μ(T), cold-injection II); steady U = 10 W/m²K (T_wh 77.1 vs ≈ 81–83 °C transient, 50 m³/h); ESP heating ≈ 0.4 K; no fracture limit; no flashing check > 100 °C; Q/Q_BEP 0.33 at 50 m³/h; `esp` water-only.
- Plant gaps: no storage component (C6; ATES outlet T unmodelled, needs a booster); HP fouling; HEX imprecise at C_r → 1, uses `abs(T_s,in − T_p,in)` (colder primary ⇒ wrong-way heat); no absorption isolation loop.
- GEMINI `Compressor` (unused) omits k (≈ 1.3 low) and 0.93 × 0.97: if used, fix behind a parameter, add `REFERENCE.md`.

**WP-13**
- Fail submission if the final verdict ≠ ACCEPTED, any final DR has `stub_evidence: true`, `run_summary.json` `stub_tool_calls > 0`, or `CostAssumptions.unset_placeholders()` is non-empty (empty today). Reports flag `cost_items[].verify` (RC-05).

**WP-14**
- Bind real tools under the same names with `stub=False`; keep the `runner/toolbox.py` reducer contract.
- WP-04: pass `context.config_dir`; `climate` → `profile_climate`; adapter adds DB-30…32 (no `open_gaps`).
- WP-05: `reservoir_conditions` (DepthArgs), `productivity_index` (NoArgs), `doublet_operating_point` (FlowArgs → `OperatingPointRequest`), `thermal_breakthrough`, `pressure_interference`; add TL-07/08 argument models (else TL-08 uses 2 doublets).
- WP-06: `generate_gemini_config`, `design_guard` (`failed_ids` → `ToolContext.guard_failed`), `simulate_day`; map `GeneratedConfig` (`hex_design`, `equipment_mw`, brine ṁ/ΔT) to `CapexRequest` (MW; `plate_hexes` ṁ/ΔT; `absorption_capacity_fraction` from WP-02; `cooling_tower_mw` only outside RC-05 plants).
- WP-08: `economics(EconomicsRequest, annual)`, `co2_emissions(Co2Request, annual)`, `exergy_kpi(annual, climate=, base_kpis=)`, `cost_sensitivity`, `scaling_check(ScalingRequest)` (C7 `co2_release_pressure_barg` from TL-05), `pipe_heat_loss`; C0 CO₂ first (`c0_co2_t_per_yr`).
- Register `verify_design_spec(collector)` before stubs go off (else P9 escalates); collector = `verification_evidence(candidate, days)` + `EspPoint(**point.esp_evidence.model_dump())`, `bubble_point.release_pressure_barg` (bare, gauge), `AbsorptionPoint`, subsurface, scaling, `stub_evidence`.
- Verifier gaps: unchecked TL-05 `feasible`, `flow_error_frac ≤ 0.01`, `intake_margin_bar ≥ 0`, TL-08 `within_limit`, TL-07 `breakthrough_after_horizon` (ENG-06); unread cooling `validity_warnings`, `DayResult.flags` (margins carry them, PH-21); `HeatPumpPoint` lacks max sink T.
- Wire `DesignCache` (versioned), `JsonProjectKnowledgeStore(runs_root).ingest_events(...)` (may pre-fill P1 gaps). WP-12 gaps = report limitations.

**L-1 / L-2**
- Confirm tags with `ollama show <tag>` (must list `tools`). Set `OLLAMA_CONTEXT_LENGTH=65536` before `cli run --engine llm` (`num_ctx` ignored); `--model TAG` overrides orchestrator and coder.
- Act/format split (spec §5.4.3) not implemented: add it if L-1 shows tool-call problems. Paused LLM runs from before a9083b7 may not reload.

**WP-15**
- Header audit of adapted Plant Debugger files (`NOTICE`).
- Spec/card: GM-05 says η ≈ 0.40, no 5 K range (code follows RC-06); DB-25 says ≈ 22 bar (card 24.5); RC-09 "0.55 K/km" not reproducible (needs R_total ≈ 1.24 < R_ins 1.59 m·K/W of 25 mm PUR); RC-10 lacks ESP/injection-pump capex.
- Nits: negative `gas_water_ratio` accepted; DPDT stores params before a bad `pvt` raises; kpi `plant_thermal_power_w` default 1050 kg/m³, unsuffixed `fluid_density`/`specific_heat`; PH-17 checks pump triples, not daily power; dissolved-gas NPSH margin unquantified; DB-30 fact stores the coerced value; SY-01, SE-02 both build the project; `resume <unknown id>` creates `runs/<id>/`; `--run-id` unsanitised; `resource.py:235` reads `costs.items[...].value`; `heat_pump_1987_usd_per_kw` unescalated.

**All WPs**
- Lint with GEMINI's pins: black 25.11.0, isort 7.0.0, flake8 7.3.0 + flake8-docstrings 1.7.0 (`--max-line-length=100`). `/opt/geo-venv` has no isort and PATH black is 26.x: `uv pip install --python /opt/geo-venv/bin/python isort==7.0.0 black==25.11.0 flake8-pyproject==1.2.3`.
- Many GEMINI files are CRLF: keep line endings (no `read_text`/`write_text` round trip).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
2026-11-01, after batch 4 (WP-03, WP-06, WP-08 merged): full suite on `main` @ 9eb7beb → **993 passed**. Real tools exist for TL-01…11, 14, 22, 23; the runner still uses stubs (WP-14 binds them). Next: batch 5 (WP-07, WP-12, WP-13).
