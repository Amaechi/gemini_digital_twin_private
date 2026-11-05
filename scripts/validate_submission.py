#!/usr/bin/env python3
# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file scripts/validate_submission.py @ d69507a. Modified by GeoAgent team, 2026-11-01: required
# file list replaced by the spec §18 checklist in gemini_agent.eval.submission; --run-dir,
# --eval-root, --config-dir, --pytest; Markdown/JSON report.
"""Check the submission against the spec §18 acceptance checklist (WP-13).

Examples::

    PYTHONPATH=src python scripts/validate_submission.py                 # runs/final/
    PYTHONPATH=src python scripts/validate_submission.py --run-dir runs/<run_id> --pytest

Fails (exit 1) unless the final run is ACCEPTED and stub-free (no stub tool call, no Decision
Record with stub evidence), cost placeholders are set, and every other automatic §18 check
passes. VERIFY-tagged cost items (RC-05) are flagged as warnings. Runs on today's stub tools are
expected to fail until WP-14.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gemini_agent.eval.submission import (
    FINAL_RUNS_DIR,
    SUBMITTED_EVAL_ROOT,
    render_markdown,
    validate_submission,
)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser."""
    parser = argparse.ArgumentParser(description="Validate the GeoAgent submission (spec §18)")
    parser.add_argument("--run-dir", type=Path, default=FINAL_RUNS_DIR, help="final run directory")
    parser.add_argument(
        "--eval-root",
        type=Path,
        default=SUBMITTED_EVAL_ROOT,
        help="submitted evals (default runs/final/eval, tracked; run_eval.py --eval-root there)",
    )
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--pytest", action="store_true", help="also run the unit-test suite")
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; 0 when no check failed, else 1."""
    args = build_parser().parse_args(argv)
    report = validate_submission(
        run_dir=args.run_dir,
        eval_root=args.eval_root,
        config_dir=args.config_dir,
        repo_root=args.repo_root,
        run_tests=args.pytest,
    )
    root_text = str(Path(args.repo_root).resolve())
    markdown = render_markdown(report).replace(root_text, ".")
    print(markdown)
    json_text = report.model_dump_json(indent=2).replace(root_text, ".")
    for path, text in ((args.output_md, markdown), (args.output_json, json_text)):
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
