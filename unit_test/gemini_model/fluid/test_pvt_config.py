"""Unit tests for gemini_model.fluid.pvt_config (GM-01, RC-01)."""

import math

import numpy as np
import pytest

from gemini_model.fluid.pvt_config import (
    PVT_PARAMETER_ALIASES,
    build_pvt,
    heat_exchanger_fluid_parameters,
    liquid_properties,
    normalise_pvt_parameters,
)
from gemini_model.fluid.pvt_water_stp import PVTConstantSTP

# Design-basis brine: DB-08 (rho, mu) [P], DB-33 (c_p) [A]; RC-01 "Inputs".
BRINE_PVT = {
    "liquid_density_kg_m3": 1030.0,
    "liquid_viscosity_pa_s": 0.40e-3,
    "liquid_specific_heat_j_kgk": 4100.0,
}


def test_no_block_gives_gemini_defaults():
    """A missing or empty block must give exactly the GEMINI water PVT."""
    default = PVTConstantSTP().parameters
    assert normalise_pvt_parameters(None) == {}
    assert normalise_pvt_parameters({}) == {}
    assert build_pvt(None).parameters == default
    assert build_pvt({}).parameters == default
    assert liquid_properties(None) == (1000.0, 4217.4)


def test_aliases_map_to_native_keys():
    """Unit-suffixed aliases set the matching native PVTConstantSTP keys."""
    normalised = normalise_pvt_parameters(BRINE_PVT)
    assert normalised == {"RHOL": 1030.0, "VISL": 0.40e-3, "CPL": 4100.0}
    pvt = build_pvt(BRINE_PVT)
    rho_g, rho_l, gmf, eta_g, eta_l, cp_g, cp_l, *_ = pvt.get_pvt(250e5, 360.0)
    assert (rho_l, eta_l, cp_l) == (1030.0, 0.40e-3, 4100.0)
    # Keys the block does not set keep the defaults.
    assert (rho_g, gmf, eta_g, cp_g) == (1.976, 0, 21.29e-6, 0.819e3)


def test_every_alias_targets_a_native_key():
    """Every alias must point at a key the PVT model actually has."""
    native = set(PVTConstantSTP().parameters)
    assert set(PVT_PARAMETER_ALIASES.values()) <= native


def test_native_keys_and_comment_keys_are_accepted():
    """Native keys work; keys starting with '_' are comments and are ignored."""
    normalised = normalise_pvt_parameters({"RHOL": 1030, "_description": "brine, DB-08"})
    assert normalised == {"RHOL": 1030.0}


@pytest.mark.parametrize("value", [np.int64(1030), np.float32(1030.0), np.float64(1030.0)])
def test_numpy_real_numbers_are_accepted(value):
    """Numpy scalars (e.g. from a config generator) are real numbers and become floats."""
    normalised = normalise_pvt_parameters({"liquid_density_kg_m3": value})
    assert normalised == {"RHOL": 1030.0}
    assert type(normalised["RHOL"]) is float


def test_alias_and_native_key_may_repeat_the_same_value():
    """Giving the same quantity twice is fine if the values agree."""
    assert normalise_pvt_parameters({"RHOL": 1030.0, "liquid_density_kg_m3": 1030.0}) == {
        "RHOL": 1030.0
    }


@pytest.mark.parametrize(
    "block, error",
    [
        ({"density": 1030.0}, ValueError),  # unknown key
        ({"RHOL": 1030.0, "liquid_density_kg_m3": 1000.0}, ValueError),  # conflict
        ({"RHOL": 0.0}, ValueError),  # not positive
        ({"VISL": -1e-3}, ValueError),
        ({"SIGMA": -0.07}, ValueError),  # Beggs-Brill needs (rho_l / sigma) ** 0.25
        ({"SIGMA": 0.0}, ValueError),
        ({"RHOL": math.nan}, ValueError),  # not finite
        ({"GMF": 1.0}, ValueError),  # outside [0, 1)
        ({"RHOL": "1030"}, TypeError),  # not a number
        ({"RHOL": True}, TypeError),
        ({"RHOL": np.True_}, TypeError),
        ([("RHOL", 1030.0)], TypeError),  # not a dict
        ({1: 1030.0}, TypeError),  # key not a string
    ],
)
def test_bad_blocks_are_rejected(block, error):
    """Typos and unphysical values must fail loudly, never silently."""
    with pytest.raises(error):
        normalise_pvt_parameters(block)


def test_build_pvt_returns_independent_objects():
    """Each call returns a fresh PVT object; the defaults are not mutated."""
    brine = build_pvt(BRINE_PVT)
    water = build_pvt()
    assert brine is not water
    assert water.parameters["RHOL"] == 1000
    assert PVTConstantSTP().parameters["RHOL"] == 1000


def test_heat_exchanger_parameters_match_the_well_pvt():
    """HEX/boiler/CHP get the same liquid rho and c_p as the wells (single source)."""
    assert heat_exchanger_fluid_parameters(BRINE_PVT) == {
        "fluid_density": 1030.0,
        "specific_heat": 4100.0,
    }
    assert liquid_properties(BRINE_PVT) == (1030.0, 4100.0)
