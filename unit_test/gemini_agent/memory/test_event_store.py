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
