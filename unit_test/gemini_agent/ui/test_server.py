"""HTTP tests of the Design Commander server on an ephemeral port (``http.client``, offline).

The answer/approve round trip runs a paused policy smoke run with the stub tool catalogue
(``stub_tools``) in a temporary runs directory; the read-only routes use a temp copy of the
fixture run ``runs/final_policy/`` (``conftest.fixture_runs``).
"""

import http.client
import json
import threading
from pathlib import Path

import pytest

from gemini_agent import cli
from gemini_agent.ui import run_view, server

pytestmark = pytest.mark.usefixtures("stub_tools")


class Client:
    """Minimal HTTP client bound to one server."""

    def __init__(self, httpd):
        self.host, self.port = httpd.server_address[:2]

    def request(self, method, path, body=None, raw=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=120)
        headers = {}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type") or ""
        conn.close()
        if content_type.startswith("application/json"):
            return response.status, json.loads(payload or b"null"), content_type
        return response.status, payload, content_type

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None, raw=None):
        return self.request("POST", path, body=body, raw=raw)[:2]


@pytest.fixture
def served():
    """Start a server on a free port in a thread; yields ``(httpd, client)``; stops it after."""
    started = []

    def start(runs_root, runner_factory=None):
        httpd = server.make_server(runs_root, port=0, runner_factory=runner_factory)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return Client(httpd)

    yield start
    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_static_files_and_api_on_the_fixture_run(served, fixture_runs):
    """The page, its assets and every read-only API route are served; path tricks are 404."""
    client = served(fixture_runs)
    status, body, content_type = client.get("/")
    assert status == 200 and content_type.startswith("text/html")
    assert b"GEOAGENT" in body and b"/static/commander.js" in body
    assert client.get("/static/commander.css")[0] == 200
    status, body, content_type = client.get("/static/commander.js")
    assert status == 200 and b"/api/runs" in body
    assert client.request("HEAD", "/")[0] == 200
    for bad in ("/static/../server.py", "/static/nope.css", "/static/.hidden", "/other", "/api/x"):
        assert client.get(bad)[0] == 404, bad

    status, runs, _ = client.get("/api/runs")
    assert status == 200 and "final_policy" in {r["run_id"] for r in runs}
    status, overview, _ = client.get("/api/runs/final_policy")
    assert status == 200 and overview["status"] == "completed"
    assert len(overview["timeline"]) == 11 and overview["pending"] is None
    assert client.get("/api/runs/final_policy/tools")[1][0]["id"] == "TR-001"
    assert client.get("/api/runs/final_policy/records")[1][0]["dr_id"] == "DR-01"
    assert client.get("/api/runs/final_policy/verifier")[1]["verdict"] == "ACCEPTED"
    status, figures, _ = client.get("/api/runs/final_policy/figures")
    assert status == 200 and any(f["name"] == "fig_candidates.png" for f in figures)
    status, png, content_type = client.get("/api/runs/final_policy/figures/fig_candidates.png")
    assert status == 200 and content_type == "image/png" and png[:4] == b"\x89PNG"
    status, md, content_type = client.get("/api/runs/final_policy/figures/verification.md")
    assert status == 200 and content_type.startswith("text/markdown") and b"Verdict" in md
    assert client.get("/api/runs/final_policy/figures/..%2Frun_summary.json")[0] == 404
    assert client.get("/api/runs/final_policy/figures/nope.png")[0] == 404
    status, report, content_type = client.get("/api/runs/final_policy/report")
    assert status == 200 and content_type.startswith("text/markdown") and report
    status, events, _ = client.get("/api/runs/final_policy/events?after=0")
    assert status == 200 and events[0]["type"] == "RUN_START"
    last = events[-1]["seq"]
    assert client.get(f"/api/runs/final_policy/events?after={last}")[1] == []
    assert client.get("/api/runs/final_policy/events?after=x")[1][0]["seq"] == 1
    assert client.get("/api/runs/final_policy/nothing")[0] == 404
    assert client.get("/api/runs/nope")[0] == 404
    assert client.get("/api/runs/..%2Ffinal_policy")[0] == 404
    # the read-only server refuses the writing routes with 501
    assert client.post("/api/runs/final_policy/answer", {"answer": "x"})[0] == 501


def test_answer_and_approve_round_trip_on_a_paused_smoke_run(tmp_path, served):
    """Answer box and Approve/Reject call the runner's resume with the CLI's validation."""
    base = ["--engine", "policy", "--runs-dir", str(tmp_path)]
    assert main_run(base, "p") == cli.EXIT_PAUSED
    assert main_run(base, "q") == cli.EXIT_PAUSED

    def factory(run_id):
        return cli.resume_runner(run_id, runs_dir=tmp_path, engine="policy")

    client = served(tmp_path, runner_factory=factory)
    status, runs, _ = client.get("/api/runs")
    assert {r["run_id"]: r["pending"] for r in runs} == {"p": True, "q": True}
    status, overview, _ = client.get("/api/runs/p")
    assert overview["status"] == "paused" and overview["pending"]["request_id"] == "REQ-01-DB-30"
    assert overview["pending"]["is_approval"] is False
    assert overview["timeline"][1]["status"] == "active"

    # invalid answers: same message as `cli resume`, the run stays paused
    status, body = client.post("/api/runs/p/answer", {"answer": "Z"})
    assert status == 400 and "must be A or B" in body["error"]
    status, body = client.post("/api/runs/p/answer", {"answer": ""})
    assert status == 400 and "answer is required" in body["error"]
    assert client.post("/api/runs/p/approve", {"decision": "approve"})[0] == 409
    assert client.post("/api/runs/p/answer", raw=b"not json")[0] == 400
    assert client.post("/api/runs/p/answer", raw=b"[1]")[0] == 400
    assert client.post("/api/runs/nope/answer", {"answer": "A"})[0] == 404
    assert client.post("/api/runs/p/other", {"answer": "A"})[0] == 404
    assert client.get("/api/runs/p")[1]["status"] == "paused"

    # the DB-30 / DB-31 / DB-32 questions, then the P10 approval
    expected = ["DB-31", "DB-32", None]
    for answer, next_id in zip(("A", "55", "26"), expected, strict=True):
        status, body = client.post("/api/runs/p/answer", {"answer": answer})
        assert status == 200 and body["status"] == "paused", body
        assert body["pending"]["assumption_id"] == next_id
    assert body["pending"]["is_approval"] is True and body["phase"] == "P10"
    # stub tools cannot be verified: the verdict is UNVERIFIED (real tools: ACCEPTED)
    assert body["verdict"] in ("UNVERIFIED", "ACCEPTED") and body["selected_candidate"]
    assert client.get("/api/runs/p")[1]["pending"]["request_id"] == "APPROVAL"
    assert client.post("/api/runs/p/approve", {"decision": "maybe"})[0] == 400
    status, body = client.post("/api/runs/p/approve", {"decision": "approve"})
    assert status == 200 and body["status"] == "completed" and body["approval"] == "approved"
    assert body["pending"] is None and "DR-10" in body["decision_records"]
    status, overview, _ = client.get("/api/runs/p")
    assert overview["status"] == "completed" and overview["approval"] == "approved"
    assert all(row["status"] == "done" for row in overview["timeline"])
    assert "Human approval: approved (user)" in overview["answers"]
    verifier = client.get("/api/runs/p/verifier")[1]
    assert verifier is None or verifier["verdict"] == overview["verdict"]
    assert client.post("/api/runs/p/answer", {"answer": "A"})[0] == 409  # not paused any more
    assert client.post("/api/runs/p/approve", {"decision": "approve"})[0] == 409

    # the Answer box can also carry the approval text; reject is recorded
    for answer in ("B", "60", "24"):
        assert client.post("/api/runs/q/answer", {"answer": answer})[0] == 200
    status, body = client.post("/api/runs/q/approve", {"decision": "reject"})
    assert status == 200 and body["approval"] == "rejected" and body["status"] == "completed"
    assert (tmp_path / "q" / "run_summary.json").exists()
    assert json.loads((tmp_path / "q" / "run_summary.json").read_text())["approval"] == "rejected"


def test_server_errors_are_json(served, fixture_runs, monkeypatch):
    """A reader exception becomes a 500 JSON error, not a dropped connection."""
    client = served(fixture_runs)

    def boom(run_dir):
        raise RuntimeError("broken view")

    monkeypatch.setattr(run_view, "run_overview", boom)
    status, body, _ = client.get("/api/runs/final_policy")
    assert status == 500 and "broken view" in body["error"]
    raw = b"{" * (server.MAX_BODY_BYTES + 1)
    assert client.post("/api/runs/final_policy/answer", raw=raw)[0] == 413


def test_cli_serve_command(tmp_path, monkeypatch, capsys):
    """`cli serve`: usage errors for a missing runs dir / bound port; serves until Ctrl-C."""
    assert cli.main(["serve", "--runs-dir", str(tmp_path / "none")]) == cli.EXIT_USAGE
    assert "runs directory not found" in capsys.readouterr().err
    assert cli.main(["serve", "--runs-dir", str(tmp_path), "--config-dir", str(tmp_path)]) == (
        cli.EXIT_CONFIG_ERROR
    )

    class FakeServer:
        server_address = ("127.0.0.1", 4242)
        closed = False

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            FakeServer.closed = True

    seen = {}

    def fake_make_server(runs_root, *, host, port, runner_factory, quiet):
        seen.update(runs_root=runs_root, host=host, port=port, factory=runner_factory)
        if port == 1:
            raise OSError("address in use")
        return FakeServer()

    monkeypatch.setattr(server, "make_server", fake_make_server)
    assert cli.main(["serve", "--runs-dir", str(tmp_path), "--port", "1"]) == cli.EXIT_USAGE
    assert "cannot bind" in capsys.readouterr().err
    assert cli.main(["serve", "--runs-dir", str(tmp_path), "--port", "0"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "http://127.0.0.1:4242/" in out and "answer/approve enabled" in out
    assert FakeServer.closed and seen["runs_root"] == tmp_path.resolve() and seen["port"] == 0
    assert callable(seen["factory"])
    assert cli.main(["serve", "--runs-dir", str(tmp_path), "--read-only"]) == cli.EXIT_OK
    assert seen["factory"] is None and "read-only" in capsys.readouterr().out
    # a non-loopback bind with the writing routes enabled is warned about (no authentication)
    FakeServer.server_address = ("0.0.0.0", 4242)
    assert cli.main(["serve", "--runs-dir", str(tmp_path), "--host", "0.0.0.0"]) == cli.EXIT_OK
    assert "no authentication" in capsys.readouterr().err
    assert server.is_loopback("127.0.0.1") and server.is_loopback("::1")
    assert not server.is_loopback("0.0.0.0")


def main_run(base, run_id):
    """Start a policy run in pause mode (exit 3 at the first question)."""
    return cli.main(["run", *base, "--run-id", run_id, "--input-mode", "pause"])


def test_resume_runner_builds_the_cli_runner(tmp_path):
    """``resume_runner`` is the CLI's own runner construction (stored engine, closable)."""
    base = ["--engine", "policy", "--runs-dir", str(tmp_path)]
    assert main_run(base, "r") == cli.EXIT_PAUSED
    runner = cli.resume_runner("r", runs_dir=tmp_path)
    try:
        assert runner.engine.name == "policy"
        assert Path(runner.runs_root) == tmp_path
    finally:
        runner.close()
