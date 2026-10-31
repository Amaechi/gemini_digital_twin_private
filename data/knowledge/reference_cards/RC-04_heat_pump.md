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
