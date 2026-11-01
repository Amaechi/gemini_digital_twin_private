"""Unit tests for the LLM call logger (spec §10.3, MEM-04/05/06) — offline, FunctionModel only."""

import json
from typing import Any

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from gemini_agent.logging.llm_logger import (
    LLMCallRecord,
    LLMLogger,
    compare_token_summaries,
    ollama_token_counts,
    write_token_comparison,
)


def _record(agent: str, phase: str, source: str = "llm", tokens: int = 10) -> LLMCallRecord:
    return LLMCallRecord(
        run_id="r1",
        agent=agent,
        phase=phase,
        model="fake",
        input_tokens=tokens,
        output_tokens=tokens // 2,
        decision_source=source,
    )


def test_jsonl_and_token_summary(tmp_path):
    """Records append to llm_calls.jsonl; token_summary.json aggregates by agent and phase."""
    logger = LLMLogger(tmp_path)
    assert logger.token_summary()["total"]["calls"] == 0
    logger.log(_record("requirements", "P1"))
    logger.log(_record("requirements", "P1", source="policy_fallback", tokens=20))
    logger.log(_record("subsurface", "P2"))
    assert len((tmp_path / "llm_calls.jsonl").read_text().splitlines()) == 3
    summary = json.loads(logger.write_token_summary().read_text())
    assert summary["total"] == {
        "calls": 3,
        "input_tokens": 40,
        "output_tokens": 20,
        "total_tokens": 60,
        "requests": 0,
        "retries": 0,
        "fallbacks": 1,
        "errors": 0,
        "latency_s": 0.0,
    }
    assert summary["by_agent"]["requirements"]["calls"] == 2
    assert summary["by_phase"]["P2"]["input_tokens"] == 10
    assert summary["by_agent_phase"]["requirements"]["P1"]["total_tokens"] == 45
    assert summary["by_decision_source"]["policy_fallback"]["calls"] == 1
    assert summary["by_model"]["fake"]["calls"] == 3 and summary["corrupt_lines"] == 0


def _scripted(messages: list[Any], info: AgentInfo) -> ModelResponse:
    """Call a tool, then answer invalidly once (retry), then answer validly.

    Counts earlier responses: the retry feedback reaches a FunctionModel as a user prompt.
    """
    responses = sum(isinstance(message, ModelResponse) for message in messages)
    if responses == 0:
        return ModelResponse(parts=[ToolCallPart(tool_name="lookup", args={"key": "DB-31"})])
    if responses == 1:
        return ModelResponse(parts=[TextPart("bad: state a number")])
    return ModelResponse(parts=[TextPart("use the register default")])


def _agent() -> Agent:
    agent = Agent(FunctionModel(_scripted, model_name="fake-llm"), output_type=str)

    @agent.tool_plain
    def lookup(key: str) -> str:
        """Return a register entry."""
        return f"{key}: district-heating return temperature"

    @agent.output_validator
    def no_bad(output: str) -> str:
        if output.startswith("bad"):
            raise ModelRetry("rationales must not state numbers")
        return output

    return agent


def test_function_model_run_is_logged_completely(tmp_path):
    """MEM-04 with a Pydantic AI FunctionModel: every required field lands in the JSONL line."""
    result = _agent().run_sync("Which return temperature should P1 assume?")
    usage = result.usage
    logger = LLMLogger(tmp_path)
    record = logger.log_messages(
        run_id="r1",
        agent="requirements",
        phase="P1",
        model="fake-llm",
        messages=result.all_messages(),
        output=result.output,
        usage=usage,
        latency_s=0.0123456,
        decision_source="llm",
    )
    (line,) = (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    logged = json.loads(line)
    for key in (
        "timestamp",
        "agent",
        "phase",
        "model",
        "messages",
        "response",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "latency_s",
        "retries",
        "decision_source",
    ):
        assert key in logged
    assert logged["tool_calls"] == [{"tool": "lookup", "args": {"key": "DB-31"}}]
    assert logged["retries"] == 1 and logged["requests"] == 3
    assert logged["response"] == "use the register default"
    assert logged["input_tokens"] == usage.input_tokens > 0
    assert logged["output_tokens"] == usage.output_tokens > 0
    assert logged["latency_s"] == 0.0123 and logged["token_source"] == "usage"
    assert "Which return temperature" in json.dumps(logged["messages"])
    assert logger.records() == [record]
    summary = logger.token_summary()
    assert summary["total"]["retries"] == 1 and summary["total"]["requests"] == 3


def test_tokens_from_messages_without_usage(tmp_path):
    """Without RunUsage the per-response usage in the messages is summed."""
    result = _agent().run_sync("prompt")
    usage = result.usage
    record = LLMCallRecord.from_messages(
        run_id="r1",
        agent="requirements",
        phase="P1",
        model="fake-llm",
        messages=result.all_messages(),
        decision_source="policy_fallback",
        error="EngineFailure: x",
    )
    assert record.token_source == "messages" and record.requests == 3
    assert (record.input_tokens, record.output_tokens) == (usage.input_tokens, usage.output_tokens)
    empty = LLMCallRecord.from_messages(
        run_id="r1", agent="a", phase="P0", model="m", messages=[], decision_source="escalated"
    )
    assert empty.token_source == "none" and empty.total_tokens == 0


def test_ollama_token_counts():
    """Native Ollama prompt_eval_count/eval_count, or the OpenAI-compatible usage block."""
    assert ollama_token_counts({"prompt_eval_count": 812, "eval_count": 64}) == (812, 64)
    assert ollama_token_counts({"usage": {"prompt_tokens": 9, "completion_tokens": 3}}) == (9, 3)
    assert ollama_token_counts({"message": {}}) is None
    record = LLMCallRecord.from_messages(
        run_id="r1",
        agent="a",
        phase="P1",
        model="qwen",
        messages=[],
        decision_source="llm",
        ollama_raw={"prompt_eval_count": 812, "eval_count": 64},
    )
    assert (record.input_tokens, record.output_tokens, record.token_source) == (812, 64, "ollama")


def test_corrupt_line_is_skipped_and_counted(tmp_path):
    """A half-written line (crash) does not break the summary; it is reported."""
    logger = LLMLogger(tmp_path)
    logger.log(_record("requirements", "P1"))
    with (tmp_path / "llm_calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"run_id": "r1", "agent": \n')
    summary = logger.token_summary()
    assert summary["total"]["calls"] == 1 and summary["corrupt_lines"] == 1


def test_naive_vs_optimised_comparison(tmp_path):
    """MEM-06: savings in total and per agent/phase, written as JSON."""
    naive, optimised = LLMLogger(tmp_path / "naive"), LLMLogger(tmp_path / "opt")
    naive.log(_record("requirements", "P1", tokens=100))
    naive.log(_record("subsurface", "P2", tokens=100))
    optimised.log(_record("requirements", "P1", tokens=40))
    comparison = compare_token_summaries(naive.token_summary(), optimised.token_summary())
    assert comparison["total"]["naive_tokens"] == 300
    assert comparison["total"]["optimised_tokens"] == 60
    assert comparison["total"]["saved_frac"] == 0.8
    assert comparison["by_agent"]["subsurface"]["optimised_tokens"] == 0
    assert comparison["by_phase"]["P1"]["saved_tokens"] == 90
    path = write_token_comparison(tmp_path / "naive", tmp_path / "opt", tmp_path / "cmp.json")
    assert json.loads(path.read_text())["total"]["saved_tokens"] == 240


def test_reading_a_missing_run_creates_nothing(tmp_path):
    """create=False only reads; comparing with a missing run does not make its directory."""
    reader = LLMLogger(tmp_path / "absent", create=False)
    assert reader.records() == [] and not (tmp_path / "absent").exists()
    write_token_comparison(tmp_path / "absent", tmp_path / "absent2", tmp_path / "cmp.json")
    assert not (tmp_path / "absent").exists() and not (tmp_path / "absent2").exists()


def test_usage_method_is_called():
    """An older Pydantic AI ``result.usage()`` bound method still yields the token counts."""

    class Usage:
        input_tokens, output_tokens, requests = 7, 3, 1

    record = LLMCallRecord.from_messages(
        run_id="r",
        agent="a",
        phase="P1",
        model="m",
        messages=[],
        decision_source="llm",
        usage=lambda: Usage(),
    )
    assert (record.input_tokens, record.output_tokens, record.token_source) == (7, 3, "usage")
