"""Deterministic candidate ranking shared by the optimiser stub and Decision Record options.

Rule (no LLM, no invented numbers): a candidate with known unmet demand > 0 ranks last; among
the rest, the lowest LCOH (from a tool's ``KPISet``) wins; unknown KPIs rank after known ones;
ties keep the generation order. With stub tools every KPI is ``None``, so the order is the
generation order and ``has_kpi_evidence`` is False.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.results import KPISet


def _unmet(kpi: KPISet | None) -> bool:
    if kpi is None:
        return False
    return (kpi.unmet_heating_mwh or 0.0) > 0.0 or (kpi.unmet_cooling_mwh or 0.0) > 0.0


def has_kpi_evidence(kpis: Mapping[str, KPISet]) -> bool:
    """Return True if any candidate has a computed LCOH (i.e. real economics ran)."""
    return any(kpi.lcoh_per_mwh is not None for kpi in kpis.values())


def rank_candidates(
    candidates: Sequence[Candidate],
    kpis: Mapping[str, KPISet],
    *,
    exclude_concepts: tuple[str, ...] = ("C0",),
) -> list[str]:
    """Candidate ids, best first (see module docstring)."""
    ranked = []
    for order, candidate in enumerate(candidates):
        if candidate.concept in exclude_concepts:
            continue
        kpi = kpis.get(candidate.id)
        lcoh = kpi.lcoh_per_mwh if kpi is not None else None
        ranked.append(
            (
                _unmet(kpi),
                lcoh is None,
                lcoh if lcoh is not None else math.inf,
                order,
                candidate.id,
            )
        )
    ranked.sort()
    return [item[-1] for item in ranked]
