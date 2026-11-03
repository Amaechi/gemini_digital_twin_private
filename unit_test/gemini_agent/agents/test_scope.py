"""Unit tests for gemini_agent.agents.scope (AG-06)."""

import pytest

from gemini_agent.agents.scope import (
    DEFAULT_BRIEF,
    constraint_conflicts,
    missing_request_inputs,
    refusal_message,
    scope_check,
)
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


@pytest.mark.parametrize(
    ("prompt", "basis_id"),
    [
        ("Supply heating at 120 °C", "DB-16"),  # spec §12 S5
        ("cooling supply at 15 degC please", "DB-17"),
        ("heating at 99.5 degC", "DB-16"),  # precision as written
    ],
)
def test_supply_temperature_conflicts(prompt, basis_id):
    """S5 (WP-13): a supply temperature other than DB-16/DB-17 is noted; the basis is kept."""
    conflicts = constraint_conflicts(prompt, load_config("limits"))
    assert len(conflicts) == 1 and basis_id in conflicts[0]
    assert "until a change of basis is confirmed" in conflicts[0]


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "Heating at 100 degC and cooling at 20 degC",
        "Cool the hospital at 20 degC and heat it at 100 °C",
        "heating at 100.0 degC",
        "heating with a return temperature of 55 °C",
        "The brine is 85 °C, heat the town at 100 °C",
        "reservoir 87 °C at 2.5 km",  # = 12 + 30 × 2.5 (DB-04, DB-03)
        "reservoir 88 °C at 2.5 km",  # within 30 K/km × 0.05 km depth precision + 0.5 K
        "at 2.5 km the reservoir is 87 °C",
        "the gradient is 30 °C/km",
        # review WP-13: not supply temperatures
        "heating demand peaks when it is -10 °C",
        "heating demand peaks when it is 5 °C",
        "The absorption chiller is driven by 90 °C water",
        "the heat exchanger approach is 3 °C",
        "Heat the buildings to 21 °C indoors",
        "heating at 100 degC and a return of 55 °C",
    ],
)
def test_basis_consistent_requests_raise_no_conflict(prompt):
    """Return, brine and basis-consistent temperatures are not supply or reservoir conflicts."""
    from gemini_agent.agents.scope import DEFAULT_BRIEF

    text = prompt or DEFAULT_BRIEF
    assert constraint_conflicts(text, load_config("limits"), load_config("design_basis")) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "The geothermal gradient is 30 °C/km but the reservoir is 150 °C at 2.5 km.",  # S9
        "The reservoir at 2.5 km is 150 °C",
        "reservoir 150 °C at 2,500 m",
        "The well at 2.5 km depth is 150 °C",
    ],
)
def test_reservoir_temperature_inconsistent_with_gradient(prompt):
    """S9 (WP-13): 150 °C at 2.5 km contradicts 30 K/km + 12 °C surface (RC-01: 87 °C)."""
    limits, basis = load_config("limits"), load_config("design_basis")
    conflicts = constraint_conflicts(prompt, limits, basis)
    assert len(conflicts) == 1
    assert "DB-03" in conflicts[0] and "DB-04" in conflicts[0] and "87.0 degC" in conflicts[0]
    assert constraint_conflicts(prompt, limits) == []  # rule needs the design basis


def test_stated_gradient_is_checked_against_the_basis():
    """A stated gradient other than DB-03 is noted; temperatures are checked against DB-03."""
    limits, basis = load_config("limits"), load_config("design_basis")
    conflicts = constraint_conflicts("gradient 35 K/km; 99.5 °C at 2500 m", limits, basis)
    assert len(conflicts) == 2
    assert "Stated gradient 35 K/km" in conflicts[0] and "(DB-03)" in conflicts[0]
    assert "Stated 99.5 degC at 2500 m" in conflicts[1] and "87.0 degC" in conflicts[1]


@pytest.mark.parametrize(
    ("prompt", "missing"),
    [
        ("Design a geothermal heating system for 30 MW", ["demand", "cooling", "reservoir"]),  # S2
        ("Supply heating at 120 °C", ["demand", "cooling", "reservoir"]),  # S5
        ("Use 5 doublets at 500 m³/h", ["demand", "cooling", "reservoir"]),  # S4
        (
            "Design the geothermal district heating and cooling system for the new neighbourhood",
            ["demand", "reservoir"],
        ),  # S6
        (
            (
                "Design the geothermal heating and cooling system; the reservoir permeability "
                "is only 50 mD"
            ),
            ["demand"],
        ),  # S8
        (
            (
                "Design the heating and cooling plant from the attached demand CSVs; the "
                "reservoir data are in the brief"
            ),
            [],
        ),
        ("Heating and cooling: daily load profiles enclosed, aquifer at 2.5 km", []),
        ("", []),
    ],
)
def test_missing_request_inputs_lists_what_the_request_lacks(prompt, missing):
    """S2 (WP-13 follow-up): the inputs a user-written request does not provide, in order."""
    labels = [item.split(" (")[0] for item in missing_request_inputs(prompt)]
    expected = {"demand": "demand CSVs", "cooling": "cooling demand", "reservoir": "reservoir data"}
    assert labels == [expected[name] for name in missing]


def test_missing_request_inputs_name_the_repository_paths():
    """The labels tell the user where the repository defaults live (the question quotes them)."""
    items = missing_request_inputs("Design a geothermal heating system for 30 MW")
    assert any("data/demand/" in item for item in items)
    assert any("config/design_basis.yaml" in item for item in items)
    # The default brief itself is never scanned by the runner (it comes with every file), but the
    # function is pure: it reports what the text does not name.
    assert len(missing_request_inputs(DEFAULT_BRIEF)) == 2
