"""Unit tests for gemini_agent.schemas.common."""

import pytest
from pydantic import BaseModel, ValidationError

from gemini_agent.schemas.common import (
    SUMMARY_MAX_CHARS,
    GasHandling,
    SourceTag,
    StrictModel,
    ToolResult,
)


class _Payload(BaseModel):
    flow_m3h: float


def test_tool_result_round_trip_keeps_typed_data():
    """A ToolResult[T] validates its payload and survives a JSON round trip."""
    result = ToolResult[_Payload](
        data={"flow_m3h": 129.0},
        summary="Doublet flow 129 m3/h.",
        assumption_ids=["DB-38"],
        sources=["RC-01"],
    )
    assert isinstance(result.data, _Payload)
    again = ToolResult[_Payload].model_validate_json(result.model_dump_json())
    assert again == result


def test_tool_result_summary_is_capped_at_300_chars():
    """Summaries longer than 300 characters are rejected."""
    assert SUMMARY_MAX_CHARS == 300
    ToolResult[int](data=1, summary="x" * 300)
    with pytest.raises(ValidationError):
        ToolResult[int](data=1, summary="x" * 301)


def test_tool_result_rejects_bad_payload_and_unknown_fields():
    """Payload type is enforced and unknown keys are forbidden."""
    with pytest.raises(ValidationError):
        ToolResult[_Payload](data={"flow": 1.0}, summary="bad key")
    with pytest.raises(ValidationError):
        ToolResult[int](data=1, summary="ok", extra_field=True)


def test_strict_model_forbids_extra_fields():
    """Subclasses of StrictModel reject unknown fields."""

    class _M(StrictModel):
        a_k: float

    with pytest.raises(ValidationError):
        _M(a_k=1.0, b=2)


def test_enums_have_expected_members():
    """Provenance tags follow spec §0; gas handling has no venting option (DB-36)."""
    assert {t.value for t in SourceTag} == {"P", "D", "L", "W", "A"}
    assert {g.value for g in GasHandling} == {"degas_use", "pressurised_reinject"}
