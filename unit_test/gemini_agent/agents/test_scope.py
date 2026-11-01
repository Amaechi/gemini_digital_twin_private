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
        "Supply heating at 120 °C",  # spec §12 S5
        "Use 5 doublets at 500 m³/h",  # S4
        "Permeability 50 mD",  # S8
        "Design a geothermal heating system for 30 MW",  # S2
        "Explain the cooling technology choice",
        "How much heat does the plant deliver?",
        "Heating at 100 degC and cooling at 20 degC",
        "We need heating and cooling for a new neighbourhood in Delft",
        "Cool the hospital at 20 degC and heat it at 100 °C",
        "How many injectors and producers?",
        "What ESP power do the producers need?",
        "How much CO2 does it save?",
        "Which heat exchanger plates?",
    ],
)
def test_in_scope(prompt):
    """Design-domain requests (and the default brief) are in scope."""
    assert scope_check(prompt).in_scope


@pytest.mark.parametrize(
    "prompt",
    [
        "What is the best pizza in Rome?",
        "Write me a poem about cats",
        "Is it well?",
        "What's the best pizza in Delft?",  # spec §12 S3
        # everyday uses of domain words are not design requests
        "Write a poem about the summer heat",
        "Which district of Paris has the best food?",
        "Recommend a heat-resistant phone case",
    ],
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
