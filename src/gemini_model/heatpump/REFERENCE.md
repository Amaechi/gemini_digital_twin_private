# REFERENCE — heat pump (GM-03)

Model: `heatpump_basic.HeatpumpBasic` (registry type `heatpump`), closed-form helpers in
`closed_form.py` (`carnot_cop`, `lorenz_cop`, `log_mean_temperature_k`, `sink_heat_from_source`,
`solve_operating_point`, `hthp_limits`, `dea_reference_cop`).
Source card: `data/knowledge/reference_cards/RC-04_heat_pump.md` (copied below).
Citations: Geothermal Direct Use Engineering & Design Guidebook (Geo-Heat Center 1989) Ch13
p.250, 258, 260, Table 13.2; RC-10 (DEA COP-by-lift, plausibility only). Spec §7.2 GM-03.

## Implementation notes (WP-03)
- **Two solvers.** `solver: "nelder_mead"` (default) is GEMINI's original path, unchanged.
  `solver: "closed_form"` solves the same problem (sink outlet at `Th_out_target`, source outlet
  unknown; if the source would fall below `Ts_in_minimum` it is held there and the sink outlet
  drops) without a starting guess: the COP is the closed-form RC-04 expression and the one
  unknown temperature is a bracketed `brentq` root of the energy balance (unique on the target
  branch; on the source-floor branch the root nearest the target, as GEMINI's start point).
  `mode` keeps GEMINI's meaning: `lorenz` = RC-04 eq. 2 on water log means; `carnot` =
  η_C · Th_out/(Th_out − Ts_in).
- **Agreement:** on a 576-case exploratory grid (both modes, both branches) the two solvers
  agree within 1.5e-5 in the 304 cases where Nelder-Mead converges to a physical point
  (acceptance: 2 %); the unit test repeats this on a 192-case subset. Nelder-Mead
  returns negative COPs or a sink outlet of hundreds of °C in some valid cases; the closed form
  satisfies both equations to 1e-9 there.
- **COP cap (closed form, `apply_cop_cap: true` default):** COP ≤ `max_carnot_fraction` ×
  Carnot at the refrigerant temperatures, condensing = sink outlet + 4 K, evaporating = source
  outlet − 4 K (RC-04 eq. 1 and 4; HP-APPROACH). Without a datasheet the fraction is RC-04's
  default 0.6 — register **VER-HP-ETA**, the DesignVerifier's own entry (PH-14), reused. At the
  design case (82→100 °C sink, brine 60 °C in) η_L 0.5 gives 4.79 uncapped, the cap 3.71 (RC-04
  expects 3.5–4.5; DEA 3.57 at the same 53 K lift). The Nelder-Mead path is never capped and
  warns when it is above the cap.
- **HTHP limits are `MISSING`** until a cited datasheet is supplied: parameters `datasheet`
  (citation, required with any limit), `max_lift_k`, `max_sink_temperature_degc`,
  `max_carnot_fraction` all default to None. Without them: lift ≤ 60 K (**VER-HP-LIFT**, reused)
  and the 0.6 cap apply; the maximum sink temperature has no default and is not checked
  (Table 13.2's 82–104 °C are outdated). `max_sink_temperature_degc` is RC-04's "max condenser
  leaving T", i.e. compared with the sink water outlet; convert a refrigerant condensing limit
  (subtract the condenser approach) before passing it. Outputs `missing_inputs`, `limits_source`,
  `assumption_ids`, `within_limits`, `validity_warnings`.
- **Evidence:** `heat_pump_point` = the fields of the verifier's `HeatPumpPoint` except `name`
  (COP, condensing/evaporating, sink/source outlet, datasheet lift/fraction or None); None when
  idle. Build it with `HeatPumpPoint(name=..., **output["heat_pump_point"])`. Also
  `source_heat_w` (heat drawn from the source = brine heat for PH-22), `lift_k` (outlet to
  outlet), `mean_lift_k` (Lorenz), `dea_reference_cop` (RC-10, 40–100 K only).
- **Idle, not an exception** (dispatch-friendly): no flow, sink inlet ≥ target, source inlet ≤
  `Ts_in_minimum`, COP ≤ 1, or no lift (uncapped water-side COP infinite — the finite cap never
  makes a pump run without lift); `running` False and the reason in `validity_warnings`.
- **Nelder-Mead plausibility:** on the default path `converged` is False (no `heat_pump_point`,
  `within_limits` False, a warning) when either GEMINI equation misses by > 0.1 % or an outlet
  leaves its bounds (e.g. a sink outlet of ≈ 800 °C). GEMINI's own outputs are unchanged. Plant
  builders should use `solver: "closed_form"`: the uncapped Nelder-Mead COP fails PH-14 at the
  design case.
- Approaches outside RC-04's 3–5 K are accepted (> 0) but warned.
- **Water ΔT > 11 K** on either side warns (4/8-pass evaporator/condenser, Guidebook p.250).
  Fouling 0.088 m²K/kW is not modelled (no HEX sizing in this model).

## Register entries
HP-ETA-L (η_L 0.5), HP-APPROACH (4 K / 4 K), VER-HP-ETA (0.6, reused), VER-HP-LIFT (60 K, reused).
`unit_test/gemini_model/heatpump/test_closed_form.py` checks model = register = verifier.

---

# RC-04 Heat pump (including high-temperature heat pump, HTHP)

**Feeds:** GM-03 (robust closed-form path for GEMINI `HeatpumpBasic`), dispatch, ECO.
**Verification status:** calibration points from Guidebook Ch13 text; HTHP limits at 100 °C are NOT covered by the 1989 book — a modern manufacturer datasheet is required (external data) — `MISSING INPUT` until found.

## Equations
1. Carnot COP (heating): `COP_C = T_cond / (T_cond − T_evap)` (K).
2. Lorenz COP (gliding temperatures, preferred): `T̄_sink = (T_out − T_in)/ln(T_out/T_in)` (log-mean, K), same for source; `COP_L = T̄_sink / (T̄_sink − T̄_source)`; `COP = η_L · COP_L`.
3. Energy balance: `Q_h = Q_src · COP/(COP − 1)`; `P_el = Q_h / COP`.
4. Approach temperatures: evaporator and condenser approach 3–5 K [A] applied before computing COP if using refrigerant temperatures.

## Parameter values
| Item | Value | Source |
|---|---|---|
| η (Carnot) heating, implied | ≈ 0.46 (COP_h 4.41 at 15.6 °C evap / 48.9 °C cond leaving water) | Guidebook p.258 |
| η_L default | 0.50 [A]; sensitivity 0.40–0.55 | GEMINI default 0.5 |
| Evap/cond water ΔT | 5.6 K rating; up to 11 K standard; more needs 4/8-pass | Guidebook p.250 |
| Fouling per HEX | 0.088 m²K/kW | Guidebook p.250 |
| Max condenser leaving T (1989) | 104 °C reciprocating R-114; 82 °C centrifugal | Guidebook Table 13.2 — outdated |
| HTHP at 100 °C supply | from a cited modern datasheet (lift, max T, COP map) | `MISSING` — flag; default cap COP ≤ 0.6 × Carnot and lift ≤ 60 K [A] |
| Peaking practice | heat pump alone covers 60–70 % of the time; boiler in series on return | Guidebook p.260 |
| Cost (1987 $) | centrifugal 2.9 MW ≈ $266k (~$91/kW); escalate | Guidebook Tables 13.3–13.4 |

## Design use (C2/C3)
Source = brine after HEX-1 (~58 → ≥ 46 °C); sink = district water after HEX-1 (~82 → 100 °C). Expected COP ≈ 3.5–4.5.

## Validation tests
- Reproduce Guidebook COP_h 4.41 with η = 0.456 at 15.6/48.9 °C (±1 %).
- Lorenz COP for source 60→46 °C, sink 82→100 °C (water temperatures, no approach), η_L = 0.5 ⇒ COP = 4.79 (±1 %).
- Closed-form path and GEMINI Nelder-Mead path agree within 2 % where both converge.
