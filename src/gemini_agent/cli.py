"""GeoAgent command-line interface.

Usage::

    PYTHONPATH=src python -m gemini_agent.cli --help
    PYTHONPATH=src python -m gemini_agent.cli validate-config
    PYTHONPATH=src python -m gemini_agent.cli run --engine policy
    PYTHONPATH=src python -m gemini_agent.cli run --engine policy --input-mode pause --run-id r1
    PYTHONPATH=src python -m gemini_agent.cli resume --run-id r1 --answer 55

``--engine policy`` is the deterministic engine used in cloud sessions, tests and CI; it never
calls an LLM. ``--engine llm`` uses a local Ollama model (spec §4.3) and only runs on a laptop;
if the model fails after its bounded retries, each step falls back to the policy (AG-03).

Input modes (AG-05): ``assume`` records labelled register defaults (non-interactive, default when
stdin is not a terminal), ``ask`` prompts on the terminal, ``pause`` saves the run and exits with
code 3 until ``resume --answer`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from gemini_agent import __version__
from gemini_agent.agents.engine import create_decision_engine
from gemini_agent.logging.llm_logger import LLMLogger
from gemini_agent.memory.event_store import SQLiteEventStore
from gemini_agent.runner.design_runner import (
    RUNS_ROOT,
    STORE_FILE,
    DesignRunner,
    InvalidAnswer,
    RunResult,
)
from gemini_agent.runner.toolbox import default_toolbox
from gemini_agent.schemas.config import CONFIG_MODELS, load_config
from gemini_agent.schemas.decision import InputRequest, RunStatus

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


def _add_engine_args(parser: argparse.ArgumentParser) -> None:
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

    resume = sub.add_parser("resume", help="answer the pending question of a paused run")
    _add_engine_args(resume)
    resume.add_argument("--run-id", required=True, help="run identifier to resume")
    resume.add_argument("--answer", default=None, help="answer to the pending question")

    check = sub.add_parser("validate-config", help="load every config/*.yaml into its schema")
    check.add_argument("--config-dir", type=Path, default=None, help="override config/ directory")
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


def _runner(args: argparse.Namespace, run_id: str, *, resuming: bool = False) -> DesignRunner:
    llm_config = load_config("llm", args.config_dir)
    runs_root = args.runs_dir or RUNS_ROOT
    stored_engine, stored_tag = _stored_engine(runs_root, run_id) if resuming else (None, "")
    engine_name = args.engine or stored_engine or llm_config.engine
    if engine_name == "llm":
        print(OLLAMA_CONTEXT_HINT.format(num_ctx=llm_config.ollama.num_ctx), file=sys.stderr)
    toolbox = default_toolbox()
    engine = create_decision_engine(
        engine_name,
        llm_config,
        logger=LLMLogger(runs_root / run_id),
        run_id=run_id,
        model_tag=args.model or stored_tag,
        validate_args=toolbox.validate_args,
    )
    return DesignRunner(
        engine,
        runs_root=runs_root,
        toolbox=toolbox,
        config_dir=args.config_dir,
        ask_fn=ask_terminal if sys.stdin.isatty() else None,
    )


def _report(result: RunResult) -> int:
    print(f"run {result.run_id}: {result.status.value} (phase {result.phase.value})")
    if result.summary:
        print(result.summary)
    if result.pending is not None:
        print(f"pending question [{result.pending.request_id}]: {result.pending.question}")
        print(
            "resume with: python -m gemini_agent.cli resume "
            f"--run-id {result.run_id} --answer '<answer>'"
        )
    print(f"outputs: {result.run_dir}")
    if result.status == RunStatus.COMPLETED and result.approval == "rejected":
        print("the recommended design was NOT approved by the user")
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
    runner = _runner(args, run_id)
    try:
        result = runner.start(
            prompt, run_id=run_id, input_mode=input_mode, model_tag=args.model or ""
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        runner.close()
    return _report(result)


def _resume(args: argparse.Namespace) -> int:
    try:
        runner = _runner(args, args.run_id, resuming=True)
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
    return _report(result)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-config":
        return _validate_config(args.config_dir)
    if args.command == "run":
        return _run(args)
    if args.command == "resume":
        return _resume(args)
    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
