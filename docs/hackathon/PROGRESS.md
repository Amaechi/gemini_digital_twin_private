# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p` of this file.

## Status board
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3), [#5](https://github.com/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](https://github.com/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | in review | [#11](https://github.com/Amaechi/gemini_digital_twin_private/pull/11) | closed-form HP (capped 3.71 @ design); e-boiler; storage |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](https://github.com/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](https://github.com/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | in review | [#13](https://github.com/Amaechi/gemini_digital_twin_private/pull/13) | TL-09/10/11; 918 passed after merging main (WP-03) |
| WP-07 | Annual simulation & dispatch | todo | | Batch 5 |
| WP-08 | Economics, CO₂, exergy, scaling | todo | | Batch 4 |
| WP-09 | Verifier, memory, logging | merged (901e110) | [#9](https://github.com/Amaechi/gemini_digital_twin_private/pull/9) | 41-check DesignVerifier |
| WP-10 | Agents, runner, CLI | merged (2285460; review a9083b7) | [#4](https://github.com/Amaechi/gemini_digital_twin_private/pull/4), [#6](https://github.com/Amaechi/gemini_digital_twin_private/pull/6) | policy run completes P0–P10 on stubs (UNVERIFIED) |
| WP-11 | Optimisation & uncertainty | todo | | Batch 6 |
| WP-12 | Model gap & Model Builder | todo | | Batch 5 |
| WP-13 | Eval suite & submission checks | todo | | Batch 5 |
| WP-14 | Integration run & figures | todo | | Batch 7 |
| L-1 | Local: pick LLM via S11 eval | todo | | user, local Ollama |
| L-2 | Local: final LLM run + eval transcripts | todo | | user, local Ollama |
| WP-15 | Final review & packaging | todo | | |

Status values: todo · in progress · in review · merged · blocked (say why).
Latest full suite (`main` @ bc8baab, 2026-11-01): `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **772 passed**.
Next: Batch 4 (WP-03, WP-06, WP-08).

## Decisions log (newest first)
- 11-01 WP-06: TL-09 `PlantSpec` → `build_plant_project` (unique names, Kahn order); `generate_plant(candidate, loads, PlantDispatch)`; GEMINI example rebuilt within 1 %. Wells via now-public `doublet.producer_well_parameters`/`injector_well_parameters` (WP-05 file, unchanged behaviour). Return temperatures = candidate's; degassing = `gas_handling`.
- 11-01 WP-06: layout (CFG-LAYOUT): degasser → filter → [absorption drive, Ti tubes] → HEX-1 → [HP evaporator] → injector; district return → [bypass] HEX-1s → CHP (all associated gas) → [bypass] HPs (100 °C) → peak boiler (grid gas, fired by simulate_day). C6 = parent plant (no storage model); C7 WHP 54.25 bar(g), flagged (> plate-HEX rating; PH-09 fails, PD-10 zero margin).
- 11-01 WP-06: CFG-SURFACE letdown WHP → SUB-PSUC in the degasser, surface adiabatic; brine > 100 °C flagged (flashing). CFG-HEX UA from RC-03 eq 1 inverted (GEMINI bisection 4.7 % high at C_r = 1); 85 °C brine: 82.2/58 °C, UA 3.19 MW/K. CFG-HP Carnot η 0.46 (Lorenz diverges), floor max(T_wh − 40, 100 − VER-HP-LIFT). CFG-CHP = DEA total 0.96 only; electrical share read from WP-03's CHP-EL-FRAC and checked = chp.py 1/3. Gas 29.67 MJ/kg & 68.56 kg/GJ; grid 50.03 MJ/kg & 56.6.
- 11-01 WP-06: TL-11 controls: HEX share bisected for DB-12 and ≤ 100 °C before HPs; HP share bisected while HeatpumpBasic is unphysical, else HPs off; DB-12 breach = invalid day. ESP electricity by rule on GEMINI pressures; burner CO₂ only. `verification_evidence()` uses HEX design points (RC-03 sizing checks). TL-10: limits.yaml + DB-40, Q_inj trace, venting trace, duplicates = hash of {project, design}; ratings are warnings.
- 11-01 WP-03: heat pump `solver: "closed_form"` behind a parameter (default `nelder_mead` unchanged: 1920 cases vs `main`, 0 new errors, 0 output diffs). Same problem as NM (sink at target; else source held at `Ts_in_minimum`), RC-04 COP + bracketed `brentq`; ≤ 1.5e-5 from NM wherever NM converges physically (NM gives COP < 0 or 800 °C outlets in some valid cases). Idle (not raise) on zero flow / no need / source ≤ min / COP ≤ 1 / no lift; negative or NaN inputs raise; `within_limits` None when off.
- 11-01 WP-03: closed form caps COP at VER-HP-ETA × Carnot(sink out + 4 K, source out − 4 K; HP-APPROACH) by default: η_L 0.5 Lorenz exceeds it at our glides (design 82→100 / brine 60: 4.79 → 3.71, brine out 47.1 °C; RC-04 3.5–4.5; DEA 3.57 @ 52.9 K). While capped η_L is inactive: vary VER-HP-ETA (0.50/0.55/0.65 → 3.14/3.43/4.00) and HP-APPROACH (3/5 K → 3.82/3.61). Evidence passes PH-14 at margin 0. NM evidence is uncapped (fails PH-14 at design) and withheld if NM did not converge.
- 11-01 WP-03: HTHP limits `datasheet`, `max_lift_k`, `max_sink_temperature_degc` (condenser leaving water = sink outlet), `max_carnot_fraction` default None = MISSING; limits without a citation raise; else VER-HP-LIFT 60 K (outlet to outlet) screens, no max-T check. `heat_pump_point` = `HeatPumpPoint` fields minus `name`. DEA table only as `dea_reference_cop` (40–100 K). E-boiler η 0.99 (EB-ETA). Storage: recovery on discharge (η_d 0.63 HT-ATES / 0.96 cold), capacity = injected energy, use `net_heat_to_plant_w` not `power_th`. CHP-EL-FRAC: GEMINI 1/3 kept (chp.py unchanged), DEA 0.50 = sensitivity. Cross-WP: edited WP-09's VER-HP-ETA (sensitivity, text) and VER-HP-LIFT (text); values unchanged.
- 11-01 WP-05: TL-05 → verifier passes a **bare p_b in bar(g)**; `OperatingPoint.esp_evidence` = `EspPoint` field for field (tested); the verifier's VER-ESP-MARGIN limit equals TL-05 `intake_setpoint_barg`; SUB-PBUB margin must equal VER-ESP-MARGIN (`load_subsurface_basis` refuses otherwise).
- 11-01 WP-05: brine bubble point by Henry's law (RC-01 item 9, Sander 2023) at T_res: p_b = Σ c_i/H_i(T); sea-water basis 46.05 bar(a), pure-water bound 52.17 bar(a) at 87 °C; ESP intake setpoint = computed 1.2·p_b = 55.26 bar(a) (not the card's 60); ESP setting depth 840 m (was 510 m).
- 11-01 WP-05: injector friction sign (RC-01 eq 12) behind `friction_opposes_flow=False` in `nodal_solver`/`well_flow` (`InjectorWell` JSON key; single-phase only); default unchanged; hand case 76.26 vs legacy 13.24 bar. WP-05 tools always pass `True`.
- 11-01 WP-05: TL-05 inverts GEMINI (Δp_ESP,req from IPR + DPDT, `brentq` on 30–70 Hz, `ProducerWell` check > 1 % ⇒ infeasible; < 30 Hz ⇒ choked). Plant power by rule P_shaft = QΔp/0.70, P_el = P_shaft/(0.93·0.97) (DB-13/DB-40); GEMINI curve power for comparison only; infeasible points carry no powers.
- 11-01 WP-05: ESP (SUB-ESP): GEMINI HC27000 curve flow-scaled ×1.642 so BEP = 300 m³/h; 16 stages; shallowest depth with intake ≥ setpoint.
- 11-01 WP-05: base case 300 m³/h (2500 m, WHP 3 bar(g)): 58.9 Hz, Δp 34.7 bar, shaft 0.413 MW, intake 54.7 bar(g), NPSHA 541 m, T_wh 85.2 °C, injector WHP 26.5 bar(g), pumps 0.81 MW_e; 50 m³/h: 30 Hz, 0.025 MW_e, T_wh 77.1 °C. Map 50–300 all feasible, cached in `runs/cache/doublet_maps/`; never interpolated across infeasible points.
- 11-01 WP-05: TL-07 Gringarten–Sauty mixing, ±6 K band, 30 yr; t_b 33.9 yr @ 1.2 km, 23.6 yr @ 1.0 km; D_min 1128 m. TL-08 line-source superposition (rise positive); ≤ 1 bar needs 1141 m (2 doublets) / 1787 m (3). Both refuse flow > DB-11 and cooling > DB-12; T_inj default T_wh − 40 K. TL-03 keeps DB-07 literally (2.6 bar step, warned).
- 11-01 WP-05: modules `resource.py` (TL-03/04, `load_subsurface_basis`), `doublet.py` (TL-05/06, `resolve_doublet`), `thermal.py` (TL-07/08), signatures as WP-04. Injector at GEMINI's 0.001 bar floor reports the signed required WHP; notes flag injector WHP < p_b, T_wh > 100 °C, injection below DB-04. `FlowArgs` bounded 50–300 m³/h.
- 11-01 WP-09: a given p_b is bare; the verifier applies VER-ESP-MARGIN in PH-18 and PD-10; per-ESP p_b wins over `co2_release_pressure_barg`. VER-ESP-PB does not follow VER-ESP-MARGIN (sensitivity changes both).
- 11-01 WP-09: without p_b, VER-ESP-PB = 60 bar(a) = 58.99 bar(g) drives PH-18 and PD-10, so a pressurised loop (DB-36 b) behind any plate HEX (≈ 20 / 20.68 bar(g)) fails PD-10: degas + inhibitor or shell-and-tube.
- 11-01 WP-09: verdict: REJECTED iff any check fails; UNVERIFIED if a required check is `not_evaluated` or `stub_evidence`; else ACCEPTED. 41 checks (SY, SE, PH-01…24, PD-01…10; EX-01 reported only); margin ≥ 0 passes; thresholds only from limits/cards/spec/register.
- 11-01 WP-09: PH-22 brine-side balance via new `DayResult.brine_heat_drawn_mw` (1 %/day) + first law per user; `heat_by_source_mw` = network heat only. HP lift outlet to outlet, COP ≥ 1. PH-16 absorption pumping ≤ 50 % of electric.
- 11-01 WP-09: runner: a real verifier's UNVERIFIED never loops back; escalates at P9 only if no tool result is a stub; P7 UNVERIFIED continues; ACCEPTED → UNVERIFIED if any earlier tool result is a stub.
- 11-01 WP-09: evidence = `VerificationEvidence` (project, full + fast results, KPIs, equipment points, subsurface, scaling, `stub_evidence`); real verifier not in `default_toolbox` (`verify_design_spec` for WP-14); `context_evidence` never reaches ACCEPTED.
- 11-01 WP-09: append-only events + `replay`; design cache key = SHA-256 canonical JSON + `<tool>:<mode>:v<N>`, never stubs; project knowledge ingests only durable `USER_ANSWER`s with `assumption_id`. One `LLMCallRecord.from_messages`; `compare_token_summaries` (MEM-06).
- 11-01 WP-02: electric chiller η 0.508 [D] on the refrigerant basis (COP 4.39 at 7.2/32.2 °C), supersedes η 0.40; COP cap 8 binds at Dutch design points. Absorption `include_heat_rejection_auxiliaries` supersedes zeroing `aux_power_fraction`.
- 11-01 WP-02: cooling models are registered GEMINI `StaticModel`s; `power_th` = useful cooling; chilled side by load or flow; 20/26 °C (DB-32); per-circuit ρ/c_p.
- 11-01 WP-02: absorption default Method B (B 1.15) calibrated to GHC1989 rating points; 85 °C drive → COP 0.64 (extrapolated, warned); capped at nameplate; `ghc1989` = lower bound. RC-05 ±5 % holds on the rating line only (card clarified).
- 11-01 WP-02: drive side needs ρc_pV_G ≥ s_G/2 (≈ 13.9 m³/h per MW) else off; Method A drive outlet ≥ 71.4 °C; part load COP 0.65 → 0.47 at 10 %; aux 2.2 % of Q_cool is extra vs electric; `dry_cooler` T_db + 8 K.
- 11-01 WP-02: `HeatExchanger` got optional `secondary_fluid_density`/`secondary_specific_heat` (default = primary); one ρc_p errs 0.13 % (brine vs water), 4.4–4.6 % with legacy 1050/4200.
- 11-01 WP-10 review: AG-02 refuses any digit run outside an id; AG-06 everyday words need a design word; DB-30 'B' = no outage; DR-10 never offers a REJECTED design.
- 11-01 WP-10: agents output typed `Decision`s; the runner executes every tool; role tool lists (≤ 6) enforced; `NativeOutput`, 2 retries, then `policy_fallback`; `UsageLimitExceeded` escalates.
- 11-01 WP-10: phases: DR-09 in P2, DR-01…05/07/08 in P5, DR-06 in P6, DR-10 end of P9; P7/P9 REJECTED → P6 (≤ 3 loops, 4th escalates); approval after P10.
- 11-01 WP-10: DR options built by code; recommended = top of `rank_candidates` (unmet last, lowest LCOH), register default (DR-06 DB-30, DR-08 DB-31) or spec rule (DR-09 Ti); guard-failed/REJECTED candidates ineligible.
- 11-01 WP-10: P1 gaps DB-30/31/32; input modes assume/ask/pause; answers logged as durable `USER_ANSWER`. Stubs never ACCEPTED. CLI exits 0 ok, 1 config, 2 usage, 3 paused, 4 escalated, 5 refused, 6 user rejected.
- 11-01 WP-10: scope check authoritative both ways; LLM engine needs an `LLMLogger`; DR rationales state no quantities; P9 reviewer sees tool results only.
- 11-01 WP-01 review: producer ESP scales head/power by pvt SG (brine ×1.03; no block unchanged); separator raises if `fuel_lhv_j_kg` is set without gas density.
- 11-01 WP-01: `"pvt"` block on `well`/`producer_well`/`injector_well` (unit-suffixed aliases); omitted keys keep water defaults, so give ρ, μ, c_p together for brine (DB-08/DB-33: 1030, 0.40e-3, 4100). GM-11 `us_g` fix.
- 11-01 WP-01: `kpi.py` ρ/c_p from the producer `pvt`, else legacy 1050/4200; HEX/boiler/CHP not fed automatically (`fluid_consistency_issues` warns).
- 11-01 WP-01: separator gas composition (CH₄ + CO₂ = 1, 22.414 Nm³/kmol, LHV 35.8 MJ/Nm³); `emission` 0, reports `inherent_co2_kg_s`, `co2_if_burned_kg_s` (0.314 kg CO₂/m³ ideal gas). Registered `heatpump`, `compressor` (+`power_el`), `co2_corrosion`.
- 11-01 WP-04: tool I/O models in tool modules; signature `(request=None, *, config_dir=None)` + config-object variants; DB-20…29 helpers public in `design_basis.py` (reused by WP-05); demand warns if the CSV ≠ DB-18/19.
- 11-01 WP-04: DB-25 drawdown follows the card (PI 12.2 → 24.5 bar), not the spec's ≈ 22 bar. Base case needs ESP and injection pump.
- 11-01 WP-04: lowest-load window = min rolling 35-day sum, no wrap, earliest on ties; days-above thresholds k × DB-23. Climate: interpolate T_db and RH daily, Stull T_wb on daily values.
- 10-31 WP-00: ECO-04 placeholders filled from RC-10 sourced values in native units/currency/year; USD derived in code (`eur_to_usd` 1.13, `dea_eur2020_to_eur2025` 1.168, `cepci_index`); sensitivity max(±30 %, stated range).
- 10-31 WP-00: reporting currency USD (ECO-CUR); KPI money fields currency-neutral + `currency`.
- 10-31 WP-00: schemas derive from `StrictModel`; day-average MW, MWh = Σ MW × 24. `Candidate` checks structure only; hard limits in DesignGuard; return temperatures from the register (DB-31/32).
- 10-31 WP-00: Ollama tags in `llm.yaml` verified on ollama.com 31 Oct (alternatives not); lint pins below (All WPs).
- 10-31: Pydantic AI 2.64.0; Ollama local models; deterministic phase skeleton; cloud sessions use policy engine / TestModel only (spec v2 §4).

## Assumptions added during build
(Record id → `config/assumptions.yaml` entry; never only here.)
- WP-00: DB-30, DB-31, DB-32, DB-33, DB-34a, DB-34b, DB-34c, DB-34d, DB-34-RH, DB-35, DB-36, DB-37, DB-38, DB-40 (the spec has no DB-39), ECO-CUR.
- WP-01: none.
- WP-04: SUB-RHOC (ρC)_a 2.5e6 J/m³K (DB-26); SUB-II II = PI (DB-21); DEM-COP-HD 0.75 (demand profile only; cases 0.60/0.70).
- WP-02: COOL-ABS-B, COOL-ABS-CAL, COOL-ABS-CAP, COOL-ABS-LB, COOL-ABS-AUX, COOL-ABS-PL, COOL-ABS-DRV, COOL-ELC (η 0.508 [D]), COOL-ELC-APPROACH, COOL-HR, COOL-FC.
- WP-09: VER-ESP-PB (60 bar(a)), VER-ESP-MARGIN (1.2), VER-HP-ETA (0.6 × Carnot), VER-HP-LIFT (60 K).
- WP-05: SUB-WELL (ID 0.216 m, roughness 0.0457 mm), SUB-PWH (WHP 3.0 bar(g)), SUB-PBUB (Henry's law, margin 1.2), SUB-NPSH (≥ 15 m), SUB-ESP, SUB-PSUC (0 bar(g)), SUB-SPACING (1200 m).

## Open follow-ups (grouped by the WP that must act)
**WP-03**
- Emit `HeatPumpPoint` evidence (condensing/evaporating and outlet temperatures; datasheet lift/fraction if cited); without a datasheet VER-HP-ETA and VER-HP-LIFT apply.
- HP COP for the ≈ 15–25 K lift from the RC-04 Lorenz/Carnot method, not the DEA table (starts at 40 K).
- CHP: GEMINI `chp.py` electrical fraction 1/3 vs DEA ≈ 0.50 — state the choice in the register (with WP-08).

**WP-06**
- Doublet JSON from `resolve_doublet(DoubletDesign(...), load_subsurface_basis())`: injector `friction_opposes_flow: true`, `roughness_m`, `pvt`; producer `pvt`, `roughness_m`, `esp_depth_m` = `esp.setting_depth_m`, `esp` {`no_stages`, `head_coeff`, `power_coeff`} (already flow-scaled), `esp_freq_hz` and boundary inputs (`producer_wellhead_pressure_barg`, `wellhead_temperature_degc`) from the TL-05 point; injector reservoir pressure = producer's; injector pump `efficiency_factor` = 0.70 × 0.93 × 0.97. Never use the 0.01 m default roughness.
- Brine-side HEX/boiler/CHP: `fluid_density`/`specific_heat` = `heat_exchanger_fluid_parameters(pvt)`; HEX `secondary_*` = district water (1000/4217.4). `kpi.plant_thermal_power_w` defaults to 1050 kg/m³: pass explicit values.
- Absorption chiller: brine drive with Ti tubes or a clean loop behind an isolation HEX; drive flow ≥ `minimum_drive_flow_rate_m3s`; DesignGuard checks `drive_delta_t_k` vs 40 K. With `heat_rejection` wired set `include_heat_rejection_auxiliaries: false` (free cooling `aux_power_fraction: 0`). Electric chiller `power_el` = compressor only; cooling `power_th` is not heating.
- DR-07 / C7: a pressurised loop must hold > p_b ≈ 45 bar(g) everywhere, above RC-03 plate-HEX ratings (PD-10 fails it): flag it or use shell-and-tube/plate-and-shell (no card cost).
- Separator gas → burner (with WP-08): separator `gas_water_ratio` 0.8, `ch4_fraction` 0.8, `co2_fraction` 0.2 (DB-09); burner `caloric_value` = `fuel_lhv_j_kg` (29.67e6) and `gas_emission_factor` 68.56 kg/GJ (legacy 50e6/56.6: fuel ×1.69, CO₂ ×1.39).
- Use `producer_well`/`injector_well`, not raw `well`. Keep GMF = 0 (DPDT uses STP gas density).
- If C7 uses GEMINI `Compressor`: power omits factor k (≈ 1.3 low) and motor/VFD 0.93 × 0.97; fix behind a parameter (`test_compressor.py:42` pins today's formula), add `compressor/REFERENCE.md`, register the efficiency.
- GEMINI `HeatExchanger` uses `abs(T_s,in − T_p,in)`: a HEX wired with the primary side colder moves heat the wrong way.
- Emit `HexPoint` (passes, NTU and Δp per pass, design/min brine pressure bar(g), construction, plate material) and `PumpPoint` evidence.

**WP-07**
- Fast mode: `doublet_performance_map(...).data.interpolate(field, Q)` for `total_pump_electric_power_w`, `wellhead_temperature_degc`, `esp_electric_power_w`, `injection_pump_electric_power_w`. TL-05 T_inj = T_wh − `brine_cooling_k` (default 40 K); pass the dispatched ΔT for reporting.
- Fill `DayResult.brine_heat_drawn_mw` every producing day (HP keys shared with `heat_by_source_mw`/`electricity_by_consumer_mw`); absorption drive heat stays out of `heat_by_source_mw`; fast and full results for the same days; margin keys unit-suffixed.
- Cooling: daily `ClimateProfile` T_wb/T_db (no re-interpolation); free cooling first, residual (`residual_cooling_w`, `chilled_water_temperature_out_degc`) to chillers; avoid very low absorption loads; dispatch with the model COP (0.64 at design), not DEM-COP-HD 0.75.
- DB-30 outages: lowest 35-day windows (heat-only 9 Jul–12 Aug; heat + cooling/0.75 6 May–9 Jun); non-overlapping placement (with WP-11). Read `answers['DB-30']` (B recorded, not applied; also DR-06).

**WP-08**
- One `usd_value(name)` cost accessor: never read `.value` of the eight RC-10 items (names say USD, `.value` is native, e.g. `hthp_capex_usd_per_kw_th.value` = 1.20 M EUR/MW_th); don't reconvert `usd_range`.
- ECO-05 CO₂ factors in the register: state grid gas 56.6 vs 51.53 kg/GJ; associated gas 68.6 kg/GJ. Optional ECO-07 RC-10 benchmarks.
- RC-05 chiller cost functions (1986 $, `VERIFY`), Ti generator tubes +10–15 %, dry cooler ≈ 3× wet-tower cost/area. TL-22 → `ScalingEvidence`.

**WP-11**
- Sensitivity cases: SUB-* ranges; SUB-PBUB (pure-water 52.2 bar(a); margin 1.1–1.3 — change VER-ESP-MARGIN and VER-ESP-PB with it); SUB-PSUC; SUB-II (cold injection: est. ≈ +6 bar net on the 26.5 bar injector WHP); TL-07 ±6 K band; COOL-ELC η 0.44–0.513.
- `ToolContext` carries `guard_failed`, `rejected_ids`, `violations`; outage windows with WP-07.

**WP-12**
- `agents/model_builder.py` is yours; role + 5 tools in `agents/roles.py`, stubbed in `runner/tool_stubs.py`; P4 only calls `model_gap_analysis`.
- Gaps: GEMINI `ESP` has no flow affinity; constant-property PVT (no μ(T), no cold-injection II factor); steady U = 10 W/m²K (T_wh 77.1 °C at 50 m³/h vs ≈ 81–83 transient); ESP heating ≈ 0.4 K; no injector fracture-pressure limit; no flashing check > 100 °C; ESP at Q/Q_BEP 0.33 at 50 m³/h (datasheet question; rule power optimistic); standalone `esp` type is water-only.
- `tools/doublet.py` imports private `well_flow._build_dpdt`/`_build_esp`: treat as API.

**WP-13**
- Fail submission unless the final verdict is ACCEPTED, or if any final DR has `stub_evidence: true`, `run_summary.json` has `stub_tool_calls > 0`, or `CostAssumptions.unset_placeholders()` is non-empty.

**WP-14**
- Bind real tools under the same names with `stub=False`; keep the reducer contract in `runner/toolbox.py`.
- WP-04 tools are still stubs: pass `context.config_dir`; bind `climate` to `profile_climate`; `DesignBasisReport` has no `open_gaps`, so the adapter adds DB-30…32.
- WP-05 tools: `reservoir_conditions` (DepthArgs), `productivity_index` (NoArgs), `doublet_operating_point` (FlowArgs → `OperatingPointRequest`), `thermal_breakthrough`, `pressure_interference`; give TL-07/08 argument models first (else TL-08 always uses 2 doublets).
- Register `verify_design_spec(collector)` before switching stubs off (else every stub-free run escalates at P9). Collector: raw project JSON, results, `esps` = `EspPoint(**point.esp_evidence.model_dump())`, `co2_release_pressure_barg` = `ResolvedDoublet.bubble_point.release_pressure_barg` (bare, gauge), `AbsorptionPoint`, subsurface, scaling, `stub_evidence`.
- Verifier gaps: no check on TL-05 `feasible`, `flow_error_frac ≤ 0.01`, `intake_margin_bar ≥ 0`, TL-08 `within_limit`, TL-07 `breakthrough_after_horizon` (ENG-06); cooling `validity_warnings` not read.
- Wire `DesignCache` (versioned namespace) and `JsonProjectKnowledgeStore(runs_root).ingest_events(...)`; later runs may pre-fill P1 gaps. State WP-12 gaps as report limitations.

**L-1 / L-2**
- Confirm tags with `ollama show <tag>` (must list `tools`). Set `OLLAMA_CONTEXT_LENGTH=65536` before `cli run --engine llm` (request `num_ctx` ignored); `--model TAG` overrides orchestrator and coder.
- Act/format split (spec §5.4.3) not implemented; add it if L-1 shows tool-call problems. Paused LLM runs saved before a9083b7 may not reload.

**WP-15**
- Header audit of the Plant Debugger adapted files listed in `NOTICE`.
- Spec fixes: GM-05 row says η ≈ 0.40 and omits the 5 K range (code follows RC-06); DB-25 says ≈ 22 bar (card 24.5).
- Known nits: negative `gas_water_ratio` accepted; DPDT stores params before a bad `pvt` raises; kpi `fluid_density`/`specific_heat` lack unit suffixes; PH-17 checks pump triples, not daily electricity; dissolved-gas NPSH margin not quantified; DB-30 fact stores the coerced value; SY-01 and SE-02 each build the project; `resume` of an unknown id creates `runs/<id>/`; `--run-id` not sanitised.

**All WPs**
- Lint with GEMINI's pins: black 25.11.0, isort 7.0.0, flake8 7.3.0 + flake8-docstrings 1.7.0 (`--max-line-length=100`). `/opt/geo-venv` has no isort and PATH black is 26.x: `uv pip install --python /opt/geo-venv/bin/python isort==7.0.0 black==25.11.0 flake8-pyproject==1.2.3`.
- Many GEMINI files are CRLF: keep line endings (no `read_text`/`write_text` round trip).

## Open questions for organisers (Discord)
See spec §17. Answers: —

## Hand-off notes (latest session writes here, replace previous)
2026-11-01 WP-06 (PR #13): TL-09/10/11 built; register CFG-LAYOUT, -SURFACE, -HEX, -HP, -CHP (total efficiency only; share = CHP-EL-FRAC), -BOILER, -GRID-GAS, -ASSOC-GAS, -SIZING. Merged main after WP-03 (#11). Runner NOT rewired. Follow-ups:
- **WP-14:** bind `generate_gemini_config`, `design_guard` (`GuardReport.failed_ids` → `ToolContext.guard_failed`) and `simulate_day` in place of the stubs; collector = `verification_evidence(candidate, days)` + subsurface/scaling/absorption points.
- **WP-07:** dispatch with `PlantDispatch` (flows, `absorption_drive_frac`, `hex_flow_frac`, `hp_flow_frac`, `boiler_gas_kg_s`) and `simulate_plant_day` (≈ 0.2–0.5 s/day). CHP burns all associated gas: summer surplus at design flows (C1×3: 1.0 MW), so lower flows. Absorption needs ≥ 138 m³/h drive; default drive share starves HEX-1 of doublet 1.
- **WP-06/WP-07:** WP-03's `solver: "closed_form"` is merged: switch CFG-HP to closed form + Lorenz (HP-ETA-L), use its `heat_pump_point`/`source_heat_w` as evidence and drop simulate_day's HP bypass guard (configs still use the default Nelder–Mead, behaviour unchanged).
- **WP-08:** CFG-GRID-GAS 56.6 vs ECO-05; grid-electricity CO₂ (DB-37) not in `co2_by_source_kg`; DEA CHP split needs a chp.py `electrical_fraction` parameter.
- PROGRESS.md is ≈ 23 KB (> 20 KB): condense after batch 4.
- **WP-11/WP-12:** CFG-SIZING sizes are peaks; HP nameplate only a margin (`hthp_heating_mw`); no storage component (C6); GEMINI HEX imprecise at C_r → 1; absorption isolation loop not representable (recycle).
2026-11-01 WP-03 (branch `claude/elegant-gauss-btr6ry`): `heatpump` closed form + HTHP screening + evidence, new `electric_boiler`, `thermal_storage` (registered; `model_heating.rst`; REFERENCE.md each). Tests `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **827 passed**. wp-reviewer + physics-checker findings fixed. Register added: HP-ETA-L, HP-APPROACH, EB-ETA, STO-REC-HT, STO-REC-COLD, CHP-EL-FRAC. The three WP-03 follow-ups above are done (entries left for the next condense; file now > 20 KB). New follow-ups:
- WP-06/07: HP JSON `solver: "closed_form"`, `mode: "lorenz"`, brine side `Cp_s` 4100 / `rho_s` 1030, district `Cp_h` 4217.4 / `rho_h` 1000; `source_heat_w` = brine heat drawn (PH-22); evidence `HeatPumpPoint(name=…, **out["heat_pump_point"])`, skip None. Storage: ATES outlet temperature not modelled (C6 needs a booster); annual balance from `charged/discharged_total_mwh`.
- WP-09/14: `HeatPumpPoint` has no max sink temperature, so a datasheet max T is only in the model's `within_limits`.
- WP-08: cost/credit CHP from simulated P_el/P_th (CHP-EL-FRAC); reuse that entry. WP-11: sensitivities above; CHP 0.50 needs a chp.py fraction parameter. WP-12 gaps: HP fouling, storage outlet temperature.
