"""LLM call log: one JSONL line per LLM call plus ``token_summary.json`` (spec §10.3, MEM-04/05).

minimal stub — WP-09 owns and extends (JSON formatter, naive-vs-optimised comparison, richer
Ollama token fields). WP-10 needs ``LLMCallRecord``, ``LLMLogger.log``, ``LLMLogger.records``,
``LLMLogger.token_summary`` and ``LLMLogger.write_token_summary``; keep these signatures.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, JsonValue

from gemini_agent.schemas.common import StrictModel

LLM_CALLS_FILE = "llm_calls.jsonl"
TOKEN_SUMMARY_FILE = "token_summary.json"


class LLMCallRecord(StrictModel):
    """One LLM call (one Pydantic AI agent run, including its retries)."""

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str
    agent: str
    phase: str
    model: str
    messages: list[JsonValue] = Field(default_factory=list, description="Full message history.")
    response: JsonValue = None
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    requests: int = Field(default=0, ge=0)
    latency_s: float = Field(default=0.0, ge=0)
    retries: int = Field(default=0, ge=0)
    decision_source: str
    error: str | None = None


def _add(bucket: dict[str, dict[str, float]], key: str, record: LLMCallRecord) -> None:
    entry = bucket.setdefault(
        key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0, "fallbacks": 0}
    )
    entry["calls"] += 1
    entry["input_tokens"] += record.input_tokens
    entry["output_tokens"] += record.output_tokens
    entry["retries"] += record.retries
    entry["fallbacks"] += int(record.decision_source == "policy_fallback")


class LLMLogger:
    """Appends ``LLMCallRecord`` lines to ``<run_dir>/llm_calls.jsonl``."""

    def __init__(self, run_dir: str | Path) -> None:
        """Create the run directory if needed; calls go to ``llm_calls.jsonl``."""
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / LLM_CALLS_FILE
        self._lock = threading.Lock()

    def log(self, record: LLMCallRecord) -> None:
        """Append one record (thread-safe)."""
        line = record.model_dump_json()
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def records(self) -> list[LLMCallRecord]:
        """All records logged so far for this run directory."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [LLMCallRecord.model_validate_json(line) for line in lines if line.strip()]

    def token_summary(self) -> dict[str, Any]:
        """Token totals overall, by agent and by phase (MEM-05)."""
        by_agent: dict[str, dict[str, float]] = {}
        by_phase: dict[str, dict[str, float]] = {}
        total: dict[str, dict[str, float]] = {}
        for record in self.records():
            _add(by_agent, record.agent, record)
            _add(by_phase, record.phase, record)
            _add(total, "all", record)
        empty = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0, "fallbacks": 0}
        return {"total": total.get("all", empty), "by_agent": by_agent, "by_phase": by_phase}

    def write_token_summary(self) -> Path:
        """Write ``token_summary.json`` next to the call log and return its path."""
        path = self.run_dir / TOKEN_SUMMARY_FILE
        path.write_text(json.dumps(self.token_summary(), indent=2), encoding="utf-8")
        return path
