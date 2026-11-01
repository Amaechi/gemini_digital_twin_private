"""Unit tests for the minimal LLM call logger (WP-09 extends it)."""

import json

from gemini_agent.logging.llm_logger import LLMCallRecord, LLMLogger


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
        "retries": 0,
        "fallbacks": 1,
    }
    assert summary["by_agent"]["requirements"]["calls"] == 2
    assert summary["by_phase"]["P2"]["input_tokens"] == 10
