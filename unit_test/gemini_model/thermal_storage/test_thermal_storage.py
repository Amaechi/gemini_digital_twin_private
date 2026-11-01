"""GM-09 (WP-03): thermal storage DynamicModel (RC-09)."""

from pathlib import Path

import pytest

from gemini_model.model_abstract import DynamicModel
from gemini_model.thermal_storage.thermal_storage import (
    J_PER_MWH,
    RECOVERY_BY_TYPE,
    ThermalStorage,
)

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "config" / "assumptions.yaml"
DAY_S = 86400.0


def storage(**parameters):
    """Return a 1000 MWh store with optional parameter overrides."""
    model = ThermalStorage()
    model.update_parameters({"capacity_mwh": 1000.0, **parameters})
    return model


def step(model, state, **inputs):
    """One GEMINI step: outputs from the start state, then the next state."""
    model.calculate_output(inputs, state)
    return model.get_output(), model.update_state(inputs, state)


def test_is_a_dynamic_model():
    """GM-09 asks for a DynamicModel (SOC is its state)."""
    assert isinstance(ThermalStorage(), DynamicModel)


def test_energy_balance_closes_over_a_cycle_with_unit_efficiency():
    """RC-09 validation: charge then discharge to empty with η = 1 returns exactly what went in."""
    model = storage(discharge_efficiency=1.0)
    state = model.initialize_state()
    charged = delivered = 0.0
    for power in (5.0e6, 10.0e6, 2.5e6):
        out, state = step(model, state, charge_power_w=power)
        charged += out["charge_power_w"] * DAY_S
        assert out["energy_balance_residual_j"] == pytest.approx(0.0, abs=1e-3)
    while state["energy_j"] > 0.0:
        out, state = step(model, state, discharge_power_w=8.0e6)
        delivered += out["discharge_power_w"] * DAY_S
        assert out["energy_balance_residual_j"] == pytest.approx(0.0, abs=1e-3)
    assert delivered == pytest.approx(charged, rel=1e-12)
    assert state["energy_j"] == 0.0
    assert out["charged_total_mwh"] == pytest.approx(out["discharged_total_mwh"], rel=1e-12)


def test_ht_ates_cycle_recovers_63_percent():
    """Default HT-ATES: one full cycle delivers 0.63 × the charged heat (STO-REC-HT)."""
    model = storage()
    state = model.initialize_state()
    out, state = step(model, state, charge_power_w=10.0e6)  # 240 MWh in one day
    assert out["energy_mwh"] == pytest.approx(240.0)
    out, state = step(model, state, discharge_power_w=1.0e9)  # ask for more than is stored
    assert out["discharge_power_w"] * DAY_S / J_PER_MWH == pytest.approx(0.63 * 240.0)
    assert out["unmet_discharge_w"] == pytest.approx(1.0e9 - out["discharge_power_w"])
    assert out["energy_mwh"] == pytest.approx(0.0, abs=1e-9)
    assert out["losses_w"] * DAY_S / J_PER_MWH == pytest.approx(0.37 * 240.0)
    assert out["recovery_efficiency"] == 0.63
    assert out["power_th"] == out["discharge_power_w"] and out["power_el"] == 0.0
    assert out["net_heat_to_plant_w"] == out["discharge_power_w"]


def test_cold_storage_recovery():
    """Cold storage default recovery 0.96 (STO-REC-COLD)."""
    model = storage(storage_type="cold")
    assert model.efficiencies() == (1.0, 0.96)


def test_capacity_and_power_limits():
    """Charge is curtailed at full capacity and by the power limit; SOC stays in [0, 1]."""
    model = storage(capacity_mwh=200.0, max_charge_power_w=3.0e6, initial_soc=0.5)
    state = model.initialize_state()
    out, state = step(model, state, charge_power_w=5.0e6)  # power limit: 3 MW × 24 h = 72 MWh
    assert out["charge_power_w"] == 3.0e6 and out["curtailed_charge_w"] == 2.0e6
    assert out["energy_mwh"] == pytest.approx(172.0)
    out, state = step(model, state, charge_power_w=3.0e6)  # 28 MWh free: capacity binds
    assert out["charge_power_w"] == pytest.approx(28.0 * J_PER_MWH / DAY_S)
    assert out["soc"] == pytest.approx(1.0) and out["energy_mwh"] == pytest.approx(200.0)


def test_standing_loss():
    """λ = 2 %/day removes 2 % of the start energy per day when idle."""
    model = storage(standing_loss_per_day=0.02, initial_soc=0.5)
    out, _ = step(model, model.initialize_state())
    assert out["energy_mwh"] == pytest.approx(490.0)
    assert out["losses_w"] * DAY_S / J_PER_MWH == pytest.approx(10.0)
    assert out["energy_balance_residual_j"] == pytest.approx(0.0, abs=1e-3)


def test_input_validation():
    """Charge and discharge in one step, missing capacity and bad types are refused."""
    model = storage()
    with pytest.raises(ValueError, match="both"):
        model.calculate_output({"charge_power_w": 1.0, "discharge_power_w": 1.0})
    with pytest.raises(ValueError, match="capacity_mwh"):
        ThermalStorage().calculate_output({})
    with pytest.raises(ValueError, match="storage_type"):
        storage(storage_type="ice").calculate_output({})
    with pytest.raises(ValueError, match="exceeds"):
        model.initialize_state({"energy_mwh": 2000.0})


def test_runs_in_a_gemini_project_over_time():
    """Registered DynamicModel: the solver carries the state across time steps."""
    from gemini_simulator.project_loader import build_project

    project = {
        "components": {
            "store": {
                "type": "thermal_storage",
                "parameters": {"capacity_mwh": 100.0, "discharge_efficiency": 1.0},
            }
        },
        "boundary_inputs": [{"component": "store", "key": "charge_power_w", "value": 1.0e6}],
        "connections": [],
    }
    solver = build_project(project)
    solver.run([0, 1, 2])
    energies = [solver.get_output("store", "energy_mwh", t) for t in range(3)]
    assert energies == pytest.approx([24.0, 48.0, 72.0])


def test_defaults_match_the_assumptions_register():
    """Recovery defaults are STO-REC-HT and STO-REC-COLD."""
    yaml = pytest.importorskip("yaml")
    with open(ASSUMPTIONS, encoding="utf-8") as file:
        register = {item["id"]: item for item in yaml.safe_load(file)["assumptions"]}
    assert register["STO-REC-HT"]["value"] == RECOVERY_BY_TYPE["ht_ates"]
    assert register["STO-REC-COLD"]["value"] == RECOVERY_BY_TYPE["cold"]
    parameters = ThermalStorage().parameters
    assert parameters["storage_type"] == "ht_ates" and parameters["charge_efficiency"] == 1.0
    assert parameters["standing_loss_per_day"] == 0.0
