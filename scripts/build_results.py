#!/usr/bin/env python3
"""Regenerate the WP-14 results pack with one command (policy pipeline → figures → checks).

Examples::

    PYTHONPATH=src python scripts/build_results.py              # full profile, ≈ 40 min (4 vCPU)
    PYTHONPATH=src python scripts/build_results.py --skip-run       # re-render docs/results only
    PYTHONPATH=src python scripts/build_results.py --budget quick --run-id quick_policy \
        --out-dir runs/quick_policy/results

Steps: (1) ``python -m gemini_agent.cli run --engine policy`` into ``runs/<run_id>/`` (the
previous run folder is replaced; input mode ``assume``: register defaults, labelled); (2) every
TL-21 figure and table from that run's tool outputs into ``--out-dir`` (``tools/plots.py``)
plus ``policy_report.md``; (3) ``validate_submission`` on the run (``submission_check.{md,json}``).
Exit 0 when the run completed and the only failed submission checks are the LLM-only ones
(SUB-12, SUB-15, SUB-16, SUB-17: local Ollama run, L-1/L-2).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gemini_agent import cli  # noqa: E402
from gemini_agent.eval.submission import (  # noqa: E402
    SUBMITTED_EVAL_ROOT,
    CheckStatus,
    render_markdown,
    validate_submission,
)
from gemini_agent.tools import plots  # noqa: E402

LLM_ONLY_CHECKS = frozenset({"SUB-12", "SUB-15", "SUB-16", "SUB-17"})
"""Need the local LLM run / evals (L-1, L-2); a policy run cannot pass them."""


def build_parser() -> argparse.ArgumentParser:
    """Argument parser."""
    parser = argparse.ArgumentParser(description="Regenerate the GeoAgent results pack (WP-14)")
    parser.add_argument("--run-id", default="final_policy", help="run folder under --runs-dir")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "docs" / "results")
    parser.add_argument(
        "--budget",
        "--tool-profile",
        dest="budget",
        choices=["full", "quick", "smoke"],
        default="full",
        help="search budget (register RUN-BUDGET); the submitted run is full",
    )
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--skip-run", action="store_true", help="re-render from the existing run")
    parser.add_argument("--eval-root", type=Path, default=SUBMITTED_EVAL_ROOT)
    return parser


def run_pipeline(args: argparse.Namespace, run_dir: Path) -> int:
    """Run the policy pipeline into ``run_dir`` (replacing an earlier run of that id)."""
    runs_dir = args.runs_dir.resolve()
    if run_dir.exists():
        if runs_dir not in run_dir.resolve().parents:
            raise SystemExit(f"refusing to delete {run_dir}: not inside {runs_dir}")
        shutil.rmtree(run_dir)
    argv = [
        "run",
        "--engine",
        "policy",
        "--run-id",
        args.run_id,
        "--runs-dir",
        str(runs_dir),
        "--input-mode",
        "assume",
        "--budget",
        args.budget,
    ]
    if args.config_dir is not None:
        argv += ["--config-dir", str(args.config_dir)]
    start = time.perf_counter()
    code = cli.main(argv)
    print(f"pipeline: exit {code} in {time.perf_counter() - start:.0f} s")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (module doc)."""
    args = build_parser().parse_args(argv)
    run_dir = args.runs_dir / args.run_id
    if not args.skip_run:
        code = run_pipeline(args, run_dir)
        if code not in (0, 3):  # 3 = paused (cannot happen in assume mode)
            print(f"pipeline did not complete (exit {code}); results rendered for diagnosis")
    view = plots.RunView.from_run(run_dir)
    out = args.out_dir
    figures, tables = plots.render_all(view, out)
    report, _ = plots.report_markdown(view, figure_dir="")
    (out / "policy_report.md").write_text(report, encoding="utf-8")
    check = validate_submission(
        run_dir=run_dir,
        eval_root=args.eval_root,
        config_dir=args.config_dir,
        repo_root=ROOT,
    )
    (out / "submission_check.md").write_text(render_markdown(check), encoding="utf-8")
    (out / "submission_check.json").write_text(check.model_dump_json(indent=2), encoding="utf-8")
    failed = [c.id for c in check.checks if c.status == CheckStatus.FAIL]
    blocking = [c for c in failed if c not in LLM_ONLY_CHECKS]
    print(f"results pack: {len(figures)} figures, {len(tables)} tables + policy_report.md in {out}")
    print(f"submission check: failed {failed or 'none'}; blocking (non-LLM) {blocking or 'none'}")
    return 0 if view.status == "completed" and not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
