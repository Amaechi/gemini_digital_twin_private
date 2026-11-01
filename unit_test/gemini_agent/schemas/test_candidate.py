"""Unit tests for gemini_agent.schemas.candidate."""

import pytest
from pydantic import ValidationError

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling


def _c2(**overrides):
    fields = {
        "id": "C2-2x300",
        "concept": "C2",
        "doublets": 2,
        "depth_m": 2500.0,
        "design_flow_m3h": 300.0,
        "equipment_sizes": {"hthp_heating_mw": 8.0, "electric_chiller_cooling_mw": 10.0},
        "gas_handling": "degas_use",
        "heating_return_degc": 55.0,
        "cooling_return_degc": 26.0,
    }
    fields.update(overrides)
    return Candidate(**fields)


def test_valid_geothermal_candidate_round_trips():
    """A C2 candidate validates and survives a JSON round trip."""
    cand = _c2()
    assert cand.gas_handling is GasHandling.DEGAS_USE
    assert Candidate.model_validate_json(cand.model_dump_json()) == cand


def test_c0_counterfactual_has_no_well_fields():
    """C0 (no geothermal) has zero doublets and no depth/flow/gas handling."""
    cand = Candidate(
        id="C0",
        concept="C0",
        doublets=0,
        equipment_sizes={"gas_boiler_heating_mw": 30.0},
        heating_return_degc=55.0,
        cooling_return_degc=26.0,
    )
    assert cand.depth_m is None and cand.gas_handling is None
    with pytest.raises(ValidationError):
        Candidate(**{**cand.model_dump(), "depth_m": 2500.0})


@pytest.mark.parametrize(("concept", "doublets"), [("C0", 2), ("C3", 0)])
def test_c0_if_and_only_if_no_doublets(concept, doublets):
    """C0 has no doublets; every other concept has at least one (spec §3.4)."""
    with pytest.raises(ValidationError, match="C0"):
        Candidate(
            id="x",
            concept=concept,
            doublets=doublets,
            heating_return_degc=55.0,
            cooling_return_degc=26.0,
        )


def test_doublet_candidate_requires_well_fields():
    """With doublets > 0, depth, design flow and gas handling are required."""
    with pytest.raises(ValidationError, match="gas_handling"):
        _c2(gas_handling=None)
    with pytest.raises(ValidationError, match="depth_m"):
        _c2(depth_m=None)


@pytest.mark.parametrize("key", ["hthp_heating", "chiller_tons", "storage"])
def test_equipment_keys_need_unit_suffix(key):
    """Equipment size keys must carry a unit suffix (CLAUDE.md rule 5)."""
    with pytest.raises(ValidationError, match="unit suffix"):
        _c2(equipment_sizes={key: 1.0})


def test_rejects_unknown_concept_negative_size_and_extra_fields():
    """Concept must be C0–C8, sizes non-negative, unknown keys forbidden."""
    with pytest.raises(ValidationError):
        _c2(concept="C9")
    with pytest.raises(ValidationError):
        _c2(equipment_sizes={"hthp_heating_mw": -1.0})
    with pytest.raises(ValidationError):
        _c2(colour="green")
    with pytest.raises(ValidationError):
        _c2(gas_handling="vent")
