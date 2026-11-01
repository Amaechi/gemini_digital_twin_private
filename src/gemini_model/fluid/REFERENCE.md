# REFERENCE — fluid PVT (GM-01)

Model: `pvt_water_stp.PVTConstantSTP` (constant properties) configured through
`pvt_config.build_pvt(pvt_block)`. Source card: `data/knowledge/reference_cards/RC-01_reservoir_wells_pumps.md`.

## What is configurable
A `"pvt"` block in the project-JSON parameters of `well`, `producer_well` and
`injector_well` overrides `PVTConstantSTP` values. Keys: native (`RHOL`, `VISL`,
`CPL`, `RHOG`, `VISG`, `CPG`, `GMF`, `TCL`, `TCG`, `SIGMA`, ...) or unit-suffixed
aliases (`liquid_density_kg_m3`, `liquid_viscosity_pa_s`, `liquid_specific_heat_j_kgk`,
`liquid_thermal_conductivity_w_mk`, `gas_density_kg_m3`, `gas_viscosity_pa_s`,
`gas_specific_heat_j_kgk`, `gas_thermal_conductivity_w_mk`, `gas_mass_fraction`,
`surface_tension_n_m`). Unknown keys, alias/native conflicts, non-finite values,
non-positive ρ/μ/c_p/k/σ and `GMF` outside [0, 1) raise; numpy real numbers are accepted.
No block = GEMINI default.

For `producer_well` the block's ρ_l also scales the ESP's water head/power curves by the
specific gravity SG = ρ_l / 1000 kg/m³ (`gemini_simulator.well_flow._FluidESP`): the pump
pressure rise is SG ρ_w g H and its brake power SG × the water value (RC-01 items 6–8).
Without a block, or with ρ_l left at 1000, SG = 1 and the GEMINI ESP is unchanged.

## Parameter values
| Quantity | GEMINI default (water, STP) | Design-basis brine | Tag / source |
|---|---|---|---|
| ρ_l (kg/m³) | 1000 | 1030 | [P] DB-08, RC-01 Inputs |
| μ_l (Pa·s) | 1.0e-3 | 0.40e-3 | [P] DB-08, RC-01 Inputs |
| c_p,l (J/kg·K) | 4217.4 | 4100 | [A] DB-33 (range 4000–4190), RC-01 Inputs |

The KPI helpers (`gemini_simulator.kpi`) read the same block from the producer
well; `heat_exchanger_fluid_parameters()` gives the matching `fluid_density`/`specific_heat`
parameters to set on GEMINI `HeatExchanger`, `Boiler` and `CHP` (single source of truth).
They are not copied automatically: the project builder (WP-06) must set them, and
`kpi.fluid_consistency_issues` / a `doublet_thermal_power_w` warning report a mismatch.
Without a block the KPI keeps its legacy 1050 kg/m³ / 4200 J/kg·K defaults.

## Governing relation (why it matters)
Hydrostatic head `ΔP = ρ g z`, g = 9.81 m/s² (RC-01 eq. 2).

## Validity
Constant properties: no pressure/temperature dependence (`pressure_min/max`,
`temperature_min/max` describe the intended range only).

## Validation (unit tests)
- RC-01: 1030 kg/m³ over 2500 m → 252.6 ± 0.1 bar; brine − water → 7.36 bar
  (`unit_test/gemini_model/well/test_pressure_drop_pvt.py`).
- Defaults unchanged: `build_pvt(None)` equals `PVTConstantSTP()`
  (`unit_test/gemini_model/fluid/test_pvt_config.py`).
