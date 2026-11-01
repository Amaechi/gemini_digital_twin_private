# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/logging_utils.py @ d69507a. Modified by GeoAgent
# team, 2026-11-01: context fields of the design workflow (run, phase, agent, tool, candidate),
# record timestamp instead of format time, configures the ``gemini_agent`` logger (not the root
# logger) with an optional per-run log file, idempotent.
"""Structured JSON application logs (spec §10.3).

One JSON object per line: ``timestamp`` (UTC, from the log record), ``severity``, ``logger``,
``message``, the workflow context passed with ``extra=`` (``run_id``, ``phase``, ``agent``,
``tool``, ``event_type``, ``step_no``, ``candidate_id``, ``decision_source``) and ``exception``.
LLM calls are logged separately by ``llm_logger`` (MEM-04); these lines are for operators.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

CONTEXT_FIELDS: tuple[str, ...] = (
    "run_id",
    "phase",
    "agent",
    "tool",
    "event_type",
    "step_no",
    "candidate_id",
    "decision_source",
)
"""``extra=`` keys copied into each JSON line when present."""

APP_LOGGER = "gemini_agent"
"""Logger configured by ``configure_logging`` (child loggers ``gemini_agent.*`` inherit it)."""

NOISY_LOGGERS: tuple[str, ...] = ("httpx", "httpcore", "openai")
"""Transport loggers kept at WARNING (successful HTTP calls to Ollama are noise)."""

_HANDLER_FLAG = "_gemini_agent_json"


class JsonFormatter(logging.Formatter):
    """Format a ``LogRecord`` as one compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the JSON line for ``record``."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(
    level: int | str = logging.INFO,
    *,
    log_file: str | Path | None = None,
    stream: TextIO | None = None,
    logger_name: str = APP_LOGGER,
) -> logging.Logger:
    """Send ``logger_name`` logs as JSON lines to ``stream`` (stderr) and optionally a file.

    Calling it again replaces the handlers it added before (no duplicated lines); other
    handlers are left alone, and the root logger is not touched.
    """
    logger = logging.getLogger(logger_name)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            logger.removeHandler(handler)
            handler.close()
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stderr)]
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        setattr(handler, _HANDLER_FLAG, True)
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return logger
