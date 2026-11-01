#!/usr/bin/env python3
"""Full-size TL-13 optimisation + TL-15 uncertainty of one concept (WP-11).

Examples::

    PYTHONPATH=src python scripts/run_optimisation.py --run-id wp11-c2 --workers 4
    PYTHONPATH=src python scripts/run_optimisation.py --concept C3 --samples 50 --skip-oat

Runs the register defaults (OPT-SPACE/OPT-DE: every doublet count × depth screened, seeded DE on
the best ``top_k``; UQ-MC: 200 Latin-hypercube samples) on the whole DB-30 year, writes
``runs/<run_id>/optimisation.{json,csv}``, ``uncertainty.json``, ``uncertainty_samples.csv``
and ``timing.json`` (git-ignored), and prints the measured runtime of each stage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gemini_agent.tools.optimise import (  # noqa: E402
    OptimisationResult,
    OptimiseRequest,
    optimise_design,
)
from gemini_agent.tools.uncertainty import UncertaintyRequest, uncertainty  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(description="TL-13 optimisation + TL-15 uncertainty (WP-11)")
    parser.add_argument("--concept", default="C2", help="Concept to optimise (default C2).")
    parser.add_argument("--run-id", default="wp11-optimisation", help="Folder under runs/.")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--seed", type=int, default=None, help="None: OPT-DE / UQ-MC seed.")
    parser.add_argument("--samples", type=int, default=None, help="None: UQ-MC (200).")
    parser.add_argument("--skip-oat", action="store_true", help="No one-at-a-time bars.")
    parser.add_argument("--skip-uncertainty", action="store_true")
    parser.add_argument(
        "--reuse-optimisation",
        action="store_true",
        help="Take the optimum from runs/<run_id>/optimisation.json instead of re-optimising.",
    )
    parser.add_argument("--runs-root", default=str(ROOT / "runs"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the optimisation, then the uncertainty analysis of its optimum; 1 if none found."""
    args = build_parser().parse_args(argv)
    run_dir = Path(args.runs_root) / args.run_id
    timing_path = run_dir / "timing.json"
    timing: dict[str, float] = {}
    if args.reuse_optimisation:
        result = OptimisationResult.model_validate_json(
            (run_dir / "optimisation.json").read_text(encoding="utf-8")
        )
        if timing_path.is_file():
            timing = json.loads(timing_path.read_text(encoding="utf-8"))
        best = result.best
    else:
        start = time.perf_counter()
        opt = optimise_design(
            OptimiseRequest(
                concept=args.concept,
                seed=args.seed,
                workers=args.workers,
                run_id=args.run_id,
            ),
            runs_root=args.runs_root,
        )
        timing["optimisation_s"] = time.perf_counter() - start
        print(opt.summary)
        print(f"optimisation: {timing['optimisation_s']:.0f} s on {args.workers} worker(s)")
        best = opt.data.best
    if best is None:
        print("no design meets demand; uncertainty skipped")
        return 1
    if not args.skip_uncertainty:
        start = time.perf_counter()
        uq = uncertainty(
            UncertaintyRequest(
                candidate=best.candidate,
                outage_start_indices=best.outage_start_indices,
                samples=args.samples,
                seed=args.seed,
                one_at_a_time=not args.skip_oat,
                workers=args.workers,
                run_id=args.run_id,
            ),
            runs_root=args.runs_root,
        )
        timing["uncertainty_s"] = time.perf_counter() - start
        print(uq.summary)
        print(f"uncertainty: {timing['uncertainty_s']:.0f} s on {args.workers} worker(s)")
    timing["workers"] = args.workers
    timing["cpu_count"] = os.cpu_count() or 0
    run_dir.mkdir(parents=True, exist_ok=True)
    timing_path.write_text(json.dumps(timing, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
