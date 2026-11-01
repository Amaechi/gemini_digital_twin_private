"""Unit tests for gemini_agent.agents.roles and gemini_agent.prompts.system (spec §5.1)."""

from gemini_agent.agents.roles import MAX_TOOLS_PER_AGENT, ROLES
from gemini_agent.prompts.system import system_prompt
from gemini_agent.runner.phases import PHASE_SPECS
from gemini_agent.runner.toolbox import default_toolbox
from gemini_agent.schemas.decision import Role


def test_every_role_has_at_most_six_known_tools():
    """≤ 6 tools per agent, each bound in the toolbox."""
    toolbox = default_toolbox()
    assert set(ROLES) == set(Role)
    for spec in ROLES.values():
        assert 1 <= len(spec.tools) <= MAX_TOOLS_PER_AGENT, spec.role
        assert len(set(spec.tools)) == len(spec.tools)
        assert all(tool in toolbox for tool in spec.tools), spec.role


def test_phase_required_tools_belong_to_the_phase_role():
    """The deciding role can name every tool its phase requires."""
    for spec in PHASE_SPECS.values():
        assert set(spec.required_tools) <= set(ROLES[spec.role].tools), spec.phase


def test_prompts_rules_and_reviewer_independence():
    """Every prompt carries the no-numbers rule; the reviewer is never told a designer exists."""
    for role in Role:
        assert "Never compute or invent numbers" in system_prompt(role)
    reviewer = system_prompt(Role.REVIEWER).lower()
    assert "designer" not in reviewer and "system architect" not in reviewer
    assert ROLES[Role.MODEL_BUILDER].model == "coder"
