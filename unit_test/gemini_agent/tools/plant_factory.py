"""Candidates and cached TL-09 configs shared by the WP-06 tests (spec §3.4 concepts)."""

from __future__ import annotations

import functools
from typing import Any

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling
from gemini_agent.tools.config_generator import generate_plant, load_generator_basis
from gemini_agent.tools.resource import load_subsurface_basis


def make_candidate(
    concept: str,
    doublets: int,
    *,
    depth_m: float = 2500.0,
    flow_m3h: float = 300.0,
    parent_id: str | None = None,
    candidate_id: str | None = None,
    **extra: Any,
) -> Candidate:
    """Return a candidate of ``concept`` with ``doublets`` doublets (spec §3.4)."""
    wells = doublets > 0
    gas = GasHandling.PRESSURISED_REINJECT if concept == "C7" else GasHandling.DEGAS_USE
    return Candidate(
        id=candidate_id or f"{concept}-{doublets}d",
        concept=concept,  # type: ignore[arg-type]
        parent_id=parent_id,
        doublets=doublets,
        depth_m=depth_m if wells else None,
        design_flow_m3h=flow_m3h if wells else None,
        gas_handling=gas if wells else None,
        heating_return_degc=55.0,
        cooling_return_degc=26.0,
        **extra,
    )


CONCEPT_CASES = [
    ("C0", 0, 2500.0, None),
    ("C1", 1, 2500.0, None),
    ("C1", 3, 2500.0, None),
    ("C2", 2, 2500.0, None),
    ("C3", 2, 2500.0, None),
    ("C4", 2, 3200.0, None),
    ("C5", 3, 1500.0, None),
    ("C6", 2, 2500.0, "C3-2d"),
    ("C7", 2, 2500.0, "C2-2d"),
]


@functools.cache
def basis():
    """Return the generator and subsurface bases (loaded once)."""
    return load_generator_basis(), load_subsurface_basis()


@functools.cache
def generated(concept: str, doublets: int, depth_m: float, parent: str | None):
    """Design-capacity config of one concept case (cached)."""
    gb, sub = basis()
    candidate = make_candidate(concept, doublets, depth_m=depth_m, parent_id=parent)
    return candidate, generate_plant(candidate, gb=gb, sub=sub)
