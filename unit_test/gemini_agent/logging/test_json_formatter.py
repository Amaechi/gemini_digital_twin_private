"""Unit tests for gemini_agent.logging.json_formatter (spec §10.3)."""

import io
import json
import logging
import sys

from gemini_agent.logging import json_formatter as jf


def test_formatter_emits_context_and_exception():
    """One JSON object with severity, message, workflow context and the exception text."""
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "gemini_agent.runner",
        logging.ERROR,
        __file__,
        1,
        "tool %s failed",
        ("economics",),
        exc_info,
    )
    record.run_id, record.phase, record.tool = "r1", "P5", "economics"
    line = json.loads(jf.JsonFormatter().format(record))
    assert line["severity"] == "ERROR" and line["message"] == "tool economics failed"
    assert (line["run_id"], line["phase"], line["tool"]) == ("r1", "P5", "economics")
    assert "agent" not in line and "ValueError: boom" in line["exception"]
    assert line["timestamp"].endswith("+00:00")


def test_configure_logging_is_idempotent_and_writes_file(tmp_path):
    """Re-configuring replaces our handlers; the run log file holds JSON lines."""
    stream = io.StringIO()
    jf.configure_logging(stream=stream)
    logger = jf.configure_logging("DEBUG", stream=stream, log_file=tmp_path / "r1" / "agent.log")
    assert logger.name == jf.APP_LOGGER and len(logger.handlers) == 2 and not logger.propagate
    logging.getLogger("gemini_agent.verify").debug("checked", extra={"candidate_id": "C2-2d"})
    for handler in logger.handlers:
        handler.flush()
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["candidate_id"] == "C2-2d"
    stored = (tmp_path / "r1" / "agent.log").read_text(encoding="utf-8").splitlines()
    assert json.loads(stored[0])["logger"] == "gemini_agent.verify"
    assert logging.getLogger("httpx").level == logging.WARNING
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
