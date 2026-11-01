"""P0 intake checks (AG-06): off-topic refusal and constraint-conflict explanations.

Deterministic keyword rules run before any agent sees the request; the Lead Engineer agent may
additionally refuse in P0, but it can never pull an off-topic request back into scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gemini_agent.schemas.config import HardLimits

DEFAULT_BRIEF = (
    "Select, size and validate a deep-geothermal district heating (100 degC) and cooling "
    "(20 degC) system with the GEMINI digital twin; compare alternatives and explain every "
    "decision."
)

_DOMAIN = re.compile(
    r"\b(geotherm\w*|heating|heat|heat[- ]?pumps?|hthp|cooling|chillers?|district|doublets?|"
    r"brine|reservoirs?|aquifers?|ates|gemini|digital twin|lcoh|lcoc|co2|boreholes?|"
    r"injectors?|producers?|esp)\b",
    re.IGNORECASE,
)
_DOUBLETS = re.compile(r"\b(\d+)\s*(?:x\s*)?doublets?\b", re.IGNORECASE)
_FLOW = re.compile(r"\b(\d+(?:\.\d+)?)\s*m(?:3|³)\s*/\s*h\b", re.IGNORECASE)

REFUSAL_TEMPLATE = (
    "GeoAgent only designs deep-geothermal district heating (100 degC) and cooling (20 degC) "
    "systems with the GEMINI digital twin. '{excerpt}' is outside that scope; please ask about "
    "the design, its alternatives, KPIs or decisions."
)


@dataclass
class ScopeResult:
    """Outcome of the deterministic scope check."""

    in_scope: bool
    reason: str
    matched_terms: list[str] = field(default_factory=list)


def scope_check(prompt: str) -> ScopeResult:
    """In scope if the request mentions the design domain (an empty prompt uses the brief)."""
    text = prompt.strip()
    if not text:
        return ScopeResult(True, "empty request: default challenge brief")
    matched = sorted({match.group(0).lower() for match in _DOMAIN.finditer(text)})
    if matched:
        return ScopeResult(True, "mentions the design domain", matched)
    return ScopeResult(False, "no geothermal heating/cooling design topic found in the request")


def refusal_message(prompt: str) -> str:
    """Polite refusal that redirects to the design task."""
    excerpt = " ".join(prompt.split())[:80]
    return REFUSAL_TEMPLATE.format(excerpt=excerpt)


def constraint_conflicts(prompt: str, limits: HardLimits) -> list[str]:
    """Explain requests that conflict with the hard limits (the design stays within them)."""
    conflicts: list[str] = []
    for match in _DOUBLETS.finditer(prompt):
        count = int(match.group(1))
        if not limits.doublet_count_min <= count <= limits.doublet_count_max:
            conflicts.append(
                f"Requested {count} doublets conflicts with the hard limit "
                f"{limits.doublet_count_min}–{limits.doublet_count_max} doublets (DB-10); "
                "candidates stay within the limit."
            )
    for match in _FLOW.finditer(prompt):
        flow = float(match.group(1))
        if not limits.flow_min_active_m3h <= flow <= limits.flow_max_m3h:
            conflicts.append(
                f"Requested {flow:g} m3/h conflicts with the per-doublet flow limit "
                f"{limits.flow_min_active_m3h:g}–{limits.flow_max_m3h:g} m3/h (DB-11); "
                "flows stay within the limit."
            )
    return conflicts
