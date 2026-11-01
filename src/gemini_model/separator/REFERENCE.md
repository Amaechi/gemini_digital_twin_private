# REFERENCE — separator associated-gas composition (GM-12)

Model: `separator.Separator` plus `separator.associated_gas_properties`.
Source card: `data/knowledge/reference_cards/RC-08_gas_boiler_chp_emissions.md` ("Associated gas", [D]).

## Inputs
GLR (`gas_water_ratio`, Nm³ gas per m³ brine) = 0.8 [P, DB-09]; composition
80 mol % CH₄ / 20 mol % CO₂ [P, DB-09] (`ch4_fraction`, `co2_fraction`; must add up to 1).
Normal conditions: 0 °C, 1.01325 bar; molar volume 22.414 Nm³/kmol.

## Equations
- Species per Nm³ gas: `m_i = x_i · M_i / 22.414` (kg/Nm³), M_CH4 = 16.04, M_CO2 = 44.01 kg/kmol.
- Density `ρ_gas = Σ m_i` (= 0.965 kg/Nm³ for 80/20).
- LHV `= x_CH4 · 35.8 MJ/Nm³` (CH₄ LHV) → 28.64 MJ/Nm³ = 29.7 MJ/kg.
- Gas flow `V_g = GLR · Q_liquid` (Nm³/s); mass flow `ṁ_g = V_g ρ_gas`.
- Fuel power (LHV) `P_fuel = ṁ_g · LHV` (W) → 1.91 MW at 300 m³/h.
- Inherent CO₂ `= V_g · m_CO2` (kg/s); CO₂ if burned `= V_g (x_CH4 + x_CO2) M_CO2 / 22.414`
  → 1.964 kg/Nm³ = 68.6 kg/GJ_LHV.

Explicit `gas_density` (kg/Nm³) and `fuel_lhv_j_kg` parameters override the derived values.
Without a composition the species/CO₂ outputs are 0 and existing outputs are unchanged;
`fuel_power_w` then needs both `gas_density` and `fuel_lhv_j_kg` (0 without an LHV; an
LHV with gas flow but no density raises, instead of reporting 0 W).
The separator's own `emission` output stays 0: CO₂ is counted where the gas is burned
(or vented; venting CH₄ is not allowed [A], RC-08).

## Validation (unit tests, RC-08)
Density 0.965 ± 0.002 kg/Nm³; LHV 29.7 ± 0.2 MJ/kg; 68.6 ± 0.5 kg/GJ;
fuel power at 300 m³/h = 1.91 ± 0.02 MW (`unit_test/gemini_model/separator/test_separator_gas.py`).
