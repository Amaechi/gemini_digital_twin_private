# PROGRESS — read first, update last

Keep it ≤ 20 KB; it is the hand-off between sessions. Old detail: merged PRs and `git log -p` of this file.

## Status board
| WP | Title | Status | PR | Notes |
|---|---|---|---|---|
| WP-00 | Bootstrap, env, CI, skeleton | merged (529bc4b) | [#1](https://github.com/Amaechi/gemini_digital_twin_private/pull/1) | doublet example Q = 129.02 m³/h |
| WP-01 | GEMINI core fixes, registry | merged (ac57442; review cac5889) | [#3](https://github.com/Amaechi/gemini_digital_twin_private/pull/3), [#5](https://github.com/Amaechi/gemini_digital_twin_private/pull/5) | pvt blocks, separator gas, registry |
| WP-02 | Cooling models | merged (6eedd33) | [#7](https://github.com/Amaechi/gemini_digital_twin_private/pull/7) | 4 cooling models; HEX per-side ρ/c_p |
| WP-03 | Heat pump, e-boiler, storage | todo | | Batch 4 |
| WP-04 | Design basis, demand, climate | merged (f7acb2a) | [#2](https://github.com/Amaechi/gemini_digital_twin_private/pull/2) | TL-01/02/02b |
| WP-05 | Subsurface & doublet tools | merged (bc8baab) | [#8](https://github.com/Amaechi/gemini_digital_twin_private/pull/8) | TL-03…08; 7 SUB-* |
| WP-06 | Config generator, guard, day sim | todo | | Batch 4 |
| WP-07 | Annual simulation & dispatch | todo | | Batch 5 |
| WP-08 | Economics, CO₂, exergy, scaling | in review | [#12](https://github.com/Amaechi/gemini_digital_twin_private/pull/12) | TL-14/22/23; C0 LCOH 137 $/MWh ≈ ACM cap |
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
- 11-01 WP-08: cost values only via `CostBook.usd(name)`/`usd_value` (economics.py): EUR × 1.13 (DEA EUR2020 × 1.168 first), AUD × 0.63, `escalation_index: cepci` × CEPCI 2023/year; unit from the name (M EUR/MW → USD/kW; EUR/GJ_GCV → USD/MWh_LHV × 3.6 × 1.111); `usd_range` as is; bounds max(±30 %, range), `*_multiplier` on the premium (ECO-SENS-MULT). Call `economics`/`cost_sensitivity` with any book: each takes `book.fresh()`.
- 11-01 WP-08: LCOH/LCOC = Gippsland Eq. 1, r 8 % real, 30 yr + 20 yr (`KPISet.lcoh_20yr_per_mwh`), FCR form and allocation-free combined cost reported; energy and CO₂ prices escalate 2.44 %/yr real = 1.05/1.025 − 1 (ECO-REAL; request 0 for constant prices); O&M 1.5 % of capex; HTHP replaced at 15 yr; pumps at 7 yr only with a caller pump capex (ECO-PUMP-CAPEX: not on RC-10 ⇒ MISSING INPUT); no salvage. Shared costs (wells, pumps, flaring) by `brine_heat_drawn_mw` per service (absorption drive = cool, rest heat), else by delivered energy (ECO-ALLOC).
- 11-01 WP-08: carriers: purchased power = daily max(0, consumed − generated), surplus not credited (ECO-ELEC-NET); degas_use ⇒ burners take associated gas first, rest grid gas, surplus flared; `gas_handling` unset with associated gas raises. CO₂: grid gas 56.6 kg/GJ (ECO-CO2-GAS; 51.53 sensitivity, its HHV/LHV basis VERIFY), associated 68.56 kg/GJ from DB-09 (DB-28), 0.3 kg/kWh on purchased power (DB-37); CO₂ price on direct emissions only (ECO-CO2-SCOPE).
- 11-01 WP-08: capex: 2 large-diameter wells/doublet (NREL table, no extrapolation, step flagged); RC-05 chiller plant functions × CEPCI 2.51 (VERIFY; include heat rejection; units ≤ 1000 nominal ton, absorption nominal = duty/F; ECO-CHILLER); gas boiler 270 $/kW 2014 × CEPCI = 374; e-boiler small ≤ 10 MW 175 $/kW, large 79; dry cooler 3× tower; Ti tubes 1.125; CHP needs `chp_electric_mw` or `chp_thermal_mw` + CHP-EL-FRAC (register wins, else parameter). Benchmarks: ACM cap 137.7 $/MWh, Gippsland 43.5, WVU 41.5 (2012 $).
- 11-01 WP-08: exergy: T₀ = daily dry-bulb of `tools/climate.py` (DB-34d); `exergy_efficiency_frac` = functional (E_heat + E_cold + W_gen)/(brine e at T_prod + W_cons + 1.04 × fuel LHV) (EXG-KPI, EXG-FUEL); spec η_u → new `exergy_utilisation_frac` (None without brine). Pass `base_kpis` to merge with the economics KPIs.
- 11-01 WP-08: TL-22: no chemistry ⇒ "insufficient data" + analytes + required mitigations (= PD-07 set); K_c(T), log K_gyp(T) are inputs (not on RC-12); fail on RI < 5.5, RS > 1.2, pressurised loop below release, or (complete data) a missing mitigation (SCL-RULES). TL-23: layered resistance needs geometry + soil (no defaults); surface = DB-34c annual-mean air; else 10 % of demand (NET-LOSS-FRAC), 5 km trunk (NET-TRUNK-M).
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
2026-11-01 WP-08 ([#12](https://github.com/Amaechi/gemini_digital_twin_private/pull/12), in review): `tools/{economics,exergy,scaling,network}.py`; `PYTHONPATH=src python -m pytest -q unit_test/gemini_model unit_test/gemini_simulator unit_test/gemini_agent -x` → **837 passed**. wp-reviewer + physics-checker: no blockers; should-fixes resolved in 6141c7c. The three WP-08 items under "Open follow-ups" are done (accessor, ECO-05 factors, RC-05 functions; TL-22 → `ScalingReport.evidence`). Register: ECO-CO2-GAS, ECO-CO2-AG, ECO-CO2-SCOPE, ECO-ELEC-NET, ECO-REAL, ECO-ALLOC, ECO-OM, ECO-PUMP-CAPEX, ECO-CHILLER, ECO-SENS-MULT, EXG-FUEL, EXG-KPI, NET-LOSS-FRAC, NET-TRUNK-M, SCL-RULES. Sanity (crude, real profiles): C0 LCOH 137 / LCOC 45 $/MWh; C2 63 (20 yr 66) / 49.5; CO₂ −57 %; exergy 16.6 % vs 37.5 %.
New follow-ups (to move into "Open follow-ups" at the next condense; this file is now > 20 KB):
- WP-06: register `CHP-EL-FRAC` as a plain number in (0, 1) (electricity / useful output; GEMINI 1/3 vs DEA ≈ 0.50); map candidate equipment to `CapexRequest` (sizes in MW; `plate_hexes` with brine ṁ and ΔT; `absorption_capacity_fraction` from the WP-02 model; `cooling_tower_mw` only for rejection outside RC-05 chiller plants).
- WP-07: name consumers/burners so `classify_service` resolves them (chiller/cool/tower/absorption/free/rejection → cool; hp/hthp/heat_pump/boiler/chp → heat; esp/pump/injection → shared) or pass `service_by_key`; fill `brine_heat_drawn_mw` (absorption drive key with "absorption") and `associated_gas_produced_nm3`; `ambient_dry_bulb_degc` = climate dry-bulb.
- WP-11: use `cost_sensitivity` (tornado, top 8) and `Co2Request(grid_gas_kg_per_gj=51.53)`; WACC finance view (ECO-06) not implemented.
- WP-13: `CostAssumptions.unset_placeholders()` still empty; reports should flag `cost_items[].verify` (RC-05).
- WP-14: bind `economics(EconomicsRequest, annual)`, `co2_emissions(Co2Request, annual)`, `exergy_kpi(annual, climate=, base_kpis=)`, `cost_sensitivity(...)`, `scaling_check(ScalingRequest)` (`co2_release_pressure_barg` from TL-05 for C7), `pipe_heat_loss(...)`; C0 CO₂ first (for `c0_co2_t_per_yr`).
- WP-15: RC-09 "0.55 K/km" not reproducible from the card's PUR data (needs R_total ≈ 1.24 m·K/W < R_ins 1.59 of 25 mm PUR) — documented discrepancy; ESP/injection-pump capex missing on RC-10; `resource.py:235` reads `costs.items[...].value` directly (harmless, breaks the accessor rule); `heat_pump_1987_usd_per_kw` unescalated if ever used.
