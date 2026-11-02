"""Deterministic candidate ranking shared by the optimiser stub and Decision Record options.

Rule (no LLM, no invented numbers): candidates that failed DesignGuard or were REJECTED by the
verifier are excluded; a candidate with known unmet demand > 0 ranks last; among
the rest, the lowest LCOH + LCOC (from a tool's ``KPISet``; the TL-13 objective OPT-OBJ, WP-14:
the plant sells heat and cooling, so ranking on LCOH alone ignored the cooling cost; an unknown
LCOC counts 0) wins; unknown LCOH ranks after known ones; ties keep the generation order. With stub
tools every KPI is ``None``, so the order is the generation order and ``has_kpi_evidence`` is
False.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence

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
    exclude_ids: Collection[str] = (),
) -> list[str]:
    """Return candidate ids, best first (see module docstring)."""
    ranked = []
    for order, candidate in enumerate(candidates):
        if candidate.concept in exclude_concepts or candidate.id in exclude_ids:
            continue
        kpi = kpis.get(candidate.id)
        lcoh = kpi.lcoh_per_mwh if kpi is not None else None
        lcoc = (kpi.lcoc_per_mwh if kpi is not None else None) or 0.0
        ranked.append(
            (
                _unmet(kpi),
                lcoh is None,
                lcoh + lcoc if lcoh is not None else math.inf,
                order,
                candidate.id,
            )
        )
    ranked.sort()
    return [item[-1] for item in ranked]
