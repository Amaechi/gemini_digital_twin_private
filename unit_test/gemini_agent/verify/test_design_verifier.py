"""Unit tests for gemini_agent.verify.design_verifier (spec §10.1, VR-01, AG-11; RC-03, RC-12)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.runner import toolbox
from gemini_agent.runner.design_runner import DesignRunner
from gemini_agent.runner.tool_stubs import stub_toolbox
from gemini_agent.runner.toolbox import CandidateArgs, ToolContext
from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, GasHandling
from gemini_agent.schemas.decision import DesignVerdict, RunStatus
from gemini_agent.schemas.results import KPISet
from gemini_agent.verify.design_verifier import (
    ESP_MARGIN_ID,
    ESP_RELEASE_PRESSURE_ID,
    HP_CARNOT_FRACTION_ID,
    HP_MAX_LIFT_ID,
    PA_PER_PSI,
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
    is_copper_alloy,
    is_titanium,
    verify_design_spec,
)
from unit_test.gemini_agent.verify import evidence_factory as ef

pytestmark = pytest.mark.usefixtures("stub_tools")

VERIFIER = DesignVerifier.from_config()


def _verify(evidence: VerificationEvidence) -> VerificationReport:
    return VERIFIER.verify(evidence)


def _with(**overrides: Any) -> VerificationEvidence:
    return ef.good_evidence(**overrides)


def _hex(**changes: Any) -> dict[str, Any]:
    return {"hex_points": [ef.hex_point(**changes)]}


def _first(field: str, **changes: Any) -> dict[str, Any]:
    point = getattr(ef.good_evidence(), field)[0].model_copy(update=changes)
    return {field: [point]}


def _days(**changes: Any) -> dict[str, Any]:
    """Change day 0 of the full-mode results (and keep fast mode identical)."""
    days = [ef.day(0, **changes), ef.day(1)]
    return {"annual": ef.annual(days), "fast_annual": ef.annual(days)}


def _day0(field: str, **changes: float) -> dict[str, float]:
    """Day-0 dict ``field`` with some entries changed."""
    return {**getattr(ef.day(0), field), **changes}


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


def _pressurised(**changes: Any) -> dict[str, Any]:
    """Pressurised no-degassing variant (DB-36 option b) with its RC-12 mitigations."""
    scaling = ScalingEvidence(
        status="insufficient data",
        mitigations=[
            Mitigation.PRESSURISED_LOOP,
            Mitigation.TITANIUM_PLATES,
            Mitigation.OXYGEN_EXCLUSION,
            Mitigation.NO_COPPER,
        ],
    )
    return {
        "candidate": ef.candidate(gas_handling=GasHandling.PRESSURISED_REINJECT),
        "scaling": scaling,
        **changes,
    }


def _unmet_day() -> dict[str, Any]:
    heat = _day0("heat_by_source_mw")
    heat["boiler"] -= 2.0
    return _days(heat_by_source_mw=heat, unmet_heating_mwh=48.0)


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
    assert report.check("PH-07").margin == pytest.approx(4.0 - 1.1)  # RC-03 approach
    assert report.check("PH-10").value == pytest.approx(24.0)  # 84 kPa / 3.5 NTU (RC-03 eq. 4)
    assert report.check("PH-13").margin == pytest.approx(376.15 / 60.0 - 3.5)  # Carnot
    assert report.check("PH-22").value == pytest.approx(0.0, abs=1e-9)  # brine balance closes
    assert report.check("PD-09").limit == pytest.approx(87.0)  # DB-20: 12 + 0.03 × 2500
    assert report.check("PH-30").margin == pytest.approx(100.0 - 86.0)  # SUB-NPSH bound
    assert set(report.assumption_ids) == {
        "DB-33",
        "SUB-NPSH",  # PH-30: water boils above 100 °C at the 1 atm letdown
        "SUB-PSUC",  # PH-30: letdown pressure 0 bar(g) (CFG-SURFACE)
        ESP_RELEASE_PRESSURE_ID,  # no computed p_b: register setpoint 60 bar(a)
        HP_CARNOT_FRACTION_ID,
        HP_MAX_LIFT_ID,
    }
    assert report.check("PH-18").margin == pytest.approx(61.0 - (60.0 - 1.01325))
    assert all(value >= 0 for value in report.margins.values())
    assert report.check("EX-01").status == CheckStatus.INFO
    VerificationReport.model_validate(report.model_dump(mode="json"))  # strict round trip


def test_fixture_brine_heat_matches_db23():
    """DB-23: 300 m³/h × 1030 kg/m³ × 4100 J/(kg K) × 40 K = 14.08 MW, split over three users."""
    drawn = ef.day(0).brine_heat_drawn_mw
    assert sum(drawn.values()) == pytest.approx(300 / 3600 * 1030 * 4100 * 40 / 1e6)
    assert drawn["hthp"] == pytest.approx(6.0 - 6.0 / 3.5)  # Q_evap = Q_cond − W
    assert drawn["absorption"] == pytest.approx(2.0 / 0.65)  # drive heat = cooling / COP


def test_candidate_only_is_unverified_never_accepted():
    """Missing evidence → not_evaluated → UNVERIFIED (no failure, no acceptance)."""
    report = _verify(VerificationEvidence(candidate=ef.candidate()))
    assert report.verdict == DesignVerdict.UNVERIFIED
    assert report.violations == []
    assert {"SY-01", "SE-02", "PH-01", "PH-07", "PH-18", "PH-22", "PD-01", "PD-06"} <= set(
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
    # physical (one failing case per check, more for the subtle ones)
    ("PH-01", lambda: _days(heat_by_source_mw=_day0("heat_by_source_mw", boiler=6.0))),
    ("PH-02", lambda: _days(injection_temperature_degc=[45.0, 46.0])),
    ("PH-02", lambda: _days(injection_temperature_degc=[90.0, 46.0])),  # injection hotter
    ("PH-03", lambda: _days(doublet_flow_m3h=[320.0, 0.0], injection_flow_m3h=[320.0, 0.0])),
    ("PH-03", lambda: _days(doublet_flow_m3h=[40.0, 0.0], injection_flow_m3h=[40.0, 0.0])),
    ("PH-03", lambda: _days(doublet_flow_m3h=[300.0, 60.0], injection_flow_m3h=[300.0, 60.0])),
    ("PH-04", lambda: _days(injection_flow_m3h=[299.0, 0.0])),
    ("PH-05", lambda: _days(heating_supply_degc=99.0)),
    ("PH-06", lambda: _days(cooling_supply_degc=21.0)),
    ("PH-07", lambda: _hex(cold_out_degc=85.5)),
    ("PH-07", lambda: _hex(hot_out_degc=90.0)),
    ("PH-07", lambda: _hex(hot_out_degc=86.0)),  # no hot-side duty, cold side heats up
    ("PH-08", lambda: _hex(hot_in_degc=155.0)),
    ("PH-09", lambda: _hex(design_pressure_barg=25.0)),
    ("PH-09", lambda: _hex(design_pressure_barg=20.5)),  # gasketed rating 20 bar
    ("PH-10", lambda: _hex(pressure_drop_pa=150_000.0)),
    ("PH-11", lambda: _hex(ntu=5.0, pressure_drop_pa=120_000.0)),
    ("PH-12", lambda: _hex(area_m2=1500.0)),
    ("PH-12", lambda: _hex(flow_m3h=1400.0)),
    ("PH-13", lambda: _first("heat_pumps", cop=7.0)),
    ("PH-13", lambda: _first("heat_pumps", evaporating_degc=104.0)),  # configuration error
    ("PH-13", lambda: _first("heat_pumps", condensing_degc=99.0)),  # below the sink outlet
    ("PH-13", lambda: _first("heat_pumps", condensing_degc=100.0)),  # zero approach
    ("PH-14", lambda: _first("heat_pumps", max_lift_k=50.0)),
    ("PH-14", lambda: _first("heat_pumps", cop=3.8)),  # > 0.6 × Carnot 6.27 = 3.76 (RC-04 [A])
    ("PH-15", lambda: _first("absorption", cop=0.8)),
    ("PH-15", lambda: _first("absorption", cooling_mw=3.5)),
    ("PH-16", lambda: _first("absorption", pumping_power_mw=0.4)),
    ("PH-17", lambda: _first("pumps", shaft_power_w=100_000.0)),
    ("PH-18", lambda: _first("esps", intake_pressure_barg=58.0)),  # < 60 bar(a) = 58.99 bar(g)
    (  # WP-05 bare p_b 45.1 bar(g): 50 bar(g) is above p_b but below 1.2·p_b = 54.32 bar(g)
        "PH-18",
        lambda: _first("esps", release_pressure_barg=45.1, intake_pressure_barg=50.0),
    ),
    (  # the same through the evidence-level p_b
        "PH-18",
        lambda: {
            "co2_release_pressure_barg": 45.1,
            **_first("esps", intake_pressure_barg=50.0),
        },
    ),
    ("PH-19", lambda: _first("esps", npsh_available_m=30.0)),
    ("PH-20", lambda: _days(electricity_by_consumer_mw={"esp": -0.4})),
    ("PH-21", lambda: _days(margins={"hex_approach_k": -0.5})),
    ("PH-22", lambda: _days(brine_heat_drawn_mw=_day0("brine_heat_drawn_mw", absorption=4.0))),
    (  # heat-pump first law: Q_cond ≠ Q_evap + W
        "PH-22",
        lambda: _days(electricity_by_consumer_mw=_day0("electricity_by_consumer_mw", hthp=2.5)),
    ),
    (  # 3 MW of 'heat from nothing': delivered without a drawn, fuel or electricity entry
        "PH-22",
        lambda: _days(
            heat_by_source_mw=_day0(
                "heat_by_source_mw", hex2=3.0, boiler=ef.day(0).heat_by_source_mw["boiler"] - 3.0
            )
        ),
    ),
    (  # drive heat inflated: cooling / drawn = 2 / 5.08 = 0.39 < GM-04 minimum 0.55
        "PH-22",
        lambda: _days(
            brine_heat_drawn_mw=_day0(
                "brine_heat_drawn_mw",
                geothermal_hex=ef.day(0).brine_heat_drawn_mw["geothermal_hex"] - 2.0,
                absorption=ef.day(0).brine_heat_drawn_mw["absorption"] + 2.0,
            )
        ),
    ),
    ("PH-23", lambda: _hex(ntu=2.5, pressure_drop_pa=60_000.0)),  # too small for 86→70/55→82
    ("PH-23", lambda: _hex(passes=3)),  # 3 × 3.5 = 10.5 > 3.24/0.85
    ("PH-24", lambda: {"candidate": ef.candidate(doublets=4)}),
    ("PH-24", lambda: {"candidate": ef.candidate(doublets=3)}),  # results hold 2 doublets
    # performance & design
    ("PD-01", _unmet_day),
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
    ("PD-06", lambda: {"fast_annual": ef.annual([ef.fast_day(0, 2.0), ef.fast_day(1)])}),
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
    ("PD-10", lambda: _pressurised()),  # default 60 bar(a) > gasketed HEX at 16 bar(g)
    (
        "PD-10",
        lambda: _pressurised(  # 16 bar(g): above p_b 15 but below 1.2·p_b = 18.20 bar(g)
            co2_release_pressure_barg=15.0, **_hex(min_brine_pressure_barg=16.0)
        ),
    ),
]


def _subsurface(**changes: Any) -> dict[str, Any]:
    return {"subsurface": ef.good_evidence().subsurface.model_copy(update=changes)}


FAILING_CASES += [  # WP-14: closed evidence gaps (TL-05, TL-07/08, model warnings, flags)
    ("PH-25", lambda: _first("doublet_points", feasible=False, reasons=["ESP above 70 Hz"])),
    ("PH-26", lambda: _first("doublet_points", flow_error_frac=0.02)),
    ("PH-27", lambda: _first("doublet_points", intake_margin_bar=-0.3)),
    ("PD-11", lambda: _subsurface(interference_within_limit=False)),
    ("PD-12", lambda: _subsurface(breakthrough_after_horizon=False)),
    (
        "PH-28",
        lambda: {
            "model_warnings": [
                "free_cooling: tower water -1.2 °C <= 0 °C: freeze protection not modelled"
            ]
        },
    ),
    (
        "PH-29",
        lambda: _days(flags=["hthp_1 (running): OUTSIDE RC-04 limits (COP 0.90, lift 70 K)"]),
    ),
    ("PH-30", lambda: _days(production_temperature_degc=[105.1, 86.0])),
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
    # PD-10 applies only to the pressurised loop; an explicit (WP-05) p_b lets it pass there
    pressurised = _verify(_with(**_pressurised(co2_release_pressure_barg=12.0)))  # 1.2·p_b 14.6
    evaluated |= {c.check_id for c in pressurised.checks if c.status == CheckStatus.PASS}
    assert pressurised.verdict == DesignVerdict.ACCEPTED
    assert evaluated == {check_id for check_id, _ in FAILING_CASES}


def test_summary_stays_short_with_many_failures():
    """VR-01: ≤ 300 characters even when many checks fail."""
    change = {
        **_days(heating_supply_degc=90.0, cooling_supply_degc=25.0, injection_flow_m3h=[1.0, 0]),
        **_hex(cold_out_degc=85.5, design_pressure_barg=30.0, area_m2=2000.0),
        "subsurface": SubsurfaceEvidence(well_spacing_m=500.0, interference_bar=3.0),
    }
    report = _verify(_with(**change))
    assert report.verdict == DesignVerdict.REJECTED and len(report.violations) >= 6
    assert len(report.summary) == SUMMARY_MAX_CHARS and report.summary.endswith("…")


# --------------------------------------------------------------------------- details
def test_values_exactly_at_a_limit_pass_despite_float_error():
    """85.9 − 45.9 = 40.000000000000014 K and an approach of 86 − 84.9 K still pass."""
    days = _days(production_temperature_degc=[85.9, 86.0], injection_temperature_degc=[45.9, 46.0])
    report = _verify(_with(**days, **_hex(cold_out_degc=84.9)))
    assert report.check("PH-02").status == CheckStatus.PASS
    assert report.check("PH-07").status == CheckStatus.PASS


def test_guidebook_plate_hex_example_passes():
    """RC-03 validation case: 160→130 °F / 110→150 °F, NTU 3.1, Δp 10.9 psi (≈ 24 kPa/NTU)."""

    def f_to_c(fahrenheit: float) -> float:
        return (fahrenheit - 32.0) / 1.8

    point = ef.hex_point(
        hot_in_degc=f_to_c(160.0),
        hot_out_degc=f_to_c(130.0),
        cold_in_degc=f_to_c(110.0),
        cold_out_degc=f_to_c(150.0),
        ntu=3.1,
        pressure_drop_pa=10.9 * PA_PER_PSI,
        flow_m3h=200.0 * 0.2271247,  # 200 gpm
        area_m2=259.0 * 0.09290304,  # 259 ft²
    )
    report = _verify(_with(hex_points=[point]))
    for check_id in ("PH-07", "PH-10", "PH-11", "PH-23"):
        assert report.check(check_id).status == CheckStatus.PASS, report.check(check_id)
    assert report.check("PH-10").value == pytest.approx(10.9 / 3.1 * PA_PER_PSI / 1000.0)


def test_hex_approach_outside_typical_range_warns_but_passes():
    """RC-03: 1.1 K is achievable (pass); outside the typical 2–5.6 K the check warns."""
    report = _verify(_with(**_hex(cold_out_degc=84.5, ntu=4.0, pressure_drop_pa=96_000.0)))
    check = report.check("PH-07")
    assert check.status == CheckStatus.PASS and check.warnings


def test_welded_plate_hex_rating():
    """RC-03: welded plate units are rated 300 psig / 400 °F; gasketed ones ~20 bar / 150 °C."""
    report = _verify(_with(**_hex(construction="welded", design_pressure_barg=20.5)))
    assert report.check("PH-09").status == CheckStatus.PASS
    assert report.check("PH-08").status == CheckStatus.PASS


def test_hex_without_pressure_is_not_evaluated():
    """A HEX point without its design pressure cannot pass the rating check."""
    report = _verify(_with(**_hex(design_pressure_barg=None)))
    assert report.check("PH-09").status == CheckStatus.NOT_EVALUATED
    assert report.verdict == DesignVerdict.UNVERIFIED


def test_release_pressure_defaults_to_register_setpoint():
    """RC-01 item 9: without a computed p_b the setpoint 60 bar(a) (1.2 · p_b) applies, in gauge."""
    assert VERIFIER.release_pressure_default_barg == pytest.approx(60.0 - 1.01325)
    esp = EspPoint(name="ESP-1", intake_pressure_barg=58.0, npsh_available_m=60, npsh_required_m=20)
    report = _verify(_with(esps=[esp]))
    check = report.check("PH-18")
    assert check.status == CheckStatus.FAIL and check.margin == pytest.approx(58.0 - 58.98675)
    assert ESP_RELEASE_PRESSURE_ID in report.assumption_ids
    assert any("60 bar(a)" in warning for warning in check.warnings)
    assert ESP_MARGIN_ID not in report.assumption_ids  # default already includes the margin
    explicit = esp.model_copy(update={"release_pressure_barg": 45.0})  # WP-05's bare p_b
    report = _verify(_with(esps=[explicit]))
    assert report.check("PH-18").status == CheckStatus.PASS  # 58 ≥ 1.2 · 46.01 − 1.01 = 54.20
    assert ESP_RELEASE_PRESSURE_ID not in report.assumption_ids
    assert ESP_MARGIN_ID in report.assumption_ids


P_B_BARG = 45.1
"""WP-05's bare bubble point (RC-01 item 9, sea water, 46.1 bar(a)) in bar(g)."""
SETPOINT_BARG = 1.2 * (P_B_BARG + 1.01325) - 1.01325
"""RC-01 item 9: p_intake ≥ 1.2 · p_b in bar(a) → 54.32 bar(g)."""


@pytest.mark.parametrize("level", ["esp", "evidence"])
@pytest.mark.parametrize(
    ("intake_barg", "status"),
    [
        (SETPOINT_BARG, CheckStatus.PASS),  # exactly 1.2 · p_b
        (SETPOINT_BARG + 0.5, CheckStatus.PASS),
        (SETPOINT_BARG - 0.05, CheckStatus.FAIL),
        (50.0, CheckStatus.FAIL),  # between p_b and 1.2 · p_b
        (P_B_BARG + 0.1, CheckStatus.FAIL),  # just above the bare p_b
    ],
)
def test_bare_bubble_point_gets_the_register_margin(level, intake_barg, status):
    """PH-18: an explicit p_b (per ESP or evidence-level) is a bare bubble point.

    The verifier applies VER-ESP-MARGIN: limit = 1.2 · (p_b + 1.01325) − 1.01325 bar(g).
    """
    assert SETPOINT_BARG == pytest.approx(54.32265)
    esp = EspPoint(
        name="ESP-1", intake_pressure_barg=intake_barg, npsh_available_m=60, npsh_required_m=20
    )
    if level == "esp":
        esp = esp.model_copy(update={"release_pressure_barg": P_B_BARG})
        report = _verify(_with(esps=[esp]))
    else:
        report = _verify(_with(esps=[esp], co2_release_pressure_barg=P_B_BARG))
    check = report.check("PH-18")
    assert check.status == status, check
    assert check.margin == pytest.approx(intake_barg - SETPOINT_BARG, abs=1e-6)
    assert "1.2 × p_b(a), p_b 45.1 bar(g) = 46.11 bar(a)" in check.detail
    assert not check.warnings and check.name.startswith("ESP intake ≥ 1.2 ·")
    assert ESP_MARGIN_ID in report.assumption_ids
    assert ESP_RELEASE_PRESSURE_ID not in report.assumption_ids


def test_esp_level_bubble_point_takes_precedence():
    """A per-ESP p_b overrides the evidence-level one (30 → 37.2 bar(g) setpoint, not 54.3)."""
    esp = EspPoint(
        name="ESP-1",
        intake_pressure_barg=40.0,
        release_pressure_barg=30.0,
        npsh_available_m=60,
        npsh_required_m=20,
    )
    report = _verify(_with(esps=[esp], co2_release_pressure_barg=P_B_BARG))
    check = report.check("PH-18")
    assert check.status == CheckStatus.PASS
    assert check.margin == pytest.approx(40.0 - (1.2 * (30.0 + 1.01325) - 1.01325))


def test_impossible_bubble_point_is_refused():
    """A bubble point below 0 bar(a) is refused at the evidence boundary."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EspPoint(name="ESP-1", intake_pressure_barg=10.0, release_pressure_barg=-5.0)
    with pytest.raises(ValidationError):
        _with(co2_release_pressure_barg=-1.1)
    assert _with(co2_release_pressure_barg=-1.0).co2_release_pressure_barg == -1.0  # 0.013 bar(a)


def test_margin_comes_from_the_register():
    """The factor is read from VER-ESP-MARGIN (not hard-coded); below 1 it is refused."""
    from gemini_agent.schemas.config import load_config

    register = load_config("assumptions")
    entry = register.get(ESP_MARGIN_ID)
    assert (entry.value, entry.unit, entry.tag.value) == (1.2, "-", "A")
    others = [a for a in register.assumptions if a.id != ESP_MARGIN_ID]

    def verifier(value: float) -> DesignVerifier:
        changed = entry.model_copy(update={"value": value})
        return DesignVerifier(
            VERIFIER.limits,
            VERIFIER.design_basis,
            register.model_copy(update={"assumptions": [*others, changed]}),
        )

    assert verifier(1.0).required_pressure_barg(P_B_BARG) == pytest.approx(P_B_BARG)
    assert verifier(1.3).required_pressure_barg(P_B_BARG) == pytest.approx(
        1.3 * (P_B_BARG + 1.01325) - 1.01325
    )
    assert VERIFIER.required_pressure_barg(P_B_BARG) == pytest.approx(SETPOINT_BARG)
    with pytest.raises(ValueError, match="VER-ESP-MARGIN"):
        verifier(0.9)
    report = verifier(1.3).verify(ef.good_evidence())  # the check name follows the register
    assert report.check("PH-18").name.startswith("ESP intake ≥ 1.3 ·")
    wrong_unit = entry.model_copy(update={"unit": "%"})
    with pytest.raises(ValueError, match="dimensionless"):
        DesignVerifier(
            VERIFIER.limits,
            VERIFIER.design_basis,
            register.model_copy(update={"assumptions": [*others, wrong_unit]}),
        )


def test_register_pressure_units_are_converted():
    """The register default may be bar(a) or bar(g); any other unit is refused."""
    from gemini_agent.schemas.config import load_config

    register = load_config("assumptions")
    entry = register.get(ESP_RELEASE_PRESSURE_ID)
    assert (entry.value, entry.unit) == (60.0, "bar(a)")
    gauge = entry.model_copy(update={"value": 59.0, "unit": "bar(g)"})
    others = [a for a in register.assumptions if a.id != ESP_RELEASE_PRESSURE_ID]
    verifier = DesignVerifier(
        VERIFIER.limits,
        VERIFIER.design_basis,
        register.model_copy(update={"assumptions": [*others, gauge]}),
    )
    assert verifier.release_pressure_default_barg == 59.0
    bad = entry.model_copy(update={"unit": "MPa"})
    with pytest.raises(ValueError, match="bar\\(a\\) or bar\\(g\\)"):
        DesignVerifier(
            VERIFIER.limits,
            VERIFIER.design_basis,
            register.model_copy(update={"assumptions": [*others, bad]}),
        )


def test_pressurised_loop_behind_gasketed_plate_hex_fails_with_reason():
    """PD-10 uses the same 60 bar(a) setpoint: a gasketed plate HEX at 20 bar cannot hold it."""
    report = _verify(
        _with(**_pressurised(**_hex(design_pressure_barg=20.0, min_brine_pressure_barg=20.0)))
    )
    check = report.check("PD-10")
    assert check.status == CheckStatus.FAIL
    assert check.limit == pytest.approx(58.98675) and check.margin == pytest.approx(20 - 58.98675)
    for phrase in ("gasketed plate HEX rated 20 bar(g)", "VER-ESP-PB 60 bar(a)", "degas"):
        assert phrase in check.detail, check.detail
    assert report.check("PH-09").status == CheckStatus.PASS  # the HEX itself is within rating
    assert ESP_RELEASE_PRESSURE_ID in report.assumption_ids
    explicit = _verify(_with(**_pressurised(co2_release_pressure_barg=12.0)))
    check = explicit.check("PD-10")  # explicit p_b overrides the default, with the margin
    assert check.status == CheckStatus.PASS
    assert check.limit == pytest.approx(1.2 * (12.0 + 1.01325) - 1.01325)
    between = _verify(_with(**_pressurised(co2_release_pressure_barg=15.0)))
    assert between.check("PD-10").status == CheckStatus.FAIL  # 16 < 1.2 · p_b = 18.20 bar(g)


def test_heat_pump_datasheet_overrides_rc04_defaults():
    """With datasheet limits the RC-04 [A] defaults are not used (and not cited)."""
    report = _verify(_with(**_first("heat_pumps", max_lift_k=58.0, max_carnot_fraction=0.65)))
    assert report.check("PH-14").status == CheckStatus.PASS
    assert HP_CARNOT_FRACTION_ID not in report.assumption_ids
    assert HP_MAX_LIFT_ID not in report.assumption_ids


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
    assert report.check("PD-10").status == CheckStatus.NOT_APPLICABLE  # degassed loop
    fixed = scaling.model_copy(update={"mitigations": [*scaling.mitigations, "calcite_inhibitor"]})
    assert _verify(_with(candidate=candidate, scaling=fixed)).verdict == DesignVerdict.ACCEPTED


def test_fast_full_discrepancy_between_two_and_five_percent_warns():
    """SIM-04: a 3 % daily discrepancy warns but passes; missing fast days are not evaluated."""
    fast = ef.annual([ef.fast_day(0, 0.6), ef.fast_day(1)])
    check = _verify(_with(fast_annual=fast)).check("PD-06")
    assert check.status == CheckStatus.PASS and check.warnings
    partial = ef.annual([ef.fast_day(0)])
    check = _verify(_with(fast_annual=partial)).check("PD-06")
    assert check.status == CheckStatus.NOT_EVALUATED


@pytest.mark.parametrize(
    ("material", "copper"),
    [
        ("CuNi 90/10", True),
        ("CuNi10Fe1Mn", True),
        ("CuZn37", True),
        ("Monel 400", True),
        ("UNS C70600", True),
        ("Sn-Pb solder", True),
        ("admiralty brass tubes", True),
        ("Cu-free duplex", False),
        ("copper-free gasket", False),
        ("Custom alloy", False),
        ("carbon steel Sch 80", False),
        ("titanium Gr.2", False),
    ],
)
def test_copper_alloy_detection(material, copper):
    """RC-12: copper alloys and solder are refused in brine contact; Cu-free is fine."""
    assert is_copper_alloy(material) is copper


@pytest.mark.parametrize(
    ("material", "titanium"),
    [
        ("titanium Gr.1", True),
        ("Ti Gr.2", True),
        ("Ti-0.15Pd (Gr.7)", True),
        ("SS 316 Ti", False),
        ("AISI 321 (Ti-stabilised)", False),
        ("316Ti", False),
        ("not titanium", False),
        ("duplex 2205", False),
    ],
)
def test_titanium_plate_detection(material, titanium):
    """RC-12/RC-03: only titanium plates count; Ti-stabilised steels do not."""
    assert is_titanium(material) is titanium


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
    expected = {"PH-02", "PH-03", "PH-04", "PH-07", "PH-17", "PH-18", "PH-22", "PH-23", "PH-24"}
    expected |= {"PD-02", "PD-03", "PD-05", "PD-07", "PD-08", "PD-09", "PD-10"}
    assert expected <= not_applicable
    assert report.verdict == DesignVerdict.UNVERIFIED  # project and daily results still missing


def test_heat_pump_listed_in_equipment_needs_evidence():
    """A candidate with an HTHP but no heat-pump operating point is not verifiable."""
    report = _verify(_with(heat_pumps=[]))
    assert report.check("PH-13").status == CheckStatus.NOT_EVALUATED
    candidate = ef.candidate(equipment_sizes={"absorption_cooling_mw": 3.0})
    report = _verify(_with(heat_pumps=[], candidate=candidate))
    assert report.check("PH-13").status == CheckStatus.NOT_APPLICABLE


def test_brine_heat_drawn_is_required_for_producing_days():
    """Without the brine-side breakdown the brine balance cannot be closed: not evaluated."""
    report = _verify(_with(**_days(brine_heat_drawn_mw={})))
    assert report.check("PH-22").status == CheckStatus.NOT_EVALUATED
    assert report.verdict == DesignVerdict.UNVERIFIED


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


def _run(tmp_path: Path, box: toolbox.Toolbox, run_id: str):
    return DesignRunner(PolicyEngine(), runs_root=tmp_path, toolbox=box).start("", run_id=run_id)


def test_only_the_real_verifier_on_real_evidence_makes_accepted_stick(tmp_path):
    """AG-11 end to end: ACCEPTED stands only from a stub=False verifier in a stub-free run."""
    spec = verify_design_spec(_good_provider, VERIFIER)
    assert spec.stub is False and spec.name == "verify_design"
    box = stub_toolbox()
    box.register(spec)
    result = _run(tmp_path, box, "stub_tools")  # upstream tools are still stubs
    assert result.status == RunStatus.COMPLETED and result.verdict == "UNVERIFIED"

    for name in box.names():
        box.register(dataclasses.replace(box.spec(name), stub=False))
    result = _run(tmp_path, box, "real")
    assert result.status == RunStatus.COMPLETED and result.verdict == "ACCEPTED"

    box.register(dataclasses.replace(spec, stub=True))
    assert _run(tmp_path, box, "stub_verifier").verdict == "UNVERIFIED"


# --------------------------------------------------------------------------- WP-14 checks
def test_wp14_gap_checks_need_evidence():
    """Without TL-05 points or TL-07/08 flags the checks are not evaluated: UNVERIFIED."""
    sub = SubsurfaceEvidence(
        well_spacing_m=1200.0, interference_bar=0.6, thermal_breakthrough_yr=33.9
    )
    report = _verify(_with(doublet_points=[], subsurface=sub))
    assert {"PH-25", "PH-26", "PH-27", "PD-11", "PD-12"} <= set(report.not_evaluated)
    assert report.verdict == DesignVerdict.UNVERIFIED


def test_wp14_informational_warnings_do_not_fail():
    """Extrapolation warnings and MISSING-datasheet flags are reported, not failed."""
    note = "electric_chiller: COP 9.10 capped at 8"
    flag = "hthp_1 (running): HTHP datasheet MISSING ['max_lift_k']: RC-04 defaults"
    report = _verify(_with(model_warnings=[note], **_days(flags=[flag])))
    assert report.verdict == DesignVerdict.ACCEPTED
    assert note in report.check("PH-28").warnings
    assert report.check("PH-29").warnings == [f"{flag} [×1]"]


def test_wp14_letdown_above_one_atmosphere_is_not_evaluated():
    """PH-30: a pressurised degasser has no card saturation data → not evaluated."""
    report = _verify(
        _with(letdown_pressure_barg=2.0, **_days(production_temperature_degc=[105.0, 86.0]))
    )
    assert report.check("PH-30").status == CheckStatus.NOT_EVALUATED


def test_npsh_required_missing_uses_the_sub_npsh_rule():
    """SUB-NPSH: without NPSH_R (no pump selected) PH-19 checks NPSH_A ≥ 15 m, with a warning."""
    ok = _verify(_with(**_first("esps", npsh_available_m=40.0, npsh_required_m=None)))
    assert ok.check("PH-19").status == CheckStatus.PASS
    assert ok.check("PH-19").margin == pytest.approx(40.0 - 15.0)
    assert "NPSH_R not given" in ok.check("PH-19").warnings[0]
    low = _verify(_with(**_first("esps", npsh_available_m=12.0, npsh_required_m=None)))
    assert low.check("PH-19").status == CheckStatus.FAIL
    missing = _verify(_with(**_first("esps", npsh_available_m=None)))
    assert "PH-19" in missing.not_evaluated
