"""Read-only run views on a copy of the fixture run ``runs/final_policy/`` (nothing is written)."""

import json
from pathlib import Path

import pytest

from gemini_agent.runner.phases import PHASE_ORDER
from gemini_agent.ui import run_view


def _mtimes(folder: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file()}


def test_list_runs_finds_the_fixture_run_and_only_run_dirs(fixture_runs, tmp_path):
    """Direct children with events.sqlite / run_summary.json are runs; others are skipped."""
    runs = {r["run_id"]: r for r in run_view.list_runs(fixture_runs)}
    assert "final_policy" in runs
    row = runs["final_policy"]
    assert row["status"] == "completed" and row["phase"] == "P10"
    assert row["selected_candidate"] == "C2-opt-robust" and row["verdict"] == "ACCEPTED"
    assert row["pending"] is False and row["engine"] == "policy"
    (tmp_path / "not_a_run").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "run_summary.json").write_text("{}")
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "run_summary.json").write_text(
        json.dumps({"status": "paused", "phase": "P1"})
    )
    assert [r["run_id"] for r in run_view.list_runs(tmp_path)] == ["r1"]
    assert run_view.list_runs(tmp_path / "missing") == []


def test_run_dir_for_rejects_bad_ids(fixture_runs):
    """Path tricks and unknown ids raise UnknownRun."""
    assert run_view.run_dir_for(fixture_runs, "final_policy") == fixture_runs / "final_policy"
    for bad in ("../final_policy", "..", ".hidden", "a/b", "nope", ""):
        with pytest.raises(run_view.UnknownRun):
            run_view.run_dir_for(fixture_runs, bad)


def test_overview_timeline_budgets_llm_and_counts(fixture_runs):
    """The overview mirrors run_summary.json and the phase budgets of the runner."""
    run = fixture_runs / "final_policy"
    before = _mtimes(run)
    overview = run_view.run_overview(run)
    summary = json.loads((run / "run_summary.json").read_text())
    assert overview["status"] == "completed" and overview["phase"] == "P10"
    assert overview["selected_candidate"] == summary["selected_candidate"]
    assert overview["verdict"] == summary["verdict"] and overview["approval"] == summary["approval"]
    assert overview["pending"] is None
    assert overview["counts"]["tool_calls"] == summary["tool_calls"] == 23
    assert overview["counts"]["stub_tool_calls"] == 0
    assert overview["counts"]["decision_records"] == len(summary["decision_records"]) == 11
    timeline = overview["timeline"]
    assert [row["phase"] for row in timeline] == [p.value for p in PHASE_ORDER]
    assert all(row["status"] == "done" for row in timeline)
    p5 = timeline[5]
    assert p5["title"] == "Screening" and p5["budget"]["max_steps"] == 12
    assert p5["used"]["tool_calls"] <= p5["budget"]["max_tool_calls"]
    assert set(p5["decision_records"]) >= {"DR-01", "DR-02"}
    assert overview["llm"]["total"].get("calls", 0) == 0  # policy engine: no LLM calls
    assert overview["answers"] and overview["figures"] > 0
    assert _mtimes(run) == before  # read-only


def test_tools_records_verifier_figures_events(fixture_runs):
    """Tool calls carry the stub flag; DR cards carry criterion + evidence + choice; the verifier
    report lists checks with margins; figures and events are listed."""
    run = fixture_runs / "final_policy"
    tools = run_view.run_tools(run)
    assert len(tools) == 23 and tools[0]["id"] == "TR-001"
    assert all(t["stub"] is False and t["ok"] for t in tools)
    assert {t["tool"] for t in tools} >= {"verify_design", "simulate_annual", "economics"}

    records = run_view.run_records(run)
    assert [r["dr_id"] for r in records] == [f"DR-{n:02d}" for n in range(1, 12)]
    dr01 = records[0]
    assert dr01["choice"] and dr01["criterion"]["text"] and dr01["evidence_ids"]
    assert any(o["id"] == dr01["choice"] for o in dr01["options"])

    verifier = run_view.run_verifier(run)
    assert verifier is not None and verifier["verdict"] == "ACCEPTED"
    assert verifier["candidate_id"] == "C2-opt-robust"  # latest gate of the selected design
    assert verifier["file"].endswith("_verify_design.json")
    checks = {c["check_id"]: c for c in verifier["checks"]}
    assert checks["PH-02"]["margin"] is not None and checks["PH-02"]["unit"] == "K"
    assert "tightest" in verifier["summary"]  # the verifier's own wording, not re-derived
    assert {layer["layer"] for layer in verifier["layers"]} >= {"syntactic", "physical"}

    figures = run_view.run_figures(run)
    names = {f["name"] for f in figures}
    assert {"fig_candidates.png", "verification.md", "candidates.csv"} <= names
    assert run_view.figure_path(run, "fig_candidates.png") is not None
    assert run_view.figure_path(run, "../run_summary.json") is None
    assert run_view.figure_path(run, "nope.png") is None

    events = run_view.run_events(run)
    assert events[0]["type"] == "RUN_START" and events[-1]["type"] == "RUN_END"
    assert run_view.run_events(run, after_seq=events[-1]["seq"] - 1) == events[-1:]
    assert all(len(e["detail"]) <= 401 for e in events)


def test_overview_without_sqlite_falls_back_to_jsonl_and_summary(fixture_runs, tmp_path):
    """A run directory with only events.jsonl + run_summary.json still renders."""
    run = fixture_runs / "final_policy"
    run_dir = tmp_path / "copy"
    run_dir.mkdir()
    for name in ("events.jsonl", "run_summary.json"):
        (run_dir / name).write_bytes((run / name).read_bytes())
    overview = run_view.run_overview(run_dir)
    assert overview["status"] == "completed" and overview["counts"]["events"] > 0
    assert overview["selected_candidate"] == "C2-opt-robust"
    assert run_view.run_tools(run_dir)[0]["tool"] == "load_design_basis"
    assert run_view.run_records(run_dir) == [] and run_view.run_verifier(run_dir) is None
    assert run_view.run_figures(run_dir) == []
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "events.sqlite").write_bytes(b"")
    assert run_view.run_overview(tmp_path / "empty")["status"] == "running"
    (tmp_path / "corrupt").mkdir()
    (tmp_path / "corrupt" / "events.sqlite").write_text("not a database")
    (tmp_path / "corrupt" / "run_summary.json").write_text(json.dumps({"status": "paused"}))
    assert run_view.run_overview(tmp_path / "corrupt")["status"] == "paused"
    assert [r["status"] for r in run_view.list_runs(tmp_path) if r["run_id"] == "corrupt"] == [
        "paused"
    ]
    odd = tmp_path / "q?x#y"
    odd.mkdir()
    (odd / "events.sqlite").write_bytes((run / "events.sqlite").read_bytes())
    assert run_view.read_store(odd)[1]  # URI special characters in the runs path are escaped
