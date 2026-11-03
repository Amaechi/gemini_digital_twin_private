"""``cli serve``: the web Design Commander on the standard library's ``http.server``.

Routes (all JSON unless noted; GET is read-only, nothing is cached):

- ``GET /`` and ``GET /static/<file>`` — the page adapted from Plant Debugger's Commander shell.
- ``GET /api/runs`` — run list of the runs directory.
- ``GET /api/runs/<id>`` — overview: header, phase timeline P0–P10 with budgets, LLM totals,
  pending question; ``/tools``, ``/records``, ``/verifier``, ``/figures``, ``/events?after=N``
  give the tool calls (stub flag), Decision Records, verifier report with margins, figure list
  and audit trail; ``/figures/<name>`` streams one figure file, ``/report`` the report draft.
- ``POST /api/runs/<id>/answer`` ``{"answer": "..."}`` — answers the pending question through
  the runner's own ``resume`` (same validation as ``cli resume``: 400 on an invalid answer,
  409 when the run is not paused). ``POST /api/runs/<id>/approve``
  ``{"decision": "approve" | "reject"}`` is the P10 approval (409 unless the pending question is
  the approval). These two are the only routes that write, and they write only what ``resume``
  writes into ``runs/<id>/``.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gemini_agent.runner.design_runner import DesignRunner, InvalidAnswer, RunResult
from gemini_agent.ui import run_view

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = "commander.html"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024
_APPROVE_ANSWERS = {"approve": "yes", "approved": "yes", "yes": "yes", "reject": "no", "no": "no"}

RunnerFactory = Callable[[str], DesignRunner]
"""Builds the runner that resumes ``run_id`` (the CLI's ``resume_runner``); closed after use."""


@dataclass
class CommanderApp:
    """Request logic behind the handler: paths → status code + JSON (testable without sockets)."""

    runs_root: Path
    runner_factory: RunnerFactory | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    # -------------------------------------------------------------------- GET
    def get(self, path: str, query: dict[str, list[str]]) -> tuple[int, Any, str]:
        """Return ``(status, body, content_type)``; ``body`` is JSON-able unless bytes."""
        parts = [p for p in path.split("/") if p]
        if parts == ["api", "runs"]:
            return HTTPStatus.OK, run_view.list_runs(self.runs_root), "application/json"
        if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
            try:
                run_dir = run_view.run_dir_for(self.runs_root, parts[2])
            except run_view.UnknownRun:
                return (
                    HTTPStatus.NOT_FOUND,
                    {"error": f"unknown run '{parts[2]}'"},
                    "application/json",
                )
            rest = parts[3:]
            if not rest:
                return HTTPStatus.OK, run_view.run_overview(run_dir), "application/json"
            if rest == ["tools"]:
                return HTTPStatus.OK, run_view.run_tools(run_dir), "application/json"
            if rest == ["records"]:
                return HTTPStatus.OK, run_view.run_records(run_dir), "application/json"
            if rest == ["verifier"]:
                return HTTPStatus.OK, run_view.run_verifier(run_dir), "application/json"
            if rest == ["figures"]:
                return HTTPStatus.OK, run_view.run_figures(run_dir), "application/json"
            if rest == ["report"]:
                report = run_dir / "report_draft.md"
                if not report.is_file():
                    return HTTPStatus.NOT_FOUND, {"error": "no report_draft.md"}, "application/json"
                return HTTPStatus.OK, report.read_bytes(), "text/markdown; charset=utf-8"
            if rest == ["events"]:
                after = _int(query.get("after", ["0"])[0])
                return HTTPStatus.OK, run_view.run_events(run_dir, after), "application/json"
            if len(rest) == 2 and rest[0] == "figures":
                figure = run_view.figure_path(run_dir, rest[1])
                if figure is None:
                    return HTTPStatus.NOT_FOUND, {"error": "unknown figure"}, "application/json"
                return HTTPStatus.OK, figure.read_bytes(), run_view.FIGURE_SUFFIXES[figure.suffix]
            return HTTPStatus.NOT_FOUND, {"error": "unknown route"}, "application/json"
        if parts and parts[0] == "api":
            return HTTPStatus.NOT_FOUND, {"error": "unknown route"}, "application/json"
        return self._static(parts)

    @staticmethod
    def _static(parts: list[str]) -> tuple[int, Any, str]:
        name = INDEX_FILE if not parts else parts[-1] if parts[0] == "static" else ""
        if parts and parts[0] != "static":
            return HTTPStatus.NOT_FOUND, {"error": "not found"}, "application/json"
        target = STATIC_DIR / name
        if (
            not name
            or len(parts) > 2
            or name.startswith(".")
            or not target.is_file()
            or target.parent != STATIC_DIR
        ):
            return HTTPStatus.NOT_FOUND, {"error": "not found"}, "application/json"
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        return HTTPStatus.OK, target.read_bytes(), content_type

    # -------------------------------------------------------------------- POST
    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        """Answer / approve through the runner's ``resume``; returns ``(status, json)``."""
        parts = [p for p in path.split("/") if p]
        if len(parts) != 4 or parts[:2] != ["api", "runs"] or parts[3] not in ("answer", "approve"):
            return HTTPStatus.NOT_FOUND, {"error": "unknown route"}
        run_id = parts[2]
        try:
            run_dir = run_view.run_dir_for(self.runs_root, run_id)
        except run_view.UnknownRun:
            return HTTPStatus.NOT_FOUND, {"error": f"unknown run '{run_id}'"}
        if self.runner_factory is None:
            return HTTPStatus.NOT_IMPLEMENTED, {"error": "this server is read-only"}
        if parts[3] == "approve":
            pending = run_view.run_overview(run_dir)["pending"]
            if pending is None or not pending.get("is_approval"):
                return HTTPStatus.CONFLICT, {
                    "error": "no P10 approval is pending for this run",
                    "pending": pending,
                }
            decision = str(body.get("decision", "")).strip().lower()
            if decision not in _APPROVE_ANSWERS:
                return HTTPStatus.BAD_REQUEST, {"error": "decision must be 'approve' or 'reject'"}
            answer = _APPROVE_ANSWERS[decision]
        else:
            answer = str(body.get("answer", "") or "")
        with self.lock:
            runner = self.runner_factory(run_id)
            try:
                result = runner.resume(run_id, answer)
            except InvalidAnswer as exc:
                return HTTPStatus.BAD_REQUEST, {"error": f"invalid answer: {exc}"}
            except (KeyError, ValueError) as exc:
                return HTTPStatus.CONFLICT, {"error": str(exc)}
            finally:
                runner.close()
        return HTTPStatus.OK, _result_json(result)


def _result_json(result: RunResult) -> dict[str, Any]:
    pending = result.pending.model_dump(mode="json") if result.pending is not None else None
    if pending is not None:
        pending["is_approval"] = result.pending.request_id == "APPROVAL"
    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "phase": result.phase.value,
        "verdict": result.verdict,
        "selected_candidate": result.selected_id,
        "approval": result.approval,
        "summary": result.summary,
        "pending": pending,
        "decision_records": list(result.decision_records),
    }


def is_loopback(host: str) -> bool:
    """True for 127.0.0.0/8, ::1 and 'localhost' (the only safe binds for the writing routes)."""
    return host in ("localhost", "::1") or host.startswith("127.")


def _int(text: str) -> int:
    try:
        return max(0, int(text))
    except ValueError:
        return 0


def make_handler(app: CommanderApp, *, quiet: bool = True) -> type[BaseHTTPRequestHandler]:
    """Bind ``app`` to a ``BaseHTTPRequestHandler`` subclass."""

    class CommanderHandler(BaseHTTPRequestHandler):
        server_version = "GeoAgentCommander/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            if not quiet:
                super().log_message(format, *args)

        def _send(self, status: int, body: Any, content_type: str) -> None:
            data = (
                body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            try:
                status, body, content_type = app.get(url.path, parse_qs(url.query))
            except Exception as exc:  # noqa: BLE001 - the page shows the error instead
                status, body, content_type = (
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"{type(exc).__name__}: {exc}"},
                    "application/json",
                )
            self._send(status, body, content_type)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            length = _int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self._send(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "body too large"},
                    "application/json",
                )
                return
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(body, dict):
                    raise TypeError("JSON object expected")
            except (ValueError, TypeError) as exc:
                self._send(
                    HTTPStatus.BAD_REQUEST, {"error": f"bad JSON body: {exc}"}, "application/json"
                )
                return
            try:
                status, payload = app.post(urlsplit(self.path).path, body)
            except Exception as exc:  # noqa: BLE001 - the page shows the error instead
                status, payload = HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": f"{type(exc).__name__}: {exc}"
                }
            self._send(status, payload, "application/json")

    return CommanderHandler


def make_server(
    runs_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    runner_factory: RunnerFactory | None = None,
    quiet: bool = True,
) -> ThreadingHTTPServer:
    """Create (bind) the server; ``port=0`` picks a free port (``server.server_address``)."""
    app = CommanderApp(runs_root=Path(runs_root), runner_factory=runner_factory)
    server = ThreadingHTTPServer((host, port), make_handler(app, quiet=quiet))
    server.daemon_threads = True
    return server


def serve(
    runs_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    runner_factory: RunnerFactory | None = None,
    quiet: bool = True,
) -> None:
    """Serve until Ctrl-C."""
    server = make_server(
        runs_root, host=host, port=port, runner_factory=runner_factory, quiet=quiet
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
