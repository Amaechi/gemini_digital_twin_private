"""Read-only JSON views of a run directory for the web Design Commander (FINAL_SPRINT §G).

Everything here reads: ``events.sqlite`` (opened read-only), ``run_summary.json``,
``decision_records/DR-*.json``, ``tool_results/*_verify_design.json``, ``figures/``,
``llm_calls.jsonl`` / ``token_summary.json``. Nothing is written, no file is created, and the
numbers shown are the tool outputs stored by the run (golden rule 1: the page computes nothing).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gemini_agent.logging.llm_logger import (
    LLM_CALLS_FILE,
    TOKEN_SUMMARY_FILE,
    LLMLogger,
)
from gemini_agent.memory.event_store import EventRecord, RunReplay, replay_events
from gemini_agent.runner.design_runner import APPROVAL_ID, STORE_FILE
from gemini_agent.runner.phases import PHASE_ORDER, PHASE_SPECS
from gemini_agent.schemas.decision import PHASE_TITLES

RUN_SUMMARY_FILE = "run_summary.json"
RECORDS_DIR = "decision_records"
TOOL_RESULTS_DIR = "tool_results"
FIGURES_DIR = "figures"
FIGURE_SUFFIXES = {".png": "image/png", ".md": "text/markdown", ".csv": "text/csv"}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
"""Run ids the API accepts: one path component, no leading dot (``runs/<id>/``)."""
_DETAIL_CHARS = 400
_DR_NUMBER = re.compile(r"(\d+)")


class UnknownRun(KeyError):
    """The run id is not a run directory under the runs root."""


def is_run_dir(path: Path) -> bool:
    """A directory holding a run: ``events.sqlite`` or ``run_summary.json``."""
    return path.is_dir() and ((path / STORE_FILE).exists() or (path / RUN_SUMMARY_FILE).exists())


def run_dir_for(runs_root: Path, run_id: str) -> Path:
    """Directory of ``run_id`` under ``runs_root`` (``UnknownRun`` if invalid or absent)."""
    if not RUN_ID_PATTERN.match(run_id):
        raise UnknownRun(run_id)
    run_dir = runs_root / run_id
    if not is_run_dir(run_dir):
        raise UnknownRun(run_id)
    return run_dir


# ----------------------------------------------------------------------------- raw readers
def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_store(run_dir: Path) -> tuple[dict[str, Any] | None, list[EventRecord]]:
    """The persisted run row (``state`` decoded) and its events, from ``events.sqlite`` read-only.

    Without the SQLite file (or with an empty one) the events come from ``events.jsonl``.
    """
    record: dict[str, Any] | None = None
    events: list[EventRecord] = []
    path = run_dir / STORE_FILE
    if path.exists():
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_dir.name,)
                ).fetchone()
                if row is None:  # a store whose run id differs from the directory name
                    row = conn.execute("SELECT * FROM runs ORDER BY created_at LIMIT 1").fetchone()
                if row is not None:
                    record = {
                        "run_id": row["run_id"],
                        "status": row["status"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "state": json.loads(row["state"]),
                    }
                    rows = conn.execute(
                        "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (row["run_id"],)
                    ).fetchall()
                    events = [
                        EventRecord(
                            run_id=r["run_id"],
                            seq=r["seq"],
                            ts=r["ts"],
                            type=r["type"],
                            phase=r["phase"],
                            payload=json.loads(r["payload"]),
                        )
                        for r in rows
                    ]
            except sqlite3.DatabaseError:  # missing tables, or not a SQLite file at all
                record, events = None, []
        finally:
            conn.close()
    if not events:
        jsonl = run_dir / "events.jsonl"
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        events.append(EventRecord.model_validate_json(line))
                    except ValueError:
                        continue
    return record, events


def _summary(run_dir: Path) -> dict[str, Any]:
    data = _load_json(run_dir / RUN_SUMMARY_FILE)
    return data if isinstance(data, dict) else {}


def _pending(state: dict[str, Any] | None) -> dict[str, Any] | None:
    pending = (state or {}).get("pending")
    if not isinstance(pending, dict):
        return None
    return {**pending, "is_approval": pending.get("request_id") == APPROVAL_ID}


# ----------------------------------------------------------------------------- run list
def list_runs(runs_root: Path) -> list[dict[str, Any]]:
    """Header of every run directory directly under ``runs_root`` (newest update first)."""
    runs: list[dict[str, Any]] = []
    if not runs_root.is_dir():
        return runs
    for child in sorted(runs_root.iterdir()):
        if not is_run_dir(child) or not RUN_ID_PATTERN.match(child.name):
            continue
        record, _ = read_store(child) if (child / STORE_FILE).exists() else (None, [])
        state = (record or {}).get("state") or {}
        summary = _summary(child)
        runs.append(
            {
                "run_id": child.name,
                "status": (record or {}).get("status") or summary.get("status") or "unknown",
                "phase": state.get("phase") or summary.get("phase"),
                "engine": state.get("engine") or summary.get("engine"),
                "model_tag": state.get("model_tag") or "",
                "selected_candidate": state.get("selected_id") or summary.get("selected_candidate"),
                "verdict": summary.get("verdict"),
                "approval": state.get("approval") or summary.get("approval"),
                "updated_at": (record or {}).get("updated_at") or "",
                "pending": _pending(state) is not None,
            }
        )
    runs.sort(key=lambda r: r["updated_at"], reverse=True)
    return runs


# ----------------------------------------------------------------------------- overview
def _llm_view(run_dir: Path) -> dict[str, Any]:
    source = None
    summary = _load_json(run_dir / TOKEN_SUMMARY_FILE)
    if isinstance(summary, dict) and summary.get("total"):
        source = TOKEN_SUMMARY_FILE
    elif (run_dir / LLM_CALLS_FILE).exists():
        summary = LLMLogger(run_dir, create=False).token_summary()
        source = LLM_CALLS_FILE
    else:
        summary = {}
    calls_logged = 0
    if (run_dir / LLM_CALLS_FILE).exists():
        with (run_dir / LLM_CALLS_FILE).open(encoding="utf-8") as handle:
            calls_logged = sum(1 for line in handle if line.strip())
    return {
        "source": source,
        "calls_logged": calls_logged,
        "total": summary.get("total") or {},
        "by_phase": summary.get("by_phase") or {},
        "by_decision_source": summary.get("by_decision_source") or {},
        "by_model": summary.get("by_model") or {},
    }


def _timeline(
    state: dict[str, Any] | None,
    status: str,
    current: str,
    replay: RunReplay,
    events: list[EventRecord],
    llm_by_phase: dict[str, Any],
    record_ids: list[str],
) -> list[dict[str, Any]]:
    progress = (state or {}).get("progress") or {}
    order = [phase.value for phase in PHASE_ORDER]
    current_index = order.index(current) if current in order else -1
    decisions = {p: 0 for p in order}
    tool_calls = {p: 0 for p in order}
    for event in events:
        if event.phase in decisions:
            if event.type == "DECISION":
                decisions[event.phase] += 1
            elif event.type == "TOOL_RESULT":
                tool_calls[event.phase] += 1
    rows: list[dict[str, Any]] = []
    for index, phase in enumerate(PHASE_ORDER):
        spec = PHASE_SPECS[phase]
        key = phase.value
        used = progress.get(key) or {}
        if status == "completed" or index < current_index:
            phase_status = "done"
        elif index == current_index:
            phase_status = "active" if status in ("running", "paused") else status
        else:
            phase_status = "pending"
        rows.append(
            {
                "phase": key,
                "title": PHASE_TITLES[phase],
                "objective": spec.objective,
                "role": spec.role.value,
                "status": phase_status,
                "passes": replay.phases.count(key),
                "budget": spec.budget.model_dump(),
                "used": {
                    "steps": int(used.get("steps_used", decisions[key])),
                    "tool_calls": int(used.get("tool_calls_used", tool_calls[key])),
                    "tokens": int(
                        used.get(
                            "tokens_used", (llm_by_phase.get(key) or {}).get("total_tokens", 0)
                        )
                    ),
                },
                "fallbacks": int(used.get("fallbacks", 0)),
                "llm_calls": int((llm_by_phase.get(key) or {}).get("calls", 0)),
                "decision_records": [d for d in spec.decision_records if d in record_ids],
                "completed_tools": list(used.get("completed_tools") or []),
            }
        )
    return rows


def run_overview(run_dir: Path) -> dict[str, Any]:
    """Header, phase timeline P0–P10 with budgets, LLM totals, pending question, answers."""
    record, events = read_store(run_dir)
    state = (record or {}).get("state")
    summary = _summary(run_dir)
    replay = replay_events(run_dir.name, events)
    status = (record or {}).get("status") or summary.get("status") or replay.status
    current = (
        (state or {}).get("phase")
        or summary.get("phase")
        or (replay.phases[-1] if replay.phases else "P0")
    )
    llm = _llm_view(run_dir)
    record_ids = [r["dr_id"] for r in run_records(run_dir)]
    pending = _pending(state)
    prompt = str((state or {}).get("user_prompt") or "")
    return {
        "run_id": run_dir.name,
        "status": status,
        "phase": current,
        "engine": (state or {}).get("engine") or summary.get("engine"),
        "model_tag": (state or {}).get("model_tag") or "",
        "input_mode": (state or {}).get("input_mode") or summary.get("input_mode"),
        "created_at": (record or {}).get("created_at") or replay.first_ts,
        "updated_at": (record or {}).get("updated_at") or replay.last_ts,
        "selected_candidate": (state or {}).get("selected_id") or summary.get("selected_candidate"),
        "verdict": summary.get("verdict") or replay.last_verdict,
        "approval": (state or {}).get("approval") or summary.get("approval"),
        "loop_count": (state or {}).get("loop_count", summary.get("loop_count", 0)),
        "llm_fallbacks": summary.get(
            "llm_fallbacks", replay.decision_sources.get("policy_fallback", 0)
        ),
        "final_summary": (state or {}).get("final_summary") or summary.get("final_summary") or "",
        "escalation_reason": (state or {}).get("escalation_reason")
        or summary.get("escalation_reason")
        or "",
        "user_prompt": prompt[:600] + ("…" if len(prompt) > 600 else ""),
        "pending": pending,
        "answers": summary.get("assumptions_and_answers")
        or [a.get("label") for a in (state or {}).get("answers") or []],
        "conflicts": (state or {}).get("conflicts") or summary.get("conflicts") or [],
        "missing_inputs": (state or {}).get("missing_inputs")
        or summary.get("missing_inputs")
        or [],
        "excluded_candidates": summary.get("excluded_candidates") or [],
        "timeline": _timeline(state, status, current, replay, events, llm["by_phase"], record_ids),
        "counts": {
            "events": replay.events,
            "tool_calls": len(replay.tool_calls),
            "stub_tool_calls": replay.stub_tool_calls,
            "tool_errors": replay.tool_errors,
            "decision_records": len(record_ids),
            "loop_backs": replay.loop_backs,
            "verify_gates": len(replay.verdicts),
        },
        "decision_sources": replay.decision_sources,
        "llm": llm,
        "figures": len(run_figures(run_dir)),
        "has_report": (run_dir / "report_draft.md").exists(),
    }


# ----------------------------------------------------------------------------- tool calls
def run_tools(run_dir: Path) -> list[dict[str, Any]]:
    """Tool calls in event order with the stub flag (``TOOL_RESULT`` / ``TOOL_ERROR`` events)."""
    record, events = read_store(run_dir)
    by_id: dict[str, dict[str, Any]] = {}
    for item in ((record or {}).get("state") or {}).get("tool_results") or []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.type not in ("TOOL_RESULT", "TOOL_ERROR"):
            continue
        payload = event.payload
        tool_id = str(payload.get("id") or "")
        extra = by_id.get(tool_id, {})
        rows.append(
            {
                "seq": event.seq,
                "ts": event.ts,
                "phase": event.phase,
                "id": tool_id,
                "tool": payload.get("tool") or extra.get("tool"),
                "ok": event.type == "TOOL_RESULT" and bool(extra.get("ok", True)),
                "stub": bool(payload.get("stub", extra.get("stub", False))),
                "source": payload.get("source") or extra.get("source"),
                "summary": str(payload.get("summary") or payload.get("message") or "")[:300],
                "error": payload.get("error") or extra.get("error"),
                "candidate_ids": list(extra.get("candidate_ids") or []),
                "warnings": len(extra.get("warnings") or []),
                "path": extra.get("path"),
                "loop": int(extra.get("loop", 0)),
            }
        )
    return rows


# ----------------------------------------------------------------------------- Decision Records
def _dr_key(dr_id: str) -> tuple[int, int, str]:
    numbers = _DR_NUMBER.findall(dr_id)
    return (1 if "-W" in dr_id else 0, int(numbers[0]) if numbers else 0, dr_id)


def run_records(run_dir: Path) -> list[dict[str, Any]]:
    """Decision Record JSON files (``decision_records/DR-*.json``), DR-01 … DR-11 then DR-W*."""
    folder = run_dir / RECORDS_DIR
    if not folder.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in folder.glob("DR-*.json"):
        data = _load_json(path)
        if isinstance(data, dict) and data.get("dr_id"):
            records.append(data)
    records.sort(key=lambda r: _dr_key(str(r["dr_id"])))
    return records


# ----------------------------------------------------------------------------- verifier
def _verifier_file(run_dir: Path, state: dict[str, Any] | None) -> Path | None:
    folder = run_dir / TOOL_RESULTS_DIR
    verifications = (state or {}).get("verifications") or []
    selected = (state or {}).get("selected_id")
    chosen = None
    for item in reversed(verifications):
        if isinstance(item, dict) and (selected is None or item.get("candidate_id") == selected):
            chosen = item
            break
    if chosen is None and verifications and isinstance(verifications[-1], dict):
        chosen = verifications[-1]
    if chosen is not None:
        tool_id = str(chosen.get("tool_result_id") or "")
        for item in (state or {}).get("tool_results") or []:
            if isinstance(item, dict) and item.get("id") == tool_id and item.get("path"):
                path = run_dir / str(item["path"])
                if path.is_file():
                    return path
        matches = sorted(folder.glob(f"{tool_id}_*.json")) if folder.is_dir() else []
        if matches:
            return matches[-1]
    matches = sorted(folder.glob("*_verify_design.json")) if folder.is_dir() else []
    return matches[-1] if matches else None


def run_verifier(run_dir: Path) -> dict[str, Any] | None:
    """Latest DesignVerifier report of the selected design: layers, checks with margins.

    Copied from the tool result as stored; the "tightest margin" is the verifier's own summary
    text (no margin is re-derived here).
    """
    record, _ = read_store(run_dir)
    path = _verifier_file(run_dir, (record or {}).get("state"))
    if path is None:
        return None
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return None
    data = doc.get("data") if isinstance(doc.get("data"), dict) else doc
    checks = [c for c in data.get("checks") or [] if isinstance(c, dict)]
    return {
        "file": path.name,
        "candidate_id": data.get("candidate_id"),
        "verdict": data.get("verdict"),
        "verifier": data.get("verifier"),
        "summary": data.get("summary") or doc.get("summary") or "",
        "violations": list(data.get("violations") or []),
        "not_evaluated": list(data.get("not_evaluated") or []),
        "layers": list(data.get("layers") or []),
        "checks": checks,
        "margins": data.get("margins") or {},
        "stub_evidence": bool(data.get("stub_evidence", False)),
        "assumption_ids": list(data.get("assumption_ids") or []),
    }


# ----------------------------------------------------------------------------- figures
def run_figures(run_dir: Path) -> list[dict[str, Any]]:
    """Files of ``figures/`` the page can show (PNG images, Markdown and CSV tables)."""
    folder = run_dir / FIGURES_DIR
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix in FIGURE_SUFFIXES and not path.name.startswith("."):
            rows.append(
                {
                    "name": path.name,
                    "kind": path.suffix.lstrip("."),
                    "content_type": FIGURE_SUFFIXES[path.suffix],
                    "bytes": path.stat().st_size,
                }
            )
    return rows


def figure_path(run_dir: Path, name: str) -> Path | None:
    """Path of a listed figure (None for anything not in ``run_figures``: no path tricks)."""
    if any(item["name"] == name for item in run_figures(run_dir)):
        return run_dir / FIGURES_DIR / name
    return None


# ----------------------------------------------------------------------------- events
def run_events(run_dir: Path, after_seq: int = 0) -> list[dict[str, Any]]:
    """Audit trail: every event after ``after_seq`` with a short human-readable detail."""
    _, events = read_store(run_dir)
    rows = []
    for event in events:
        if event.seq <= after_seq:
            continue
        payload = event.payload
        detail = payload.get("message") or payload.get("summary")
        if not isinstance(detail, str) or not detail:
            detail = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        rows.append(
            {
                "seq": event.seq,
                "ts": event.ts,
                "type": event.type,
                "phase": event.phase,
                "detail": detail[:_DETAIL_CHARS] + ("…" if len(detail) > _DETAIL_CHARS else ""),
            }
        )
    return rows
