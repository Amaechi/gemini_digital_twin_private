"""Injector friction sign in ``nodal_solver`` (WP-05; RC-01 eq 12).

``friction_opposes_flow=False`` (default) must keep GEMINI's original behaviour, in which
friction *raises* the injector bottomhole pressure; ``True`` makes friction a loss for the
downward injection flow: ``P_wh = P_bh,req − ρ g H + ΔP_fric``. Hand calculations use RC-01 eq 5
(Darcy–Weisbach with Swamee–Jain) independently of GEMINI.
"""

import math

import numpy as np
import pytest

from gemini_model.reservoir.inflow_performance import IPR
from gemini_model.well.pressure_drop import DPDT
from gemini_simulator.nodal_solver import (
    solve_injection_flow_rate,
    solve_injection_wellhead_pressure,
)
from gemini_simulator.well_components import InjectorWell
from gemini_simulator.well_flow import solve_injector_wellhead_pressure_bar

G = 9.81
DEPTH_M = 2500.0
DIAMETER_M = 0.2
ROUGHNESS_M = 0.01
RESERVOIR_BAR = 260.0
II_M3H_PER_BAR = 10.0
FLOW_M3H = 300.0
WATER = {"rho": 1000.0, "mu": 1.0e-3}  # GEMINI PVTConstantSTP defaults


def _friction_bar(flow_m3h, rho, mu, diameter_m=DIAMETER_M, roughness_m=ROUGHNESS_M):
    """RC-01 eq 5: Darcy–Weisbach friction with Swamee–Jain, by hand."""
    area = math.pi / 4.0 * diameter_m**2
    velocity = flow_m3h / 3600.0 / area
    reynolds = rho * velocity * diameter_m / mu
    f = 0.25 / math.log10(roughness_m / (3.7 * diameter_m) + 5.74 / reynolds**0.9) ** 2
    return f * DEPTH_M / diameter_m * rho * velocity**2 / 2.0 / 1e5


def _config(**extra):
    return {
        "reservoir_pressure_bar": RESERVOIR_BAR,
        "productivity_index_m3h_per_bar": II_M3H_PER_BAR,
        "welltrajectory": [{"angle": 90.0, "depth": DEPTH_M, "diameter": DIAMETER_M}],
        "roughness_m": ROUGHNESS_M,
        **extra,
    }


def _dpdt(pvt_gas_mass_fraction=0.0):
    well = DPDT()
    parameters = {
        "diameter": np.array([DIAMETER_M]),
        "length": np.array([DEPTH_M]),
        "angle": np.array([np.pi / 2]),
        "roughness": np.array([ROUGHNESS_M]),
        "friction_correlation": "darcy_weisbach",
        "friction_correlation_2p": "BeggsBrill",
        "correction_factors": [1, 0],
        "pvt": {"gas_mass_fraction": pvt_gas_mass_fraction},
    }
    well.update_parameters(parameters)
    return well


def _ipr():
    ipr = IPR()
    ipr.update_parameters(
        {
            "type": "injection_reservoir",
            "reservoir_pressure": RESERVOIR_BAR * 1e5,
            "injectivity_index": II_M3H_PER_BAR / 3.6e8,
        }
    )
    return ipr


def _expected_whp(sign):
    friction = _friction_bar(FLOW_M3H, **WATER)
    bottomhole = RESERVOIR_BAR + FLOW_M3H / II_M3H_PER_BAR
    return bottomhole - WATER["rho"] * G * DEPTH_M / 1e5 + sign * friction


def test_default_keeps_gemini_sign():
    """Without the flag friction lowers the required WHP (original GEMINI behaviour)."""
    whp = solve_injector_wellhead_pressure_bar(_config(), FLOW_M3H, 20.0)
    assert whp == pytest.approx(_expected_whp(-1.0), abs=1e-6)
    assert whp == pytest.approx(13.24, abs=0.01)  # value quoted in PROGRESS.md (WP-01 review)


def test_flag_makes_friction_a_loss_rc01_eq12():
    """``friction_opposes_flow`` gives RC-01 eq 12: P_wh = P_bh − ρgH + ΔP_fric."""
    whp = solve_injector_wellhead_pressure_bar(_config(friction_opposes_flow=True), FLOW_M3H, 20.0)
    assert whp == pytest.approx(_expected_whp(+1.0), abs=1e-6)
    assert whp == pytest.approx(76.26, abs=0.01)
    legacy = solve_injector_wellhead_pressure_bar(_config(), FLOW_M3H, 20.0)
    assert whp - legacy == pytest.approx(2.0 * _friction_bar(FLOW_M3H, **WATER), rel=1e-9)


def test_false_flag_equals_absent_key():
    """An explicit ``false`` key is the default behaviour."""
    explicit = solve_injector_wellhead_pressure_bar(
        _config(friction_opposes_flow=False), FLOW_M3H, 20.0
    )
    assert explicit == solve_injector_wellhead_pressure_bar(_config(), FLOW_M3H, 20.0)


def test_flag_must_be_boolean():
    """A string such as 'false' is refused instead of being read as true."""
    with pytest.raises(ValueError, match="true or false"):
        solve_injector_wellhead_pressure_bar(_config(friction_opposes_flow="false"), FLOW_M3H, 20.0)


def test_nodal_wellhead_pressure_solver_flag():
    """The Pa-level solver honours the flag (default unchanged)."""
    legacy = solve_injection_wellhead_pressure(_ipr(), _dpdt(), FLOW_M3H / 3600.0, 293.15)
    fixed = solve_injection_wellhead_pressure(
        _ipr(), _dpdt(), FLOW_M3H / 3600.0, 293.15, friction_opposes_flow=True
    )
    assert legacy / 1e5 == pytest.approx(_expected_whp(-1.0), abs=1e-6)
    assert fixed / 1e5 == pytest.approx(_expected_whp(+1.0), abs=1e-6)


def test_injection_flow_rate_balances_with_friction_as_loss():
    """Solved injection flow satisfies P_wh + ρgH − ΔP_fric(Q) = P_res + Q/II."""
    wellhead_bar = 40.0
    flow_m3s = solve_injection_flow_rate(
        _ipr(),
        _dpdt(),
        wellhead_bar * 1e5,
        293.15,
        flow_bracket=(1e-6, 0.2),
        friction_opposes_flow=True,
    )
    flow_m3h = flow_m3s * 3600.0
    well_side = wellhead_bar + WATER["rho"] * G * DEPTH_M / 1e5 - _friction_bar(flow_m3h, **WATER)
    reservoir_side = RESERVOIR_BAR + flow_m3h / II_M3H_PER_BAR
    assert well_side == pytest.approx(reservoir_side, abs=1e-6)
    frictionless_m3h = (wellhead_bar + WATER["rho"] * G * DEPTH_M / 1e5 - RESERVOIR_BAR) * (
        II_M3H_PER_BAR
    )
    assert flow_m3h < frictionless_m3h  # friction now resists injection


def test_injector_well_component_uses_flag():
    """``InjectorWell`` JSON parameters reach the corrected solve."""
    component = InjectorWell()
    component.update_parameters(_config(friction_opposes_flow=True))
    component.calculate_output({"flow_rate": FLOW_M3H, "wellhead_temperature": 20.0})
    assert component.get_output()["wellhead_pressure"] == pytest.approx(
        _expected_whp(+1.0), abs=1e-6
    )


def test_flag_rejects_two_phase():
    """Downward Beggs–Brill flow is not supported: the flag refuses a gas mass fraction."""
    with pytest.raises(ValueError, match="single-phase"):
        solve_injection_wellhead_pressure(
            _ipr(), _dpdt(0.1), FLOW_M3H / 3600.0, 293.15, friction_opposes_flow=True
        )


def test_negative_flow_walked_down_relaxes_towards_ambient():
    """DPDT convention used by the flag: a downward (negative) flow heats/cools toward ambient."""
    well = _dpdt()
    well.calculate_output(
        {
            "pressure": 30e5,
            "temperature": 320.0,
            "temperature_ambient": 350.0,
            "flowrate": -FLOW_M3H / 3600.0,
            "direction": "down",
        },
        None,
    )
    temperature = well.get_output()["temperature_output"]
    assert 320.0 < temperature < 350.0
    assert well.get_output()["pressuredrop_fric_output"] < 0.0
