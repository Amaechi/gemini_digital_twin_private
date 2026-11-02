"""Unit tests for gemini_agent.memory.project_knowledge (MEM-01: explicit user facts only)."""

import pytest
from pydantic import ValidationError

from gemini_agent.memory.event_store import EventRecord, SQLiteEventStore
from gemini_agent.memory.project_knowledge import (
    KNOWLEDGE_FILE,
    JsonProjectKnowledgeStore,
    ProjectFact,
    fact_from_event,
    new_user_assertion,
)

pytestmark = pytest.mark.usefixtures("stub_tools")


def _event(event_type: str, seq: int = 1, **payload) -> EventRecord:
    return EventRecord(run_id="r1", seq=seq, ts="t", type=event_type, phase="P1", payload=payload)


def test_only_durable_user_answers_become_facts():
    """USER_ANSWER with durable true and a register id → fact; everything else is ignored."""
    answer = _event(
        "USER_ANSWER",
        message="DB-31 = 60 degC (user answer)",
        durable=True,
        assumption_id="DB-31",
        answer=60.0,
    )
    fact = fact_from_event(answer)
    assert fact is not None and fact.source == "user_answer"
    assert (fact.assumption_id, fact.value, fact.learned_in_run, fact.event_seq) == (
        "DB-31",
        60.0,
        "r1",
        1,
    )
    ignored = [
        _event(
            "ASSUMPTION",
            message="DB-32 = 26 (assumed)",
            durable=False,
            assumption_id="DB-32",
            answer=26.0,
        ),
        _event("USER_ANSWER", message="DB-30 = A", durable=False, assumption_id="DB-30"),
        _event(
            "USER_ANSWER",
            message="Human approval: approved",
            durable=True,
            assumption_id=None,
            answer="approved",
        ),
        _event("DECISION", source="llm", steps=[]),
        _event("TOOL_RESULT", tool="economics", summary="LCOH 50 USD/MWh"),
    ]
    assert [fact_from_event(e) for e in ignored] == [None] * len(ignored)


def test_non_user_sources_are_refused(tmp_path):
    """The schema admits user sources only, even for objects built without validation."""
    with pytest.raises(ValidationError):
        ProjectFact(knowledge_id="k", assertion="LLM says 60", source="llm", learned_at="t")
    store = JsonProjectKnowledgeStore(tmp_path)
    forged = ProjectFact.model_construct(
        knowledge_id="k", assertion="assumed default", source="assumption", learned_at="t"
    )
    with pytest.raises(ValidationError):
        store.save(forged)
    assert store.list() == []


def test_assertions_are_normalised_and_bounded():
    """Whitespace collapses; too-short or too-long assertions are refused."""
    fact = new_user_assertion(assertion="  Brine   TDS is 120 g/l  ", run_id="r2")
    assert fact.assertion == "Brine TDS is 120 g/l" and fact.source == "user_assertion"
    with pytest.raises(ValueError, match="too short"):
        new_user_assertion(assertion=" a ")
    with pytest.raises(ValueError, match="exceeds"):
        new_user_assertion(assertion="x" * 501)


def test_store_dedupes_and_latest_answer_wins(tmp_path):
    """One fact per register id (latest wins); free text de-duplicated case-insensitively."""
    store = JsonProjectKnowledgeStore(tmp_path)
    assert store.path == tmp_path / KNOWLEDGE_FILE
    store.save(
        new_user_assertion(
            assertion="DB-31 = 60", assumption_id="DB-31", value=60.0, source="user_answer"
        )
    )
    store.save(
        new_user_assertion(
            assertion="DB-31 = 50", assumption_id="DB-31", value=50.0, source="user_answer"
        )
    )
    store.save(new_user_assertion(assertion="No Cu in the plant"))
    store.save(new_user_assertion(assertion="no cu in the plant"))
    facts = store.list()
    assert len(facts) == 2 and store.answers() == {"DB-31": 50.0}
    used = store.mark_used(store.get("DB-31").knowledge_id)
    assert used.use_count == 1 and used.last_used_at is not None
    assert JsonProjectKnowledgeStore(tmp_path / KNOWLEDGE_FILE).get("DB-31").use_count == 1
    with pytest.raises(KeyError):
        store.mark_used("nope")
    store.clear()
    assert store.list() == [] and store.get("DB-31") is None


def test_ingest_from_event_store(tmp_path):
    """MEM-01 end to end: only the user's answer from the run's event log is kept."""
    events = SQLiteEventStore(tmp_path / "events.sqlite")
    events.create_run("r1", "running", {})
    events.append_event(
        "r1",
        "ASSUMPTION",
        {
            "message": "DB-30 = A (assumed)",
            "durable": False,
            "assumption_id": "DB-30",
            "answer": "A",
        },
        "P1",
    )
    events.append_event(
        "r1",
        "USER_ANSWER",
        {
            "message": "DB-32 = 24 degC (user answer)",
            "durable": True,
            "assumption_id": "DB-32",
            "answer": 24.0,
        },
        "P1",
    )
    store = JsonProjectKnowledgeStore(tmp_path / "kb.json")
    stored = store.ingest_events(events.list_events("r1"))
    assert [f.assumption_id for f in stored] == ["DB-32"]
    assert store.answers() == {"DB-32": 24.0}
    events.close()


def test_runner_answers_flow_into_project_knowledge(tmp_path):
    """MEM-01 end to end: a paused run answered A/60/25/yes yields DB-30/31/32, not the approval."""
    from gemini_agent.agents.engine import PolicyEngine
    from gemini_agent.runner.design_runner import DesignRunner
    from gemini_agent.schemas.decision import RunStatus

    runner = DesignRunner(PolicyEngine(), runs_root=tmp_path)
    result = runner.start("", run_id="mem01", input_mode="pause")
    for answer in ("A", "60", "25", "yes"):
        assert result.status == RunStatus.PAUSED
        result = runner.resume("mem01", answer)
    assert result.status == RunStatus.COMPLETED

    store = runner.store("mem01")
    trace = store.replay("mem01")
    assert trace.status == result.status.value and trace.last_verdict == result.verdict
    assert trace.missing_seq == []

    knowledge = JsonProjectKnowledgeStore(tmp_path / "kb")
    stored = knowledge.ingest_events(store.list_events("mem01"))
    assert sorted(f.assumption_id for f in stored) == ["DB-30", "DB-31", "DB-32"]
    answers = knowledge.answers()
    assert answers["DB-31"] == 60.0 and answers["DB-32"] == 25.0
    assert all(f.source == "user_answer" and f.learned_in_run == "mem01" for f in stored)
