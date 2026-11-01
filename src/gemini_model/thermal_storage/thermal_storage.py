"""Thermal energy storage (heat or cold) as a GEMINI DynamicModel (GM-09).

Source: RC-09 "Thermal storage" (``data/knowledge/reference_cards/RC-09_district_network_
storage.md``, copied in ``REFERENCE.md``); spec §7.2 GM-09 and candidate C6 (seasonal HT-ATES).

- State update (RC-09): ``E_{t+1} = E_t + η_c P_c Δt - P_d Δt / η_d - losses``; here
  ``losses = λ (Δt / 1 day) E_t`` (standing loss, default λ = 0).
- Recovery (energy delivered / energy charged over a cycle) = ``η_c η_d``. Defaults put all of it
  on discharge (``η_c = 1``, ``η_d`` = recovery) so a full cycle returns exactly the literature
  recovery: HT-ATES 0.63 after 10 years (TU Delft; register STO-REC-HT) or cold storage 0.96
  (lower end of 0.96-0.99, RTES citing Pepin 2021; register STO-REC-COLD).
- Charge and discharge are limited by the power limits and by the stored energy / free capacity;
  the curtailed charge and unmet discharge are reported. Charge and discharge in the same step are
  refused (a dispatch error).

Daily storage adds little at daily resolution (RC-09); seasonal ATES needs an annual heat/cold
balance (regulators): the cumulative charged and discharged energy are outputs for that check.
The stored energy counts heat before the recovery loss: at most ``η_d`` × capacity can be
delivered, so size ``capacity_mwh`` as injected energy. Outlet temperature is not modelled (an
HT-ATES returns below the 100 °C supply: a booster or temperature check is needed).
Units: powers in W, energies in MWh at the boundary, J internally; time step in s (default 1 day).
"""

import math

from gemini_model.model_abstract import DynamicModel

J_PER_MWH = 3.6e9
SECONDS_PER_DAY = 86400.0
RECOVERY_BY_TYPE = {"ht_ates": 0.63, "cold": 0.96}
"""Cycle recovery defaults: HT-ATES 0.63 [L TU Delft] (STO-REC-HT); cold 0.96 [L RTES, lower end
of 0.96-0.99] (STO-REC-COLD)."""


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _non_negative(value, name):
    value = _finite(value, name)
    if value < 0.0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _fraction(value, name, allow_zero=False):
    value = _finite(value, name)
    if not (0.0 <= value <= 1.0) or (value == 0.0 and not allow_zero):
        raise ValueError(f"{name} must be in {'[0' if allow_zero else '(0'}, 1], got {value}")
    return value


def _limit(value, name):
    return math.inf if value is None else _non_negative(value, name)


class ThermalStorage(DynamicModel):
    """Thermal storage with state of charge, power limits and cycle recovery (RC-09)."""

    def __init__(self):
        """Model initialization (capacity must be set; default type HT-ATES)."""
        self.parameters = {
            "capacity_mwh": None,
            "storage_type": "ht_ates",  # or "cold"; sets the default discharge efficiency
            "initial_soc": 0.0,
            "charge_efficiency": 1.0,
            "discharge_efficiency": None,  # None -> RECOVERY_BY_TYPE[storage_type]
            "standing_loss_per_day": 0.0,  # fraction of stored energy per day
            "max_charge_power_w": None,  # None = no limit
            "max_discharge_power_w": None,  # None = no limit
            "time_step_s": SECONDS_PER_DAY,
        }
        self.output = {}

    def update_parameters(self, parameters):
        """Update model parameters.

        Parameters
        ----------
        parameters: dict
            Parameters dict as defined by the model.
        """
        for key, value in parameters.items():
            self.parameters[key] = value

    def capacity_j(self):
        """Return the capacity in J."""
        capacity = self.parameters["capacity_mwh"]
        if capacity is None:
            raise ValueError("capacity_mwh must be set")
        capacity = _finite(capacity, "capacity_mwh")
        if capacity <= 0.0:
            raise ValueError(f"capacity_mwh must be > 0, got {capacity}")
        return capacity * J_PER_MWH

    def efficiencies(self):
        """Return ``(η_c, η_d)``; η_d defaults to the storage type's cycle recovery."""
        p = self.parameters
        eta_c = _fraction(p["charge_efficiency"], "charge_efficiency")
        eta_d = p["discharge_efficiency"]
        if eta_d is None:
            storage_type = p["storage_type"]
            if storage_type not in RECOVERY_BY_TYPE:
                raise ValueError(
                    f"storage_type must be one of {sorted(RECOVERY_BY_TYPE)}, got {storage_type!r}"
                )
            eta_d = RECOVERY_BY_TYPE[storage_type]
        return eta_c, _fraction(eta_d, "discharge_efficiency")

    def initialize_state(self, x=None):
        """Return the initial state; ``x`` may carry ``energy_mwh`` (else ``initial_soc``).

        The GEMINI solver passes the component's inputs here, so other keys are ignored.
        """
        capacity = self.capacity_j()
        if isinstance(x, dict) and x.get("energy_mwh") is not None:
            energy = _non_negative(x["energy_mwh"], "energy_mwh") * J_PER_MWH
            if energy > capacity:
                raise ValueError("initial energy_mwh exceeds capacity_mwh")
        else:
            energy = _fraction(self.parameters["initial_soc"], "initial_soc", True) * capacity
        return {"energy_j": energy, "charged_j": 0.0, "discharged_j": 0.0}

    def _step(self, u, x):
        """Return ``(output, next_state)`` for one time step from state ``x``."""
        p = self.parameters
        state = x if isinstance(x, dict) and "energy_j" in x else self.initialize_state(None)
        capacity = self.capacity_j()
        eta_c, eta_d = self.efficiencies()
        dt = _finite(u.get("time_step_s", p["time_step_s"]), "time_step_s")
        if dt <= 0.0:
            raise ValueError(f"time_step_s must be > 0, got {dt}")
        charge_req = _non_negative(u.get("charge_power_w", 0.0) or 0.0, "charge_power_w")
        discharge_req = _non_negative(u.get("discharge_power_w", 0.0) or 0.0, "discharge_power_w")
        if charge_req > 0.0 and discharge_req > 0.0:
            raise ValueError("charge_power_w and discharge_power_w cannot both be > 0 in one step")

        energy_0 = state["energy_j"]
        loss_fraction = _non_negative(p["standing_loss_per_day"], "standing_loss_per_day") * (
            dt / SECONDS_PER_DAY
        )
        if loss_fraction > 1.0:
            raise ValueError("standing_loss_per_day × time step exceeds the stored energy")
        standing_loss = loss_fraction * energy_0
        available = energy_0 - standing_loss

        charge = min(
            charge_req,
            _limit(p["max_charge_power_w"], "max_charge_power_w"),
            max(capacity - available, 0.0) / (eta_c * dt),
        )
        discharge = min(
            discharge_req,
            _limit(p["max_discharge_power_w"], "max_discharge_power_w"),
            available * eta_d / dt,
        )
        energy_1 = available + eta_c * charge * dt - discharge * dt / eta_d
        energy_1 = min(max(energy_1, 0.0), capacity)  # float noise only
        conversion_loss = (1.0 - eta_c) * charge * dt + discharge * dt * (1.0 / eta_d - 1.0)
        losses_j = standing_loss + conversion_loss
        next_state = {
            "energy_j": energy_1,
            "charged_j": state["charged_j"] + charge * dt,
            "discharged_j": state["discharged_j"] + discharge * dt,
        }
        output = {
            "charge_power_w": charge,
            "discharge_power_w": discharge,
            "curtailed_charge_w": charge_req - charge,
            "unmet_discharge_w": discharge_req - discharge,
            "energy_start_mwh": energy_0 / J_PER_MWH,
            "energy_mwh": energy_1 / J_PER_MWH,
            "soc": energy_1 / capacity,
            "losses_w": losses_j / dt,
            "energy_balance_residual_j": energy_1 - energy_0 - (charge - discharge) * dt + losses_j,
            "charged_total_mwh": next_state["charged_j"] / J_PER_MWH,
            "discharged_total_mwh": next_state["discharged_j"] / J_PER_MWH,
            "recovery_efficiency": eta_c * eta_d,
            "storage_type": p["storage_type"],
            "time_step_s": dt,
            "net_heat_to_plant_w": discharge - charge,
            "power_el": 0.0,
            "power_th": discharge,
            "emission": 0.0,
        }
        return output, next_state

    def calculate_output(self, u, x=None):
        """Calculate the step: actual charge/discharge power, end-of-step energy and losses.

        Inputs ``u``: ``charge_power_w`` or ``discharge_power_w`` (requested, W, ≥ 0; not both),
        optional ``time_step_s``. ``x`` is the state from :meth:`initialize_state` /
        :meth:`update_state` (None → initial state). ``power_th`` is the heat (or cold)
        delivered by discharging; charging draws ``charge_power_w`` from the plant. Do not add
        ``power_th`` to the plant's production totals (the stored heat was produced upstream and
        would be counted twice): use ``net_heat_to_plant_w`` = discharge - charge.
        """
        self.output, _ = self._step(u, x)

    def update_state(self, u, x):
        """Return the state at the end of the step (same computation as calculate_output)."""
        return self._step(u, x)[1]
