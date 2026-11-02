# GeoAgent — Design & Requirements Specification (v2)
SPE Europe Energy GeoHackathon Express 2026 · Agentic AI for geothermal heating & cooling of urban districts

Status: design only — nothing built · Version 2.1, 31 Oct 2026 (v1 + books review, framework/LLM research, Dutch climate, revised cooling concept, cloud-build kit) · Submission: one week from kickoff, end of day CET (≈ Wed 4 / Thu 5 Nov 2026 — confirm on Discord)

**Building this in Claude Code cloud sessions:** `CLAUDE.md` (repo root) sets the rules, `docs/hackathon/WORK_PACKAGES.md` splits the build into WP-00…WP-15 (one session each), `docs/hackathon/PROGRESS.md` carries state between sessions, `data/knowledge/reference_cards/RC-01…12` hold every equation and value with citations, and `docs/hackathon/CLOUD_SETUP.md` is the operator guide. Sessions read only the § sections their WP lists.

---

## 0. How to read this document

**Keywords.** MUST = needed to submit and score; SHOULD = needed for a top-tier score; MAY = bonus/stretch.

**Requirement IDs.** `DB` design basis · `ENG` engineering · `AG` agents · `TL` tools · `GM` GEMINI extensions · `SIM` simulation · `OPT` optimisation · `ECO` economics · `UQ` uncertainty · `VR` verification · `MEM` memory/logging · `REU` reuse/licensing · `REF` references · `REPO` repository · `EV` evaluation · `DL` deliverables · `NFR` non-functional.

**Provenance tags.** **[P]** provided by organisers · **[D]** derived by us (formula shown) · **[L]** literature in `papers/` or `books/` (source + page in §19) · **[W]** web source verified 31 Oct 2026 · **[A]** our assumption — stated in the report, tested in sensitivity.

**Sources.** GeoSystem.txt; GeoHackcleaned1/2.txt; slides sc1–sc5; GEMINI_Hackathon_Challenge_Requirements.docx.pdf; demand CSVs; GEMINI repo (`gemini_digital_twin_private` = upstream `development` @ d86e8bb, Apache-2.0); GEMINI docs (`Gemini data/`); 30 papers (`papers/`); ~50 books/reports (`books/`); arXiv 2610.40239 (Plant-as-Code); Plant Debugger + Warrant write-ups; `plantdebugger_private` (Apache-2.0).

---

## 1. What wins: scoring → design strategy

| Judging signal | What we build |
|---|---|
| **50 % agentic workflow**: autonomous analysis, interact with GEMINI, select/execute models, identify missing capabilities, integrate tools, compare alternatives, explain (sc4) | Code-driven phase workflow with LLM agents making bounded choices; model-gap detector + Model Builder that writes, tests (against textbook worked examples) and registers missing GEMINI models; Decision Records for every major choice |
| Explain LLM, reasoning, memory, architecture (kickoff) | Architecture slide + 4-tier memory (§10.2) |
| **Open-weight LLMs score higher** (kickoff Q&A) | Primary engine: open-weight model on Ollama, local, offline; commercial model only as a labelled comparison run through the same code path |
| Physics-consistent, validated, no hallucination (kickoff) | LLM never computes numbers; every model cites a textbook source and reproduces a worked example in a unit test; 4-layer deterministic verifier; independent reviewer agent |
| **50 % system design**: technology selection, sizing, realised capacities, meet demand (sc4/sc5) | 365-day simulation showing 100 % of heating and cooling met; sizing table; ≥ 8 alternatives with KPIs |
| Bonus: multi-agent, optimisation, uncertainty, techno-economics, auto model generation, autonomous verification, external data, **token minimisation** | All in scope (§7–§10); Dutch climate data as external data; naive-vs-optimised token comparison |
| Evidence: prompts, answers, tokens; agent identifies missing inputs (sc1, kickoff) | JSONL log of every LLM call with tokens; 10 scripted evaluation scenarios (§12) |
| ≤ 5 pages or ≤ 10 slides; ≤ 5-min video; push to the fork (sc1) | §13 |
| "Explain every major engineering decision"; "detect violations and iteratively improve" (Requirements PDF) | Decision Records; closed propose → guard → simulate → verify → revise loop |

Design principle (Plant-as-Code paper, Plant Debugger, Warrant): **agents propose, deterministic tools compute and check, only the verifier can accept a design, the human approves.**

---

## 2. Design basis (DB)

### 2.1 Fixed inputs [P] — loaded from one config file (`config/design_basis.yaml`), never re-typed in code

| ID | Item | Value |
|---|---|---|
| DB-01 | Reservoir mid-depth | 2,500 m TVD |
| DB-02 | Gross thickness *h* | 100 m |
| DB-03 | Geothermal gradient | 30 °C/km |
| DB-04 | Surface temperature | 12 °C |
| DB-05 | Reservoir pressure | 250 bar(g) |
| DB-06 | Permeability *k* | 200 mD |
| DB-07 | Other depths | allowed — hydrostatic pressure + gradient |
| DB-08 | Brine density / viscosity | 1,030 kg/m³ / 0.40 mPa·s |
| DB-09 | Gas–liquid ratio | 0.8 Nm³/m³; 80 mol % CH₄, 20 mol % CO₂ |
| DB-10 | Doublets | 1–3 |
| DB-11 | Flow per doublet | max 300, min 50 m³/h while active |
| DB-12 | Brine cooling producer→injector | ≤ 40 K |
| DB-13 | Pump hydraulic efficiency | 70 % |
| DB-14 | Production/injection | equal volumes, same reservoir |
| DB-15 | Doublet annual operation | 330 daily steps (interpretation DB-30) |
| DB-16 | Heating supply | 100 °C |
| DB-17 | Cooling supply | 20 °C |
| DB-18 | Heating demand | daily CSV, peak 30.0 MW (28 Jan), 111.67 GWh/yr |
| DB-19 | Cooling demand | daily CSV, peak 10.0 MW (19 Jul), 23.74 GWh/yr, 219 non-zero days |

### 2.2 Derived quantities — computed by tools; also the sanity envelope the verifier checks

| ID | Quantity | Value | Basis |
|---|---|---|---|
| DB-20 | Reservoir temperature | 87 °C | 12 + 0.03 × 2500 [D] |
| DB-21 | Hydrostatic at 2.5 km | 252.6 bar ≈ P_res ⇒ ESP needed; injection pump needed | ρgz [D]; normally pressured [L DiPippo Ch1/4] |
| DB-22 | Mass flow at 300 m³/h | 85.8 kg/s | [D] |
| DB-23 | Max geothermal heat per doublet | ≈ 14.1 MW (c_p 4.1, ΔT 40 K) | [D] |
| DB-24 | Min heat of an active doublet | ≈ 2.35 MW at 50 m³/h, 40 K | [D] |
| DB-25 | PI | ≈ 12–16 m³/h/bar (no skin); ≈ 22 bar drawdown at 300 m³/h | radial Darcy [L DiPippo eq 4.2–4.4] [D] |
| DB-26 | Thermal breakthrough | 24 yr at 1.0 km, 34 yr at 1.2 km well spacing | Gringarten–Sauty [L Paris Basin] [D] |
| DB-27 | Associated gas per doublet at 300 m³/h | 240 Nm³/h ⇒ ≈ 1.9 MW fuel (≈ 0.46 kg CH₄ + 0.32 kg CO₂ per m³ brine) | [D] |
| DB-28 | Gas mix | 0.965 kg/Nm³; LHV 29.7 MJ/kg; 68.6 kg CO₂/GJ burned incl. inherent CO₂ | [D] |
| DB-29 | Temperature at depth | 1.5 km 57 °C · 2 km 72 · 3 km 102 · 3.2 km 108 · 3.5 km 117 °C | [D] |

### 2.3 Netherlands climate (external data) — DB-34 resolved

KNMI De Bilt normals 1991–2020, monthly mean dry-bulb [W]; relative humidity is an assumption [A]; wet-bulb computed with the Stull (2011) formula [D]:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T_db (°C) [W] | 3.6 | 3.9 | 6.5 | 9.9 | 13.4 | 16.1 | 18.2 | 17.8 | 14.7 | 10.9 | 7.0 | 4.2 |
| RH (%) [A] | 87 | 84 | 80 | 75 | 74 | 76 | 77 | 78 | 82 | 85 | 88 | 88 |
| T_wb (°C) [D] | 2.4 | 2.4 | 4.6 | 7.3 | 10.6 | 13.3 | 15.4 | 15.2 | 12.7 | 9.3 | 5.8 | 3.0 |

- DB-34a MUST: daily ambient series for 2027 = piecewise-linear interpolation of monthly normals placed at mid-month (smooth seasonal profile). Replace RH with KNMI normals if obtained.
- DB-34b MUST: summer design point for chiller/heat-rejection sizing = 1 % cooling dry-bulb 25.0 °C with mean coincident wet-bulb 18.4 °C (Amsterdam WMO 062400, 1973–1996) [W NOAA Engineering Weather Data]. Sensitivity "warm climate" case 30 °C DB / 21 °C WB [A] because the record pre-dates recent warming [W].
- DB-34c: annual mean ≈ 10.5 °C; shallow ground ≈ air mean + 2 °C ≈ 12.5 °C [L Shallow DHC review p.3], consistent with DB-04.
- DB-34d: exergy dead state T₀ = daily ambient (dry-bulb), stated in every exergy KPI [L DiPippo Ch10 p.7].

### 2.4 Ambiguities — defaults to proceed with if Discord does not answer in time

| ID | Gap | Default (proceed with this) | Justification | Sensitivity |
|---|---|---|---|---|
| DB-30 | "330 daily steps" | **Availability interpretation:** each doublet is available 330 of 365 days (35-day planned outage per doublet, scheduled by the optimiser, never overlapping); heating and cooling demand must be met on all 365 days | 330/365 = 90.4 % — matches the standard 90 % availability planning assumption [L ESMAP p.31]; it is the conservative reading (forces backup capacity), and a design that passes it also passes the "simulate only 330 days" reading | Run interpretation B (simulate 330 days, no outage) and report both |
| DB-31 | District heating return temperature | **55 °C** (100/55 °C network) | Between measured Danish returns of 40–43 °C [L Danfoss/Thisted p.4–5] and conventional geothermal practice of ΔT ≥ 17–22 K [L Guidebook p.231, 234]; 55 °C lets the plate HEX cool brine ≈ 86 → 58 °C before the heat-pump evaporator takes it to ≥ 46 °C | 45 / 65 / 75 °C; agent MAY treat return temperature as a design lever (a 3 K lower reinjection gave +10 % geothermal capacity at Thisted [L]) |
| DB-32 | Cooling return temperature | **26 °C** (20/26 °C) | Direct-cooling grids run ΔT 5–6 K [L Shallow DHC review p.8] | 24 / 28 °C |
| DB-33 | Brine heat capacity | **4.1 kJ/kg·K** | Saline brine below fresh water 4.19 (Guidebook uses fresh-water values) | 4.0 / 4.19 |
| DB-35 | Cost data | Labelled sources only (§9.2) | — | ±30 % on placeholders |
| DB-36 | Associated gas handling | **Decision variable, not assumption** (DR-07): (a) degas + use gas in CHP/boiler with calcite inhibitor, or (b) keep loop pressurised and reinject dissolved gas. Venting CH₄ is never allowed | Degassing CO₂ raises calcite scaling risk [L DiPippo §4.4; WEC p.15; Guidebook p.160]; Pleasant Bayou precedent for burning separated methane [L DiPippo §9.6.3] | both variants simulated |
| DB-37 | Grid electricity CO₂ | 0.3 kg/kWh | GEMINI example | 0.2 / 0.4 |
| DB-38 | Skin, rw, re | skin 0, rw 0.108 m (8½″ hole), re 1,000 m | — | skin −1…+5 |
| DB-40 | Meaning of 70 % efficiency | Pump hydraulic efficiency (shaft); motor/VFD losses added separately (motor 93 %, VFD 97 % [A]) | Guidebook examples use 60–65 % wire-to-water [L p.256–263] | wire-to-water 70 % |

---

## 3. Engineering concept (ENG)

### 3.1 Four hard facts the agent must discover

1. **87 °C brine < 100 °C supply** ⇒ every MWh of heat needs a top-up (heat pump, boiler, CHP) unless wells reach ≥ ~3.1 km.
2. **≤ 40 K and ≤ 300 m³/h** ⇒ ≤ ≈ 14 MW geothermal per doublet ⇒ 30 MW peak needs ≥ 2 doublets + top-up, or 3 doublets.
3. **Hydrostatic head ≈ reservoir pressure** ⇒ ESP in producer, injection pump at surface; pumping is a real parasitic load.
4. **GEMINI lacks cooling, storage, electric boiler, economics and brine properties** ⇒ capabilities must be detected and added.

### 3.2 Load structure (verified from the CSVs)

| Month | Heat avg (MW) | Cool avg (MW) |
|---|---|---|
| Jan | 26.1 | 0.0 |
| Feb | 22.6 | 0.0 |
| Mar | 17.2 | 0.0 |
| Apr | 11.7 | 0.8 |
| May | 5.5 | 3.5 |
| Jun | 3.8 | 6.6 |
| Jul | 3.5 | 8.4 |
| Aug | 3.0 | 7.0 |
| Sep | 6.0 | 4.6 |
| Oct | 12.0 | 1.2 |
| Nov | 18.2 | 0.1 |
| Dec | 24.0 | 0.0 |

- Heating load factor 42 %; demand > 28.2 MW on only 4 days; > 15.7 MW on 145 days.
- Lowest 35-day heat-only window: 9 Jul–12 Aug (≤ 3.5 MW). If cooling is heat-driven (COP 0.75), the combined window moves to 6 May–9 Jun (≤ 12.2 MW) and geothermal-side heat rises from 111.7 to ≈ 143 GWh/yr.
- Rule of thumb: geothermal sized at ≈ 50–60 % of peak covers 80–90 % of annual energy [L WEC p.7; Guidebook Fig 12.30 p.244] ⇒ a strong case for geothermal base load + cheap peak/backup.

### 3.3 Cooling — revised after the books review

The books change the v1 assumption that geothermal-driven absorption cooling is the obvious answer:

- **Absorption (single-effect LiBr)** at rating conditions (6.7 °C chilled, 29.4 °C cooling water) gives only **≈ 32 % of nominal capacity and COP ≈ 0.60 at 85 °C drive**; ≈ 66 % / 0.63 at 100 °C; zero capacity ≈ 71 °C [L Guidebook Ch14 p.262–269 correlations]. Our 20 °C chilled water is outside that correlation's validity but **helps strongly**: with the characteristic-equation method, raising the mean evaporator temperature by ≈ 14 K is worth roughly the same as ≈ +15 K of drive temperature [D, to be calibrated] — i.e. 85 °C brine at 20 °C chilled ≈ 100 °C drive at 6.7 °C chilled ⇒ capacity fraction ≈ 0.6–0.7. This MUST be modelled, not assumed. Copper/CuNi generator tubes are incompatible with geothermal fluid ⇒ isolation HEX or titanium tubes [L Guidebook p.266–267]. Pumping for absorption can reach 50 % of an electric chiller's power [L p.270].
- **Electric chiller** at 20 °C chilled water: COP ≈ 6–8 (η_Carnot ≈ 0.40 calibrated from Guidebook p.257–258; Halloran: 6.4–8.5 at warmer conditions [L]).
- **Free cooling** (cooling tower + plate HEX, water-side economiser): feasible whenever T_wb + tower approach (4–5 K) + HEX approach (2 K) ≤ 20 °C, i.e. T_wb ≤ ≈ 13.5 °C. With Dutch monthly wet-bulb (§2.3) that covers Apr, May, Jun (marginal), Sep, Oct ⇒ **≈ 12 of 23.7 GWh/yr (≈ 50 %) of cooling energy is free-cooling-eligible** on a monthly-mean basis [D, screening only — daily simulation decides]. Seasonal performance factor of free cooling 24–53 [L Stockholm ATES].
- **Adsorption chiller** (MAY): lower drive temperatures than LiBr; "adsorption cooling with deep geothermal wells is planned" in Munich [L Shallow DHC review p.8]; no performance data in our sources ⇒ only if a datasheet is found.

⇒ The agent MUST compare cooling options (free cooling + electric, free cooling + absorption, absorption-only, electric-only) rather than presuppose one.

### 3.4 Candidate configurations the agent MUST generate and compare (ENG-01) — the "≥ 8 alternatives"

| ID | Wells | Heat to 100 °C | Cooling | Gas | Question it answers |
|---|---|---|---|---|---|
| **C0** | none | grid-gas boilers | electric chillers | — | Counterfactual for CO₂ and cost |
| **C1** | 3 doublets @ 2.5 km | plate HEX + gas boiler/CHP top-up | electric chillers | degas → CHP | Is "more wells, no heat pump" best? |
| **C2** | 2 doublets @ 2.5 km | plate HEX + HTHP in series on brine + CHP + peak boiler | **free cooling + electric chillers** | degas → CHP | Heat-pump route, simplest cooling |
| **C3** | 2 doublets @ 2.5 km | as C2 | **free cooling + absorption** (drive from brine via isolation HEX) + small electric peak chiller | degas → CHP | Does heat-driven cooling pay off (summer load fill vs poor low-temp absorption performance)? |
| **C4** | 2 doublets @ ≈ 3.2 km (108 °C) | direct plate HEX to 100 °C, no HTHP; small peak boiler | free cooling + absorption (drive ≈ 105 °C, capacity fraction ≈ 0.77 [L]) | degas → CHP | Drill deeper vs add a heat pump (the organisers' own example) |
| **C5** | 2–3 doublets @ 1.5 km (57 °C) | large heat pumps | free cooling + electric | degas → CHP | Shallow + heat pump |
| **C6** | best of C2/C3 + seasonal HT-ATES | storage of summer surplus | as parent | as parent | Is storage worth it? (≈ 63 % recovery [L TU Delft]) |
| **C7** | best of C2/C3 with **pressurised loop, no degassing** | as parent, gas reinjected | as parent | reinjected | Gas utilisation vs scaling risk (DR-07) |
| C8 (MAY) | best + 1 doublet as N-1 redundancy | — | — | — | Reliability vs cost |

KPIs reported for every candidate (ENG-01a MUST): unmet heating/cooling days and MWh; installed heating MW and cooling MW; annual energy by source; doublet capacity factors; electricity, gas, CO₂ (t/yr and % vs C0); CAPEX, OPEX, LCOH, LCOC; exergy efficiency (§10.1); minimum constraint margins; t_b; P10/P90 robustness (for the top 2).

### 3.5 Reference process for C2/C3 (per doublet) — answer envelope, to be confirmed by simulation

```
PRIMARY (brine)
 Reservoir 87°C ─► Producer + ESP (intake below bubble/CO2-release depth) ─► [Degasser/separator ─gas─► CHP/boiler]  (C7: no degasser, pressure held)
                                                                             │ brine ≈ 85–86 °C, inhibitor dosed
                                                                             ▼
                                                                  Filter A ─► HEX-1 (titanium plates) brine 86 → ~58 °C
                                                                             ├──► HEX-AC absorption drive (C3/C4, summer)
                                                                             ▼
                                                                  HEX-HP (heat-pump evaporator) brine → ≥ 46 °C (ΔT ≤ 40 K)
                                                                             ▼
                                                                  Filter B ─► Injection pump ─► Injector (spacing ≥ 1.2 km)
DISTRICT HEATING   return 55 °C ─► HEX-1 (→ ~82 °C) ─► HTHP condenser (→ 100 °C) ─► [CHP/boiler trim] ─► 100 °C
COOLING            20/26 °C loop ─► free-cooling HEX (cooling tower) when T_wb ≤ ~13.5 °C ─► absorption or electric chiller ─► cooling tower
```

Hand estimates (sanity only): direct HEX ≈ 9–10 MW + HTHP ≈ 6–7 MW (COP ≈ 4, ≈ 1.6 MW_e) ⇒ ≈ 15.7 MW at 100 °C per doublet; 2 doublets ≈ 31 MW ≥ 30 MW peak; pumping ≈ 0.4 MW ESP + 0.3 MW injection per doublet at full flow.

### 3.6 Engineering requirements
- ENG-02 MUST: meet 100 % of daily heating (100 °C) and cooling (20 °C) demand on all 365 days, or report and justify every unmet day.
- ENG-03 MUST: DB-10…DB-14 on every day; balanced injection per doublet.
- ENG-04 MUST: size every component and report realised heating and cooling capacities.
- ENG-05 MUST: annual energy by source, electricity, gas, CO₂, capacity factor per doublet.
- ENG-06 SHOULD: well spacing ≥ 1.2 km (t_b ≥ 30 yr); inter-doublet interference ≤ 1 bar [L Dogger].
- ENG-07 SHOULD: CO₂ reduction vs C0, LCOH, LCOC, exergy efficiency.
- ENG-08 SHOULD: maintenance outages in the lowest-load windows, never overlapping.
- ENG-09 SHOULD: scaling/corrosion screen — Ryznar index, CO₂-release pressure margin, O₂ exclusion, material selection (titanium plates for saline brine; no copper alloys) [L Guidebook Ch8].
- ENG-10 SHOULD: isolation HEX between brine and any district/chiller loop (recommended practice [L Guidebook p.156]).

---

## 4. Application architecture

### 4.1 Overview

```
 USER (CLI chat; optional web "Design Commander")
   │
 WORKFLOW ENGINE (plain Python state machine, phases P0–P10, SQLite-persisted, pause/resume)   ◄── reused Plant Debugger runner pattern
   │   decides WHICH phase runs; enforces budgets; only the verifier can ACCEPT
   ▼
 AGENT LAYER (Pydantic AI agents, open-weight model via Ollama; deterministic PolicyEngine fallback)
   Lead Engineer · Requirements & Data · Subsurface · System Architect · Model Builder · Economics · Independent Reviewer · Reporter
   │ typed tool calls (3–6 tools per agent), structured outputs (grammar-constrained)
   ▼
 TOOL LAYER (deterministic Python): design basis · climate · resource · doublet · config generator · DesignGuard · annual sim/dispatch ·
                                    optimiser · uncertainty · economics/CO₂/exergy · thermal lifetime · scaling · knowledge search · plots
   ▼
 ENGINE: GEMINI gemini_model + gemini_simulator (+ new models, registry entries, configurable brine PVT)
   ▼
 VERIFICATION: DesignGuard (pre-sim) · DesignVerifier (4 layers) · Reviewer agent · human approval
   ▼
 STATE & EVIDENCE: event log · design cache · project knowledge · LLM call log (prompts, answers, tokens) · Decision Records · run artefacts
```

Key change from v1: **the workflow skeleton is deterministic code; LLMs choose within bounded options at each phase.** This is the main defence against open-weight tool-calling unreliability (§5.4) and matches Warrant's "do not let a model do arithmetic" and Plant Debugger's "ADK chooses, code constrains".

### 4.2 Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Python | 3.12 (3.11 OK) | GEMINI needs ≥3.11,<3.13 |
| GEMINI | repo source + numpy + scipy only | No platform install (§20 Q5) |
| **Agent framework** | **Pydantic AI 2.x** (2.64.0 on PyPI, 31 Oct 2026 [W]; pin the version on day 1 and do not upgrade during the week), MIT | First-class Ollama support; `NativeOutput` uses Ollama's grammar-constrained JSON; output validators with automatic retry; deferred tools for human-in-the-loop; `result.usage()` token counts; `UsageLimits` budgets; Pydantic Evals for the scenario suite; same code path for a commercial comparison model |
| Fallback framework | Google ADK 2.x (2.13.0 latest [W]; Plant Debugger pins 2.7.1) | Only if the team prefers reusing Plant Debugger's two ADK files; local models then go through LiteLLM (`ollama_chat/…`), which has known tool-calling issues [W] |
| LLM runtime | **Ollama** (Mac and Windows; MLX on Apple silicon) | Simplest; OpenAI-compatible `/v1`; JSON-schema `format` |
| LLM models | See §4.3 | Open-weight, Apache-2.0, tool-capable |
| Schemas | Pydantic v2 | Typed tool I/O |
| Optimiser | scipy (`differential_evolution`, `linprog`) + enumeration | Deterministic |
| Properties (optional) | CoolProp (water/refrigerants) | Exergy and HTHP checks; DiPippo App. D points to REFPROP |
| Storage | SQLite + `runs/<run_id>/` files | Offline |
| Plots | matplotlib | Report figures |
| UI | CLI (MUST); web view reusing Plant Debugger static Commander page (MAY) | Organisers: text output is the minimum |
| Tests | pytest mirrored in `unit_test/`; LLM paths tested with Pydantic AI `TestModel`/`FunctionModel` (offline, no API) | GEMINI conventions; cloud sessions have no GPU |
| Where things run | Cloud sessions: code, tests, policy-engine runs. Your laptop: Ollama LLM runs (model pick, final run, eval transcripts) | `docs/hackathon/CLOUD_SETUP.md` §7 |

### 4.3 Local LLM selection [W — vendor model cards; independent BFCL leaderboard last updated Apr 2026 and does not cover these models, so we MUST run our own tool-call eval on day 1]

| Hardware | Orchestrator / most agents | Code writer (Model Builder) | Notes |
|---|---|---|---|
| **16 GB** — Windows RTX 4060 Ti 16 GB / 4080, or 16 GB Mac | **Qwen3.5-9B** q4_K_M (6.6 GB; vendor BFCL-V4 66.1, TAU2 79.1; 256K ctx; Apache-2.0) | **Gemma 4 12B** q4_K_M/QAT (7–8 GB; LiveCodeBench 72 %) or Qwen3.5-9B for everything | gpt-oss-20b (14 GB) fits a 16 GB **GPU** with short context but not a 16 GB Mac; running two models at once is borderline — use one model or `keep_alive` swapping |
| **36 GB** — Mac M3/M4 Pro/Max 36 GB (Windows nearest: RTX 4090 24 GB / RTX 5090 32 GB) | **Qwen3.6-35B-A3B** (23 GB; MoE, 3B active ⇒ fast on Mac; TAU3 67.2, MCP-Atlas 62.8) — on a 24 GB card use **Gemma 4 26B-A4B** QAT (16 GB) | **Qwen3.6-27B** (18–19 GB; SWE-bench Verified 77.2) or Qwen3.8-27B | Cannot hold 23 + 18 GB together on 36 GB: either one model for all, or Qwen3.5-9B (router) + Qwen3.6-27B (coder) ≈ 25 GB + KV cache. Gemma 4 31B has the best Tau2 (76.9 %) but is slower (dense) |

Runtime settings (NFR): `num_ctx` / `OLLAMA_CONTEXT_LENGTH` ≥ 32K (64K preferred — Ollama defaults to 4K under 24 GB VRAM [W]); `keep_alive` long; temperature 0 for formatting/extraction, model-recommended (≈ 0.6) for planning with thinking; use `format=<JSON schema>`; verify `ollama show <model>` lists `tools`.

---

## 5. Agents (AG)

### 5.1 Roles

| Agent | Model | Responsibilities | Tools (≤ 6) | Output |
|---|---|---|---|---|
| Lead Engineer | orchestrator model | Chooses among options the workflow offers; delegates; decides iterate/stop; writes recommendation | delegate, request_missing_input, finalise, escalate | Plan, Decision Records, recommendation |
| Requirements & Data | orchestrator (or small) | Builds DesignBasis; detects gaps/contradictions (DB-30…40); asks or assumes | load_design_basis, profile_demand, climate, knowledge_search, request_missing_input | design_basis.json, assumptions.md |
| Subsurface | orchestrator | Reservoir T/P, PI/II, doublet operating points, ESP/injection power, spacing, t_b, interference, depth options | reservoir_conditions, productivity_index, doublet_operating_point, thermal_breakthrough, pressure_interference | resource_assessment.json |
| System Architect | orchestrator | Generates C0–C8, chooses technologies, writes GEMINI JSON through the generator | generate_gemini_config, design_guard, simulate_annual, knowledge_search | candidates/*.json |
| Model Builder | **code model** | Model-gap analysis → writes `StaticModel` + tests from the reference card (§7.1) → pytest → register | model_gap_analysis, read_reference_card, write_model_file, run_pytest, register_model | new models + test report |
| Economics | mostly deterministic; LLM explains | CAPEX/OPEX/LCOH/LCOC, CO₂, exergy, sensitivity | economics, co2, exergy, sensitivity | economics.json |
| Independent Reviewer | different prompt, **separate context**, never told the designer exists | Finds errors in final config + KPIs + verifier report | read-only tools, verify_design | review.md |
| Reporter | orchestrator + plotting | Report sections, figures, decision log, token summary — numbers only from `kpis.json` | plot_*, build_report | report_draft.md, figures |

- AG-01 MUST: typed messages and tool calls only; everything logged (MEM-04).
- AG-02 MUST: no LLM-authored number in the report; Reporter fills numbers from tool outputs.
- AG-03 MUST: structured output with validation; on failure: send validation error back (≤ 2 retries) → then deterministic PolicyEngine for that step, recorded as `decision_source=policy`.
- AG-04 MUST: step, tool-call and token budgets per phase (Pydantic AI `UsageLimits`); exceeding ⇒ escalate with summary.
- AG-05 MUST: `request_missing_input` pauses the workflow (state saved), resumes with the answer; non-interactive runs record a labelled assumption instead.
- AG-06 MUST: off-topic questions refused/redirected; constraint conflicts explained.
- AG-07 MUST: Decision Records for: DR-01 number of doublets; DR-02 well depth; DR-03 heat top-up technology; DR-04 cooling technology mix; DR-05 storage yes/no; DR-06 maintenance windows; DR-07 gas handling (degas+CHP vs pressurised reinjection); DR-08 return temperature; DR-09 materials; DR-10 final selection; DR-11 backup capacity (added pre-L-2, 2026-11-02: install the TL-15 robust backup nameplates or keep the base-year ones, OPT-ROBUST-BACKUP, decided after P8). Each: question, options, evidence (tool-result IDs), choice, why others rejected.
- AG-08 SHOULD: reviewer independence (different prompt; MAY use a different model family).

### 5.2 Workflow phases (deterministic skeleton)

```
P0 Intake        scope check (off-topic → refuse)
P1 Design basis  load + derive + climate + gap detection → ask or assume
P2 Resource      reservoir, PI/II, doublet performance map (GEMINI), depth options, spacing
P3 Concepts      C0–C8 generated → DesignGuard
P4 Model gaps    missing capabilities → Model Builder → tests → register
P5 Screening     365-day fast simulation of all candidates
P6 Optimisation  size top 2–3; maintenance windows
P7 Verification  full GEMINI daily run of best design; 4-layer verifier — FAIL ⇒ violations back to P6/P3 (≤ 3 loops)
P8 Uncertainty   Monte Carlo on best design
P9 Review        independent reviewer; issues → P6/P7
P10 Report       recommendation, DRs, figures, tokens; human approval
```
- AG-10 MUST: phases persisted; trace replayable. AG-11 MUST: only DesignVerifier sets `ACCEPTED`. AG-12 MUST: whole pipeline runs with `--engine policy` (no LLM).

### 5.3 Decision schema
```python
class NextStep(BaseModel):
    kind: Literal["call_tool","request_input","propose_candidates","generate_model",
                  "run_optimisation","verify","delegate","finalise","escalate"]
    tool: str | None          # must be in this agent's tool list
    args: dict                # validated against the tool's input schema
    rationale: str            # ≤ 60 words
    expected_outcome: str
class Decision(BaseModel):
    phase: Phase
    steps: list[NextStep]     # ≤ 3
    open_questions: list[str]
```

### 5.4 Making open-weight models reliable (AG-13 MUST implement all)
1. Deterministic workflow skeleton — the LLM chooses within a phase, never the phase order.
2. Grammar-constrained output (Pydantic AI `NativeOutput` / Ollama `format`), not prompt-only JSON.
3. Split "act" and "format": tool-using steps in free text, then a tool-free formatter step with the schema.
4. ≤ 3–6 tools per agent; flat arguments; enums; unit suffixes; one-sentence descriptions; 1–2 few-shot call examples.
5. Validate → feed error back → bounded retry → PolicyEngine fallback.
6. Deterministic calls where inputs are known (call GEMINI/scipy from the workflow, not via the LLM).
7. Runtime hygiene: `num_ctx` ≥ 32–64K; no aggressive KV quantisation; check model template supports tools.
8. Measure: tool-call validity rate, retries, fallbacks per model in the eval suite (§12) — choose the model by data on day 1.

---

## 6. Tool catalogue (TL)
Pydantic I/O, SI internally with unit suffixes, deterministic, unit-tested, compact `summary` (≤ 300 chars) for the LLM; full data to disk.

| ID | Tool | Function | Basis |
|---|---|---|---|
| TL-01 | load_design_basis | DB-01…19 + derived DB-20…29 | provided |
| TL-02 | profile_demand | peaks, monthly, load-duration, lowest-load windows, combined profiles | CSVs |
| TL-02b | climate | daily T_db, RH, T_wb (Stull), design days | §2.3 |
| TL-03 | reservoir_conditions(depth) | T, P (hydrostatic if z ≠ 2500) | Req. PDF |
| TL-04 | productivity_index | radial Darcy PI/II with skin | DiPippo eq 4.2–4.4; GEMINI `bottomhole_skin_dp` |
| TL-05 | doublet_operating_point(Q) | solve ESP frequency for target Q; wellhead T via DPDT "up"; ESP Δp/power (curve and QΔp/η); NPSH and bubble/CO₂-release depth check for ESP setting; injection WHP and pump power | GEMINI ProducerWell/InjectorWell/nodal_solver/ESP/InjectorPump; NPSH [L Guidebook p.173]; flash-depth method [L DiPippo eq 4.16–4.20] |
| TL-06 | doublet_performance_map | TL-05 at 50…300 m³/h, cached | GEMINI |
| TL-07 | thermal_breakthrough / production_temperature_curve | t_b = πhD²ρ_aC_a/(3ρ_wC_wQ); erfc decline | [L Paris Basin] |
| TL-08 | pressure_interference | line-source superposition; ≤ 1 bar | [L Dogger] |
| TL-09 | generate_gemini_config | valid JSON (`components`, `boundary_inputs`, `connections`) for N doublets; topological order; unique names; units | GEMINI loader |
| TL-10 | design_guard | hard limits before simulation; duplicate-config hash | Plant Debugger ActionGuard |
| TL-11 | simulate_day | GEMINI build + run for one day | GEMINI |
| TL-12 | simulate_annual(mode) | 365-day dispatch; fast (maps) / full (GEMINI daily) | §8 |
| TL-13 | optimise_design | outer sizing | §9.1 |
| TL-14 | economics / co2 / exergy | §9.2; exergy per §10.1 | [L Gippsland, NREL, Guidebook Ch18, ESMAP, DiPippo Ch10] |
| TL-15 | uncertainty | Monte Carlo | §9.3 |
| TL-16 | verify_design | 4-layer report | §10.1 |
| TL-17 | model_gap_analysis | required component types vs `COMPONENT_REGISTRY`; known limitations | GEMINI registry |
| TL-18 | read_reference_card / write_model_file / run_pytest / register_model | sandboxed model generation (writes only to new model + test folders) | §7.1 |
| TL-19 | knowledge_search | keyword (MUST) / embeddings (MAY) over GEMINI `.rst`, reference cards, paper/book notes; ≤ 1,200-char cited snippets | Plant Debugger RunbookSearch |
| TL-20 | request_missing_input | pause/ask or record assumption | — |
| TL-21 | plot_* | stacked daily supply vs demand (heat, cooling), load duration, Sankey, candidate comparison, uncertainty fan, token chart | matplotlib |
| TL-22 | scaling_check | Ryznar index RI = 2pHs − pHa; gypsum RS; CO₂-release pressure margin; requires brine chemistry — if missing, flag as missing input and recommend inhibitor + pressure margin | [L Guidebook p.160–163; DiPippo §4.4] |
| TL-23 | pipe_heat_loss | ΔT = q·L/(ṁc_p); buried pre-insulated q from layered resistances | [L Guidebook p.211–214] |

---

## 7. GEMINI extensions, fixes and model provenance (GM, REF)

### 7.1 Where new models come from (REF) — answers "generated and referenced from textbooks"

- REF-01 MUST: every new or modified model has a **reference card** `src/gemini_model/<name>/REFERENCE.md` containing: governing equations (with variables and units), parameter defaults with ranges, validity limits, and citations (book, edition, chapter, page, equation/table number). The Model Builder agent writes code **from the card**, not from memory.
- REF-02 MUST: each model has at least one **unit test that reproduces a worked example from its cited source** (e.g. Guidebook plate HEX example p.222–223; OIT absorption case p.263; Guidebook groundwater chiller COP p.257–258; DiPippo pumped-well exergy example Ch10 p.26–29) within a stated tolerance.
- REF-03 MUST: copyrighted texts are cited, not copied — equations re-implemented in our own words, page references in docstrings.
- REF-04: **Sources available in `books/` and `papers/` today** (sufficient for the MUST models): Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989) — absorption Ch14, heat pumps Ch13, heat exchangers Ch11, space/district heating Ch12, piping Ch10, pumps Ch9, materials Ch8, injection Ch7, economics Ch18; DiPippo *Geothermal Power Plants* 3e — reservoir Ch4, binary-HEX Ch8 (U values Table 8.1), exergy Ch10, CHP utilisation §9.7, scaling §4.4/§6.6, environment Ch23; GNS Practical Guide (plate HEX U 3–4 kW/m²K, approach 2–3 K); Danfoss/Thisted (low-temperature DH); RTES data-centre cooling paper (chiller, dry cooler); NREL drilling cost curves; Gippsland techno-economics; ESMAP handbook (risk, finance).
- REF-05 SHOULD (only if the team has legal access): **Perry's Chemical Engineers' Handbook**, **GPSA Engineering Data Book**, **Arnold & Stewart *Surface Production Operations* Vol 1/2** are **not** in `books/`. If available, add reference cards from: Perry's — psychrometry/evaporative cooling (cooling-tower Merkel method), heat-transfer equipment, refrigeration; GPSA — heat exchangers, air-cooled exchangers, cooling towers, pumps, refrigeration, fluid flow; Surface Production Operations Vol 1 — gas–liquid separator sizing (degasser at GLR 0.8), Vol 2 — pumps, heat exchangers, piping (verify section numbers against your edition). Modern manufacturer datasheets (low-temperature LiBr chiller, high-temperature heat pump) are the best source for current performance and count as external data.

### 7.2 Changes to GEMINI (each: StaticModel/DynamicModel, `power_th`, `power_el`, `emission` outputs, unit test, `.rst` page, registry entry)

| ID | Change | Specification and source |
|---|---|---|
| GM-01 MUST | Configurable brine PVT | `PVTConstantSTP` hard-codes ρ 1000, μ 1e-3, c_p 4217 (`fluid/pvt_water_stp.py:20-25`); add JSON-settable `pvt` params on `well`/`producer_well`/`injector_well`; same values to HEX and `kpi.py` (single source of truth). ≈ 7 bar head difference at 2.5 km |
| GM-02 MUST | Register existing models | `heatpump` (HeatpumpBasic), `compressor`, optionally `co2_corrosion` |
| GM-03 MUST | Robust heat pump | closed-form Lorenz path: COP = η_L·T̄_sink/(T̄_sink − T̄_source), η_L 0.5 (calibrate: Guidebook implies η_Carnot ≈ 0.46 heating [L p.258]); Q_h = Q_src·COP/(COP−1); limits: lift and max condenser temperature from a cited HTHP datasheet (1989 limits of 82–104 °C are outdated [L Guidebook Table 13.2]); evaporator/condenser ΔT 5.6 K default, fouling 0.088 m²K/kW per HEX [L p.250] |
| GM-04 MUST | **AbsorptionChiller** | Two selectable methods: (a) **Guidebook 1989 correlation** (°F): F_T = (−239 + 1.65T_hw − 0.001T_hw²)/100; F_c = (323 − 0.838T_chw − (3.77 − 0.0358T_chw)T_cw)/100; F = F_T·F_c; COP = 0.67(55.4 + 0.185T_hw)/100; validity T_chw 40–50 °F, T_cw 75–90 °F [L Ch14 p.267–269] — used as conservative bound and for its validation test; (b) **characteristic-equation (ΔΔt) method**: Q_E = s_E(ΔΔt − ΔΔt_min), ΔΔt = (t̄_G − t̄_AC) − B(t̄_AC − t̄_E), B ≈ 1.1–1.2, coefficients calibrated to a manufacturer datasheet [A] — needed for 20 °C chilled water. Outputs Q_cool, Q_drive, Q_reject = Q_cool + Q_drive + aux, drive T_out, power_el (aux ≈ 2.2 % of Q_cool [L p.269]). Generator ΔT ≈ 7 K [L]. Isolation HEX or titanium generator tubes [L p.266] |
| GM-05 MUST | **ElectricChiller** | COP = η·T_evap/(T_cond − T_evap), η ≈ 0.40 [L Guidebook p.257–258], T_evap = 20 − 4 °C, T_cond = T_wb + approach + 3 K (tower) or T_db + 8 K (dry); cap COP 8; −2.6 %/K condenser sensitivity check [L] |
| GM-06 MUST | **HeatRejection** (cooling tower / dry cooler) | Q_rej = Q_c(1 + 1/COP) [L Guidebook p.262]; tower leaving water = T_wb + approach (4–5 K); fan + pump ≈ 2 % of heat rejected [L p.269]; dry cooler approach 5 K [L RTES]; dry option ≈ 3× cost, area and fan power of wet [L DiPippo Ch23 p.11–12]; water use MAY |
| GM-07 MUST | **FreeCooling** (water-side economiser) | active when T_wb + tower approach + HEX approach ≤ chilled supply; otherwise pre-cools chiller return; power = tower fan + pumps |
| GM-08 SHOULD | ElectricBoiler | η 0.99 |
| GM-09 SHOULD | ThermalStorage (DynamicModel) | SOC, capacity, charge/discharge limits, recovery 0.63 HT-ATES [L TU Delft] / 0.96–0.99 cold [L RTES] |
| GM-10 SHOULD | Generalise `kpi.py` | no hard-coded names or ρ/c_p |
| GM-11 SHOULD | Fix `pressure_drop.py:105` | `us_g` gas mass flow |
| GM-12 SHOULD | Separator gas composition | `ch4_fraction`, `co2_fraction`, gas density 0.965, fuel LHV 29.7 MJ/kg; inherent CO₂ reported |
| GM-13 SHOULD | Multi-doublet heating+cooling example | `simulator_examples/projects/multi_doublet_heat_cool.json` |
| GM-14 MAY | Boiler/CHP setpoint mode | back-calculate gas flow from outlet temperature |

GEMINI behaviours the tools MUST handle: producer flow is an output (TL-05 inverts it); wellhead temperature is a boundary input (computed from reservoir T); key is `boundary_inputs` (plural); connections run in declared order (generator sorts); mixed units; HEX is a rating model (size by solving UA); `BoosterPump` has `power_el = 0`; set injector reservoir pressure to 250 bar.

---

## 8. Simulation & dispatch (SIM)
- SIM-01 MUST: daily step, 365 days; energy = MW × 24 h.
- SIM-02 MUST: availability calendar per DB-30.
- SIM-03 MUST: daily dispatch (merit order or LP minimising operating cost + CO₂ price), subject to all hard limits: (1) cooling: free cooling first when climate allows → absorption (if present, capacity from GM-04) → electric chiller; (2) heat requirement = heating + absorption drive; (3) doublets: flow ∈ [50, 300] m³/h or off, brine ΔT ≤ 40 K; (4) HTHP to 100 °C up to capacity; (5) associated gas burned (if degassed); CHP electricity offsets HP/pumps; (6) peak/backup boiler; (7) unmet demand recorded.
- SIM-04 MUST: fast mode (performance maps) and full mode (GEMINI every day); daily discrepancy > 2 % warning, > 5 % fail.
- SIM-05 MUST: per-day node flows, temperatures, heat/cooling by source, electricity, gas, CO₂, unmet, margins, ambient.
- SIM-06 SHOULD: end-of-life (year 30) production-temperature check via TL-07.
- SIM-07 SHOULD: results cached by config hash.

---

## 9. Optimisation, economics, uncertainty (OPT, ECO, UQ)

### 9.1 Optimisation
- OPT-01 MUST: variables — doublets (1–3), depth (1.5/2.0/2.5/3.0/3.2/3.5 km), design flow, HEX UA, HTHP MW, absorption MW, electric chiller MW, free-cooling HEX MW, boiler/CHP MW, storage, return temperature (MAY), outage start per doublet.
- OPT-02 MUST: minimise combined LCOH + LCOC subject to zero unmet demand and hard limits; report CO₂ Pareto (2–3 points).
- OPT-03 MUST: integers enumerated; continuous by seeded `differential_evolution`.
- OPT-04: LLM chooses concepts to optimise and interprets; never sets values.

### 9.2 Economics
- ECO-01 MUST: LCOH = Σ(C_t + M_t + F_t)/(1+r)^t ÷ ΣH_t/(1+r)^t [L Gippsland]; r = 8 %; analysis period 20–30 yr (Guidebook advises ≤ 20 yr [L Ch18]; report both); O&M 1–2 % of CAPEX/yr (maintenance ≈ 1 %, insurance 0.35 %, tax 0.15 % [L Guidebook Table 18.7]); pumps replaced every 5–10 yr; heat pump at 15 yr. Shared costs allocated between heat and cooling by energy (stated).
- ECO-02 MUST: wells — C(z) from the RC-10 depth table (large-diameter vertical, NREL 2025 Fig. 5 markers: $3.45 M at 2.5 km, $6.0 M at 3 km — casing-program step between them, interpolate linearly, never smooth across the step) [L NREL 2025, ±0.15 M]; smooth fit 1.45 M$·e^(3.66×10⁻⁴ z) only for sweeps ≤ 2.5 km; cross-check US$2–6 M per full-size well [L ESMAP p.69]; injection "at least doubles" well costs vs production-only [L Guidebook p.141]; 300 m³/h needs 12″ pump in 16″ casing (≥ 13⅜″ production casing) [L Guidebook Table 6.3; Jacobs].
- ECO-03 MUST: plate HEX = 2000 + 48·ṁ·ΔT AUD [L Gippsland]; titanium plates +50 % [L Guidebook p.220]; pre-insulated pipe 500 AUD/m [L Gippsland].
- ECO-04 MUST: chillers — Guidebook 1986 cost functions (absorption vs electric, $ by tons, escalate ×≈2.5 by CEPCI [A]) [L Ch14 p.269]; remaining items in `config/cost_assumptions.yaml` with value, unit, range, **source**; HTHP, CHP, cooling tower, electric boiler, free-cooling HEX capex and electricity/gas/CO₂ prices take the sourced values in RC-10 "Sourced values" (DEA technology catalogues, NETL, Eurostat, NEa; stored in native units with price year, converted in code via `eur_to_usd` and the CEPCI factor) — no `[A] placeholder` may remain unset; ±30 % sensitivity or the card's range, whichever is wider.
- ECO-05 MUST: CO₂ factors — grid gas 56.6 kg/GJ (GEMINI) or 51.53 (Gippsland) — state choice; associated gas 68.6 kg/GJ incl. inherent CO₂ [D]; grid electricity 0.3 kg/kWh [A].
- ECO-06 SHOULD: alternative LCOH via fixed-charge-rate method LCOH = (FCR·TCC + FOC)/AEP + VOC [L NREL desalination App. E]; finance view WACC = i_d(1−tax)D + R_e·E (base 11.2 %) [L ESMAP Annex 3]; tornado on top 8 inputs.
- ECO-07 SHOULD: benchmarks for sanity — WVU heating + absorption cooling ≈ $40–43/MWh_th (full retrofit, 2012 $) [L WVU slides]; Gippsland GRAC 19.16 AUD/GJ.

### 9.3 Uncertainty
- UQ-01 SHOULD: Monte Carlo (≥ 200 fast-mode samples): permeability lognormal (≈ 100–400 mD P90–P10), gradient 28–32 °C/km, skin −1…+5, demand ±10 %, return temperature 45–65 °C, climate warm case, absorption/HTHP performance ±15 %, cost placeholders ±30 %, heterogeneity ±6 °C on 30-yr production temperature [L Paris Basin].
- UQ-02 SHOULD: P10/P50/P90 of unmet days, LCOH, CO₂; probability of meeting demand; dominant risk drivers.
- UQ-03 MAY: drilling-success prior — 68 % average well success [L ESMAP Annex 2] is for volcanic power fields; for Dutch sedimentary doublets the main risk is flow-rate (transmissivity) — use permeability distribution, cite ESMAP only as generic context; decision-tree value-of-information example [L ESMAP p.137–140] for a "test well first" Decision Record.

---

## 10. Verification, memory, logging (VR, MEM)

### 10.1 Verification layers (Plant-as-Code paper)
| Layer | Checks |
|---|---|
| Syntactic | GEMINI JSON loads; schemas valid; unit suffixes present |
| Semantic | connections reference existing components/keys; splitter fractions sum to 1; each producer paired with an injector; unique names |
| Physical | daily energy balance < 1 %; brine ΔT ≤ 40 K; 50 ≤ Q ≤ 300 m³/h; Q_inj = Q_prod; 100 °C / 20 °C met; plate-HEX approach ≥ 1.1 K (typical 2–5.6 K) [L Guidebook p.217–223; GNS]; HEX within 150 °C / 20 bar plate rating [L GNS p.40] and Δp ≈ 24 kPa/NTU [L Guidebook p.221]; HP COP ≤ Carnot and within datasheet lift; absorption capacity/COP within GM-04 bounds; absorption pumping ≤ 50 % of electric-chiller power [L Guidebook p.270]; pump power = QΔp/0.7; ESP intake above bubble/CO₂-release pressure with NPSH margin; no negative flows/powers |
| Performance & design | unmet = 0; outages non-overlapping; spacing ≥ 1.2 km; interference ≤ 1 bar; t_b ≥ 30 yr; fast vs full agreement; scaling screen passed or mitigated; materials compatible (no Cu alloys in brine; Ti plates for saline brine); sanity envelope (§2.2, §3.5) |
| Exergy KPI | specific exergy e = (h − h₀) − T₀(s − s₀); heat delivered exergy E = ṁc[(T_in − T_out) − T₀ln(T_in/T_out)]; cold exergy E = Q(T₀/T − 1); utilisation η_u = (W_net + E_heat + E_cold)/(ṁ·e_reservoir) [L DiPippo Ch10, §9.7] — reported, not pass/fail |

- VR-01 MUST: DesignVerifier is deterministic; returns deltas + ≤ 300-char summary (Plant Debugger DeterministicVerifier pattern).
- VR-02 MUST: verification report in deliverables. VR-03 SHOULD: regression test pins final KPIs.

### 10.2 Memory
| Tier | What | Implementation |
|---|---|---|
| Working | phase state, last 12–14 compacted events, candidates | Plant Debugger `_build_context`/`_compact_payload` |
| Episodic | append-only event log | Plant Debugger SQLite store |
| Design | simulated configs by hash + KPIs | new |
| Project knowledge | explicit user-confirmed facts only | Plant Debugger SiteKnowledge pattern |
| Reference | GEMINI `.rst`, reference cards, book/paper notes | TL-19 |
- MEM-01 MUST: only explicit user answers become durable project knowledge.

### 10.3 Logging and tokens
- MEM-04 MUST: every LLM call → `runs/<id>/llm_calls.jsonl`: timestamp, agent, phase, model, messages, response, tool calls, input/output tokens (Pydantic AI `result.usage()`; Ollama `prompt_eval_count`/`eval_count`), latency, retries, decision source.
- MEM-05 MUST: `token_summary.json` by agent/phase. MEM-06 SHOULD: token-saving measures switchable (summaries not arrays; compact context; cache; small model for extraction; policy for routine steps; short rationales) and a naive-vs-optimised comparison.

---

## 11. Reuse and licensing (REU)

### 11.1 Code that requires attribution
Only **Apache-2.0 code we copy or adapt** requires attribution. Ideas (Warrant, papers, books) require citation, not licence attribution.

**From `plantdebugger_private` (marcosanyo/PlantDebugger, Apache-2.0, "Copyright 2026 Plant Debugger contributors") — framework-neutral, reused with Pydantic AI:**

| File (`services/plant-debugger/plant_debugger/…`) | Lines | Code reused | Becomes |
|---|---|---|---|
| `agent/decision_engine.py` | 279 | `DecisionEngine`, `PolicyDecisionEngine` (pattern), `ResilientDecisionEngine`, `create_decision_engine` | `gemini_agent/agents/engine.py` |
| `agent/schemas.py` | 52 | `NextStep` (+ kind↔tool validator), `NextStepDecision`, `DecisionContext` | `gemini_agent/schemas/decision.py` |
| `agent/prompt.py` | 25 | prompt structure/rules style | `gemini_agent/prompts/` |
| `runner/incident_runner.py` | 577 | bounded loop, budgets, verifier-gated resolve, `_build_context`, `_compact_payload`, `_prior_action_signatures`, `_bounded_float/_int` | `gemini_agent/runner/design_runner.py` |
| `safety/action_guard.py` | 110 | `ActionGuard` validate/normalise/`signature`/`action_id` | `gemini_agent/verify/design_guard.py` |
| `tools/verify.py` | 103 | `DeterministicVerifier.evaluate` structure, `VerifyResult` pattern | `gemini_agent/verify/design_verifier.py` |
| `tools/runbook.py` | 59 | `RunbookSearch`, `RunbookSnippet`, `_compact` | `gemini_agent/tools/knowledge.py` |
| `persistence/base.py`, `sqlite_store.py` | 41 + 198 | `IncidentStore` ABC, `SQLiteIncidentStore` | `gemini_agent/memory/event_store.py` |
| `field_ops/site_knowledge.py`, `field_ops/models.py` | 206 (part) | `JsonSiteKnowledgeStore`, `new_operator_assertion`, `SiteKnowledge` (Firestore parts dropped) | `gemini_agent/memory/project_knowledge.py` |
| `logging_utils.py` | 38 | `JsonFormatter`, `configure_logging` | `gemini_agent/logging/` |
| `scripts/run_eval.py`, `scripts/validate_submission.py` | 189 + 58 | `EvalRun`, `render_markdown`, checklist structure | `scripts/` |
| `static/commander.html` + CSS/JS (MAY) | — | UI shell | `gemini_agent/ui/` |

**Only if the ADK fallback is chosen:** `agent/adk_agent.py` (228 lines — `AdkDecisionEngine`, repair turn, `_parse_candidates`) and `field_ops/adk_orchestration.py` (489 lines — `AdkDynamicFieldWorkflow`, `_validate_plan`, pause/resume). With Pydantic AI we re-implement their ideas (repair turn, plan validation, pause/resume) and credit them in the README.

**Not reused:** `deploy/`, Firestore/GCS stores, vision, auth/security, plant-sim physics, operator choreography, `tools/simulator.py` (HTTP client; pattern only).

**From GEMINI (Apache-2.0):** our fork modifies GEMINI files (PVT, registry, kpi, pressure_drop, new models). Apache §4(b) requires **prominent notices in modified files** stating they were changed.

- REU-01 MUST — compliance steps: (1) copy Plant Debugger's `LICENSE` to `third_party/plantdebugger/LICENSE`; (2) header in every copied/adapted file: `# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors. Modified by <team>, <date>: <what changed>.`; (3) header in every modified GEMINI file: `# Modified by <team> for SPE GeoHackathon 2026, <date>: <what changed>.`; (4) `NOTICE`/README section listing reused files and credits (Plant Debugger, Warrant ideas, Plant-as-Code paper); (5) disclose reuse in the report (fairness); (6) our new code under Apache-2.0 to match GEMINI.

---

## 12. Evaluation & evidence (EV)
- EV-01 MUST: scenario suite (Pydantic Evals or adapted `run_eval.py`), all transcripts with prompts, answers, tokens → appendix:

| # | Prompt / situation | Expected behaviour |
|---|---|---|
| S1 | Full brief + all files | Complete design, ACCEPTED |
| S2 | "Design a geothermal heating system for 30 MW" (no CSVs/cooling/reservoir) | Lists missing inputs, asks, does not invent |
| S3 | "What's the best pizza in Delft?" | Polite refusal/redirect |
| S4 | "Use 5 doublets at 500 m³/h" | Flags DB-10/11 violation, proposes compliant option |
| S5 | "Supply heating at 120 °C" | Notes conflict with DB-16, asks to confirm change of basis |
| S6 | Return temperature not given | Asks or records DB-31 assumption + sensitivity |
| S7 | Cooling model removed from registry | Gap detected; Model Builder writes model + passes reference-example test |
| S8 | Permeability 50 mD | Re-sizes and explains |
| S9 | Gradient 30 °C/km but "reservoir 150 °C at 2.5 km" | Detects inconsistency |
| S10 | Same task, naive vs optimised token settings | Token comparison chart |
| S11 | Same task on 16 GB-class vs 36 GB-class model | Tool-call validity, retries, fallbacks, tokens, quality |

- EV-02 MUST: unit tests for every tool/model; existing GEMINI tests still pass. EV-03 SHOULD: hand-calc and textbook worked-example tests. EV-04 SHOULD: success rate, tool calls, tokens, runtime per scenario.

---

## 13. Deliverables (DL)
- DL-01 MUST: **10-slide deck** + evidence appendix. DL-02 MUST: **≤ 5-min video**. DL-03 MUST: code pushed to team fork of `GEMINI-Digital-Twin/gemini-geothermal-suite-open` (branch `hackathon/<team>`), README, API placeholders documented. DL-04 MUST: interactions (prompts, answers, tokens), final configuration and supporting calculations.

Slides: 1 answer first (system, % met, CO₂ cut, LCOH/LCOC) · 2 problem + design basis + four hard facts · 3 agent architecture (open-weight, deterministic skeleton, memory, verifier authority) · 4 agent at work (missing input, model gap → textbook-referenced chiller model with passing test) · 5 GEMINI contributions · 6 alternatives C0–C8 table + why rejected · 7 final design (process diagram + sizing) · 8 annual performance (daily stacked heat and cooling, free-cooling share, capacity factor, Sankey) · 9 verification + uncertainty · 10 economics, CO₂, exergy, tokens, novelty. Appendix: transcripts S1–S11, token table, assumptions register, calculations, Decision Records, references.

Video (5:00): 0:00 answer · 0:30 problem · 1:15 architecture · 2:00 live clip (asks for missing input; builds chiller model from reference card; verifier rejects then accepts) · 3:15 results · 4:00 uncertainty/economics/CO₂ · 4:30 novelty + open-source contributions.

---

## 14. Repository structure (REPO)
```
gemini-geothermal-suite-open/  (fork, branch hackathon/<team>)
├─ src/
│  ├─ gemini_model/ absorption_chiller/ electric_chiller/ heat_rejection/ free_cooling/ electric_boiler/ thermal_storage/   (each with REFERENCE.md)
│  ├─ gemini_simulator/ registry.py  kpi.py  well_flow.py
│  ├─ gemini_documentation/source/ model_cooling.rst
│  └─ gemini_agent/
│     ├─ agents/ engine.py lead.py requirements.py subsurface.py architect.py model_builder.py economics.py reviewer.py reporter.py
│     ├─ runner/ design_runner.py   (phases P0–P10, pause/resume)
│     ├─ tools/  design_basis.py demand.py climate.py resource.py doublet.py config_generator.py simulate.py dispatch.py optimise.py uncertainty.py economics.py exergy.py thermal.py scaling.py knowledge.py plots.py
│     ├─ verify/ design_guard.py design_verifier.py
│     ├─ memory/ event_store.py design_cache.py project_knowledge.py
│     ├─ logging/ llm_logger.py
│     ├─ schemas/  prompts/  cli.py
├─ config/ design_basis.yaml climate_nl.yaml cost_assumptions.yaml limits.yaml llm.yaml
├─ data/ demand CSVs; knowledge/ (reference cards, book/paper notes)
├─ unit_test/ gemini_model/<new>/  gemini_agent/…
├─ scripts/ run_eval.py validate_submission.py
├─ runs/ (final submitted run committed)
├─ third_party/plantdebugger/LICENSE  NOTICE
└─ docs/ figures, slides, appendix
```

---

## 15. Non-functional (NFR)
- NFR-01 MUST: reproducible (seeds; pinned versions; final run committed). NFR-02 MUST: offline on a laptop (Ollama + numpy/scipy). NFR-03 MUST: fast screening of all candidates < 5 min; full verification < 30 min. NFR-04 MUST: no secrets; `.env.example`. NFR-05 SHOULD: GEMINI lint/tests pass. NFR-06 SHOULD: one command reproduces the result: `geoagent run --brief briefs/challenge.md --engine llm --model <tag>`.

---

## 16. Build plan (4 people, ~5 days) and risks
For cloud-session execution use the work packages in `docs/hackathon/WORK_PACKAGES.md` (WP-00…WP-15 plus local steps L-1, L-2); the table below is the equivalent plan for a human team.

| Day | A: agents/LLM | B: GEMINI & models | C: sim/dispatch/economics | D: verification/eval/deliverables |
|---|---|---|---|---|
| 1 | Fork; env; Ollama + 2 candidate models; **tool-call eval (S11) to pick model**; Pydantic AI skeleton; LLM logger; reuse files with headers | GM-01, GM-02, TL-05 doublet wrapper, TL-03/04 | design_basis.yaml, climate_nl.yaml, TL-01/02/02b | limits.yaml, DesignGuard, verifier skeleton; Discord questions |
| 2 | Workflow P0–P3 with PolicyEngine end-to-end | Reference cards + GM-03–07 with textbook-example tests | TL-09 generator (N doublets), fast annual sim + dispatch | S2–S6 scripted |
| 3 | LLM agents (Lead, Requirements, Subsurface, Architect); pause/resume | TL-06 maps, full mode | Economics, CO₂, exergy, optimiser | Physical/semantic checks; first end-to-end run |
| 4 | Model Builder demo (S7), Reviewer, token comparison | GM-08–12, `.rst` docs | Uncertainty, sensitivity; **design freeze** | Figures, DRs, S1–S11 runs |
| 5 | Final reproducible run | Tests, push | Final numbers | Slides, video, appendix, `validate_submission.py`, push |

| Risk | Mitigation |
|---|---|
| Open-weight tool calling unreliable | §5.4 measures; model chosen by eval; PolicyEngine fallback |
| GEMINI full runs slow | Performance maps for screening; full only for final |
| Absorption/HTHP performance uncertain at our temperatures | Two-method absorption model + datasheet calibration; sensitivity; free cooling reduces dependence |
| HeatpumpBasic solve fails | Closed-form path |
| Ambiguities unanswered | §2.4 defaults + sensitivity |
| Missing cost data | Sourced YAML + placeholders + robustness of ranking shown |
| Brine chemistry unknown (scaling) | Flag as missing input; inhibitor + pressure margin; C7 variant |
| Scope creep | MUST first; MAY after freeze |

---

## 17. Questions for Discord (day 1)
1. "330 daily steps": availability (35 days off) or simulate 330 days?
2. District heating and cooling return temperatures?
3. Confirm well depth may vary (hydrostatic + gradient)?
4. Brine chemistry (TDS, Cl, pH, alkalinity, Ca) for scaling/corrosion checks?
5. Cost basis (currency, discount rate, energy prices)?
6. Exact deadline (date/time CET) and submission channel.

---

## 18. Acceptance checklist
- [ ] ACCEPTED design, 0 unmet heating/cooling days over 365 days (or justified).
- [ ] DB-10…17 satisfied every day (verifier report).
- [ ] C0–C7 compared with KPIs; DR-01…DR-10 (+ DR-11) written.
- [ ] Absorption, electric chiller, heat rejection, free cooling models with reference cards, textbook-example tests, registered, documented.
- [ ] Brine PVT configurable and consistent.
- [ ] LCOH/LCOC, CO₂ vs C0, exergy, sensitivity, Monte Carlo P10/P50/P90.
- [ ] `llm_calls.jsonl` + `token_summary.json`; naive-vs-optimised and model-size comparisons.
- [ ] S1–S11 transcripts in appendix.
- [ ] Apache-2.0 notices for Plant Debugger reuse and modified GEMINI files.
- [ ] 10 slides + appendix, ≤ 5-min video, code pushed with README.

---

## 19. Reference register (REF) — cite page numbers in code and report

| Short name | File | Used for |
|---|---|---|
| Guidebook | books/GEOTHERMAL DIRECT USE ENGINEERING AND DESIGN GUIDEBOOK.pdf (Lienau & Lunis, Geo-Heat Center 1989; book p. = PDF p. − 7) | Absorption Ch14 p.261–270; heat pumps Ch13 p.249–260; plate HEX Ch11 p.217–223; district design Ch12 p.231–244; piping Ch10 p.201–214; pumps Ch9 p.167–190; materials/scaling Ch8 p.149–166; injection Ch7 p.141–146; economics Ch18 p.319–360 |
| DiPippo | books/Chapter 4, 8, 9, 10, 22, 23 … *Geothermal Power Plants* 3e | Reservoir eq 4.1–4.20, scaling §4.4; HEX U Table 8.1; exergy Ch10; CHP utilisation §9.7; dissolved methane §9.6.3; German doublets Ch22; environment Ch23 |
| GNS guide | books/A practical guide to exploiting low temperature geothermal resources.pdf | Plate HEX U, approach, limits p.40–42, 57–58 |
| Danfoss/Thisted | books/The benefits of low-temperature district heating and geothermal heat sources.pdf | Return temperatures, losses p.3–6 |
| Shallow DHC review | books/Shallow geothermal energy systems for district heating and cooling networks….pdf | Direct cooling ΔT, NL cases p.3–9 |
| ESMAP | books/FINAL_Geothermal Handbook_TR002-12_Reduced.pdf | Availability, costs, risk, WACC |
| WEC 2013 | books/World Energy Resources_2013_9_Geothermal.pdf | Sizing rule p.7; degassing/scaling p.15 |
| NREL desal. | books/Use of Low-Temperature Geothermal Energy for Desalination….pdf | FCR LCOH method |
| WVU | books/Low-temperature Geothermal Utilization.pdf | Heating + absorption cost benchmark p.31 |
| NREL drilling | papers/2025 Geothermal Drilling Cost Curves Update.pdf | Well cost vs depth |
| Gippsland | papers/Techno-Economics of Direct Use Geothermal Energy in Gippsland, Australia.pdf | LCOH, HEX/pipe cost |
| Paris Basin | papers/Flow and Thermal Modeling of a Geothermal Doublet….pdf | Breakthrough |
| RTES | papers/Grid Impact of Reservoir Thermal Energy Storage for Data Center Cooling.pdf | Chiller/dry cooler |
| Stockholm ATES | papers/Long-term Monitoring … ATES-GSHP ….pdf | Free cooling SPF |
| TU Delft HT-ATES | papers/Predictive Modeling of Heat Storage in TU Delft Campus.pdf | Storage recovery |
| Dogger multi-leg | papers/Comparison of the Modelled Hydraulic Impact….pdf | PI benchmarks, 1 bar interference |
| TNO digital twin | papers/An Open Digital Twin Platform….pdf | GEMINI architecture |
| GAIA | papers/Smarter Geothermal Field Development with an Agentic AI System.pdf | Agent loop |
| Plant-as-Code | useful input/Engineering process plants as code….pdf | Verification layers, Design-Ops |
| KNMI / NOAA | web (KNMI De Bilt normals 1991–2020; NOAA Engineering Weather Data, Amsterdam) | Climate |

---

## 20. Answers to design questions raised on 31 Oct (kept for the record)
- **Q5 — Do we need to install GEMINI?** No platform install (no Docker, Flask, InfluxDB, Celery, Chroma, Poetry full stack). We need the repo source (already cloned; fork it on GitHub), a Python 3.11/3.12 environment with numpy + scipy (+ pytest), and `PYTHONPATH=src`. "GEMINI" here is TNO's digital twin, unrelated to Google's Gemini LLM, which we do not need.
