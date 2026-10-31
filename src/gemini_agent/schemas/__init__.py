"""Shared Pydantic v2 schemas for all tool I/O (spec §6, WORK_PACKAGES.md WP-00).

Later WPs may extend these models (add fields/classes) but never rename or remove them.
"""

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import (
    ConceptId,
    GasHandling,
    SourceTag,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import (
    Assumption,
    AssumptionsRegister,
    ClimateConfig,
    CostAssumptions,
    CostItem,
    DesignBasis,
    HardLimits,
    LLMConfig,
    load_all_configs,
    load_config,
)
from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet

__all__ = [
    "AnnualResult",
    "Assumption",
    "AssumptionsRegister",
    "Candidate",
    "ClimateConfig",
    "ConceptId",
    "CostAssumptions",
    "CostItem",
    "DayResult",
    "DesignBasis",
    "GasHandling",
    "HardLimits",
    "KPISet",
    "LLMConfig",
    "SourceTag",
    "StrictModel",
    "ToolResult",
    "load_all_configs",
    "load_config",
]
