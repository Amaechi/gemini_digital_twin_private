# REFERENCE — thermal storage (GM-09)

Model: `thermal_storage.ThermalStorage` (registry type `thermal_storage`, a GEMINI
`DynamicModel`: the solver calls `initialize_state`, then per time step `calculate_output` and
`update_state`).
Source card: `data/knowledge/reference_cards/RC-09_district_network_storage.md`, section
"Thermal storage" (copied below). Spec §7.2 GM-09, candidate C6 (seasonal HT-ATES).

## Implementation notes (WP-03)
- State `{"energy_j", "charged_j", "discharged_j"}`; parameters `capacity_mwh` (required),
  `initial_soc`, `charge_efficiency` η_c (1.0), `discharge_efficiency` η_d (None → recovery of
  `storage_type`), `standing_loss_per_day` λ (0), `max_charge_power_w` /
  `max_discharge_power_w` (None = no limit), `time_step_s` (86400).
- `E_{t+1} = E_t − λ (Δt/1 d) E_t + η_c P_c Δt − P_d Δt / η_d` (RC-09). Charge and discharge are
  limited by the power limits and by the free capacity / stored energy; `curtailed_charge_w`
  and `unmet_discharge_w` report the rest. Both in one step → ValueError.
- **Recovery mapping [A]:** cycle recovery = η_c η_d; defaults put it all on discharge
  (η_c = 1, η_d = recovery) so one full cycle returns exactly the literature value whatever the
  holding time: `ht_ates` 0.63 (STO-REC-HT, TU Delft), `cold` 0.96 (STO-REC-COLD, lower end of
  RTES 0.96–0.99). Do not add a standing loss on top (double count).
- Outputs: `charge_power_w`, `discharge_power_w`, `energy_mwh`, `soc`, `losses_w`,
  `energy_balance_residual_j` (first law, ≈ 0), cumulative `charged_total_mwh` /
  `discharged_total_mwh` (for the ATES annual-balance check), `power_th` = discharge delivered,
  `power_el` 0 (circulation pumps not modelled), `emission` 0, `net_heat_to_plant_w` =
  discharge − charge. **Do not sum `power_th` into plant production** (the stored heat was
  produced upstream; it would be counted twice) — use `net_heat_to_plant_w`.
- Energy is counted before the recovery loss: at most η_d × capacity can be delivered, so size
  `capacity_mwh` as injected energy. The recovery is independent of holding time (sound for a
  yearly cycle).
- **Not modelled:** outlet temperature. An HT-ATES returns below the 100 °C supply, so candidate
  C6 needs a booster (heat pump / e-boiler) or a temperature check downstream.

## Validation tests
`unit_test/gemini_model/thermal_storage/test_thermal_storage.py`: energy balance closes exactly
over a cycle with η = 1 (card); HT-ATES returns 63 %; limits; standing loss; runs over time in a
GEMINI project.

---

## Thermal storage (GM-09, optional)
- State: `E_{t+1} = E_t + η_c P_c Δt − P_d Δt / η_d − losses`.
- High-temperature ATES recovery ≈ 63 % after 10 years (TU Delft, papers/); cold RTES 96–99 % (RTES paper citing Pepin 2021).
- Daily storage adds little at daily resolution; ~12 h duration captures most value for cooling peaks (RTES paper) — only relevant with sub-daily data.
- Seasonal ATES requires annual heat/cold balance (Stockholm, Munich regulators).

## Validation tests
- Pipe ΔT example reproduces 0.55 K/km ±10 %.
- Storage energy balance closes exactly over a cycle with η = 1.
