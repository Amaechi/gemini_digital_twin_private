"""TL-21 ``plot_*``: figures, tables and the report draft of a GeoAgent run (spec §6, §13).

Everything is drawn from tool outputs only (golden rule 1): the run state (candidates, KPIs from
TL-12/13/14/15, Decision Records, verifier result) and the files the tools write into
``runs/<id>/`` (TL-12 daily CSV, TL-15 ``uncertainty_<id>.json``, TL-14
``sensitivity_<id>.json``, TL-13 ``optimisation_<concept>.json``, the sized designs and the
``verify_design`` tool result). Nothing is recomputed here except sums, sorts and percentile
look-ups of those outputs for display.

Figures (PNG, modest DPI): candidate comparison, daily stacked heat and cooling supply vs demand,
load-duration curves, annual energy balance (stacked-bar alternative to a Sankey), Monte Carlo
uncertainty fan, tornado, token chart (per-phase LLM tokens from ``token_summary.json``; the
runner writes an interim summary before P10 plots and redraws the chart from the final summary
at run end, ``render_tokens_figure``; a policy run draws a placeholder). Tables
(Markdown + CSV): candidates C0–C8 (+ sized optima), final design sizing, verification report,
uncertainty, Decision Records. Colours: one fixed categorical order per energy source (colour
follows the source, never its rank); a single y-axis per panel.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from matplotlib.figure import Figure

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.results import KPISet

DPI = 110
"""Modest PNG resolution (figures are committed with the results pack)."""
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
"""Validated categorical order (dataviz reference palette, light mode)."""
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

HEAT_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("hex", "Geothermal plate HEX", SERIES[0]),
    ("hthp", "High-temperature heat pump", SERIES[1]),
    ("chp", "CHP (associated gas)", SERIES[2]),
    ("peak_boiler", "Peak boiler (grid gas)", SERIES[3]),
    ("storage", "HT-ATES storage", SERIES[4]),
)
"""Heat-source key prefix → label, fixed colour (DayResult.heat_by_source_mw keys)."""
COOL_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("free_cooling", "Free cooling", SERIES[0]),
    ("absorption", "Absorption chiller", SERIES[1]),
    ("electric_chiller", "Electric chiller", SERIES[2]),
)
INPUT_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("brine", "Brine heat drawn", SERIES[0]),
    ("fuel", "Gas fuel (LHV)", SERIES[1]),
    ("elec", "Electricity consumed", SERIES[2]),
)


# =========================================================================== data view
@dataclass
class RunView:
    """Everything the figures and tables need, from the run state and its files."""

    run_id: str
    run_dir: Path
    candidates: list[Candidate]
    kpis: dict[str, KPISet]
    selected_id: str | None
    shortlist: list[str] = field(default_factory=list)
    decision_records: list[dict[str, Any]] = field(default_factory=list)
    verdict: str | None = None
    status: str | None = None
    answers: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_session(cls, session: Any, context: Any) -> RunView:
        """View of a run in progress (P10 tools): the ``ToolContext`` + the run's files."""
        verification = latest_tool_data(Path(context.run_dir), "verify_design")
        return cls(
            run_id=context.run_id,
            run_dir=Path(context.run_dir),
            candidates=list(context.candidates),
            kpis=dict(context.kpis),
            selected_id=context.selected_id,
            shortlist=list(context.shortlist),
            decision_records=list(context.decision_records),
            verdict=(verification or {}).get("verdict"),
            answers=[f"{k} = {v}" for k, v in context.answers.items()],
        )

    @classmethod
    def from_run(cls, run_dir: Path | str) -> RunView:
        """View of a finished run (``run_summary.json`` + the state in ``events.sqlite``)."""
        from gemini_agent.eval.submission import load_final_run

        run = load_final_run(Path(run_dir))
        state = run.state
        return cls(
            run_id=state.run_id,
            run_dir=Path(run_dir),
            candidates=list(state.candidates),
            kpis=dict(state.kpis),
            selected_id=state.selected_id,
            shortlist=list(state.shortlist),
            decision_records=[r.model_dump(mode="json") for r in state.decision_records],
            verdict=state.verdict_value(),
            status=state.status.value,
            answers=[a.label for a in state.answers],
        )

    # ------------------------------------------------------------------ files
    def candidate(self, candidate_id: str | None) -> Candidate | None:
        """Candidate by id."""
        return next((c for c in self.candidates if c.id == candidate_id), None)

    def daily(self) -> pd.DataFrame | None:
        """TL-12 daily results of the selected design (full mode, else fast)."""
        for mode in ("full", "fast"):
            path = self.run_dir / f"annual_{self.selected_id}_{mode}.csv"
            if path.is_file():
                return pd.read_csv(path)
        return None

    def json_file(self, name: str) -> Any:
        """Return a JSON output of the run (None if absent)."""
        path = self.run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def analysed_id(self) -> str | None:
        """Design TL-15 analysed for the selected one (its source when DR-11 installed backups)."""
        sized = self.sized().get(self.selected_id or "") or {}
        return sized.get("robust_backup_of") or self.selected_id

    def uncertainty(self) -> dict[str, Any] | None:
        """TL-15 report covering the selected design (``analysed_id``)."""
        return self.json_file(f"uncertainty_{self.analysed_id()}.json")

    def sensitivity(self) -> dict[str, Any] | None:
        """TL-14 cost sensitivity covering the selected design (``analysed_id``)."""
        return self.json_file(f"sensitivity_{self.analysed_id()}.json")

    def sized(self) -> dict[str, Any]:
        """TL-13 sized designs (``session/sized_designs.json``)."""
        return self.json_file("session/sized_designs.json") or {}

    def verification(self) -> dict[str, Any] | None:
        """Latest DesignVerifier report (``verify_design`` tool result)."""
        return latest_tool_data(self.run_dir, "verify_design")

    def economics_rows(self) -> dict[str, dict[str, Any]]:
        """Latest TL-14 summary row per candidate (``economics`` tool results)."""
        rows: dict[str, dict[str, Any]] = {}
        for path in sorted((self.run_dir / "tool_results").glob("*_economics.json")):
            data = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
            for row in data.get("economics") or []:
                rows[row["candidate_id"]] = row
        return rows


def latest_tool_data(run_dir: Path, tool: str) -> dict[str, Any] | None:
    """Return ``data`` of the newest ``tool_results/TR-*_<tool>.json`` (None: never ran)."""
    paths = sorted((run_dir / "tool_results").glob(f"TR-*_{tool}.json"))
    if not paths:
        return None
    return json.loads(paths[-1].read_text(encoding="utf-8")).get("data")


# =========================================================================== helpers
def _style(ax: Any, title: str, ylabel: str = "", xlabel: str = "") -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def _figure(ncols: int = 1, width: float = 9.0, height: float = 4.2) -> tuple[Any, Any]:
    fig = Figure(figsize=(width, height), facecolor=SURFACE)  # headless, no pyplot state
    axes = fig.subplots(1, ncols)
    for ax in axes if ncols > 1 else [axes]:
        ax.set_facecolor(SURFACE)
    return fig, axes


def _save(fig: Any, out_dir: Path, name: str, files: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / name, dpi=DPI, facecolor=SURFACE)
    files.append(name)


def _group_columns(frame: pd.DataFrame, prefix: str, groups: tuple[tuple[str, str, str], ...]):
    """Sum the daily ``<prefix>_<key>_mw`` columns into the fixed source groups."""
    out = []
    for key, label, colour in groups:
        cols = [
            c
            for c in frame.columns
            if c.startswith(f"{prefix}_{key}") and c.endswith("_mw") and c != f"{prefix}_mw"
        ]
        if cols:
            series = frame[cols].sum(axis=1)
            if series.abs().sum() > 0:
                out.append((label, colour, series))
    return out


def _num(value: Any, spec: str = ".1f", empty: str = "–") -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return empty
    return format(value, spec)


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


# =========================================================================== figures
def plot_candidates(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Candidate comparison: LCOH + LCOC (stacked) and CO₂ per candidate (two panels)."""
    rows = [
        (c.id, view.kpis[c.id])
        for c in view.candidates
        if c.id in view.kpis and view.kpis[c.id].lcoh_per_mwh is not None
    ]
    if not rows:
        return
    rows.sort(key=lambda r: (r[1].lcoh_per_mwh or 0) + (r[1].lcoc_per_mwh or 0), reverse=True)
    names = [f"{cid}{'  (selected)' if cid == view.selected_id else ''}" for cid, _ in rows]
    lcoh = [k.lcoh_per_mwh or 0.0 for _, k in rows]
    lcoc = [k.lcoc_per_mwh or 0.0 for _, k in rows]
    co2 = [(k.co2_t_per_yr or 0.0) / 1000.0 for _, k in rows]
    fig, (a, b) = _figure(2, width=11.0, height=0.42 * len(rows) + 1.6)
    a.barh(names, lcoh, color=SERIES[0], height=0.62, label="LCOH (heat)")
    a.barh(names, lcoc, left=lcoh, color=SERIES[1], height=0.62, label="LCOC (cooling)",
           edgecolor=SURFACE, linewidth=2)  # fmt: skip
    for y, (h, c) in enumerate(zip(lcoh, lcoc, strict=True)):
        a.text(h + c + 1, y, f"{h + c:.0f}", va="center", fontsize=8, color=INK_2)
    _style(a, "Levelised cost (30 yr, TL-14)", xlabel="USD/MWh")
    a.grid(axis="x", color=GRID, linewidth=0.8)
    a.grid(axis="y", visible=False)
    a.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(0.0, -0.1), ncol=2)
    b.barh(names, co2, color=SERIES[2], height=0.62)
    _style(b, "CO₂ emissions (TL-14, ECO-05)", xlabel="kt CO₂ / yr")
    b.grid(axis="x", color=GRID, linewidth=0.8)
    b.grid(axis="y", visible=False)
    b.set_yticklabels([])
    _save(fig, out_dir, "fig_candidates.png", files)


def plot_daily(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Daily stacked supply by source vs demand (heating and cooling, TL-12 full mode)."""
    frame = view.daily()
    if frame is None:
        return
    dates = pd.to_datetime(frame["date"])
    for side, prefix, groups, demand_col, title in (
        ("heat", "heat", HEAT_GROUPS, "heating_demand_mw", "Daily heat supply by source vs demand"),
        ("cooling", "cool", COOL_GROUPS, "cooling_demand_mw",
         "Daily cooling supply by source vs demand"),
    ):  # fmt: skip
        parts = _group_columns(frame, prefix, groups)
        fig, ax = _figure(1, width=11.0, height=4.0)
        positive = [(lab, col, s.clip(lower=0)) for lab, col, s in parts]
        if positive:
            ax.stackplot(
                dates,
                *[s for _, _, s in positive],
                labels=[lab for lab, _, _ in positive],
                colors=[col for _, col, _ in positive],
                linewidth=0,
            )
        ax.plot(dates, frame[demand_col], color=INK, linewidth=1.2, label="Demand")
        _style(ax, f"{title} — {view.selected_id}", ylabel="MW (day average)")
        ax.legend(frameon=False, fontsize=8, ncol=len(positive) + 1, loc="upper center")
        ax.set_ylim(bottom=0)
        _save(fig, out_dir, f"fig_daily_{side}.png", files)


def plot_load_duration(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Load-duration curves: demand sorted high → low, supply by source along that order."""
    frame = view.daily()
    if frame is None:
        return
    fig, axes = _figure(2, width=11.0, height=4.0)
    for ax, prefix, groups, demand_col, title in (
        (axes[0], "heat", HEAT_GROUPS, "heating_demand_mw", "Heating load duration"),
        (axes[1], "cool", COOL_GROUPS, "cooling_demand_mw", "Cooling load duration"),
    ):
        order = frame[demand_col].sort_values(ascending=False).index
        days = range(1, len(order) + 1)
        parts = _group_columns(frame.loc[order].reset_index(drop=True), prefix, groups)
        if parts:
            ax.stackplot(
                days,
                *[s.clip(lower=0) for _, _, s in parts],
                labels=[lab for lab, _, _ in parts],
                colors=[col for _, col, _ in parts],
                linewidth=0,
            )
        ax.plot(days, frame.loc[order, demand_col].to_numpy(), color=INK, linewidth=1.2,
                label="Demand")  # fmt: skip
        _style(ax, title, ylabel="MW (day average)", xlabel="days, sorted by demand")
        ax.legend(frameon=False, fontsize=7, loc="upper right")
        ax.set_ylim(bottom=0)
    _save(fig, out_dir, "fig_load_duration.png", files)


def plot_energy_balance(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Annual energy balance (stacked bars; Sankey alternative): heat, cooling, inputs (GWh)."""
    frame = view.daily()
    if frame is None:
        return
    panels = []
    for title, prefix, groups in (
        ("Heat delivered", "heat", HEAT_GROUPS),
        ("Cooling delivered", "cool", COOL_GROUPS),
        ("Inputs", None, INPUT_GROUPS),
    ):
        if prefix is not None:
            parts = [(lab, col, s.clip(lower=0).sum() * 24 / 1000) for lab, col, s in
                     _group_columns(frame, prefix, groups)]  # fmt: skip
        else:
            parts = []
            for key, lab, col in groups:
                cols = [c for c in frame.columns if c.startswith(f"{key}_") and c.endswith("_mw")]
                total = frame[cols].clip(lower=0).sum().sum() * 24 / 1000 if cols else 0.0
                if total > 0:
                    parts.append((lab, col, total))
        panels.append((title, parts))
    fig, axes = _figure(3, width=12.0, height=3.6)
    for ax, (title, parts) in zip(axes, panels, strict=True):
        left = 0.0
        for lab, col, value in parts:
            ax.barh([0], [value], left=left, color=col, height=0.5, label=f"{lab}: {value:.1f}",
                    edgecolor=SURFACE, linewidth=2)  # fmt: skip
            left += value
        _style(ax, f"{title} ({left:.1f} GWh/yr)", xlabel="GWh / yr")
        ax.set_yticks([])
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.grid(axis="y", visible=False)
        ax.legend(frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.25))
    _save(fig, out_dir, "fig_energy_balance.png", files)


def plot_uncertainty(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Monte Carlo fan: sorted samples (CDF) with the P10–P90 band, LCOH and LCOC panels."""
    report = view.uncertainty()
    if not report or not report.get("samples"):
        return
    fig, axes = _figure(2, width=11.0, height=4.0)
    for ax, kpi, label, colour in (
        (axes[0], "lcoh_per_mwh", "LCOH", SERIES[0]),
        (axes[1], "lcoc_per_mwh", "LCOC", SERIES[1]),
    ):
        values = sorted(
            s["kpis"][kpi] for s in report["samples"] if s.get("kpis", {}).get(kpi) is not None
        )
        pct = report.get("percentiles", {}).get(kpi, {})
        if not values:
            continue
        probs = [(i + 0.5) / len(values) for i in range(len(values))]
        if "p10" in pct and "p90" in pct:
            ax.axvspan(pct["p10"], pct["p90"], color=colour, alpha=0.12, linewidth=0,
                       label=f"P10–P90 {pct['p10']:.1f}–{pct['p90']:.1f}")  # fmt: skip
        ax.plot(values, probs, color=colour, linewidth=2, label=f"{len(values)} samples")
        if "p50" in pct:
            ax.axvline(pct["p50"], color=INK, linewidth=1, label=f"P50 {pct['p50']:.1f}")
        base = (report.get("base") or {}).get(kpi)
        if base is not None:
            ax.axvline(base, color=INK_2, linewidth=1, linestyle="--", label=f"base {base:.1f}")
        _style(ax, f"{label} uncertainty (TL-15 Monte Carlo, {report.get('candidate_id')})",
               ylabel="cumulative probability", xlabel="USD/MWh")  # fmt: skip
        ax.legend(frameon=False, fontsize=8, loc="lower right")
    _save(fig, out_dir, "fig_uncertainty.png", files)


def tornado_bars(view: RunView) -> tuple[str, list[tuple[str, float, float, float]]]:
    """(title, [(input, base, at_low, at_high)]): TL-15 one-at-a-time, else TL-14 cost bars."""
    report = view.uncertainty() or {}
    oat = [
        b for b in report.get("one_at_a_time") or [] if b.get("objective_low_per_mwh") is not None
    ]
    base = (report.get("base") or {}).get("objective_per_mwh")
    if oat and base is not None:
        bars = [
            (b["input_name"], base, b["objective_low_per_mwh"], b["objective_high_per_mwh"])
            for b in oat
            if b.get("objective_high_per_mwh") is not None
        ][:10]
        return "Objective swing per input: LCOH + LCOC + unmet penalty (TL-15 OAT)", bars
    sens = (view.sensitivity() or {}).get("lcoh") or {}
    bars = [
        (b["input_name"], sens["kpi_base"], b["kpi_at_low"], b["kpi_at_high"])
        for b in sens.get("bars") or []
    ][:10]
    return "LCOH swing per cost input (TL-14 cost sensitivity)", bars


def plot_tornado(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Tornado chart around the base value (input low = slot 1, input high = slot 2)."""
    title, bars = tornado_bars(view)
    if not bars:
        return
    bars = sorted(bars, key=lambda b: abs(b[3] - b[2]))
    fig, ax = _figure(1, width=10.0, height=0.42 * len(bars) + 1.5)
    base = bars[0][1]
    names = [b[0] for b in bars]
    ax.barh(names, [b[2] - base for b in bars], left=base, color=SERIES[0], height=0.6,
            label="input at low value")  # fmt: skip
    ax.barh(names, [b[3] - base for b in bars], left=base, color=SERIES[1], height=0.6,
            label="input at high value")  # fmt: skip
    ax.axvline(base, color=INK, linewidth=1)
    _style(ax, title, xlabel=f"USD/MWh (base {base:.1f})")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    _save(fig, out_dir, "fig_tornado.png", files)


TOKENS_FIGURE = "fig_tokens.png"
TOKEN_SUMMARY = "token_summary.json"


def tokens_by_phase(summary: dict[str, Any] | None) -> dict[str, float]:
    """Input + output tokens per phase from a ``token_summary`` dict (empty → no LLM tokens)."""
    by_phase = (summary or {}).get("by_phase") or {}
    return {
        p: (v.get("input_tokens", 0) + v.get("output_tokens", 0)) if isinstance(v, dict) else 0
        for p, v in by_phase.items()
    }


def plot_token_summary(summary: dict[str, Any] | None, out_dir: Path, files: list[str]) -> bool:
    """Draw ``fig_tokens.png`` from a token summary; return True when it holds real tokens."""
    tokens = tokens_by_phase(summary)
    filled = any(tokens.values())
    fig, ax = _figure(1, width=9.0, height=3.2)
    if filled:
        ax.bar(list(tokens), list(tokens.values()), color=SERIES[0], width=0.6)
        _style(ax, "LLM tokens per phase", ylabel="tokens")
    else:
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            "Token chart placeholder\nno LLM tokens were logged for this run (deterministic "
            "policy engine);\nan LLM run fills it from token_summary.json",
            ha="center",
            va="center",
            fontsize=11,
            color=INK_2,
        )
    _save(fig, out_dir, TOKENS_FIGURE, files)
    return filled


def plot_tokens(view: RunView, out_dir: Path, files: list[str]) -> None:
    """Token chart: per-phase tokens from ``token_summary.json``; placeholder for policy runs."""
    plot_token_summary(view.json_file(TOKEN_SUMMARY), out_dir, files)


def render_tokens_figure(run_dir: Path | str, figures_dir: Path | str | None = None) -> bool:
    """Redraw ``figures/fig_tokens.png`` of a run from its ``token_summary.json`` (figure only).

    Used by the runner at run end (the P10 plot ran before the last LLM calls of the run were
    summarised) and by ``scripts/regenerate_fig_tokens.py``. Returns True when the chart holds
    real tokens; nothing else in the run folder is touched.
    """
    run_dir = Path(run_dir)
    out_dir = Path(figures_dir) if figures_dir is not None else run_dir / "figures"
    path = run_dir / TOKEN_SUMMARY
    summary = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    out_dir.mkdir(parents=True, exist_ok=True)
    return plot_token_summary(summary, out_dir, [])


# =========================================================================== tables
def _short_reason(reason: str | None) -> str | None:
    """Short form of a DesignGuard / verifier pre-check reason for the table."""
    if not reason:
        return None
    for marker in ("PH-19", "PH-30", "DB-"):
        if marker in reason:
            return f"excluded ({marker})" if marker != "DB-" else "excluded (hard limit)"
    return "excluded"


def candidates_table(view: RunView) -> tuple[str, pd.DataFrame]:
    """Candidate comparison (C0–C8 + sized optima): structure and KPIs from TL-12/13/14."""
    eco = view.economics_rows()
    guard = latest_tool_data(view.run_dir, "design_guard") or {}
    excluded = {
        c["candidate_id"]: (c.get("violations") or ["DesignGuard"])[0]
        for c in guard.get("checks") or []
        if not c.get("passed")
    }
    records = []
    for c in view.candidates:
        k = view.kpis.get(c.id)
        if k is None:
            continue
        row = eco.get(c.id, {})
        records.append(
            {
                "candidate": c.id + (" *" if c.id == view.selected_id else ""),
                "concept": c.concept,
                "doublets": c.doublets,
                "depth_m": c.depth_m,
                "flow_m3h": c.design_flow_m3h,
                "unmet_heat_d": k.unmet_heating_days,
                "unmet_cool_d": k.unmet_cooling_days,
                "capex_musd": row.get("capex_musd", k.capex_million),
                "lcoh_usd_mwh": k.lcoh_per_mwh,
                "lcoc_usd_mwh": k.lcoc_per_mwh,
                "co2_t_yr": k.co2_t_per_yr,
                "co2_cut_vs_c0": k.co2_reduction_vs_c0_frac,
                "exergy_eff": k.exergy_efficiency_frac,
                "excluded": _short_reason(excluded.get(c.id)),
            }
        )
    frame = pd.DataFrame(records)
    fmt = {
        "depth_m": ".0f", "flow_m3h": ".0f", "capex_musd": ".1f", "lcoh_usd_mwh": ".1f",
        "lcoc_usd_mwh": ".1f", "co2_t_yr": ".0f", "co2_cut_vs_c0": ".0%", "exergy_eff": ".2f",
    }  # fmt: skip
    rows = []
    for rec in records:
        rows.append(
            [
                (
                    _num(rec[col], fmt[col])
                    if col in fmt
                    else ("–" if rec[col] is None else str(rec[col]))
                )
                for col in frame.columns
            ]
        )
    md = _md_table(list(frame.columns), rows) if records else "No candidate KPIs.\n"
    return md, frame


def sizing_table(view: RunView) -> str:
    """Return the final design sizing: candidate, TL-13 sizes, outages, key checks."""
    c = view.candidate(view.selected_id)
    if c is None:
        return "No selected design.\n"
    k = view.kpis.get(c.id)
    sized = view.sized().get(c.id, {})
    source = sized.get("robust_backup_of")  # DR-11 variant: backups above the source's
    source_sizes = ((view.sized().get(source) or {}).get("candidate") or {}).get(
        "equipment_sizes", {}
    )
    rows = [
        ["Design", c.id, c.description],
        ["Doublets", str(c.doublets), "DB-10: 1–3"],
        ["Reservoir depth", _num(c.depth_m, ".0f") + " m", "TL-13 (verifiable depth options)"],
        ["Design flow per doublet", _num(c.design_flow_m3h, ".0f") + " m³/h", "DB-11: 50–300"],
        ["Gas handling", str(c.gas_handling.value if c.gas_handling else "–"), "DR-07"],
        ["Heating return (DB-31)", _num(c.heating_return_degc, ".0f") + " °C", "supply 100 °C"],
        ["Cooling return (DB-32)", _num(c.cooling_return_degc, ".0f") + " °C", "supply 20 °C"],
    ]
    for key, value in sorted(c.equipment_sizes.items()):
        basis = "TL-13 size / right-sized backup"
        if source and value != source_sizes.get(key):
            basis = (
                f"TL-15 robust backup (DR-11, OPT-ROBUST-BACKUP); {source}: "
                f"{_num(source_sizes.get(key), '.2f')}"
            )
        rows.append([key, _num(value, ".2f"), basis])
    if sized.get("outage_start_indices"):
        rows.append(["Outage start days (0-based)", str(sized["outage_start_indices"]), "DR-06"])
    if sized.get("excluded_depths_m"):
        rows.append(
            ["Depths excluded", str(sized["excluded_depths_m"]), "PH-19 NPSH / PH-30 letdown"]
        )
    if k is not None:
        rows += [
            ["Installed heating", _num(k.installed_heating_mw, ".1f") + " MW", "TL-12"],
            ["Installed cooling", _num(k.installed_cooling_mw, ".1f") + " MW", "TL-12"],
            ["Doublet capacity factors (flow-based)", ", ".join(
                _num(v, ".2f") for v in k.doublet_capacity_factors_frac), "TL-12 mean/design"],
            ["Thermal breakthrough (annual-mean flow)", _num(k.thermal_breakthrough_yr, ".1f")
             + " yr", "TL-12 end of life via TL-07"],
        ]  # fmt: skip
    sub = view.json_file(f"session/subsurface_{c.id}.json") or {}
    point = sub.get("operating_point") or {}
    for label, value, spec, unit, basis in (
        ("Wellhead temperature (design flow)", point.get("wellhead_temperature_degc"), ".1f",
         "°C", "TL-05"),
        ("ESP frequency", point.get("esp_frequency_hz"), ".1f", "Hz", "TL-05"),
        ("ESP electric power per doublet", (point.get("esp_electric_power_w") or 0) / 1e6,
         ".3f", "MW", "TL-05 QΔp/0.70/(0.93·0.97)"),
        ("ESP intake margin over 1.2 · p_b", point.get("intake_margin_bar"), ".2f", "bar",
         "TL-05 (SUB-PBUB basis)"),
        ("Injector wellhead pressure", point.get("injector_wellhead_pressure_barg"), ".1f",
         "bar(g)", "TL-05"),
        ("Injection pump electric power per doublet",
         (point.get("injection_pump_electric_power_w") or 0) / 1e6, ".3f", "MW", "TL-05"),
        ("Thermal breakthrough (design flow)", sub.get("breakthrough_time_yr"), ".1f", "yr",
         "TL-07, ≥ 30 yr (ENG-06)"),
        ("Doublet separation", sub.get("doublet_separation_m"), ".0f", "m",
         "TL-08 minimum for ≤ 1 bar"),
        ("Well spacing", sub.get("well_spacing_m"), ".0f", "m", "SUB-SPACING (≥ 1.2 km)"),
    ):  # fmt: skip
        if point or sub:
            rows.append([label, f"{_num(value, spec)} {unit}", basis])
    if sub.get("scaling_status"):
        rows.append(
            ["Scaling screen (TL-22)", sub["scaling_status"], ", ".join(sub["scaling_mitigations"])]
        )
    report = view.verification() or {}
    for check in report.get("checks") or []:
        if check.get("check_id") in {"PH-02", "PH-18", "PD-04", "PD-05", "PH-30"}:
            rows.append(
                [
                    f"{check['check_id']} {check['name']}",
                    f"worst {_num(check.get('value'), '.3g')} {check.get('unit', '')}",
                    f"margin {_num(check.get('margin'), '.3g')} (verifier)",
                ]
            )
    return _md_table(["Item", "Value", "Basis"], rows)


def verification_table(view: RunView) -> str:
    """Return the DesignVerifier report: verdict, violations and every check with margin."""
    report = view.verification()
    if not report:
        return "No verification report.\n"
    head = (
        f"**Verdict: {report.get('verdict')}** ({report.get('verifier')}) — "
        f"{report.get('summary', '')}\n\n"
        f"Violations: {report.get('violations') or 'none'}; not evaluated: "
        f"{report.get('not_evaluated') or 'none'}\n\n"
    )
    rows = [
        [
            c["check_id"],
            c.get("layer", ""),
            c.get("name", ""),
            str(c.get("status", "")),
            _num(c.get("value"), ".4g"),
            _num(c.get("limit"), ".4g"),
            _num(c.get("margin"), ".4g"),
            c.get("unit", ""),
        ]
        for c in report.get("checks") or []
    ]
    return head + _md_table(
        ["Check", "Layer", "Name", "Status", "Value", "Limit", "Margin", "Unit"], rows
    )


def uncertainty_table(view: RunView) -> str:
    """TL-15 percentiles, probability of meeting demand and the sensitivity cases."""
    report = view.uncertainty()
    if not report:
        return "No uncertainty analysis.\n"
    analysed = report.get("candidate_id")
    head = f"TL-15 on {analysed}"
    if analysed != view.selected_id:
        head += (
            f" (base-year backups); the selected {view.selected_id} installs its robust backups "
            "(DR-11, OPT-ROBUST-BACKUP)"
        )
    rows = [
        [kpi] + [_num(v.get(p), ".2f") for p in ("p10", "p50", "p90")]
        for kpi, v in sorted((report.get("percentiles") or {}).items())
    ]
    text = head + ".\n\n" + _md_table(["KPI", "P10", "P50", "P90"], rows)
    text += (
        f"\nSamples {len(report.get('samples') or [])} (failed {report.get('failed_samples')}); "
        f"P(demand met) at installed backups {report.get('probability_demand_met')}; robust "
        f"backups {report.get('robust_backup_mw')} → P "
        f"{report.get('probability_demand_met_robust')}"
        "\n\n"
    )
    cases = [
        [
            c["case"],
            str(c.get("assumption_id")),
            str(c.get("value")),
            _num((c.get("evaluation") or {}).get("lcoh_per_mwh"), ".1f"),
            _num((c.get("evaluation") or {}).get("lcoc_per_mwh"), ".1f"),
            _num(c.get("unmet_mwh"), ".0f"),
        ]
        for c in report.get("cases") or []
    ]
    if cases:
        text += _md_table(["Case", "Register id", "Value", "LCOH", "LCOC", "Unmet MWh"], cases)
    return text


def decision_records_markdown(view: RunView) -> str:
    """All Decision Records (the runner's ``decision_records/*.md``, else from the state)."""
    folder = view.run_dir / "decision_records"
    parts = [p.read_text(encoding="utf-8") for p in sorted(folder.glob("DR-*.md"))]
    if parts:
        return "\n\n".join(parts)
    lines = []
    for r in view.decision_records:
        lines.append(f"## {r['dr_id']} {r['title']}\n\nChoice: {r['choice_label']}\n")
    return "\n".join(lines) or "No Decision Records.\n"


# =========================================================================== entry points
FIGURES = (
    plot_candidates,
    plot_daily,
    plot_load_duration,
    plot_energy_balance,
    plot_uncertainty,
    plot_tornado,
    plot_tokens,
)


def render_all(view: RunView, out_dir: Path) -> tuple[list[str], list[str]]:
    """Write every figure and table into ``out_dir``; return (figure files, table files)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for plot in FIGURES:
        plot(view, out_dir, files)
    md, frame = candidates_table(view)
    tables = {
        "candidates.md": md,
        "sizing.md": sizing_table(view),
        "verification.md": verification_table(view),
        "uncertainty.md": uncertainty_table(view),
        "decision_records.md": decision_records_markdown(view),
    }
    for name, text in tables.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    frame.to_csv(out_dir / "candidates.csv", index=False)
    return files, [*tables, "candidates.csv"]


def report_markdown(view: RunView, figure_dir: str = "figures/") -> tuple[str, list[str]]:
    """Report draft (answer first, alternatives, design, performance, verification, …).

    ``figure_dir`` prefixes the figure links (``figures/`` inside a run folder).
    """
    c = view.candidate(view.selected_id)
    k = view.kpis.get(view.selected_id or "")
    sections = []
    lines = [f"# GeoAgent design report — run {view.run_id}", ""]

    sections.append("Answer")
    lines += ["## Answer", ""]
    if c is not None and k is not None:
        lines += [
            f"- Recommended design: **{c.id}** — {c.description}",
            f"- Verifier verdict: **{view.verdict or 'not verified'}**",
            (
                f"- Unmet demand: heating {k.unmet_heating_days} d / "
                f"{_num(k.unmet_heating_mwh, '.0f')} MWh, cooling {k.unmet_cooling_days} d / "
                f"{_num(k.unmet_cooling_mwh, '.0f')} MWh"
            ),
            (
                f"- LCOH {_num(k.lcoh_per_mwh)} USD/MWh, LCOC {_num(k.lcoc_per_mwh)} USD/MWh "
                f"(30 yr, TL-14); CAPEX {_num(k.capex_million)} MUSD"
            ),
            (
                f"- CO₂ {_num(k.co2_t_per_yr, '.0f')} t/yr, "
                f"{_num(k.co2_reduction_vs_c0_frac, '.0%')} below the C0 counterfactual"
            ),
            (
                f"- Exergy efficiency {_num(k.exergy_efficiency_frac, '.2f')} "
                f"({k.exergy_dead_state or 'dead state n/a'})"
            ),
        ]
        uq = view.uncertainty() or {}
        if uq.get("probability_demand_met") is not None:
            analysed = view.analysed_id()
            dr11 = next((r for r in view.decision_records if r.get("dr_id") == "DR-11"), None)
            if analysed != view.selected_id:
                status = f"installed by DR-11, {(dr11 or {}).get('decision_source', 'n/a')}"
            elif dr11 is not None:
                status = f"not installed: DR-11 chose {dr11['choice']}"
            else:
                status = "not installed: no DR-11"
            lines.append(
                f"- Robustness (TL-15 on {analysed}, {len(uq.get('samples') or [])} samples): "
                f"demand met in {uq['probability_demand_met']:.0%} of samples with the "
                f"base-year backups {uq.get('installed_backup_mw')} MW; robust backups "
                f"{uq.get('robust_backup_mw')} MW → "
                f"{_num(uq.get('probability_demand_met_robust'), '.0%')} ({status})"
            )
        backup = latest_tool_data(view.run_dir, "robust_backup") or {}
        if backup.get("robust_candidate_id"):
            lines.append(
                f"- Backup capacity (DR-11): {backup['robust_candidate_id']} vs "
                f"{backup['candidate_id']}: ΔLCOH {_num(backup.get('delta_lcoh_per_mwh'), '+.2f')}"
                f", ΔLCOC {_num(backup.get('delta_lcoc_per_mwh'), '+.2f')} USD/MWh, ΔCAPEX "
                f"{_num(backup.get('delta_capex_million'), '+.2f')} MUSD "
                f"({backup.get('year_mode')} year, TL-12 + TL-14)"
            )
    else:
        lines.append("No design selected.")
    opt = latest_tool_data(view.run_dir, "optimise_design") or {}
    notes = "".join(
        f"\n- TL-13 {d['candidate_id']} (from {d['from']}): depths excluded "
        f"{d.get('excluded_depths_m') or 'none'} (verifier PH-19/PH-30, `depth_check`)"
        for d in opt.get("designs") or []
    ) + "".join(f"\n- {cid}: {why}" for cid, why in (opt.get("skipped") or {}).items())
    alternatives = candidates_table(view)[0] + (
        "\nRows `C*-Nd` are the spec §3.4 screening points (TL-12 fast year); `*-opt` rows are "
        "the TL-13 optima (P6); a `*-robust` row is an optimum with its TL-15 robust backups "
        "(P8, DR-11); `*` marks the selected design." + notes + "\n"
    )
    for title, body in (
        ("Alternatives compared (C0–C8)", alternatives),
        ("Final design sizing", sizing_table(view)),
        ("Verification", verification_table(view)),
        ("Uncertainty", uncertainty_table(view)),
    ):
        sections.append(title)
        lines += ["", f"## {title}", "", body]
    sections.append("Figures")
    lines += ["", "## Figures", ""]
    for name in (
        "fig_candidates.png", "fig_daily_heat.png", "fig_daily_cooling.png",
        "fig_load_duration.png", "fig_energy_balance.png", "fig_uncertainty.png",
        "fig_tornado.png", "fig_tokens.png",
    ):  # fmt: skip
        lines.append(f"![{name}]({figure_dir}{name})")
    gaps = latest_tool_data(view.run_dir, "model_gap_analysis") or {}
    if gaps.get("limitations") or gaps.get("missing_component_types"):
        sections.append("Model gaps and limitations")
        lines += ["", "## Model gaps and limitations (TL-17)", ""]
        lines.append(f"- Missing component types: {gaps.get('missing_component_types') or 'none'}")
        lines += [
            f"- {lim.get('id')} {lim.get('title')}: {lim.get('impact', '')}"
            for lim in gaps.get("limitations") or []
        ]
    sections.append("Decision Records")
    lines += ["", "## Decision Records", ""]
    for r in view.decision_records:
        criterion = (r.get("criterion") or {}).get("id")  # #19: decisive criterion + evidence
        why = f"; criterion {criterion}" if criterion else ""
        lines.append(
            f"- {r['dr_id']} {r['title']}: {r['choice_label']} ({r['decision_source']}{why})"
        )
    if view.answers:
        sections.append("Assumptions and answers")
        lines += ["", "## Assumptions and answers", ""] + [f"- {a}" for a in view.answers]
    return "\n".join(lines) + "\n", sections


__all__ = [
    "FIGURES",
    "TOKENS_FIGURE",
    "RunView",
    "candidates_table",
    "decision_records_markdown",
    "latest_tool_data",
    "plot_token_summary",
    "render_all",
    "render_tokens_figure",
    "report_markdown",
    "sizing_table",
    "tokens_by_phase",
    "tornado_bars",
    "uncertainty_table",
    "verification_table",
]
