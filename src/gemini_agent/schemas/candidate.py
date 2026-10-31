"""Candidate plant configuration (spec §3.4 ENG-01).

A ``Candidate`` is a *proposal*. This schema checks structure and units only; hard limits
(1–3 doublets, 50–300 m³/h per active doublet, ΔT ≤ 40 K, …) are enforced by ``DesignGuard``
from ``config/limits.yaml`` so that out-of-range proposals are rejected with an explanation
instead of a bare validation error. Return temperatures have no defaults on purpose: the
config generator fills them from ``config/assumptions.yaml`` (DB-31, DB-32).
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from gemini_agent.schemas.common import ConceptId, GasHandling, StrictModel

EQUIPMENT_SIZE_SUFFIXES: tuple[str, ...] = (
    "_mw",
    "_kw",
    "_w",
    "_mwh",
    "_kwh",
    "_m3",
    "_m3h",
    "_m2",
    "_m",
    "_k",
    "_degc",
    "_bar",
    "_frac",
    "_count",
)
"""Allowed unit suffixes for ``Candidate.equipment_sizes`` keys (CLAUDE.md rule 5)."""


class Candidate(StrictModel):
    """One alternative plant configuration to be screened, simulated and verified."""

    id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$",
        max_length=64,
        description="Unique candidate id, e.g. 'C2-2x300-hthp'.",
    )
    concept: ConceptId = Field(description="Concept family C0–C8 (spec §3.4).")
    parent_id: str | None = Field(
        default=None, description="Parent candidate for derived concepts (C6, C7, C8)."
    )
    description: str = ""
    doublets: int = Field(
        ge=0, description="Number of doublets; 0 if and only if concept is C0 (spec §3.4)."
    )
    depth_m: float | None = Field(
        default=None,
        gt=0,
        description="Reservoir mid-depth TVD (DB-01, DB-07); None if no doublets.",
    )
    design_flow_m3h: float | None = Field(
        default=None, gt=0, description="Design production flow per active doublet (DB-11)."
    )
    equipment_sizes: dict[str, float] = Field(
        default_factory=dict,
        description="Installed sizes, keys carry a unit suffix, e.g. {'hthp_heating_mw': 8.0}.",
    )
    gas_handling: GasHandling | None = Field(
        default=None, description="Associated-gas variant (DB-36); None if no doublets."
    )
    heating_return_degc: float = Field(description="District-heating return temperature (DB-31).")
    cooling_return_degc: float = Field(description="Cooling-network return temperature (DB-32).")

    @field_validator("equipment_sizes")
    @classmethod
    def _check_equipment_sizes(cls, sizes: dict[str, float]) -> dict[str, float]:
        for key, value in sizes.items():
            if not key.endswith(EQUIPMENT_SIZE_SUFFIXES):
                raise ValueError(
                    f"equipment size key '{key}' must end with a unit suffix "
                    f"{EQUIPMENT_SIZE_SUFFIXES}"
                )
            if value < 0:
                raise ValueError(f"equipment size '{key}' must be >= 0, got {value}")
        return sizes

    @model_validator(mode="after")
    def _check_doublet_fields(self) -> Candidate:
        well_fields = {
            "depth_m": self.depth_m,
            "design_flow_m3h": self.design_flow_m3h,
            "gas_handling": self.gas_handling,
        }
        if (self.concept == "C0") != (self.doublets == 0):
            raise ValueError(
                f"concept {self.concept} with {self.doublets} doublet(s): C0 (no geothermal) must "
                "have 0 doublets and every other concept at least 1 (spec §3.4)"
            )
        if self.doublets == 0:
            given = [name for name, value in well_fields.items() if value is not None]
            if given:
                raise ValueError(f"candidate without doublets must not set {given}")
        else:
            missing = [name for name, value in well_fields.items() if value is None]
            if missing:
                raise ValueError(f"candidate with {self.doublets} doublet(s) must set {missing}")
        return self
