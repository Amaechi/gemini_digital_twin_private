"""LLM call log: one JSONL line per LLM call plus ``token_summary.json`` (spec §10.3, MEM-04/05).

Every LLM call (one Pydantic AI agent run, including its output retries) becomes one
``LLMCallRecord`` in ``runs/<id>/llm_calls.jsonl``: timestamp, agent, phase, model, the full
message history (prompts and responses), the structured response, tool calls, input/output
tokens, requests, latency, retries and the decision source (``llm``, ``policy_fallback``,
``escalated``). ``token_summary.json`` aggregates the calls overall, by agent, by phase, by
agent × phase, by decision source and by model (MEM-05). ``compare_token_summaries`` gives the
naive-vs-optimised comparison of MEM-06.

Token counts come from Pydantic AI's ``RunUsage`` (``result.usage``; with Ollama's OpenAI
endpoint these are its ``prompt_eval_count``/``eval_count``); without it they are summed from the
per-response usage in the messages; a raw Ollama response can be read with
``ollama_token_counts``. The helpers ``messages_json`` / ``retries_and_tool_calls`` /
``token_counts`` are shared with ``agents/engine.py`` so all agents log the same way.

WP-10 relies on ``LLMCallRecord``, ``LLMLogger(run_dir)``, ``log``, ``records``,
``token_summary`` and ``write_token_summary``; their signatures are stable.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from gemini_agent.schemas.common import StrictModel

LLM_CALLS_FILE = "llm_calls.jsonl"
TOKEN_SUMMARY_FILE = "token_summary.json"
TOKEN_COMPARISON_FILE = "token_comparison.json"

RETRY_PARTS = frozenset({"RetryPromptPart", "RetryFeedbackPart"})
"""Pydantic AI part classes that carry a validation error back to the model."""

TokenSource = Literal["usage", "messages", "ollama", "none"]
"""Where the token counts of a record came from."""


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
    token_source: TokenSource = "usage"

    @property
    def total_tokens(self) -> int:
        """Input plus output tokens."""
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_messages(
        cls,
        *,
        run_id: str,
        agent: str,
        phase: str,
        model: str,
        messages: Sequence[Any],
        decision_source: str,
        output: Any = None,
        usage: Any = None,
        latency_s: float = 0.0,
        error: str | None = None,
        ollama_raw: Mapping[str, Any] | None = None,
    ) -> LLMCallRecord:
        """Build a record from a Pydantic AI message history (and ``RunUsage`` if available).

        ``ollama_raw`` (a native Ollama response) overrides the token counts when given.
        """
        message_list = list(messages)
        retries, calls = retries_and_tool_calls(message_list)
        input_tokens, output_tokens, requests, source = token_counts(message_list, usage)
        if ollama_raw is not None:
            counted = ollama_token_counts(ollama_raw)
            if counted is not None:
                input_tokens, output_tokens = counted
                source = "ollama"
        if isinstance(output, BaseModel):
            response: JsonValue = output.model_dump(mode="json")
        elif output is None or isinstance(output, str | int | float | bool):
            response = output
        else:
            response = str(output)
        return cls(
            run_id=run_id,
            agent=agent,
            phase=phase,
            model=model,
            messages=messages_json(message_list),
            response=response,
            tool_calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            requests=requests,
            latency_s=round(max(latency_s, 0.0), 4),
            retries=retries,
            decision_source=decision_source,
            error=error,
            token_source=source,
        )


# --------------------------------------------------------------------------- message helpers
def messages_json(messages: Sequence[Any]) -> list[Any]:
    """Return Pydantic AI messages as JSON (falls back to ``str`` per message; never raises)."""
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        return list(ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"))
    except Exception:  # noqa: BLE001 - logging must never break a run
        return [str(message) for message in messages]


def retries_and_tool_calls(messages: Sequence[Any]) -> tuple[int, list[dict[str, Any]]]:
    """Count validation retries and list the tool calls (name + args) in a message history."""
    from pydantic_ai.messages import ToolCallPart

    retries = 0
    calls: list[dict[str, Any]] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if type(part).__name__ in RETRY_PARTS:
                retries += 1
            elif isinstance(part, ToolCallPart):
                calls.append({"tool": part.tool_name, "args": part.args_as_dict()})
    return retries, calls


def token_counts(messages: Sequence[Any], usage: Any = None) -> tuple[int, int, int, TokenSource]:
    """Return ``(input, output, requests, source)`` from ``RunUsage`` or the responses' usage."""
    if callable(usage) and not hasattr(usage, "input_tokens"):  # a bound ``result.usage``
        usage = usage()
    if usage is not None:
        return (
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "requests", 0) or 0),
            "usage",
        )
    input_tokens = output_tokens = requests = 0
    for message in messages:
        msg_usage = getattr(message, "usage", None)
        if msg_usage is not None:
            input_tokens += int(getattr(msg_usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(msg_usage, "output_tokens", 0) or 0)
            requests += 1
    return input_tokens, output_tokens, requests, "messages" if requests else "none"


def ollama_token_counts(raw: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return (input, output) tokens of a raw Ollama response.

    Native ``/api/chat``: ``prompt_eval_count`` / ``eval_count``; OpenAI-compatible endpoint:
    ``usage.prompt_tokens`` / ``usage.completion_tokens``. None if neither is present.
    """
    if "prompt_eval_count" in raw or "eval_count" in raw:
        return int(raw.get("prompt_eval_count") or 0), int(raw.get("eval_count") or 0)
    usage = raw.get("usage")
    if isinstance(usage, Mapping) and ("prompt_tokens" in usage or "completion_tokens" in usage):
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return None


# --------------------------------------------------------------------------- aggregation
_COUNTERS = (
    "calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "requests",
    "retries",
    "fallbacks",
    "errors",
)


def _empty() -> dict[str, float]:
    return {**{key: 0 for key in _COUNTERS}, "latency_s": 0.0}


def _add(bucket: dict[str, dict[str, float]], key: str, record: LLMCallRecord) -> None:
    entry = bucket.setdefault(key, _empty())
    entry["calls"] += 1
    entry["input_tokens"] += record.input_tokens
    entry["output_tokens"] += record.output_tokens
    entry["total_tokens"] += record.total_tokens
    entry["requests"] += record.requests
    entry["retries"] += record.retries
    entry["fallbacks"] += int(record.decision_source == "policy_fallback")
    entry["errors"] += int(record.error is not None)
    entry["latency_s"] = round(entry["latency_s"] + record.latency_s, 4)


class LLMLogger:
    """Appends ``LLMCallRecord`` lines to ``<run_dir>/llm_calls.jsonl``."""

    def __init__(self, run_dir: str | Path, *, create: bool = True) -> None:
        """Log to ``<run_dir>/llm_calls.jsonl``; ``create=False`` only reads (no mkdir)."""
        self.run_dir = Path(run_dir)
        if create:
            self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / LLM_CALLS_FILE
        self._lock = threading.Lock()
        self.corrupt_lines = 0

    def log(self, record: LLMCallRecord) -> None:
        """Append one record (thread-safe)."""
        line = record.model_dump_json()
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def log_messages(self, **kwargs: Any) -> LLMCallRecord:
        """Build a record with ``LLMCallRecord.from_messages(**kwargs)``, log and return it."""
        record = LLMCallRecord.from_messages(**kwargs)
        self.log(record)
        return record

    def records(self) -> list[LLMCallRecord]:
        """All records logged so far (a corrupt line, e.g. from a crash, is skipped and counted)."""
        if not self.path.exists():
            return []
        records, corrupt = [], 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(LLMCallRecord.model_validate_json(line))
            except ValidationError:
                corrupt += 1
        self.corrupt_lines = corrupt
        return records

    def token_summary(self) -> dict[str, Any]:
        """Token totals overall, by agent, phase, agent × phase, decision source, model (MEM-05)."""
        by_agent: dict[str, dict[str, float]] = {}
        by_phase: dict[str, dict[str, float]] = {}
        by_source: dict[str, dict[str, float]] = {}
        by_model: dict[str, dict[str, float]] = {}
        by_agent_phase: dict[str, dict[str, dict[str, float]]] = {}
        total: dict[str, dict[str, float]] = {}
        for record in self.records():
            _add(by_agent, record.agent, record)
            _add(by_phase, record.phase, record)
            _add(by_source, record.decision_source, record)
            _add(by_model, record.model, record)
            _add(by_agent_phase.setdefault(record.agent, {}), record.phase, record)
            _add(total, "all", record)
        return {
            "total": total.get("all", _empty()),
            "by_agent": by_agent,
            "by_phase": by_phase,
            "by_agent_phase": by_agent_phase,
            "by_decision_source": by_source,
            "by_model": by_model,
            "corrupt_lines": self.corrupt_lines,
        }

    def write_token_summary(self) -> Path:
        """Write ``token_summary.json`` next to the call log and return its path."""
        path = self.run_dir / TOKEN_SUMMARY_FILE
        path.write_text(json.dumps(self.token_summary(), indent=2), encoding="utf-8")
        return path


# --------------------------------------------------------------------------- MEM-06 comparison
def _saving(naive: Mapping[str, Any], optimised: Mapping[str, Any]) -> dict[str, float]:
    before = float(naive.get("total_tokens", 0) or 0) or float(
        (naive.get("input_tokens", 0) or 0) + (naive.get("output_tokens", 0) or 0)
    )
    after = float(optimised.get("total_tokens", 0) or 0) or float(
        (optimised.get("input_tokens", 0) or 0) + (optimised.get("output_tokens", 0) or 0)
    )
    return {
        "naive_tokens": before,
        "optimised_tokens": after,
        "saved_tokens": before - after,
        "saved_frac": round((before - after) / before, 4) if before else 0.0,
        "naive_calls": float(naive.get("calls", 0) or 0),
        "optimised_calls": float(optimised.get("calls", 0) or 0),
    }


def compare_token_summaries(
    naive: Mapping[str, Any], optimised: Mapping[str, Any]
) -> dict[str, Any]:
    """Naive-vs-optimised token comparison (MEM-06) of two ``token_summary`` dicts.

    Returns the total saving and the saving per agent and per phase (keys from either run).
    """
    result: dict[str, Any] = {"total": _saving(naive.get("total", {}), optimised.get("total", {}))}
    for group in ("by_agent", "by_phase"):
        left, right = naive.get(group, {}), optimised.get(group, {})
        result[group] = {
            key: _saving(left.get(key, {}), right.get(key, {}))
            for key in sorted(set(left) | set(right))
        }
    return result


def write_token_comparison(
    naive_run_dir: str | Path, optimised_run_dir: str | Path, out_path: str | Path
) -> Path:
    """Compare two runs' call logs and write ``token_comparison.json``-style output."""
    comparison = compare_token_summaries(
        LLMLogger(naive_run_dir, create=False).token_summary(),
        LLMLogger(optimised_run_dir, create=False).token_summary(),
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    return path
