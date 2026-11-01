"""Shared primitive types and the generic tool-result envelope.

Conventions (CLAUDE.md rule 5): SI internally; every numeric field name carries a unit suffix
(``_m3h``, ``_m3s``, ``_bar``, ``_pa``, ``_degc``, ``_k``, ``_w``, ``_mw``, ``_mwh``, ``_frac``).
Provenance tags follow spec §0: [P] organisers, [D] derived, [L] literature, [W] web,
[A] assumption.

Later WPs may EXTEND these schemas (add fields/classes) but never rename or remove them
(WORK_PACKAGES.md).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

SUMMARY_MAX_CHARS = 300
"""Upper bound on ``ToolResult.summary`` (CLAUDE.md code conventions)."""

HOURS_PER_DAY = 24.0
"""Day-average MW × 24 h = MWh per day (demand CSVs are day-average MW)."""

ConceptId = Literal["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
"""Candidate concept families of spec §3.4 (ENG-01). C0 = no-geothermal counterfactual."""


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and NaN/inf.

    Unknown fields: catches YAML typos and hallucinated LLM keys. NaN/inf: JSON would serialise
    them as null and break dump → validate round-trips.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceTag(StrEnum):
    """Provenance tag of a value (spec §0)."""

    P = "P"  # provided by organisers
    D = "D"  # derived by us (formula shown)
    L = "L"  # literature (source + page in spec §19)
    W = "W"  # web source verified 31 Oct 2026
    A = "A"  # our assumption: stated in the report, tested in sensitivity


class GasHandling(StrEnum):
    """Associated-gas handling variant (DB-36 / DR-07). Venting CH4 is never an option."""

    DEGAS_USE = "degas_use"  # (a) degas and burn the gas in CHP/boiler, with calcite inhibitor
    PRESSURISED_REINJECT = (
        "pressurised_reinject"  # (b) keep loop pressurised, reinject dissolved gas
    )


T = TypeVar("T")


class ToolResult(StrictModel, Generic[T]):
    """Envelope returned by every deterministic tool.

    ``data`` holds the full typed result; ``summary`` is the compact text an agent reads
    (≤ 300 characters). Numbers quoted in reports must come from ``data``.
    """

    data: T
    summary: str = Field(max_length=SUMMARY_MAX_CHARS)
    warnings: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(
        default_factory=list, description="IDs from config/assumptions.yaml used by this result."
    )
    sources: list[str] = Field(
        default_factory=list, description="Reference cards / spec items cited, e.g. 'RC-07'."
    )
