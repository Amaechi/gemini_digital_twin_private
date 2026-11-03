"""Redraw ``figures/fig_tokens.png`` of finished runs from their ``token_summary.json``.

One-off, post-submission (FINAL_SPRINT §C): the P10 plot used to run before the token summary
was written, so every LLM run drew the placeholder. This script touches **figures only** — one
PNG per run folder, nothing else — and skips a run without a ``token_summary.json`` or without
a figures folder (the run never reached P10).

Usage::

    PYTHONPATH=src python scripts/regenerate_fig_tokens.py            # final_llm + eval runs
    PYTHONPATH=src python scripts/regenerate_fig_tokens.py runs/x     # given run folder(s)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = [REPO / "runs" / "final" / "final_llm"]
EVAL_ROOT = REPO / "runs" / "final" / "eval"


def default_run_dirs() -> list[Path]:
    """The final LLM run and every eval run folder (``runs/final/eval/<model>/<scenario>/``)."""
    runs = list(DEFAULT_RUNS)
    if EVAL_ROOT.is_dir():
        runs += sorted(p for p in EVAL_ROOT.glob("*/*") if p.is_dir())
    return runs


def _plots():
    """``gemini_agent.tools.plots`` with ``src/`` on the path (no PYTHONPATH needed)."""
    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    from gemini_agent.tools import plots

    return plots


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def regenerate(run_dir: Path) -> str:
    """Redraw the figure of one run; return a one-line status."""
    plots = _plots()
    figure = run_dir / "figures" / plots.TOKENS_FIGURE
    if not (run_dir / plots.TOKEN_SUMMARY).is_file():
        return f"skip  {_rel(run_dir)}: no {plots.TOKEN_SUMMARY}"
    if not figure.is_file():
        return f"skip  {_rel(run_dir)}: no figures/{plots.TOKENS_FIGURE} (run did not reach P10)"
    filled = plots.render_tokens_figure(run_dir)
    return f"{'drawn' if filled else 'blank'} {_rel(figure)}"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dirs", nargs="*", type=Path, help="run folders (default: final runs)")
    args = parser.parse_args(argv)
    run_dirs = [Path(p) for p in args.run_dirs] or default_run_dirs()
    for run_dir in run_dirs:
        print(regenerate(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
