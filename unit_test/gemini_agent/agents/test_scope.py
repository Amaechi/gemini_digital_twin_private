"""Unit tests for gemini_agent.agents.scope (AG-06)."""

import pytest

from gemini_agent.agents.scope import constraint_conflicts, refusal_message, scope_check
from gemini_agent.schemas.config import load_config


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "Design the geothermal district heating and cooling plant",
        "Compare heat pump and absorption chiller options for the doublets",
        "What LCOH does the GEMINI digital twin give?",
    ],
)
def test_in_scope(prompt):
    """Design-domain requests (and the default brief) are in scope."""
    assert scope_check(prompt).in_scope


@pytest.mark.parametrize(
    "prompt", ["What is the best pizza in Rome?", "Write me a poem about cats", "Is it well?"]
)
def test_off_topic_is_refused_with_redirect(prompt):
    """Off-topic requests are out of scope; the refusal redirects to the design task."""
    result = scope_check(prompt)
    assert not result.in_scope
    message = refusal_message(prompt)
    assert "outside that scope" in message and "district heating" in message


def test_constraint_conflicts_are_explained():
    """Requests beyond the hard limits are explained, not silently ignored."""
    limits = load_config("limits")
    conflicts = constraint_conflicts("use 5 doublets at 400 m3/h each", limits)
    assert len(conflicts) == 2
    assert "1–3 doublets" in conflicts[0] and "50–300 m3/h" in conflicts[1]
    assert constraint_conflicts("2 doublets at 250 m³/h", limits) == []
