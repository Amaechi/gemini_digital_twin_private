"""GeoAgent command-line interface (skeleton; the workflow runner arrives in WP-10).

Usage::

    PYTHONPATH=src python -m gemini_agent.cli --help
    PYTHONPATH=src python -m gemini_agent.cli validate-config
    PYTHONPATH=src python -m gemini_agent.cli run --engine policy

``--engine policy`` is the deterministic engine used in cloud sessions, tests and CI; it never
calls an LLM. ``--engine llm`` uses a local Ollama model (spec §4.3) and only runs on a laptop.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from gemini_agent import __version__
from gemini_agent.schemas.config import CONFIG_MODELS, load_config

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (WP-10 extends the ``run`` command)."""
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
    run.add_argument(
        "--engine",
        choices=["policy", "llm"],
        default=None,
        help="decision engine; default from config/llm.yaml (policy = deterministic, no LLM)",
    )
    run.add_argument("--brief", type=Path, default=None, help="challenge brief (Markdown)")
    run.add_argument("--model", default=None, help="Ollama model tag (engine llm only)")
    run.add_argument("--config-dir", type=Path, default=None, help="override config/ directory")
    run.add_argument("--run-id", default=None, help="run identifier (outputs in runs/<run_id>/)")

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


def _run(args: argparse.Namespace) -> int:
    print(
        "geoagent run: the workflow runner is not implemented yet (WP-10). "
        f"engine={args.engine or 'from config/llm.yaml'}",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-config":
        return _validate_config(args.config_dir)
    if args.command == "run":
        return _run(args)
    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
