"""Assemble docs/results/REPORT.md (the deck's report pack) from tool outputs only.

Every table is copied or derived from a run artefact and cites its source file; nothing is typed
by hand. Inputs (all tracked):

* ``runs/final/final_llm/`` — the final LLM run (``report_draft.md``, ``run_summary.json``,
  ``token_summary.json``, ``decision_records/``, ``figures/``);
* ``runs/final/eval/<model>/eval_results.json`` — S1–S11 (chosen model) and S11 (every model);
* ``runs/final/eval/gemma4_12b-it-q4_K_M/S10-token_comparison.json`` — naive vs optimised;
* ``docs/results/s7_demo/s7_report.json`` — the S7 Model Builder evidence.

Usage::

    PYTHONPATH=src python scripts/build_report_pack.py [--run-dir runs/final/final_llm]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RUN = REPO / "runs" / "final" / "final_llm"
EVAL_ROOT = REPO / "runs" / "final" / "eval"
S7_REPORT = REPO / "docs" / "results" / "s7_demo" / "s7_report.json"
FIGURES = REPO / "docs" / "results" / "final_llm"
OUT = REPO / "docs" / "results" / "REPORT.md"

CAPTIONS = {
    "fig_candidates.png": "Levelised cost (LCOH + LCOC stacked, 30 yr, TL-14) and CO₂ emissions "
    "(ECO-05) of every candidate: C0–C8 screening points, the TL-13 optima and the selected "
    "C2-opt-robust.",
    "fig_daily_heat.png": "Daily heat supply by source (geothermal plate HEX, HTHP, CHP on "
    "associated gas, peak boiler on grid gas) against the heating demand, C2-opt-robust, 2027.",
    "fig_daily_cooling.png": "Daily cooling supply by source (free cooling, electric chiller) "
    "against the cooling demand, C2-opt-robust, 2027.",
    "fig_load_duration.png": "Heating and cooling load-duration curves (days sorted by demand) "
    "with the supply stack of the selected design.",
    "fig_energy_balance.png": "Annual energy balance: heat delivered by source, cooling delivered "
    "by source, and the inputs (brine heat drawn, gas fuel, electricity consumed), GWh/yr.",
    "fig_uncertainty.png": "TL-15 Monte Carlo (200 samples, C2-opt): cumulative distributions of "
    "LCOH and LCOC with the P10–P90 band, P50 and the base value.",
    "fig_tornado.png": "TL-15 one-at-a-time sensitivity: swing of the objective (LCOH + LCOC + "
    "unmet penalty) per input at its low and high value.",
    "fig_tokens.png": "LLM tokens (input + output) per phase, the run's own TL-21 chart "
    "(redrawn from token_summary.json after submission, scripts/regenerate_fig_tokens.py; the "
    "run had drawn the placeholder because the P10 plot preceded the summary).",
    "fig_tokens_by_phase.png": "LLM tokens of the final run per phase (input and output, "
    "stacked) and per agent, drawn by this script from token_summary.json.",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(path: Path) -> dict:
    return json.loads(_read(path))


def _rel(path: Path) -> str:
    return path.resolve().relative_to(REPO).as_posix()


def section(markdown: str, title: str) -> str:
    """Body of the ``## <title>`` section of a report (up to the next ``## ``)."""
    pattern = rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown, re.DOTALL | re.MULTILINE)
    if match is None:
        raise KeyError(f"section '{title}' not found")
    return match.group(1).strip("\n")


def _row(cells: list[object]) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _fmt(value: object, digits: int = 0) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def token_tables(summary: dict) -> str:
    """Token tables (totals, by phase, by agent, by decision source) from token_summary.json."""
    cols = [
        "calls",
        "requests",
        "retries",
        "fallbacks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_s",
    ]
    header = _row(["Group"] + cols) + "\n" + _row(["---"] * (len(cols) + 1))

    def rows(group: dict) -> str:
        return "\n".join(
            _row([name] + [_fmt(v.get(c), 1 if c == "latency_s" else 0) for c in cols])
            for name, v in group.items()
        )

    total = {"total": summary["total"]}
    return "\n\n".join(
        [
            "**Totals**\n\n" + header + "\n" + rows(total),
            "**By phase**\n\n" + header + "\n" + rows(summary["by_phase"]),
            "**By agent**\n\n" + header + "\n" + rows(summary["by_agent"]),
            "**By decision source**\n\n" + header + "\n" + rows(summary["by_decision_source"]),
        ]
    )


def s10_table(comparison: dict) -> str:
    """Naive vs optimised token table from S10-token_comparison.json."""
    cols = [
        "naive_tokens",
        "optimised_tokens",
        "saved_tokens",
        "saved_frac",
        "naive_calls",
        "optimised_calls",
    ]
    lines = [_row(["Agent"] + cols), _row(["---"] * (len(cols) + 1))]
    groups = {"total": comparison["total"], **comparison.get("by_agent", {})}
    for name, v in groups.items():
        lines.append(_row([name] + [_fmt(v.get(c), 4 if c == "saved_frac" else 0) for c in cols]))
    return "\n".join(lines)


def s11_table(eval_root: Path) -> tuple[str, list[str]]:
    """One S11 row per model from every ``eval_results.json`` under ``eval_root``."""
    cols = [
        "Model",
        "Outcome",
        "Status",
        "Phase",
        "Verdict",
        "LLM calls",
        "Valid",
        "Validity",
        "Retries",
        "Fallbacks",
        "Tokens",
        "Runtime s",
    ]
    lines = [_row(cols), _row(["---"] * len(cols))]
    sources = []
    for results in sorted(eval_root.glob("*/eval_results.json")):
        data = _json(results)
        for run in data["runs"]:
            if run["scenario"] != "S11":
                continue
            m = run["metrics"]
            valid = m["llm_valid_calls"] / m["llm_calls"] if m["llm_calls"] else 0.0
            lines.append(
                _row(
                    [
                        data["model"],
                        run["outcome"],
                        run["status"],
                        run["phase"],
                        run["verdict"] or "-",
                        m["llm_calls"],
                        m["llm_valid_calls"],
                        f"{valid:.0%}",
                        m["retries"],
                        m["fallbacks"],
                        _fmt(m["input_tokens"] + m["output_tokens"]),
                        _fmt(m["runtime_s"], 1),
                    ]
                )
            )
            sources.append(_rel(results))
    return "\n".join(lines), sources


def scenario_table(results: Path) -> str:
    """S1–S11 outcome table of one ``eval_results.json``."""
    data = _json(results)
    cols = [
        "Scenario",
        "Expected",
        "Outcome",
        "Status",
        "Phase",
        "Verdict",
        "Tool calls (stub)",
        "LLM calls",
        "Validity",
        "Fallbacks",
        "Tokens",
        "Runtime s",
        "Pending / failed",
    ]
    lines = [_row(cols), _row(["---"] * len(cols))]
    for run in data["runs"]:
        m = run["metrics"]
        valid = m["llm_valid_calls"] / m["llm_calls"] if m["llm_calls"] else 0.0
        notes = [p["expect"] for p in run.get("pending", [])]
        notes += [c["detail"] for c in run["checks"] if not c["passed"]]
        lines.append(
            _row(
                [
                    run["scenario"],
                    run["expected"],
                    run["outcome"],
                    run["status"],
                    run["phase"],
                    run["verdict"] or "-",
                    f"{m['tool_calls']} ({m['stub_tool_calls']})",
                    m["llm_calls"],
                    f"{valid:.0%}",
                    m["fallbacks"],
                    _fmt(m["input_tokens"] + m["output_tokens"]),
                    _fmt(m["runtime_s"], 1),
                    "; ".join(notes) or "–",
                ]
            )
        )
    return "\n".join(lines)


def s7_table(report: dict) -> str:
    """S7 Model Builder result and drafts from ``s7_report.json``."""
    result = report["run"]["results"][0]
    lines = [
        _row(["Field", "Value"]),
        _row(["---", "---"]),
        _row(["Removed registry type", f"`{report['removed_type']}`"]),
        _row(["Gap detected / closed", f"{report['gap_detected']} / {report['gap_closed']}"]),
        _row(
            [
                "Registered class",
                f"`{report['registered_class']}` (card {result['reference_card']})",
            ]
        ),
        _row(["Status", result["status"]]),
        _row(["Types still missing", ", ".join(report["run"]["still_missing"]) or "none"]),
    ]
    for attempt in result["attempts"]:
        pytest_result = attempt["pytest"]
        lines.append(
            _row(
                [
                    f"Draft {attempt['attempt']}",
                    (
                        f"{attempt['decision_source']}, {attempt['input_tokens']} in / "
                        f"{attempt['output_tokens']} out tokens, {attempt['elapsed_s']:.1f} s; "
                        f"pytest {pytest_result['tests']} tests, {pytest_result['failures']} "
                        f"failure(s), passed = {pytest_result['passed']}"
                    ),
                ]
            )
        )
    return "\n".join(lines)


def draw_token_figure(token_summary: dict, out_path: Path) -> None:
    """Draw tokens per phase (input/output stacked) and per agent from ``token_summary.json``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6), gridspec_kw={"width_ratios": [1.3, 1]})
    for ax, (title, group) in zip(
        axes, [("tokens per phase", "by_phase"), ("tokens per agent", "by_agent")]
    ):
        names = list(token_summary[group])
        inputs = [token_summary[group][n]["input_tokens"] for n in names]
        outputs = [token_summary[group][n]["output_tokens"] for n in names]
        ax.bar(names, inputs, color="#1f77b4", label="input")
        ax.bar(names, outputs, bottom=inputs, color="#ff7f0e", label="output")
        ax.set_title(f"LLM {title} (final run)", loc="left", fontsize=11)
        ax.set_ylabel("tokens")
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def decision_records(run_dir: Path, out_path: Path = OUT) -> str:
    """Concatenate the run's Decision Record index and DR-01…DR-11 files, in order.

    The index links ``DR-xx.md`` relative to the run's ``decision_records/`` folder; they are
    rewritten relative to the report pack's own folder so they resolve from ``out_path``.
    """
    folder = run_dir / "decision_records"
    link_base = os.path.relpath(folder.resolve(), out_path.resolve().parent).replace(os.sep, "/")
    index = re.sub(r"\]\((DR-\d+\.md)\)", rf"]({link_base}/\1)", _read(folder / "index.md").strip())
    parts = [index]
    for path in sorted(folder.glob("DR-*.md"), key=lambda p: int(p.stem.split("-")[1])):
        parts.append(_read(path).strip())
    return "\n\n".join(parts)


def build(run_dir: Path, out_path: Path = OUT) -> str:
    """Build the whole report pack as Markdown."""
    draft = _read(run_dir / "report_draft.md")
    run_summary = _json(run_dir / "run_summary.json")
    token_summary = _json(run_dir / "token_summary.json")
    figures_dir = run_dir / "figures"
    chosen_eval = EVAL_ROOT / "gemma4_12b-it-q4_K_M"
    s10 = _json(chosen_eval / "S10-token_comparison.json")
    s11, s11_sources = s11_table(EVAL_ROOT)
    s7 = _json(S7_REPORT)
    comparison = _json(EVAL_ROOT / "model_comparison.json")

    cite = _rel(run_dir)
    out: list[str] = []
    out.append(f"# GeoAgent results pack — run `{run_summary['run_id']}`\n")
    out.append(
        "Built by `scripts/build_report_pack.py` from tool outputs only; each table names its "
        "source file. Rebuild with `PYTHONPATH=src python scripts/build_report_pack.py`.\n"
    )
    out.append(
        f"Run: engine `{run_summary['engine']}`, model "
        f"`{next(iter(token_summary['by_model']))}`, input mode `{run_summary['input_mode']}`, "
        f"status `{run_summary['status']}` in phase {run_summary['phase']}, selected "
        f"`{run_summary['selected_candidate']}`, verdict **{run_summary['verdict']}**, approval "
        f"`{run_summary['approval']}`, tool calls {run_summary['tool_calls']} "
        f"({run_summary['stub_tool_calls']} stubs), LLM fallbacks {run_summary['llm_fallbacks']}. "
        f"Source: `{cite}/run_summary.json`.\n"
    )

    out.append("## 1. Answer\n")
    out.append(f"Source: `{cite}/report_draft.md` (section *Answer*).\n")
    out.append(section(draft, "Answer") + "\n")

    out.append("## 2. Candidate comparison C0–C8 and optima\n")
    out.append(f"Source: `{_rel(figures_dir / 'candidates.md')}` (also `candidates.csv`).\n")
    out.append(_read(figures_dir / "candidates.md").strip() + "\n")

    out.append("## 3. Final design sizing\n")
    out.append(f"Source: `{_rel(figures_dir / 'sizing.md')}`.\n")
    out.append(_read(figures_dir / "sizing.md").strip() + "\n")

    out.append("## 4. Verification report\n")
    out.append(f"Source: `{_rel(figures_dir / 'verification.md')}` (DesignVerifier, TR-021).\n")
    out.append(_read(figures_dir / "verification.md").strip() + "\n")

    out.append("## 5. Uncertainty and sensitivity\n")
    out.append(
        f"Source: `{cite}/report_draft.md` (section *Uncertainty*; TR-017, TR-018, TR-019).\n"
    )
    out.append(section(draft, "Uncertainty") + "\n")

    out.append("## 6. Model gaps and limitations (TL-17)\n")
    out.append(f"Source: `{cite}/report_draft.md` (section *Model gaps and limitations*).\n")
    out.append(section(draft, "Model gaps and limitations (TL-17)") + "\n")

    out.append("## 7. Decision Records DR-01…DR-11\n")
    out.append(f"Source: `{cite}/decision_records/` (index and one file per record).\n")
    out.append(decision_records(run_dir, out_path) + "\n")

    out.append("## 8. LLM calls and tokens of the final run\n")
    out.append(f"Source: `{cite}/token_summary.json` (every call: `{cite}/llm_calls.jsonl`).\n")
    out.append(token_tables(token_summary) + "\n")

    out.append("## 9. S10 — naive vs optimised context (token saving)\n")
    out.append(
        f"Source: `{_rel(chosen_eval / 'S10-token_comparison.json')}` (runs `S10-naive/`, "
        "`S10-optimised/`).\n"
    )
    out.append(s10_table(s10) + "\n")

    out.append("## 10. S11 — model comparison (same task, one S11 run per model)\n")
    out.append("Sources: " + ", ".join(f"`{s}`" for s in s11_sources) + ".\n")
    out.append(s11 + "\n")
    out.append(
        f"`{_rel(EVAL_ROOT / 'model_comparison.json')}` (generated {comparison['generated_at']}) "
        "sums every scenario in each eval folder; for the chosen model that is S1–S11, so its "
        "like-for-like S11 line is the one above, taken from its `eval_results.json`.\n"
    )

    out.append("## 11. S1–S11 evaluation of the chosen model\n")
    out.append(
        f"Source: `{_rel(chosen_eval / 'eval_results.json')}` (`EVAL_REPORT.md` alongside).\n"
    )
    out.append(scenario_table(chosen_eval / "eval_results.json") + "\n")

    out.append("## 12. S7 — model gap closed by the Model Builder (local run after #23)\n")
    out.append(f"Source: `{_rel(S7_REPORT)}` (evidence folder `docs/results/s7_demo/`).\n")
    out.append(s7_table(s7) + "\n")

    draw_token_figure(token_summary, FIGURES / "fig_tokens_by_phase.png")
    out.append("## 13. Figures\n")
    out.append(
        f"Copies of `{_rel(figures_dir)}/*.png` in `{_rel(FIGURES)}/` (TR-022 `plot_figures`).\n"
    )
    out.append(_row(["Figure", "Caption"]) + "\n" + _row(["---", "---"]))
    for name, caption in CAPTIONS.items():
        out.append(_row([f"`{_rel(FIGURES / name)}`", caption]))
    out.append("")

    out.append("## 14. Assumptions and answers recorded in the run\n")
    out.append(f"Source: `{cite}/run_summary.json` → `assumptions_and_answers`.\n")
    out.extend(f"- {line}" for line in run_summary["assumptions_and_answers"])
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Write the report pack; returns 0."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    text = build(args.run_dir.resolve(), args.out.resolve())
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text.encode('utf-8')) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
