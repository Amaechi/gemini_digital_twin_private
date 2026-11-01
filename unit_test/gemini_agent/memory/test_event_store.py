"""Unit tests for the minimal SQLite event store used by the runner (WP-09 extends it)."""

import pytest

from gemini_agent.memory.event_store import SQLiteEventStore


def test_run_state_round_trip_and_events(tmp_path):
    """Runs persist across store instances; events are append-only and ordered."""
    path = tmp_path / "events.sqlite"
    store = SQLiteEventStore(path)
    store.create_run("r1", "running", {"phase": "P0"})
    store.append_event("r1", "RUN_START", {"engine": "policy"}, phase="P0")
    store.append_event("r1", "PAUSE", {"q": "DB-31?"}, phase="P1")
    store.update_run("r1", "paused", {"phase": "P1"})
    store.close()

    reopened = SQLiteEventStore(path)
    run = reopened.get_run("r1")
    assert run is not None and run.status == "paused" and run.state == {"phase": "P1"}
    events = reopened.list_events("r1")
    assert [(e.seq, e.type, e.phase) for e in events] == [
        (1, "RUN_START", "P0"),
        (2, "PAUSE", "P1"),
    ]
    assert reopened.get_run("missing") is None
    with pytest.raises(KeyError):
        reopened.update_run("missing", "running", {})
    reopened.close()


# --------------------------------------------------------------------------- WP-09 extensions
def _seeded_store(tmp_path) -> SQLiteEventStore:
    """Return a store holding a short but complete run trace (the runner's event types)."""
    store = SQLiteEventStore(tmp_path / "events.sqlite")
    store.create_run("r1", "running", {})
    events = [
        ("RUN_START", "P0", {"engine": "policy"}),
        ("PHASE_START", "P0", {"title": "Intake", "loop": 0}),
        ("PHASE_START", "P1", {"title": "Requirements", "loop": 0}),
        ("DECISION", "P1", {"source": "policy", "steps": []}),
        (
            "TOOL_RESULT",
            "P1",
            {
                "id": "TR-001",
                "tool": "load_design_basis",
                "stub": True,
                "source": "agent",
                "summary": "s",
            },
        ),
        ("PAUSE", "P1", {"request": {"request_id": "REQ-01-DB-31"}}),
        ("RESUME", "P1", {"request_id": "REQ-01-DB-31"}),
        (
            "USER_ANSWER",
            "P1",
            {
                "message": "DB-31 = 60 degC (user answer)",
                "durable": True,
                "assumption_id": "DB-31",
                "answer": 60.0,
            },
        ),
        (
            "ASSUMPTION",
            "P1",
            {
                "message": "DB-32 = 26 degC (assumed)",
                "durable": False,
                "assumption_id": "DB-32",
                "answer": 26.0,
            },
        ),
        ("TOOL_ERROR", "P5", {"id": "TR-002", "tool": "economics", "error": "boom"}),
        ("DECISION", "P5", {"source": "policy_fallback", "steps": []}),
        ("DECISION_RECORD", "P5", {"dr_id": "DR-01", "choice": "C2"}),
        ("PHASE_START", "P7", {"title": "Verification", "loop": 0}),
        ("VERIFY_GATE", "P7", {"candidate_id": "C2-2d", "verdict": "REJECTED"}),
        ("LOOP_BACK", "P7", {"loop": 1}),
        ("VERIFY_GATE", "P9", {"candidate_id": "C2-3d", "verdict": "ACCEPTED"}),
        ("RUN_END", "P10", {"message": "Completed P0–P10"}),
    ]
    for event_type, phase, payload in events:
        store.append_event("r1", event_type, payload, phase=phase)
    return store


def test_events_are_append_only(tmp_path):
    """Spec §10.2 episodic tier: the event log cannot be edited or deleted."""
    import sqlite3

    store = _seeded_store(tmp_path)
    for statement in ("UPDATE events SET type = 'X'", "DELETE FROM events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"), store._conn:
            store._conn.execute(statement)
    assert len(store.list_events("r1")) == 17
    store.close()


def test_list_events_filters_and_last_event(tmp_path):
    """Filters by type, phase and sequence keep order; last_event is the newest."""
    store = _seeded_store(tmp_path)
    assert [e.seq for e in store.list_events("r1", types=["VERIFY_GATE"])] == [14, 16]
    assert [e.type for e in store.list_events("r1", phase="P7")] == [
        "PHASE_START",
        "VERIFY_GATE",
        "LOOP_BACK",
    ]
    assert [e.seq for e in store.list_events("r1", after_seq=15)] == [16, 17]
    last = store.last_event("r1")
    assert last is not None and last.type == "RUN_END"
    assert store.last_event("missing") is None
    assert [r.run_id for r in store.list_runs()] == ["r1"]
    assert store.list_runs(status="paused") == []
    store.close()


def test_replay_rebuilds_the_trace(tmp_path):
    """AG-10: the trace is replayable from the events alone, in order."""
    store = _seeded_store(tmp_path)
    seen = []
    trace = store.replay("r1", on_event=lambda e: seen.append(e.seq))
    assert seen == list(range(1, 18)) and trace.events == 17
    assert trace.status == "completed" and trace.final_message == "Completed P0–P10"
    assert trace.phases == ["P0", "P1", "P7"]
    assert trace.decision_sources == {"policy": 1, "policy_fallback": 1}
    assert trace.tool_calls == [
        {
            "seq": 5,
            "phase": "P1",
            "id": "TR-001",
            "tool": "load_design_basis",
            "stub": True,
            "source": "agent",
        }
    ]
    assert trace.stub_tool_calls == 1 and trace.tool_errors == 1
    assert trace.decision_records == ["DR-01"] and trace.loop_backs == 1
    assert trace.last_verdict == "ACCEPTED" and len(trace.verdicts) == 2
    assert [a["assumption_id"] for a in trace.user_answers] == ["DB-31"]  # durable only
    assert trace.missing_seq == [] and trace.counts["PHASE_START"] == 3
    with pytest.raises(KeyError):
        store.replay("missing")
    store.close()


def test_replay_status_follows_pause_and_resume(tmp_path):
    """A run paused and not resumed replays as paused; an escalation ends it."""
    store = SQLiteEventStore(tmp_path / "e.sqlite")
    store.create_run("p", "paused", {})
    store.append_event("p", "PAUSE", {"request": {}}, phase="P1")
    assert store.replay("p").status == "paused"
    store.append_event("p", "RESUME", {}, phase="P1")
    assert store.replay("p").status == "running"
    store.append_event("p", "ESCALATE", {"message": "budget", "reason": "budget"}, phase="P5")
    trace = store.replay("p")
    assert trace.status == "escalated" and trace.final_message == "budget"
    assert store.replay("p").events == 3
    store.close()


def test_export_jsonl_round_trips(tmp_path):
    """events.jsonl holds one validated EventRecord per line, in order."""
    from gemini_agent.memory.event_store import EventRecord

    store = _seeded_store(tmp_path)
    path = store.export_jsonl("r1", tmp_path / "out" / "events.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [EventRecord.model_validate_json(line) for line in lines]
    assert [r.seq for r in records] == list(range(1, 18))
    assert records == store.list_events("r1")
    store.close()
