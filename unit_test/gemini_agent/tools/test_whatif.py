"""Tests for the what-if on a finished run (post-submission feature, FINAL_SPRINT §B).

One module-scoped smoke run (policy engine, real tools, register RUN-BUDGET ``smoke``: every
30th day) is the fixture run; every what-if below runs on the same smoke budget, offline.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.cli import EXIT_OK, EXIT_REFUSED, EXIT_USAGE, main
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.runner.real_tools import real_toolbox
from gemini_agent.schemas.decision import DesignVerdict, reject_llm_quantities
from gemini_agent.tools.whatif import (
    LEVERS,
    UnknownLever,
    WhatIfRefused,
    WhatIfResult,
    lever_help,
    load_run,
    next_index,
    parse_levers,
    register_range,
    render_table,
    whatif,
)


def _digest(run_dir: Path) -> dict[str, str]:
    """SHA-256 of every file of the run outside ``whatif/`` (the submitted artefacts)."""
    out = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and "whatif" not in path.relative_to(run_dir).parts:
            out[str(path.relative_to(run_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    """Policy workflow P0–P10 on the real tools (smoke budget), once for the module."""
    root = tmp_path_factory.mktemp("runs")
    runner = DesignRunner(PolicyEngine(), runs_root=root, toolbox=real_toolbox("smoke"))
    try:
        result = runner.start("", run_id="smoke")
    finally:
        runner.close()
    assert result.verdict == DesignVerdict.ACCEPTED.value, result.summary
    run_dir = root / "smoke"
    return run_dir, _digest(run_dir)


# ----------------------------------------------------------------------------- levers (cheap)
def test_parse_levers_types_and_order():
    """``LEVER=value`` strings are typed, matched case-insensitively and kept in catalogue order."""
    parsed = parse_levers(["doublets=3", "db-31=65", "OPT-ROBUST-BACKUP=on", "DB-30=b"])
    assert list(parsed) == ["DB-31", "DB-30", "OPT-ROBUST-BACKUP", "doublets"]
    assert parsed == {"DB-31": 65.0, "DB-30": "B", "OPT-ROBUST-BACKUP": True, "doublets": 3}
    assert parse_levers({"ECO-CO2-GAS": 51.53, "concept": "C3"}) == {
        "ECO-CO2-GAS": 51.53,
        "concept": "C3",
    }
    with pytest.raises(ValueError, match="expected a number"):
        parse_levers(["DB-31=hot"])
    with pytest.raises(ValueError, match=r"expected on\|off"):
        parse_levers(["OPT-ROBUST-BACKUP=maybe"])
    with pytest.raises(ValueError, match="given twice"):
        parse_levers(["DB-31=65", "db-31=45"])


def test_unknown_lever_lists_the_valid_ones():
    """An unknown lever (or a bare word) is refused with the whole catalogue in the message."""
    for bad in (["foo=1"], ["DB-31"]):
        with pytest.raises(UnknownLever) as exc:
            parse_levers(bad)
        for lever in LEVERS:
            assert lever in str(exc.value)
    assert all(lever in lever_help() for lever in LEVERS)


def test_register_range_is_the_entry_value_and_its_sensitivity_list():
    """Bounds of the register levers come from config/assumptions.yaml, nothing else."""
    from gemini_agent.schemas.config import load_config

    register = load_config("assumptions")
    assert register_range(register, "DB-31") == (45.0, 75.0)
    assert register_range(register, "DB-32") == (24.0, 28.0)
    assert register_range(register, "ECO-CO2-GAS") == (51.53, 56.6)
    low, high = register_range(register, "CHP-EL-FRAC")
    assert (low, high) == pytest.approx((0.3333, 0.5))


# ----------------------------------------------------------------------------- on the run
def test_db31_lever_changes_lcoh_and_the_run_keeps_its_verdict(smoke_run):
    """DB-31=65: the what-if design runs at a 65 °C return, its LCOH differs, the run's verdict
    stands (before = the recorded ACCEPTED design) and the changed design gets its own verdict."""
    run_dir, _ = smoke_run
    result = whatif(run_dir, ["DB-31=65"], "smoke")
    before, after = result.before, result.after
    assert (before.candidate.heating_return_degc, after.candidate.heating_return_degc) == (55, 65)
    assert result.scenario.heating_return_degc == 65.0 and not result.scenario.edits_config
    assert after.candidate.id == f"{before.candidate.id}-w1"
    assert before.kpis is not None and after.kpis is not None
    assert after.kpis.lcoh_per_mwh != before.kpis.lcoh_per_mwh
    lcoh = next(r for r in result.table if r.metric == "lcoh_per_mwh")
    assert lcoh.delta == pytest.approx(after.kpis.lcoh_per_mwh - before.kpis.lcoh_per_mwh)
    # The run keeps its verdict: before is the recorded design, re-verified ACCEPTED on the
    # same budget; the changed design is verified on its own (no decision is taken).
    summary = json.loads((run_dir / "run_summary.json").read_text("utf-8"))
    assert before.verdict == DesignVerdict(summary["verdict"]) == DesignVerdict.ACCEPTED
    assert before.guard_approved and after.guard_approved
    # On the smoke fixture (C1-opt: HEX + CHP + boiler sized to the base year, no P90 reserve)
    # a 65 °C return starves the direct HEX and the capped boiler leaves heat unmet, so the
    # changed design is REJECTED by the verifier (PH-21/PD-01) and the hint names the boiler
    # nameplate that would meet it. (On the submitted run the DR-11 P90 boiler absorbs it.)
    assert after.verdict == DesignVerdict.REJECTED and after.kpis.unmet_heating_days > 0
    assert (
        after.required_backup_mw["peak_boiler_mw"]
        > after.candidate.equipment_sizes["peak_boiler_mw"]
    )
    assert any("nameplates that would meet it" in w for w in result.warnings)
    assert result.recorded_kpis is not None and result.recorded_kpis.candidate_id == (
        before.candidate.id
    )
    assert before.probability_demand_met is not None  # the run's TL-15 (robust mapping)
    assert after.probability_demand_met is None  # needs --budget full
    assert any("--budget full" in w for w in result.warnings)
    assert "min margin" in render_table(result) and "LCOH" in result.summary
    # Files: JSON + Markdown + DR-W1 with evidence keys, tool results of both sides.
    folder = run_dir / "whatif" / "1"
    for name in ("whatif.json", "whatif.md", "DR-W1.json", "DR-W1.md"):
        assert (folder / name).is_file(), name
    assert WhatIfResult.model_validate_json((folder / "whatif.json").read_text("utf-8"))
    record = json.loads((folder / "DR-W1.json").read_text("utf-8"))
    assert (record["dr_id"], record["title"]) == ("DR-W1", "what-if DB-31=65")
    assert record["choice"] == "after" and record["criterion"]["key"] == "DB-31"
    assert {"DB-31", "lcoh_per_mwh", "verdict", "simulate_annual", "verify_design"} <= set(
        record["evidence_keys"]
    )
    for option in record["options"]:
        assert option["evidence_ids"], option["id"]
        for tr_id in option["evidence_ids"]:
            assert list((folder / "tool_results").glob(f"{tr_id}_*.json")), tr_id
    recommended = [o["id"] for o in record["options"] if o["recommended"]]
    assert recommended == ["before"]  # optimiser rule: the REJECTED what-if is not eligible
    assert "differs" in (folder / "DR-W1.md").read_text("utf-8")
    assert reject_llm_quantities(record["rationale"]) == record["rationale"]  # AG-02: no numbers


def test_db31_lower_return_keeps_the_design_accepted(smoke_run):
    """DB-31=45 (more direct HEX heat) keeps the selected design ACCEPTED at its sizes."""
    run_dir, _ = smoke_run
    result = whatif(run_dir, {"DB-31": 45}, "smoke")
    assert result.index == 2 and result.after.verdict == DesignVerdict.ACCEPTED
    assert result.after.kpis is not None and result.after.kpis.unmet_heating_days == 0
    assert result.after.kpis.lcoh_per_mwh != result.before.kpis.lcoh_per_mwh


def test_out_of_limit_levers_are_refused_with_the_guard_reason(smoke_run):
    """doublets=5 (DB-10), a 20 m³/h flow (DB-11), DB-31 outside the register range, C0 and a
    candidate the run excluded are refused before anything is written."""
    run_dir, _ = smoke_run
    index = next_index(run_dir)
    cases = {
        "doublets=5": "DB-10: 5 doublets outside 1–3",
        "design_flow_m3h=20": "DB-11",
        "DB-31=90": "outside the register's documented range 45–75",
        "ECO-CO2-GAS=0": "outside the register's documented range",
        "concept=C0-0d": "counterfactual",
    }
    snapshot = load_run(run_dir)
    if snapshot.state.guard_failed:
        cases[f"concept={snapshot.state.guard_failed[0]}"] = "was excluded in the run"
    for lever, reason in cases.items():
        with pytest.raises(WhatIfRefused, match=reason):
            whatif(run_dir, [lever], "smoke")
    with pytest.raises(WhatIfRefused, match="not a candidate of the run"):
        whatif(run_dir, ["concept=C9-9d"], "smoke")
    assert next_index(run_dir) == index  # nothing written


def test_cli_whatif_prints_the_table_and_exit_codes(smoke_run, capsys, monkeypatch):
    """``cli whatif``: the before → after table on stdout and the folder; refused → 5, usage → 2.

    With ``--speak`` (post-submission) the before/after verdicts and the record's rationale are
    also spoken through the (monkeypatched) ``say`` subprocess; the printed output is unchanged.
    """
    from gemini_agent import speech

    spoken: list[str] = []
    monkeypatch.setattr(speech.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        speech.subprocess,
        "run",
        lambda argv, **kw: spoken.append(argv[2]) or subprocess.CompletedProcess(argv, 0),
    )
    run_dir, _ = smoke_run
    root = run_dir.parent
    base = ["whatif", "--run-id", "smoke", "--runs-dir", str(root), "--budget", "smoke", "--speak"]
    snapshot = load_run(run_dir)
    robust = snapshot.uncertainty is not None and bool(snapshot.uncertainty.robust_backup_mw)
    # (DB-30=B needs the full year: the smoke day subset holds days outside B's 330-day window)
    levers = ["--set", "ECO-CO2-GAS=51.53", "--set", "DB-32=24"]
    if robust:
        levers += ["--set", "OPT-ROBUST-BACKUP=on"]
    assert main([*base, *levers]) == EXIT_OK
    out = capsys.readouterr().out
    assert "| LCOH | USD/MWh |" in out and "DesignGuard / verifier" in out
    assert "record: DR-W3" in out and "ECO-CO2-GAS: 56.6 -> 51.53" in out
    result = WhatIfResult.model_validate_json(
        (run_dir / "whatif" / "3" / "whatif.json").read_text("utf-8")
    )
    lcoh = next(r for r in result.table if r.metric == "lcoh_per_mwh")
    assert f"| {lcoh.before:.2f} | {lcoh.after:.2f} | {lcoh.delta:+.2f} |" in out  # 2 dp
    assert result.scenario.assumptions == {"DB-32": 24.0, "ECO-CO2-GAS": 51.53}
    assert spoken[:2] == [
        f"before: {result.before.status}. {result.before.verifier_summary}".strip(),
        f"after: {result.after.status}. {result.after.verifier_summary}".strip(),
    ]
    assert spoken[2] == f"Record DR-W3, {result.record.title}: {result.record.rationale}"
    assert len(spoken) == 3
    assert result.scenario.edits_config and result.after.candidate.cooling_return_degc == 24.0
    assert result.after.kpis is not None and result.after.status == "ok", result.after.reason
    assert result.after.verdict == DesignVerdict.ACCEPTED
    assert result.after.kpis.co2_t_per_yr != result.before.kpis.co2_t_per_yr  # new CO2 factor
    if robust and snapshot.selected.id == snapshot.analysed_id:
        installed = snapshot.selected.equipment_sizes
        assert result.after.candidate.equipment_sizes != installed
    assert main([*base, "--set", "doublets=5"]) == EXIT_REFUSED
    assert "DB-10" in capsys.readouterr().err
    assert main([*base, "--set", "foo=1"]) == EXIT_USAGE
    assert "Valid levers" in capsys.readouterr().err
    assert main(base) == EXIT_USAGE  # no --set
    assert main(["whatif", "--run-id", "nope", "--runs-dir", str(root), "--set", "DB-31=65"]) == (
        EXIT_USAGE
    )
    assert "not a run folder" in capsys.readouterr().err


def test_run_artefacts_are_byte_identical_after_the_commands(smoke_run):
    """Every file of the run outside ``whatif/`` is unchanged by the what-ifs above."""
    run_dir, before = smoke_run
    assert _digest(run_dir) == before
    assert sorted(p.name for p in (run_dir / "whatif").iterdir()) == ["1", "2", "3"]
