"""Unit tests for gemini_agent.verify.design_verifier (spec §10.1, VR-01, AG-11; RC-03, RC-12)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.runner import toolbox
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.runner.toolbox import CandidateArgs, ToolContext, ToolSpec
from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, GasHandling
from gemini_agent.schemas.decision import DesignVerdict, RunStatus
from gemini_agent.schemas.results import KPISet
from gemini_agent.verify.design_verifier import (
    ESP_RELEASE_PRESSURE_ID,
    VERIFIER_NAME,
    CheckStatus,
    DesignVerifier,
    EspPoint,
    Layer,
    Mitigation,
    ScalingEvidence,
    SubsurfaceEvidence,
    VerificationEvidence,
    VerificationReport,
    VerifyDesignTool,
    verify_design_spec,
)
from unit_test.gemini_agent.verify import evidence_factory as ef

VERIFIER = DesignVerifier.from_config()


def _verify(evidence: VerificationEvidence) -> VerificationReport:
    return VERIFIER.verify(evidence)


def _with(**overrides: Any) -> VerificationEvidence:
    return ef.good_evidence(**overrides)


def _hex(**changes: Any) -> dict[str, Any]:
    point = ef.good_evidence().hex_points[0].model_copy(update=changes)
    return {"hex_points": [point]}


def _first(field: str, **changes: Any) -> dict[str, Any]:
    point = getattr(ef.good_evidence(), field)[0].model_copy(update=changes)
    return {field: [point]}


def _days(**changes: Any) -> dict[str, Any]:
    """Change day 0 of the full-mode results (and keep fast mode identical)."""
    days = [ef.day(0, **changes), ef.day(1)]
    return {"annual": ef.annual(days), "fast_annual": ef.annual(days)}


def _project(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    project = ef.project()
    mutate(project)
    return {"project": project}


def _drop_connection_to_injector(project: dict[str, Any]) -> None:
    project["connections"] = [
        c for c in project["connections"] if c["successor"] != "injector_well"
    ]


def _duplicate_component_text() -> dict[str, Any]:
    text = json.dumps(ef.project())
    first = text.index('"filter_a"')
    duplicate = '"filter_b": {"type": "filter", "parameters": {}}, '
    return {"project": text[:first] + duplicate + text[first:]}


# --------------------------------------------------------------------------- verdicts
def test_good_design_is_accepted_with_margins_and_short_summary():
    """Every layer passes: ACCEPTED, margins for each bounded check, summary ≤ 300 chars."""
    report = _verify(ef.good_evidence())
    assert report.verdict == DesignVerdict.ACCEPTED
    assert report.verifier == VERIFIER_NAME and report.candidate_id == ef.CANDIDATE_ID
    assert report.violations == [] and report.not_evaluated == []
    assert {layer.layer: layer.status for layer in report.layers} == {
        Layer.SYNTACTIC: "pass",
        Layer.SEMANTIC: "pass",
        Layer.PHYSICAL: "pass",
        Layer.PERFORMANCE: "pass",
        Layer.EXERGY: "info",
    }
    assert len(report.summary) <= SUMMARY_MAX_CHARS and "ACCEPTED" in report.summary
    assert report.check("PH-02").margin == pytest.approx(0.0)  # 86 → 46 °C is exactly 40 K
    assert report.check("PH-07").margin == pytest.approx(3.0 - 1.1)  # RC-03 approach
    assert report.check("PH-10").value == pytest.approx(24.0)  # 72 kPa / 3 NTU (RC-03 eq. 4)
    assert report.check("PH-13").margin == pytest.approx(374.15 / 57.0 - 4.0)  # Carnot
    assert report.check("PD-09").limit == pytest.approx(87.0)  # DB-20: 12 + 0.03 × 2500
    assert all(value >= 0 for value in report.margins.values())
    assert report.check("EX-01").status == CheckStatus.INFO
    VerificationReport.model_validate(report.model_dump(mode="json"))  # strict round trip


def test_candidate_only_is_unverified_never_accepted():
    """Missing evidence → not_evaluated → UNVERIFIED (no failure, no acceptance)."""
    report = _verify(VerificationEvidence(candidate=ef.candidate()))
    assert report.verdict == DesignVerdict.UNVERIFIED
    assert report.violations == []
    assert {"SY-01", "SE-02", "PH-01", "PH-07", "PH-18", "PD-01", "PD-06"} <= set(
        report.not_evaluated
    )
    assert "lack evidence" in report.summary and len(report.summary) <= SUMMARY_MAX_CHARS


def test_stub_evidence_is_never_accepted():
    """AG-11: passing evidence from a stub tool stays UNVERIFIED."""
    report = _verify(_with(stub_evidence=True))
    assert report.verdict == DesignVerdict.UNVERIFIED and report.not_evaluated == []
    assert "stub" in report.summary


def test_report_model_refuses_inconsistent_verdicts():
    """The report schema itself refuses ACCEPTED with missing checks and REJECTED without a fail."""
    good = _verify(ef.good_evidence()).model_dump()
    with pytest.raises(ValueError, match="ACCEPTED"):
        VerificationReport.model_validate({**good, "not_evaluated": ["PH-01"]})
    with pytest.raises(ValueError, match="REJECTED"):
        VerificationReport.model_validate({**good, "verdict": "REJECTED"})


# --------------------------------------------------------------------------- failing cases
FAILING_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    # syntactic
    ("SY-01", lambda: {"project": "{not json"}),
    ("SY-01", lambda: _project(lambda p: p.update(boundary_input=p.pop("boundary_inputs")))),
    ("SY-01", lambda: _project(lambda p: p["components"]["filter_a"].update(type="no_such_type"))),
    ("SY-02", lambda: {"kpis": KPISet(candidate_id="other")}),
    ("SY-03", lambda: _days(margins={"hex_approach": 3.0})),
    # semantic
    ("SE-01", _duplicate_component_text),
    ("SE-02", lambda: _project(lambda p: p["connections"][0].update(successor="ghost"))),
    (
        "SE-02",
        lambda: _project(
            lambda p: p["connections"].append(
                {
                    "predecessor": "filter_b",
                    "successor": "injector_well",
                    "predecessor_output": "no_such_output",
                    "successor_input": "flow_rate",
                }
            )
        ),
    ),
    (
        "SE-03",
        lambda: _project(
            lambda p: p["components"]["gas_splitter"]["parameters"].update(
                split_fractions=[0.8, 0.3]
            )
        ),
    ),
    ("SE-04", lambda: _project(_drop_connection_to_injector)),
    # physical (one failing case per check)
    ("PH-01", lambda: _days(heat_by_source_mw={"geothermal_hex": 13.0, "hthp": 6.0})),
    ("PH-02", lambda: _days(injection_temperature_degc=[45.0, 46.0])),
    ("PH-03", lambda: _days(doublet_flow_m3h=[320.0, 0.0], injection_flow_m3h=[320.0, 0.0])),
    ("PH-03", lambda: _days(doublet_flow_m3h=[40.0, 0.0], injection_flow_m3h=[40.0, 0.0])),
    ("PH-03", lambda: _days(doublet_flow_m3h=[300.0, 60.0], injection_flow_m3h=[300.0, 60.0])),
    ("PH-03", lambda: {"candidate": ef.candidate(doublets=4)}),
    ("PH-04", lambda: _days(injection_flow_m3h=[299.0, 0.0])),
    ("PH-05", lambda: _days(heating_supply_degc=99.0)),
    ("PH-06", lambda: _days(cooling_supply_degc=21.0)),
    ("PH-07", lambda: _hex(cold_out_degc=85.5)),
    ("PH-07", lambda: _hex(hot_out_degc=90.0)),
    ("PH-08", lambda: _hex(hot_in_degc=155.0)),
    ("PH-09", lambda: _hex(design_pressure_bar=25.0)),
    ("PH-10", lambda: _hex(pressure_drop_pa=150_000.0)),
    ("PH-11", lambda: _hex(ntu=5.0, pressure_drop_pa=120_000.0)),
    ("PH-12", lambda: _hex(area_m2=1500.0)),
    ("PH-12", lambda: _hex(flow_m3h=1400.0)),
    ("PH-13", lambda: _first("heat_pumps", cop=7.0)),
    ("PH-13", lambda: _first("heat_pumps", source_degc=101.0)),
    ("PH-14", lambda: _first("heat_pumps", max_lift_k=50.0)),
    ("PH-15", lambda: _first("absorption", cop=0.8)),
    ("PH-15", lambda: _first("absorption", cooling_mw=3.5)),
    ("PH-16", lambda: _first("absorption", pumping_power_mw=0.4)),
    ("PH-17", lambda: _first("pumps", shaft_power_w=100_000.0)),
    ("PH-18", lambda: _first("esps", intake_pressure_bar=15.0)),
    ("PH-19", lambda: _first("esps", npsh_available_m=30.0)),
    ("PH-20", lambda: _days(electricity_by_consumer_mw={"esp": -0.4})),
    ("PH-21", lambda: _days(margins={"hex_approach_k": -0.5})),
    # performance & design
    (
        "PD-01",
        lambda: _days(
            heat_by_source_mw={"geothermal_hex": 12.0, "hthp": 6.0}, unmet_heating_mwh=48
        ),
    ),
    (
        "PD-02",
        lambda: _days(
            doublet_available=[False, False],
            doublet_flow_m3h=[0.0, 0.0],
            injection_flow_m3h=[0.0, 0.0],
        ),
    ),
    ("PD-03", lambda: {"subsurface": SubsurfaceEvidence(well_spacing_m=1000.0)}),
    ("PD-04", lambda: {"subsurface": SubsurfaceEvidence(interference_bar=1.5)}),
    ("PD-05", lambda: {"subsurface": SubsurfaceEvidence(thermal_breakthrough_yr=25.0)}),
    (
        "PD-06",
        lambda: {
            "fast_annual": ef.annual(
                [ef.day(0, heat_by_source_mw={"geothermal_hex": 12.0, "hthp": 6.0}), ef.day(1)]
            )
        },
    ),
    ("PD-07", lambda: {"scaling": ScalingEvidence(status="fail", detail="RI 4.2")}),
    (
        "PD-07",
        lambda: {
            "scaling": ScalingEvidence(
                status="insufficient data",
                mitigations=[Mitigation.PRESSURISED_LOOP, Mitigation.NO_COPPER],
            )
        },
    ),
    ("PD-08", lambda: {"brine_contact_materials": {"filter_a": "CuNi 90/10"}}),
    ("PD-08", lambda: _hex(plate_material="316L stainless steel")),
    (
        "PD-09",
        lambda: _days(
            production_temperature_degc=[90.0, 86.0], injection_temperature_degc=[52.0, 46.0]
        ),
    ),
]


@pytest.mark.parametrize(
    ("check_id", "change"),
    FAILING_CASES,
    ids=[f"{cid}-{i}" for i, (cid, _) in enumerate(FAILING_CASES)],
)
def test_each_check_has_a_failing_case(check_id, change):
    """Every check fails on a violating design: REJECTED, negative margin, listed violation."""
    report = _verify(_with(**change()))
    check = report.check(check_id)
    assert check.status == CheckStatus.FAIL, check
    assert report.verdict == DesignVerdict.REJECTED
    assert any(v.startswith(check_id) for v in report.violations)
    if check.margin is not None:
        assert check.margin < 0
    assert len(report.summary) <= SUMMARY_MAX_CHARS


def test_every_pass_fail_check_has_a_failing_case():
    """Guard: the table above covers each pass/fail check id of the verifier."""
    evaluated = {
        c.check_id for c in _verify(ef.good_evidence()).checks if c.status == CheckStatus.PASS
    }
    assert evaluated == {check_id for check_id, _ in FAILING_CASES}


def test_summary_stays_short_with_many_failures():
    """VR-01: ≤ 300 characters even when many checks fail."""
    change = {
        **_days(heating_supply_degc=90.0, cooling_supply_degc=25.0, injection_flow_m3h=[1.0, 0]),
        **_hex(cold_out_degc=85.5, design_pressure_bar=30.0, area_m2=2000.0),
        "subsurface": SubsurfaceEvidence(well_spacing_m=500.0, interference_bar=3.0),
    }
    report = _verify(_with(**change))
    assert report.verdict == DesignVerdict.REJECTED and len(report.violations) >= 6
    assert len(report.summary) == SUMMARY_MAX_CHARS and report.summary.endswith("…")


# --------------------------------------------------------------------------- details
def test_hex_approach_below_typical_range_warns_but_passes():
    """RC-03: 1.1 K is achievable (pass); below the typical 2–5.6 K the check warns."""
    report = _verify(_with(**_hex(cold_out_degc=84.5)))  # approach 1.5 K
    check = report.check("PH-07")
    assert check.status == CheckStatus.PASS and check.warnings
    assert report.verdict == DesignVerdict.ACCEPTED


def test_hex_without_pressure_is_not_evaluated():
    """A HEX point without its design pressure cannot pass the 20-bar rating check."""
    report = _verify(_with(**_hex(design_pressure_bar=None)))
    assert report.check("PH-09").status == CheckStatus.NOT_EVALUATED
    assert report.verdict == DesignVerdict.UNVERIFIED


def test_esp_release_pressure_defaults_to_register():
    """RC-01 item 9: without a bubble-point pressure the [A] default 20 bar(g) is used."""
    esp = EspPoint(name="ESP-1", intake_pressure_bar=19.0, npsh_available_m=60, npsh_required_m=20)
    report = _verify(_with(esps=[esp]))
    check = report.check("PH-18")
    assert check.status == CheckStatus.FAIL and check.margin == pytest.approx(-1.0)
    assert ESP_RELEASE_PRESSURE_ID in report.assumption_ids and check.warnings


def test_degassing_needs_calcite_inhibitor_when_chemistry_missing():
    """RC-12: a degassing design without brine chemistry must dose a calcite inhibitor."""
    candidate = ef.candidate(gas_handling=GasHandling.DEGAS_USE)
    scaling = ScalingEvidence(
        status="insufficient data",
        mitigations=[Mitigation.TITANIUM_PLATES, Mitigation.OXYGEN_EXCLUSION, Mitigation.NO_COPPER],
    )
    report = _verify(_with(candidate=candidate, scaling=scaling))
    assert report.check("PD-07").status == CheckStatus.FAIL
    assert "calcite_inhibitor" in report.check("PD-07").detail
    fixed = scaling.model_copy(update={"mitigations": [*scaling.mitigations, "calcite_inhibitor"]})
    assert _verify(_with(candidate=candidate, scaling=fixed)).verdict == DesignVerdict.ACCEPTED


def test_fast_full_discrepancy_between_two_and_five_percent_warns():
    """SIM-04: a 3 % daily discrepancy warns but passes."""
    fast = ef.annual(
        [ef.day(0, heat_by_source_mw={"geothermal_hex": 13.4, "hthp": 6.0}), ef.day(1)]
    )
    check = _verify(_with(fast_annual=fast)).check("PD-06")
    assert check.status == CheckStatus.PASS and check.warnings


def test_titanium_not_required_for_non_saline_brine():
    """RC-12: the titanium rule applies to saline brine only; copper is never allowed."""
    report = _verify(_with(saline_brine=False, **_hex(plate_material="316L stainless")))
    assert report.check("PD-08").status == CheckStatus.PASS


def test_c0_candidate_skips_geothermal_checks():
    """C0 (no doublets): well, HEX, pump, ESP, subsurface and scaling checks do not apply."""
    c0 = ef.candidate(
        concept="C0",
        doublets=0,
        depth_m=None,
        design_flow_m3h=None,
        gas_handling=None,
        equipment_sizes={"boiler_heating_mw": 30.0},
    )
    report = _verify(VerificationEvidence(candidate=c0))
    not_applicable = {c.check_id for c in report.checks if c.status == CheckStatus.NOT_APPLICABLE}
    assert {
        "PH-02",
        "PH-03",
        "PH-04",
        "PH-07",
        "PH-17",
        "PH-18",
        "PD-02",
        "PD-03",
        "PD-05",
        "PD-07",
        "PD-08",
        "PD-09",
    } <= not_applicable
    assert report.verdict == DesignVerdict.UNVERIFIED  # project and daily results still missing


def test_heat_pump_listed_in_equipment_needs_evidence():
    """A candidate with an HTHP but no heat-pump operating point is not verifiable."""
    report = _verify(_with(heat_pumps=[]))
    assert report.check("PH-13").status == CheckStatus.NOT_EVALUATED
    report = _verify(
        _with(heat_pumps=[], candidate=ef.candidate(equipment_sizes={"absorption_cooling_mw": 3.0}))
    )
    assert report.check("PH-13").status == CheckStatus.NOT_APPLICABLE


def test_kpi_fallbacks_for_unmet_and_breakthrough():
    """Without daily results PD-01 reads the unmet KPIs; t_b falls back to the KPI set."""
    kpis = KPISet(
        candidate_id=ef.CANDIDATE_ID,
        unmet_heating_mwh=0.0,
        unmet_cooling_mwh=12.0,
        thermal_breakthrough_yr=24.0,
    )
    report = _verify(_with(annual=None, kpis=kpis, subsurface=SubsurfaceEvidence()))
    assert report.check("PD-01").status == CheckStatus.FAIL
    assert report.check("PD-05").status == CheckStatus.FAIL
    assert report.check("PH-01").status == CheckStatus.NOT_EVALUATED


# --------------------------------------------------------------------------- toolbox / runner
def _context(tmp_path: Path, selected: str = ef.CANDIDATE_ID) -> ToolContext:
    return ToolContext(
        run_id="t",
        run_dir=tmp_path,
        config_dir=None,
        candidates=(ef.candidate(),),
        selected_id=selected,
    )


def test_tool_with_context_evidence_is_unverified(tmp_path):
    """The default provider sees only the candidate and its KPIs: never ACCEPTED."""
    result = VerifyDesignTool()(CandidateArgs(), _context(tmp_path))
    assert result.data.verdict == DesignVerdict.UNVERIFIED
    assert result.summary == result.data.summary and "spec §10.1" in result.sources
    with pytest.raises(ValueError, match="unknown candidate"):
        VerifyDesignTool()(CandidateArgs(candidate_id="nope"), _context(tmp_path))
    with pytest.raises(ValueError, match="no candidate selected"):
        VerifyDesignTool()(CandidateArgs(), _context(tmp_path, selected=""))


def _good_provider(candidate, context):
    return ef.good_evidence(candidate.id)


def test_tool_with_full_evidence_accepts(tmp_path):
    """With a provider of complete evidence the real verifier returns ACCEPTED."""
    tool = VerifyDesignTool(_good_provider, VERIFIER)
    result = tool(CandidateArgs(candidate_id=ef.CANDIDATE_ID), _context(tmp_path))
    assert result.data.verdict == DesignVerdict.ACCEPTED and result.data.verifier == VERIFIER_NAME


def test_only_the_real_verifier_binding_makes_accepted_stick(tmp_path):
    """AG-11 end to end: stub=False keeps ACCEPTED; the same tool bound as a stub is downgraded."""
    spec = verify_design_spec(_good_provider, VERIFIER)
    assert spec.stub is False and spec.name == "verify_design"
    box = toolbox.default_toolbox()
    box.register(spec)
    result = DesignRunner(PolicyEngine(), runs_root=tmp_path, toolbox=box).start("", run_id="real")
    assert result.status == RunStatus.COMPLETED and result.verdict == "ACCEPTED"

    box.register(
        ToolSpec(spec.name, spec.description, spec.args_model, spec.func, owner="test", stub=True)
    )
    result = DesignRunner(PolicyEngine(), runs_root=tmp_path, toolbox=box).start(
        "", run_id="stubbed"
    )
    assert result.verdict == "UNVERIFIED"
