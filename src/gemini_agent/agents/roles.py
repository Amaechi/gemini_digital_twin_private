"""Agent roles, their tool allow-lists (≤ 6 each) and which model profile they use (spec §5.1).

Tools named here are the only tools an agent may put in a ``NextStep``; the workflow runner
executes them. Model Builder internals belong to WP-12 (``agents/model_builder.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from gemini_agent.schemas.decision import Role

MAX_TOOLS_PER_AGENT = 6
"""Spec §5.1 / §5.4.4: 3–6 tools per agent."""


@dataclass(frozen=True)
class RoleSpec:
    """Static description of one agent role."""

    role: Role
    title: str
    tools: tuple[str, ...]
    model: Literal["orchestrator", "coder"] = "orchestrator"


ROLES: dict[Role, RoleSpec] = {
    spec.role: spec
    for spec in (
        RoleSpec(
            Role.LEAD_ENGINEER,
            "Lead Engineer",
            ("request_missing_input", "knowledge_search", "simulate_annual", "verify_design"),
        ),
        RoleSpec(
            Role.REQUIREMENTS,
            "Requirements & Data",
            (
                "load_design_basis",
                "profile_demand",
                "climate",
                "knowledge_search",
                "request_missing_input",
            ),
        ),
        RoleSpec(
            Role.SUBSURFACE,
            "Subsurface",
            (
                "reservoir_conditions",
                "productivity_index",
                "doublet_operating_point",
                "thermal_breakthrough",
                "pressure_interference",
                "knowledge_search",
            ),
        ),
        RoleSpec(
            Role.SYSTEM_ARCHITECT,
            "System Architect",
            (
                "generate_candidates",
                "design_guard",
                "simulate_annual",
                "economics",
                "optimise_design",
                "knowledge_search",
            ),
        ),
        RoleSpec(
            Role.MODEL_BUILDER,
            "Model Builder",
            (
                "model_gap_analysis",
                "read_reference_card",
                "write_model_file",
                "run_pytest",
                "register_model",
            ),
            model="coder",
        ),
        RoleSpec(
            Role.ECONOMICS,
            "Economics",
            ("economics", "co2", "exergy", "sensitivity", "uncertainty", "knowledge_search"),
        ),
        RoleSpec(
            Role.REVIEWER,
            "Independent Reviewer",
            ("read_results", "verify_design", "knowledge_search"),
        ),
        RoleSpec(Role.REPORTER, "Reporter", ("build_report", "plot_figures", "read_results")),
    )
}


def role_spec(role: Role) -> RoleSpec:
    """Return the spec of a role."""
    return ROLES[role]
