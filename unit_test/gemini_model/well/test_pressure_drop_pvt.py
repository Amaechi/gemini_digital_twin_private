"""DPDT tests for the GM-01 "pvt" parameter and the GM-11 us_g fix.

Reference values: RC-01 validation tests (hydrostatic 2500 m, 1030 kg/m3 ->
252.6 bar +/-0.1 bar; brine vs water over 2500 m -> 7.36 bar).
"""

import numpy as np
import pytest

from gemini_model.fluid.pvt_water_stp import PVTConstantSTP
from gemini_model.well.correlation.beggsbrill import BeggsBrill
from gemini_model.well.pressure_drop import DPDT

DEPTH_M = 2500.0
DIAMETER_M = 0.2
TINY_FLOW_M3S = 1e-6  # DPDT returns dp = 0 at exactly zero flow; friction is negligible here


def _vertical_well(pvt=None, n_segments=5):
    """Return a vertical DPDT well of DEPTH_M, optionally with a 'pvt' block."""
    well = DPDT()
    well.PVT = PVTConstantSTP()  # what the registry/well_flow do by default
    parameters = {
        "diameter": np.full(n_segments, DIAMETER_M),
        "length": np.full(n_segments, DEPTH_M / n_segments),
        "angle": np.full(n_segments, np.pi / 2),
        "roughness": np.full(n_segments, 4.57e-5),
        "friction_correlation": "darcy_weisbach",
        "friction_correlation_2p": "BeggsBrill",
        "correction_factors": [1, 0],
    }
    if pvt is not None:
        parameters["pvt"] = pvt
    well.update_parameters(parameters)
    return well


def _gravity_head_bar(well, flow_m3s=TINY_FLOW_M3S):
    well.calculate_output(
        {
            "direction": "down",
            "pressure": 1e5,
            "temperature": 300.0,
            "flowrate": flow_m3s,
            "temperature_ambient": 300.0,
        },
        None,
    )
    return well.get_output()["pressuredrop_grav_output"] / 1e5


def test_brine_hydrostatic_head_matches_rc01():
    """1030 kg/m3 over 2500 m gives 252.6 bar (RC-01 eq. 2, g = 9.81)."""
    head_bar = _gravity_head_bar(_vertical_well({"liquid_density_kg_m3": 1030.0}))
    assert head_bar == pytest.approx(252.6, abs=0.1)


def test_brine_raises_head_by_7_4_bar_over_water():
    """Brine PVT adds ~7.36 bar of head over 2.5 km compared with the water default."""
    water_bar = _gravity_head_bar(_vertical_well())
    brine_bar = _gravity_head_bar(_vertical_well({"liquid_density_kg_m3": 1030.0}))
    assert water_bar == pytest.approx(245.25, abs=0.01)
    assert brine_bar - water_bar == pytest.approx(7.36, abs=0.02)


def test_pvt_parameter_works_without_a_preset_pvt_object():
    """update_parameters({'pvt': ...}) creates the PVT object if none was set."""
    well = DPDT()
    assert well.PVT is None
    well.update_parameters({"pvt": {"RHOL": 1030.0, "VISL": 4e-4}})
    assert well.PVT.parameters["RHOL"] == 1030.0
    assert well.PVT.parameters["VISL"] == 4e-4
    assert well.PVT.parameters["CPL"] == PVTConstantSTP().parameters["CPL"]


def test_bad_pvt_block_is_rejected():
    """A misspelt key must raise instead of being ignored."""
    with pytest.raises(ValueError):
        DPDT().update_parameters({"pvt": {"rho": 1030.0}})


def test_superficial_gas_velocity_uses_gas_mass_flow(monkeypatch):
    """GM-11: us_g = m_g / (rho_g A), so it scales with flow (it did not before)."""
    captured = []

    def fake_beggsbrill(us_g, us_l, rho_g, rho_l, *args):
        captured.append((us_g, us_l))
        return 0.0, 0.0

    monkeypatch.setattr(BeggsBrill, "calculate_dp", staticmethod(fake_beggsbrill))

    gmf, rho_g, rho_l = 0.1, 1.976, 1000.0
    well = _vertical_well({"GMF": gmf}, n_segments=1)
    area_m2 = np.pi / 4 * DIAMETER_M**2
    rho_mix = 1.0 / (gmf / rho_g + (1.0 - gmf) / rho_l)

    for flow_m3s in (0.01, 0.02):
        captured.clear()
        _gravity_head_bar(well, flow_m3s=flow_m3s)
        us_g, us_l = captured[0]
        mass_flow_kg_s = flow_m3s * rho_mix
        assert us_g == pytest.approx(gmf * mass_flow_kg_s / rho_g / area_m2, rel=1e-12)
        assert us_l == pytest.approx((1.0 - gmf) * mass_flow_kg_s / rho_l / area_m2, rel=1e-12)


def test_single_phase_default_does_not_use_beggsbrill(monkeypatch):
    """With the default GMF = 0 the Darcy-Weisbach path is used, as before."""

    def fail(*args):
        raise AssertionError("BeggsBrill must not be called for single-phase flow")

    monkeypatch.setattr(BeggsBrill, "calculate_dp", staticmethod(fail))
    assert _gravity_head_bar(_vertical_well(), flow_m3s=0.05) > 0.0
