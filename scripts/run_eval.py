#!/usr/bin/env python3
# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file scripts/run_eval.py @ d69507a. Modified by GeoAgent team, 2026-11-01: seeded HTTP scenarios
# replaced by the spec §12 S1–S11 suite run in-process (gemini_agent.eval.harness); --engine
# policy|testmodel|llm, --model, --scenarios, --compare; JSON + Markdown report per model.
"""Run the GeoAgent evaluation suite (spec §12 EV-01, EV-04; WP-13).

Examples::

    PYTHONPATH=src python scripts/run_eval.py --engine policy
    PYTHONPATH=src python scripts/run_eval.py --engine testmodel --scenarios S1,S10
    PYTHONPATH=src python scripts/run_eval.py --engine llm --model qwen3.5:9b   # laptop only
    PYTHONPATH=src python scripts/run_eval.py --compare runs/eval/qwen3.5_9b runs/eval/<other>

Outputs go to ``runs/eval/<model>/`` (``policy``, ``testmodel`` or the Ollama tag): one run
directory per scenario (transcripts: ``events.jsonl``, ``llm_calls.jsonl``, ``token_summary.json``),
``eval_results.json`` and ``EVAL_REPORT.md``. ``--compare`` writes ``model_comparison.{json,md}``.
``runs/eval/`` is git-ignored: for the submission use ``--eval-root runs/final/eval`` (tracked),
which is where ``validate_submission.py`` looks. Without ``--model``, engine llm uses the active
profile's orchestrator and coder models from config/llm.yaml (as ``cli run --engine llm``).
Exit code 0 when no scenario failed or errored (pending and skipped scenarios do not fail).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gemini_agent.eval.harness import (
    EVAL_ROOT,
    REPORT_FILE,
    compare_evals,
    eval_dir_name,
    model_label,
    render_comparison,
    run_suite,
)
from gemini_agent.eval.scenarios import SCENARIOS_FILE, load_scenarios
from gemini_agent.schemas.config import load_config

OLLAMA_HINT = (
    "engine llm: start Ollama with OLLAMA_CONTEXT_LENGTH >= {num_ctx} (spec §5.4.7); results and "
    "transcripts go to {eval_dir}"
)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser."""
    parser = argparse.ArgumentParser(description="Run the GeoAgent scenario suite S1–S11")
    parser.add_argument(
        "--engine",
        choices=["policy", "testmodel", "llm"],
        default="policy",
        help="policy (deterministic), testmodel (Pydantic AI TestModel, offline) or llm (Ollama)",
    )
    parser.add_argument("--model", default="", help="Ollama tag for --engine llm")
    parser.add_argument(
        "--tool-profile",
        choices=["full", "quick", "smoke"],
        default="quick",
        help="search effort of the real tools (WP-14; the final run uses full)",
    )
    parser.add_argument("--scenarios", default="", help="comma-separated ids, e.g. S1,S3")
    parser.add_argument("--scenarios-file", type=Path, default=SCENARIOS_FILE)
    parser.add_argument("--eval-root", type=Path, default=EVAL_ROOT)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument(
        "--compare",
        nargs="+",
        type=Path,
        default=None,
        metavar="EVAL_DIR",
        help="compare finished evals (S11) instead of running; writes model_comparison.*",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.compare:
        comparison = compare_evals(args.compare, args.eval_root)
        print(render_comparison(comparison))
        print(f"Wrote {args.eval_root / 'model_comparison.json'}")
        return 0
    if args.model and args.engine != "llm":
        print("--model is only used with --engine llm", file=sys.stderr)
        return 2
    suite = load_scenarios(args.scenarios_file)
    ids = [sid.strip() for sid in args.scenarios.split(",") if sid.strip()] or None
    unknown = [sid for sid in ids or [] if sid not in {s.id for s in suite.scenarios}]
    if unknown:
        print(f"unknown scenario(s) {unknown} in {args.scenarios_file}", file=sys.stderr)
        return 2
    if args.engine == "llm":
        llm_config = load_config("llm", args.config_dir)
        model_dir = args.eval_root / eval_dir_name(model_label("llm", args.model, llm_config))
        hint = OLLAMA_HINT.format(num_ctx=llm_config.ollama.num_ctx, eval_dir=model_dir)
        print(hint, file=sys.stderr)
    try:
        source = str(args.scenarios_file.resolve().relative_to(ROOT))
    except ValueError:
        source = str(args.scenarios_file)
    results = run_suite(
        suite,
        engine=args.engine,
        eval_root=args.eval_root,
        scenario_ids=ids,
        config_dir=args.config_dir,
        model_tag=args.model,
        scenarios_source=source,
        tool_profile=args.tool_profile,
    )
    for run in results.runs:
        m = run.metrics
        print(
            f"{run.scenario}: {run.outcome.value} status={run.status or '-'} "
            f"tool_calls={m.tool_calls} llm_calls={m.llm_calls} fallbacks={m.fallbacks} "
            f"tokens={m.total_tokens} seconds={m.runtime_s:.2f}"
            + (f" ({run.note})" if run.note else "")
        )
    print(f"Wrote {Path(results.eval_dir) / 'eval_results.json'} and {REPORT_FILE}")
    return 0 if results.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
