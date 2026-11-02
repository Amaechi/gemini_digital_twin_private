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
    kpis = {
        "C0-0d": KPISet(candidate_id="C0-0d", lcoh_per_mwh=137.0, lcoc_per_mwh=36.0),
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
        candidates=[_candidate("C0-0d", "C0", 0), _candidate("C1-opt", "C1", 3)],
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
    assert "PH-30" in (tmp_path / "out" / "sizing.md").read_text("utf-8")
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
