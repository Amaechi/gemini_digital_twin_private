"""GeoAgent command-line interface.

Usage::

    PYTHONPATH=src python -m gemini_agent.cli --help
    PYTHONPATH=src python -m gemini_agent.cli validate-config
    PYTHONPATH=src python -m gemini_agent.cli run --engine policy
    PYTHONPATH=src python -m gemini_agent.cli run --engine policy --input-mode pause --run-id r1
    PYTHONPATH=src python -m gemini_agent.cli resume --run-id r1 --answer 55
    PYTHONPATH=src python -m gemini_agent.cli whatif --run-id final_llm --runs-dir runs/final \
        --set DB-31=65 --budget quick
    PYTHONPATH=src python -m gemini_agent.cli serve --runs-dir runs/final --port 8765
    PYTHONPATH=src python -m gemini_agent.cli talk --engine llm --model <tag> --budget quick

``--engine policy`` is the deterministic engine used in cloud sessions, tests and CI; it never
calls an LLM. ``--engine llm`` uses a local Ollama model (spec §4.3) and only runs on a laptop;
if the model fails after its bounded retries, each step falls back to the policy (AG-03).

Input modes (AG-05): ``assume`` records labelled register defaults (non-interactive, default when
stdin is not a terminal), ``ask`` prompts on the terminal, ``pause`` saves the run and exits with
code 3 until ``resume --answer`` is given.

``whatif`` (post-submission) changes bounded design levers of a finished run's selected design,
reruns the year, economics and the verifier, and writes ``runs/<id>/whatif/<n>/`` without
touching the run (``gemini_agent.tools.whatif``). Exit codes: 0 done, 2 usage (unknown lever,
bad value, not a run), 5 refused (lever outside its bound, with the guard's reason).

``serve`` (post-submission) is the web Design Commander view (``gemini_agent.ui``): a standard
library HTTP server that shows the runs of a directory read-only and answers a paused run's
question (or the P10 approval) through the same ``resume`` as the CLI.

``run --review`` (post-submission) adds the P9b user review: after the verifier verdict the user
is asked for an objection to the recommended design (ask and pause modes; assume mode records
"no objection"); the engine maps it to one bounded what-if lever, the what-if tool computes the
before → after table, and the user adopts the change (P6–P9 rerun) or keeps the design — DR-12
(``gemini_agent.runner.review``). Default off: runs and evals without the flag are unchanged.

``--speak`` (``run``, ``resume``, ``whatif``; post-submission) reads the questions, the P10
summary with the verifier verdict, the DR-11 rationale and the what-if verdicts aloud with the
macOS ``say`` command (``gemini_agent.speech``); elsewhere it prints one note and is a no-op.
Everything is printed exactly as without the flag.

``talk`` (post-submission, experimental) is the push-to-talk voice front-end
(``gemini_agent.ui.voice``): the request is recorded (Enter starts and stops the recording),
transcribed offline with faster-whisper and confirmed; every question of the run is spoken, the
spoken answer is normalised into the expected form (one logged LLM call with ``--engine llm``,
keywords with ``--engine policy``), echoed and confirmed on the keyboard before the runner
validates it as usual; the P10 approval is spoken with the verifier's summary. The optional
packages live in ``requirements-voice.txt``; ``--typed`` uses the keyboard instead of a
microphone. The runner, DesignGuard, verifier and approval are the same as for ``run``.
After the P10 approval — or at once when ``--run-id`` names a completed run — the voice dialogue
(``gemini_agent.ui.dialogue``) asks "What would you like to change?", maps each request to one
bounded what-if lever with the review loop's Lead Engineer schema, runs the what-if, speaks a
three-sentence before → after summary and asks "Adopt it, try another, or stop?" (adopt = the
review loop's P6–P9 rerun and DR-12; max 5 rounds; ``--no-dialogue`` skips it).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from gemini_agent import __version__
from gemini_agent.agents import engine as agent_engine
from gemini_agent.agents.engine import create_decision_engine
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner.design_runner import (
    RUNS_ROOT,
    STORE_FILE,
    AskFn,
    DesignRunner,
    InvalidAnswer,
    RunResult,
)
from gemini_agent.runner.toolbox import default_toolbox
from gemini_agent.schemas.config import CONFIG_MODELS, load_config
from gemini_agent.schemas.decision import InputRequest, RunStatus
from gemini_agent.speech import Speaker, request_text, speaking_ask
from gemini_agent.tools import whatif as whatif_tool
from gemini_agent.ui import dialogue as voice_dialogue
from gemini_agent.ui import server as ui_server
from gemini_agent.ui import voice as voice_ui

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_USAGE = 2
EXIT_PAUSED = 3
EXIT_ESCALATED = 4
EXIT_REFUSED = 5
EXIT_NOT_APPROVED = 6
"""Run completed but the human rejected the recommended design at the P10 approval."""

STATUS_EXIT: dict[RunStatus, int] = {
    RunStatus.COMPLETED: EXIT_OK,
    RunStatus.PAUSED: EXIT_PAUSED,
    RunStatus.ESCALATED: EXIT_ESCALATED,
    RunStatus.REFUSED: EXIT_REFUSED,
    RunStatus.RUNNING: EXIT_USAGE,
}


def _add_speak_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--speak",
        action="store_true",
        help="also read questions, the P10 summary + verdict and the DR-11 rationale aloud "
        "(macOS `say`; a no-op with a note elsewhere)",
    )


def _add_engine_args(parser: argparse.ArgumentParser, *, speak: bool = True) -> None:
    if speak:
        _add_speak_arg(parser)
    parser.add_argument(
        "--engine",
        choices=["policy", "llm"],
        default=None,
        help="decision engine; default from config/llm.yaml (policy = deterministic, no LLM)",
    )
    parser.add_argument("--model", default=None, help="Ollama model tag (engine llm only)")
    parser.add_argument("--config-dir", type=Path, default=None, help="override config/ directory")
    parser.add_argument(
        "--runs-dir", type=Path, default=None, help=f"runs directory (default {RUNS_ROOT})"
    )
    parser.add_argument(
        "--budget",
        "--tool-profile",
        dest="budget",
        choices=["full", "quick", "smoke"],
        default=None,
        help="search budget of the real tools (register RUN-BUDGET): full = OPT-DE/UQ-MC, smoke = "
        "CI (tiny DE/Monte Carlo, 13 days, no worker pool); default $GEOAGENT_BUDGET, else full",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="geoagent",
        description=(
            "GeoAgent: select, size and validate a deep-geothermal district heating (100 °C) and "
            "cooling (20 °C) system with the GEMINI digital twin."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = sub.add_parser("run", help="run the design workflow (phases P0–P10)")
    _add_engine_args(run)
    run.add_argument("--brief", type=Path, default=None, help="challenge brief (Markdown)")
    run.add_argument("--prompt", default=None, help="request text (default: the brief)")
    run.add_argument("--run-id", default=None, help="run identifier (outputs in runs/<run_id>/)")
    run.add_argument(
        "--input-mode",
        choices=["assume", "ask", "pause"],
        default=None,
        help="missing inputs: assume defaults, ask on the terminal, or pause (default: ask if "
        "stdin is a terminal, else assume)",
    )
    run.add_argument(
        "--review",
        action="store_true",
        help="after the P9 verdict ask for objections to the recommended design, map each to "
        "one bounded what-if lever, show the before -> after table and offer to adopt the change "
        "(P6-P9 rerun; DR-12); off by default",
    )

    resume = sub.add_parser("resume", help="answer the pending question of a paused run")
    _add_engine_args(resume)
    resume.add_argument("--run-id", required=True, help="run identifier to resume")
    resume.add_argument("--answer", default=None, help="answer to the pending question")

    check = sub.add_parser("validate-config", help="load every config/*.yaml into its schema")
    check.add_argument("--config-dir", type=Path, default=None, help="override config/ directory")

    what = sub.add_parser(
        "whatif",
        help="what-if on a finished run: change a design lever, rerun year + economics + verifier",
        description=(
            "Apply bounded design levers to the selected design of a finished run, rerun the "
            "TL-12 year, TL-14 economics (+ CO2, exergy) and the DesignGuard/DesignVerifier, and "
            "print a before -> after table. Writes runs/<id>/whatif/<n>/; the run's own files "
            "are never changed.\nLevers:\n" + whatif_tool.lever_help()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    what.add_argument("--run-id", required=True, help="finished run (runs/<run-id>/)")
    what.add_argument(
        "--runs-dir", type=Path, default=None, help=f"runs directory (default {RUNS_ROOT})"
    )
    what.add_argument("--config-dir", type=Path, default=None, help="override config/ directory")
    _add_speak_arg(what)
    what.add_argument(
        "--set",
        dest="levers",
        action="append",
        metavar="LEVER=VALUE",
        default=[],
        help="lever to change (repeatable), e.g. --set DB-31=65 --set doublets=3",
    )
    what.add_argument(
        "--budget",
        choices=list(whatif_tool.BUDGETS),
        default=whatif_tool.DEFAULT_BUDGET,
        help="quick = full year (default); full = also the TL-15 Monte Carlo (P(demand met)); "
        "smoke = CI day subset",
    )

    serve = sub.add_parser(
        "serve",
        help="web Design Commander: browse the runs of a directory, answer a paused run",
        description=(
            "Serve the web Design Commander view (standard library HTTP server, no extra "
            "dependency): run list, phase timeline with budgets, tool calls, LLM tokens, Decision "
            "Records, verifier report, figures, and the pending question of a paused run with an "
            "Answer box / Approve-Reject buttons that call the same resume logic as `resume`. "
            "Read-only otherwise."
        ),
    )
    _add_engine_args(serve)
    serve.add_argument("--host", default=ui_server.DEFAULT_HOST, help="bind address")
    serve.add_argument(
        "--port", type=int, default=ui_server.DEFAULT_PORT, help="TCP port (0 = any free port)"
    )
    serve.add_argument(
        "--read-only", action="store_true", help="disable the answer/approve routes (501)"
    )

    talk = sub.add_parser(
        "talk",
        help="push-to-talk voice front-end: speak the request and the answers (experimental)",
        description=(
            "Run the design workflow by voice: record the request (Enter starts and stops the "
            "recording; faster-whisper transcribes it offline), confirm the transcript, then "
            "answer every question of the run by voice (spoken with its default, normalised into "
            "the expected form, echoed and confirmed with [Y/n]) up to the P10 approval. Speech "
            "output uses the macOS `say` command (printed only elsewhere). After the approval "
            "(or at once on a completed run given with --run-id) the voice dialogue asks what to "
            "change, computes each change as a what-if and offers to adopt it (P6-P9 rerun, "
            f"DR-12; max {voice_dialogue.MAX_ROUNDS} rounds). Needs the optional packages of "
            f"{voice_ui.VOICE_REQUIREMENTS} unless --typed is given."
        ),
    )
    _add_engine_args(talk, speak=False)
    talk.add_argument(
        "--run-id",
        default=None,
        help="run identifier (outputs in runs/<run_id>/); a completed run starts the what-if "
        "dialogue on it directly (nothing outside runs/<run_id>/whatif/ is written unless a "
        "change is adopted)",
    )
    talk.add_argument(
        "--no-dialogue",
        action="store_true",
        help="end after the P10 approval without the post-run what-if dialogue",
    )
    talk.add_argument(
        "--stt-model",
        default=voice_ui.STT_MODEL,
        help=f"faster-whisper model size (default {voice_ui.STT_MODEL}; tiny/base/small/medium)",
    )
    talk.add_argument(
        "--typed",
        action="store_true",
        help="type instead of speaking (no microphone, no faster-whisper/sounddevice needed)",
    )
    talk.add_argument(
        "--mute", action="store_true", help="do not call `say`; print the spoken texts only"
    )
    return parser


def _validate_config(config_dir: Path | None) -> int:
    failures = 0
    for name in CONFIG_MODELS:
        try:
            model = load_config(name, config_dir)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            failures += 1
            detail = " | ".join(str(exc).splitlines()[:3]) or type(exc).__name__
            print(f"FAIL {name}.yaml: {detail}")
        else:
            print(f"ok   {name}.yaml -> {type(model).__name__}")
    return EXIT_CONFIG_ERROR if failures else EXIT_OK


def ask_terminal(request: InputRequest) -> str | None:
    """Interactive prompt for ``--input-mode ask`` (Enter keeps the default)."""
    default = request.default
    shown = json.dumps(default) if isinstance(default, dict) else default
    unit = f" {request.unit}" if request.unit else ""
    print(f"\n[{request.phase.value}] {request.question}\n  default: {shown}{unit}")
    try:
        return input("  answer (Enter = default): ")
    except EOFError:
        return None


OLLAMA_CONTEXT_HINT = (
    "engine llm: start the Ollama server with OLLAMA_CONTEXT_LENGTH >= {num_ctx} (or a Modelfile "
    "num_ctx); its OpenAI endpoint may ignore num_ctx from the request (spec §5.4.7)."
)


def _stored_engine(runs_root: Path, run_id: str) -> tuple[str | None, str]:
    """Engine ('policy'/'llm') and ``--model`` tag a run was started with (None if unknown)."""
    path = runs_root / run_id / STORE_FILE
    if not path.exists():
        return None, ""
    store = SQLiteEventStore(path)
    try:
        record = store.get_run(run_id)
    finally:
        store.close()
    if record is None:
        return None, ""
    engine = "llm" if str(record.state.get("engine", "")).startswith("llm") else "policy"
    return engine, str(record.state.get("model_tag", "") or "")


def _run_status(runs_root: Path, run_id: str) -> str | None:
    """Stored status of ``runs/<run_id>/`` (None when no run is stored there)."""
    path = runs_root / run_id / STORE_FILE
    if not path.exists():
        return None
    store = SQLiteEventStore(path)
    try:
        record = store.get_run(run_id)
    finally:
        store.close()
    return None if record is None else str(record.state.get("status", "")) or None


def _speaker(args: argparse.Namespace) -> Speaker:
    return Speaker(bool(getattr(args, "speak", False)))


def _runner(
    args: argparse.Namespace,
    run_id: str,
    *,
    resuming: bool = False,
    speaker: Speaker | None = None,
    ask_fn: AskFn | None = None,
    model: Any = None,
    log_dir: Path | None = None,
) -> DesignRunner:
    """Build the runner of ``run``/``resume``/``serve``/``talk``.

    ``ask_fn`` replaces the terminal prompt (``talk`` passes the voice session's ``ask``);
    ``model`` is a prebuilt Pydantic AI model shared with another caller (``talk`` shares the
    Ollama model with its normaliser so the HTTP client is closed exactly once, by the engine);
    ``log_dir`` moves the engine's ``llm_calls.jsonl`` (default ``runs/<run_id>/``; the dialogue
    on a completed run logs under its ``whatif/`` so the run's own files stay untouched).
    """
    llm_config = load_config("llm", args.config_dir)
    runs_root = args.runs_dir or RUNS_ROOT
    stored_engine, stored_tag = _stored_engine(runs_root, run_id) if resuming else (None, "")
    engine_name = args.engine or stored_engine or llm_config.engine
    if engine_name == "llm":
        print(OLLAMA_CONTEXT_HINT.format(num_ctx=llm_config.ollama.num_ctx), file=sys.stderr)
    toolbox = default_toolbox(getattr(args, "budget", None), config_dir=args.config_dir)
    engine = create_decision_engine(
        engine_name,
        llm_config,
        logger=LLMLogger(log_dir or runs_root / run_id),
        run_id=run_id,
        model=model,
        model_tag=args.model or stored_tag,
        validate_args=toolbox.validate_args,
    )
    if ask_fn is None:
        ask_fn = ask_terminal if sys.stdin.isatty() else None
        if ask_fn is not None and speaker is not None and speaker.enabled:
            ask_fn = speaking_ask(speaker, ask_fn)
    budget = getattr(args, "budget", None)
    return DesignRunner(
        engine,
        runs_root=runs_root,
        toolbox=toolbox,
        config_dir=args.config_dir,
        ask_fn=ask_fn,
        review=bool(getattr(args, "review", False)),
        # The review's what-if runs on the run's own budget (full / quick / smoke); without a
        # --budget the what-if default (quick = full year, no Monte Carlo) keeps the review short.
        review_budget=budget if budget in whatif_tool.BUDGETS else whatif_tool.DEFAULT_BUDGET,
    )


def resume_runner(
    run_id: str,
    *,
    runs_dir: Path | None = None,
    config_dir: Path | None = None,
    engine: str | None = None,
    model: str | None = None,
    budget: str | None = None,
) -> DesignRunner:
    """Runner for ``DesignRunner.resume`` built exactly as ``cli resume`` builds it.

    Used by the web view (``gemini_agent.ui.server``) so a browser answer goes through the same
    engine selection (the run's stored engine and model unless overridden) and the same
    validation. The caller closes the runner.
    """
    args = argparse.Namespace(
        runs_dir=runs_dir, config_dir=config_dir, engine=engine, model=model, budget=budget
    )
    return _runner(args, run_id, resuming=True)


DR11_FILE = "DR-11.json"
"""Decision Record whose rationale ``--speak`` reads (backup capacity, the last design choice)."""


def _dr11_text(run_dir: Path) -> str | None:
    """Spoken DR-11 line from ``runs/<id>/decision_records/DR-11.json`` (None if absent)."""
    path = run_dir / "decision_records" / DR11_FILE
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (
        f"Decision record {record.get('dr_id', 'DR-11')}, {record.get('title', '')}: "
        f"{record.get('choice_label', '')}. {record.get('rationale', '')}"
    )


def _report(result: RunResult, speaker: Speaker | None = None) -> int:
    speaker = speaker or Speaker(False)
    print(f"run {result.run_id}: {result.status.value} (phase {result.phase.value})")
    if result.summary:
        print(result.summary)
    if result.pending is not None:
        print(f"pending question [{result.pending.request_id}]: {result.pending.question}")
        print(
            "resume with: python -m gemini_agent.cli resume "
            f"--run-id {result.run_id} --answer '<answer>'"
        )
        speaker.say(request_text(result.pending))
    else:
        speaker.say(f"Run {result.run_id} {result.status.value}. {result.summary}")
    print(f"outputs: {result.run_dir}")
    if result.status == RunStatus.COMPLETED:
        rationale = _dr11_text(result.run_dir)
        if rationale:
            speaker.say(rationale)
    if result.status == RunStatus.COMPLETED and result.approval == "rejected":
        print("the recommended design was NOT approved by the user")
        speaker.say("The recommended design was not approved by the user.")
        return EXIT_NOT_APPROVED
    return STATUS_EXIT[result.status]


def _run(args: argparse.Namespace) -> int:
    try:
        llm_config = load_config("llm", args.config_dir)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    engine_name = args.engine or llm_config.engine
    run_id = args.run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{engine_name}"
    prompt = args.prompt or ""
    if not prompt and args.brief is not None:
        try:
            prompt = args.brief.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read brief: {exc}", file=sys.stderr)
            return EXIT_USAGE
    input_mode = args.input_mode or ("ask" if sys.stdin.isatty() else "assume")
    speaker = _speaker(args)
    runner = _runner(args, run_id, speaker=speaker)
    try:
        result = runner.start(
            prompt, run_id=run_id, input_mode=input_mode, model_tag=args.model or ""
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        runner.close()
    return _report(result, speaker)


def _resume(args: argparse.Namespace) -> int:
    speaker = _speaker(args)
    try:
        runner = _runner(args, args.run_id, resuming=True, speaker=speaker)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        result = runner.resume(args.run_id, args.answer)
    except InvalidAnswer as exc:
        print(f"invalid answer: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        runner.close()
    return _report(result, speaker)


def _whatif(args: argparse.Namespace) -> int:
    speaker = _speaker(args)
    runs_root = args.runs_dir or RUNS_ROOT
    try:
        result = whatif_tool.whatif(
            runs_root / args.run_id, args.levers, args.budget, config_dir=args.config_dir
        )
    except whatif_tool.WhatIfRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (whatif_tool.UnknownLever, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"what-if {result.index} on run {result.run_id}: {whatif_tool.format_levers(result.levers)}"
    )
    for item in result.applied:
        print(f"  {item.lever}: {item.base_value!r} -> {item.value!r} ({item.mechanism})")
    print()
    print(whatif_tool.render_table(result))
    print()
    for outcome in (result.before, result.after):
        line = f"{outcome.label}: {outcome.status}"
        if outcome.reason:
            line += f" - {outcome.reason}"
        if outcome.verifier_summary:
            line += f"; {outcome.verifier_summary}"
        print(line)
        speaker.say(f"{outcome.label}: {outcome.status}. {outcome.verifier_summary}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"record: {result.record.dr_id} ({result.record.title})")
    speaker.say(f"Record {result.record.dr_id}, {result.record.title}: {result.record.rationale}")
    print(f"outputs: {runs_root / args.run_id / whatif_tool.WHATIF_DIR / str(result.index)}")
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    runs_root = (args.runs_dir or RUNS_ROOT).resolve()
    if not runs_root.is_dir():
        print(f"error: runs directory not found: {runs_root}", file=sys.stderr)
        return EXIT_USAGE
    try:
        load_config("llm", args.config_dir)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    def factory(run_id: str) -> DesignRunner:
        return resume_runner(
            run_id,
            runs_dir=runs_root,
            config_dir=args.config_dir,
            engine=args.engine,
            model=args.model,
            budget=args.budget,
        )

    try:
        httpd = ui_server.make_server(
            runs_root,
            host=args.host,
            port=args.port,
            runner_factory=None if args.read_only else factory,
            quiet=False,
        )
    except OSError as exc:
        print(f"error: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    host, port = httpd.server_address[:2]
    mode = "read-only" if args.read_only else "answer/approve enabled"
    if not args.read_only and not ui_server.is_loopback(str(host)):
        print(
            f"warning: {host} is not a loopback address and the answer/approve routes have no "
            "authentication; use --read-only or --host 127.0.0.1 outside a trusted network",
            file=sys.stderr,
        )
    print(f"Design Commander: http://{host}:{port}/  runs: {runs_root}  ({mode}; Ctrl-C stops)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return EXIT_OK


def _talk(args: argparse.Namespace) -> int:
    """``talk``: the push-to-talk loop around the ordinary ``run`` (``gemini_agent.ui.voice``).

    With ``--run-id`` naming a completed run the request and the run are skipped and only the
    what-if dialogue (``gemini_agent.ui.dialogue``) runs on it; a stored run in any other state
    is refused (resume it with ``resume`` first). A new run gets the dialogue after its approval
    unless ``--no-dialogue`` is given.
    """
    try:
        llm_config = load_config("llm", args.config_dir)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    engine_name = args.engine or llm_config.engine
    run_id = args.run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{engine_name}-talk"
    runs_root = args.runs_dir or RUNS_ROOT
    run_dir = runs_root / run_id
    status = _run_status(runs_root, run_id) if args.run_id else None
    finished = status == RunStatus.COMPLETED.value
    if status is not None and not finished:
        print(
            f"error: run '{run_id}' is {status}; the voice dialogue needs a completed run "
            "(answer a paused run with `resume` first)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    speaker = Speaker(not args.mute)
    try:
        listen = (
            voice_ui.typed_listener() if args.typed else voice_ui.whisper_listener(args.stt_model)
        )
        if not args.typed:
            # Fail before the first question if the optional packages are missing.
            voice_ui.import_voice_module("sounddevice")
            voice_ui.import_voice_module("faster_whisper")
    except voice_ui.VoiceDependencyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    session = voice_ui.VoiceSession(
        speaker=speaker,
        listen=listen,
        normaliser=voice_ui.PolicyNormaliser(),
        run_dir=run_dir,
    )
    prompt = ""
    if not finished:
        prompt = session.request_prompt()
        if prompt is None:
            if not session.confirm("No request was confirmed. Use the default challenge brief?"):
                print("talk: no request; nothing started", file=sys.stderr)
                return EXIT_USAGE
            prompt = ""
    # Only now is anything written under runs/<run_id>/ (the normaliser's logger creates it).
    # On a completed run every log of the dialogue goes under its whatif/ folder.
    log_dir = run_dir / whatif_tool.WHATIF_DIR if finished else run_dir
    model: Any = None
    normaliser: voice_ui.Normaliser = session.normaliser
    stored_engine, stored_tag = _stored_engine(runs_root, run_id) if finished else (None, "")
    if (args.engine or stored_engine or llm_config.engine) == "llm":
        # Looked up on the module so tests (and the test-wide real-LLM block) can replace it.
        # On a completed run the normaliser shares the run's stored model tag with the runner.
        model = agent_engine.build_ollama_model(
            llm_config, "orchestrator", args.model or stored_tag
        )
        normaliser = voice_ui.LLMNormaliser(llm_config, model, LLMLogger(log_dir), run_id=run_id)
        session.normaliser = normaliser
    runner = _runner(
        args, run_id, resuming=finished, ask_fn=session.ask, model=model, log_dir=log_dir
    )
    budget = getattr(args, "budget", None)
    loop: voice_dialogue.DialogueLoop | None = None
    if not args.no_dialogue:
        loop = voice_dialogue.DialogueLoop(
            session,
            runner,
            run_id,
            budget=budget if budget in whatif_tool.BUDGETS else whatif_tool.DEFAULT_BUDGET,
            log_path=run_dir / whatif_tool.WHATIF_DIR / voice_dialogue.DIALOGUE_FILE,
            adopt_block=voice_dialogue.submitted_run_reason(run_dir),
        )
        session.dialogue_hook = loop.run
    try:
        if finished:
            result = runner.result(run_id)
            print(
                f"run {run_id}: {result.status.value} (phase {result.phase.value}); selected "
                f"{result.selected_id}, verdict {result.verdict}, approval {result.approval}"
            )
            code = EXIT_OK
        else:
            result = runner.start(
                prompt, run_id=run_id, input_mode="ask", model_tag=args.model or ""
            )
            code = _report(result, speaker)
        # FINAL_SPRINT §I: the post-run voice dialogue (what-if by voice) runs here, before the
        # runner closes the shared model, so the loop can still make LLM calls.
        session.after_run(result)
        if loop is not None and loop.summary.adopted and loop.summary.last_result is not None:
            code = _report(loop.summary.last_result, speaker)  # the rerun's status and approval
        if isinstance(normaliser, voice_ui.LLMNormaliser):
            print(
                f"voice: {normaliser.calls} normaliser LLM calls, {normaliser.fallbacks} policy "
                f"fallbacks ({len(session.transcripts)} transcripts)"
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        runner.close()
    return code


def _exit_on_sigterm(signum: int, frame: object) -> None:
    """SIGTERM (CI time limit, kill) → SystemExit, so context managers stop worker pools."""
    raise SystemExit(128 + signum)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, _exit_on_sigterm) if main_thread else None
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.command == "validate-config":
            return _validate_config(args.config_dir)
        if args.command == "run":
            return _run(args)
        if args.command == "resume":
            return _resume(args)
        if args.command == "whatif":
            return _whatif(args)
        if args.command == "serve":
            return _serve(args)
        if args.command == "talk":
            return _talk(args)
        parser.print_help()
        return EXIT_OK
    finally:
        if main_thread:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
