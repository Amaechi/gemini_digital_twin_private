# REFERENCE — well pressure/temperature model DPDT (GM-01, GM-11)

Model: `pressure_drop.DPDT`. Source card: `data/knowledge/reference_cards/RC-01_reservoir_wells_pumps.md`.

## Equations (RC-01)
- Single-phase segment loss (RC-01 eq. 5, DiPippo eq. 4.10–4.11; the card marks its DiPippo
  equation forms `VERIFY` against the book — GEMINI's code is authoritative for behaviour):
  `ΔP = ρ g L sin θ + f (L/D) ρ v²/2`, Swamee–Jain friction factor
  `f = 0.25 / [log10(ε/(3.7D) + 5.74/Re^0.9)]²` (`correlation/darcyweisbach.py`).
- Two-phase segments (gas mass fraction > 0) use Beggs–Brill with superficial
  velocities
  `u_s,l = ṁ_l / (ρ_l A)`, `u_s,g = ṁ_g / (ρ_g A)`, where
  `ṁ = Q ρ_mix`, `ρ_mix = 1 / (x/ρ_g + (1 − x)/ρ_l)`, `ṁ_g = x ṁ`, `ṁ_l = (1 − x) ṁ`.

## Changes in WP-01
- GM-01: `update_parameters({"pvt": {...}})` builds `self.PVT` from the block
  (see `src/gemini_model/fluid/REFERENCE.md`). Without it, the registry and
  `well_flow` still set GEMINI's default water PVT.
- GM-11: `u_s,g` used `x / (ρ_g A)` (gas mass *fraction*, kg/kg) instead of
  `ṁ_g / (ρ_g A)`, so it did not scale with flow and had the wrong units. Fixed
  to the gas mass flow. Single-phase cases (default `GMF = 0`) are unaffected.

## Known GEMINI behaviour (not changed here)
- Overall heat-transfer coefficient is hard-coded at U = 10 W/m²K.
- `direction="down"` adds friction to the pressure while walking down. That is
  right for a producer (walking against upward flow), but the injector solve
  (`nodal_solver.solve_injection_wellhead_pressure`) uses it for downward flow,
  where friction should be a loss (RC-01 eq. 12). Flagged for WP-05.

## Validation (unit tests)
- RC-01: hydrostatic 2500 m, 1030 kg/m³ → 252.6 ± 0.1 bar; brine − water 7.36 bar.
- GM-11: captured `u_s,g` equals `x ṁ / (ρ_g A)` and doubles with flow
  (`unit_test/gemini_model/well/test_pressure_drop_pvt.py`).
