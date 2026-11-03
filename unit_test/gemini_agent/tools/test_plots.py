"""Unit tests for gemini_agent.tools.plots (TL-21 figures, tables, report; spec §6, §13)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import plots


def _candidate(cid: str, concept: str, doublets: int) -> Candidate:
    wells = doublets > 0
    return Candidate(
        id=cid,
        concept=concept,  # type: ignore[arg-type]
        doublets=doublets,
        depth_m=3000.0 if wells else None,
        design_flow_m3h=250.0 if wells else None,
        gas_handling=GasHandling.DEGAS_USE if wells else None,
        heating_return_degc=55.0,
        cooling_return_degc=26.0,
        equipment_sizes={"peak_boiler_mw": 4.5} if wells else {},
    )


def _view(run_dir: Path) -> plots.RunView:
    """A run folder holding the files the tools write (values are made up for layout only)."""
    days = pd.DataFrame(
        {
            "date": ["2027-01-01", "2027-07-01", "2027-12-01"],
            "heating_demand_mw": [20.0, 3.0, 15.0],
            "cooling_demand_mw": [0.5, 9.0, 1.0],
            "heat_hex_1_mw": [12.0, 3.0, 10.0],
            "heat_peak_boiler_mw": [8.0, 0.0, 5.0],
            "cool_free_cooling_mw": [0.5, 0.0, 1.0],
            "cool_electric_chiller_mw": [0.0, 9.0, 0.0],
            "brine_hex_1_mw": [12.0, 3.0, 10.0],
            "fuel_peak_boiler_mw": [8.9, 0.0, 5.6],
            "elec_electric_chiller_mw": [0.0, 2.0, 0.0],
        }
    )
    run_dir.mkdir(parents=True)
    days.to_csv(run_dir / "annual_C1-opt_full.csv", index=False)
    samples = [
        {"kpis": {"lcoh_per_mwh": 30.0 + i, "lcoc_per_mwh": 20.0 + i / 2}} for i in range(10)
    ]
    uq = {
        "samples": samples,
        "percentiles": {"lcoh_per_mwh": {"p10": 31.0, "p50": 34.5, "p90": 38.0}},
        "base": {"lcoh_per_mwh": 33.0, "lcoc_per_mwh": 22.0, "objective_per_mwh": 55.0},
        "one_at_a_time": [],
        "cases": [],
    }
    (run_dir / "uncertainty_C1-opt.json").write_text(json.dumps(uq), "utf-8")
    sens = {
        "lcoh": {
            "kpi_base": 33.0,
            "bars": [
                {"input_name": "well cost", "kpi_at_low": 29.0, "kpi_at_high": 37.0},
                {"input_name": "gas price", "kpi_at_low": 32.0, "kpi_at_high": 34.0},
            ],
        }
    }
    (run_dir / "sensitivity_C1-opt.json").write_text(json.dumps(sens), "utf-8")
    report = {
        "verdict": "ACCEPTED",
        "verifier": "DesignVerifier",
        "summary": "C1-opt ACCEPTED",
        "violations": [],
        "not_evaluated": [],
        "checks": [
            {"check_id": "PH-30", "layer": "physical", "name": "letdown", "status": "pass",
             "value": 99.4, "limit": 100.0, "margin": 0.6, "unit": "degC"},
        ],
    }  # fmt: skip
    (run_dir / "tool_results").mkdir()
    (run_dir / "tool_results" / "TR-020_verify_design.json").write_text(
        json.dumps({"data": report}), "utf-8"
    )
    guard = {
        "checks": [
            {"candidate_id": "C0-0d", "passed": True, "violations": []},
            {"candidate_id": "C4-2d", "passed": False,
             "violations": ["verifier pre-check: depth 3200 m — NPSH (SUB-NPSH, PH-19)"]},
        ]
    }  # fmt: skip
    (run_dir / "tool_results" / "TR-010_design_guard.json").write_text(
        json.dumps({"data": guard}), "utf-8"
    )
    (run_dir / "session").mkdir()
    sub = {
        "operating_point": {"wellhead_temperature_degc": 85.2, "esp_frequency_hz": 58.9},
        "doublet_separation_m": 1141.0,
        "breakthrough_time_yr": 34.4,
        "scaling_status": "insufficient data",
        "scaling_mitigations": ["calcite_inhibitor"],
    }
    (run_dir / "session" / "subsurface_C1-opt.json").write_text(json.dumps(sub), "utf-8")
    gaps = {
        "missing_component_types": [],
        "limitations": [{"id": "LIM-SUB-01", "title": "ESP has no flow affinity", "impact": "x"}],
    }
    (run_dir / "tool_results" / "TR-011_model_gap_analysis.json").write_text(
        json.dumps({"data": gaps}), "utf-8"
    )
    kpis = {
        "C0-0d": KPISet(candidate_id="C0-0d", lcoh_per_mwh=137.0, lcoc_per_mwh=36.0),
        "C4-2d": KPISet(candidate_id="C4-2d", lcoh_per_mwh=29.4, lcoc_per_mwh=38.7),
        "C1-opt": KPISet(
            candidate_id="C1-opt",
            lcoh_per_mwh=33.0,
            lcoc_per_mwh=22.0,
            co2_t_per_yr=4000.0,
            co2_reduction_vs_c0_frac=0.85,
            unmet_heating_days=0,
            unmet_cooling_days=0,
        ),
    }
    return plots.RunView(
        run_id="r1",
        run_dir=run_dir,
        candidates=[
            _candidate("C0-0d", "C0", 0),
            _candidate("C1-opt", "C1", 3),
            _candidate("C4-2d", "C4", 2),
        ],
        kpis=kpis,
        selected_id="C1-opt",
        decision_records=[
            {
                "dr_id": "DR-01",
                "title": "Doublets",
                "choice_label": "3",
                "decision_source": "policy",
            }
        ],
        verdict="ACCEPTED",
    )


def test_render_all_writes_every_figure_and_table(tmp_path):
    """Figures and tables come from the run's files; the token chart is a placeholder."""
    view = _view(tmp_path / "run")
    files, tables = plots.render_all(view, tmp_path / "out")
    assert set(files) == {
        "fig_candidates.png", "fig_daily_heat.png", "fig_daily_cooling.png",
        "fig_load_duration.png", "fig_energy_balance.png", "fig_uncertainty.png",
        "fig_tornado.png", "fig_tokens.png",
    }  # fmt: skip
    for name in files + tables:
        assert (tmp_path / "out" / name).stat().st_size > 0
    table = (tmp_path / "out" / "candidates.md").read_text("utf-8")
    assert "C1-opt *" in table and "85%" in table
    assert "excluded (PH-19)" in table  # verifier pre-check reason travels to the table
    sizing = (tmp_path / "out" / "sizing.md").read_text("utf-8")
    assert "Doublet separation | 1141 m" in sizing and "Wellhead temperature" in sizing
    assert "Thermal breakthrough (design flow) | 34.4 yr" in sizing
    assert "Verdict: ACCEPTED" in (tmp_path / "out" / "verification.md").read_text("utf-8")


def test_tornado_falls_back_to_the_cost_sensitivity(tmp_path):
    """Without TL-15 one-at-a-time bars the TL-14 cost tornado is drawn."""
    title, bars = plots.tornado_bars(_view(tmp_path / "run"))
    assert "TL-14" in title
    assert bars[0] == ("well cost", 33.0, 29.0, 37.0)


def test_report_puts_the_answer_first(tmp_path):
    """Report draft: answer section with KPIs from the state, then the tables and figures."""
    text, sections = plots.report_markdown(_view(tmp_path / "run"), figure_dir="")
    assert sections[0] == "Answer" and "Figures" in sections
    assert "**C1-opt**" in text and "LCOH 33.0 USD/MWh" in text and "85% below" in text
    assert "![fig_daily_heat.png](fig_daily_heat.png)" in text
    assert "LIM-SUB-01 ESP has no flow affinity" in text  # WP-12 limitations reported


def test_dr11_variant_reports_its_tl15_source_and_the_change(tmp_path):
    """Pre-L-2: a DR-11 robust-backup variant reads the TL-15 analysis of its source design."""
    view = _view(tmp_path / "run")
    run_dir = view.run_dir
    robust = _candidate("C1-opt", "C1", 3).model_copy(
        update={"id": "C1-opt-robust", "equipment_sizes": {"peak_boiler_mw": 9.0}}
    )
    sized = {
        "C1-opt": {"candidate": _candidate("C1-opt", "C1", 3).model_dump(mode="json")},
        "C1-opt-robust": {
            "candidate": robust.model_dump(mode="json"),
            "robust_backup_of": "C1-opt",
        },
    }
    (run_dir / "session" / "sized_designs.json").write_text(json.dumps(sized), "utf-8")
    uq = json.loads((run_dir / "uncertainty_C1-opt.json").read_text("utf-8"))
    uq |= {
        "candidate_id": "C1-opt",
        "probability_demand_met": 0.46,
        "probability_demand_met_robust": 0.9,
        "installed_backup_mw": {"peak_boiler_mw": 4.5},
        "robust_backup_mw": {"peak_boiler_mw": 9.0},
    }
    (run_dir / "uncertainty_C1-opt.json").write_text(json.dumps(uq), "utf-8")
    step = {
        "candidate_id": "C1-opt",
        "robust_candidate_id": "C1-opt-robust",
        "delta_lcoh_per_mwh": 2.05,
        "delta_lcoc_per_mwh": 0.0,
        "delta_capex_million": 2.2,
        "year_mode": "full",
    }
    (run_dir / "tool_results" / "TR-019_robust_backup.json").write_text(
        json.dumps({"data": step}), "utf-8"
    )
    (run_dir / "annual_C1-opt-robust_full.csv").write_text(
        (run_dir / "annual_C1-opt_full.csv").read_text("utf-8"), "utf-8"
    )
    view.candidates.append(robust)
    view.kpis["C1-opt-robust"] = view.kpis["C1-opt"].model_copy(
        update={"candidate_id": "C1-opt-robust", "lcoh_per_mwh": 35.2}
    )
    view.selected_id = "C1-opt-robust"
    view.decision_records.append(
        {"dr_id": "DR-11", "title": "Backup", "choice": "p90-robust-backup",
         "choice_label": "robust", "decision_source": "llm"}
    )  # fmt: skip
    assert view.analysed_id() == "C1-opt" and view.uncertainty()["candidate_id"] == "C1-opt"
    assert view.sensitivity() is not None
    sizing = plots.sizing_table(view)
    assert (
        "| peak_boiler_mw | 9.00 | TL-15 robust backup (DR-11, OPT-ROBUST-BACKUP); C1-opt: 4.50 |"
        in sizing
    )
    table = plots.uncertainty_table(view)
    assert table.startswith("TL-15 on C1-opt (base-year backups); the selected C1-opt-robust")
    text, _ = plots.report_markdown(view)
    assert "**C1-opt-robust**" in text and "LCOH 35.2 USD/MWh" in text
    assert "demand met in 46% of samples" in text and "→ 90% (installed by DR-11, llm)" in text
    assert "ΔLCOH +2.05, ΔLCOC +0.00 USD/MWh, ΔCAPEX +2.20 MUSD (full year" in text


# --------------------------------------------------------------------------- token chart
BLUE = tuple(int(plots.SERIES[0][i : i + 2], 16) / 255 for i in (1, 3, 5))


def has_bars(png: Path) -> bool:
    """True when the PNG holds pixels in the bar colour (the placeholder is text only)."""
    import matplotlib.image as mpimg

    rgb = mpimg.imread(png)[:, :, :3]
    return bool((abs(rgb - BLUE).max(axis=2) < 0.03).any())


def test_token_figure_is_drawn_from_the_summary_and_redrawn_at_run_end(tmp_path):
    """Post-submission fix: ``render_tokens_figure`` fills the chart from ``token_summary.json``.

    Without a summary (the state at P10 before the fix) the chart is the placeholder; with the
    run's final summary it holds one bar per phase; only ``figures/fig_tokens.png`` is written.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert plots.render_tokens_figure(run_dir) is False  # no summary yet → placeholder
    figure = run_dir / "figures" / plots.TOKENS_FIGURE
    assert figure.is_file() and not has_bars(figure)
    assert sorted(p.name for p in (run_dir / "figures").iterdir()) == [plots.TOKENS_FIGURE]
    summary = {
        "total": {"input_tokens": 1500, "output_tokens": 300},
        "by_phase": {
            "P0": {"input_tokens": 500, "output_tokens": 100, "calls": 1},
            "P1": {"input_tokens": 1000, "output_tokens": 200, "calls": 2},
            "P2": "not a dict",
        },
    }
    (run_dir / plots.TOKEN_SUMMARY).write_text(json.dumps(summary), "utf-8")
    assert plots.tokens_by_phase(summary) == {"P0": 600, "P1": 1200, "P2": 0}
    assert plots.render_tokens_figure(run_dir) is True
    assert has_bars(figure)
    assert sorted(p.name for p in run_dir.iterdir()) == ["figures", plots.TOKEN_SUMMARY]
    # An explicit figures folder (the one-off script on a copied run) works the same way.
    assert plots.render_tokens_figure(run_dir, tmp_path / "elsewhere") is True
    assert has_bars(tmp_path / "elsewhere" / plots.TOKENS_FIGURE)
