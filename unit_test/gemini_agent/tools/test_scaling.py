"""Tests for TL-22 scaling_check (RC-12 validation tests)."""

from __future__ import annotations

import math

import pytest

from gemini_agent.schemas.common import GasHandling
from gemini_agent.tools import scaling as sc
from gemini_agent.tools.scaling import BrineChemistry, ScalingRequest
from gemini_agent.verify.design_verifier import Mitigation

ALL_DEGAS = [
    Mitigation.TITANIUM_PLATES,
    Mitigation.OXYGEN_EXCLUSION,
    Mitigation.NO_COPPER,
    Mitigation.CALCITE_INHIBITOR,
]


@pytest.mark.parametrize(
    ("ri", "expected"),
    [
        (3.99, "extreme"),
        (4.0, "heavy"),
        (5.49, "heavy"),
        (5.5, "slight"),
        (6.19, "slight"),
        (6.2, "little or none"),
        (7.0, "little or none"),
        (7.01, "none (corrosive)"),
    ],
)
def test_ryznar_class_boundaries(ri: float, expected: str) -> None:
    """RC-12 validation: RI classification boundaries."""
    assert sc.classify_ryznar(ri) == expected


def test_missing_chemistry_returns_analyte_list_and_mitigations() -> None:
    """RC-12 validation: missing-data path returns the analyte list."""
    result = sc.scaling_check(ScalingRequest(gas_handling=GasHandling.DEGAS_USE))
    report = result.data
    assert report.status == "insufficient data"
    assert report.missing_inputs[:6] == ["Ca", "alkalinity", "pH", "Cl", "SO4", "TDS"]
    assert set(report.required_mitigations) == set(ALL_DEGAS)
    assert set(report.missing_mitigations) == set(ALL_DEGAS)
    assert len(report.recommendations) == 4
    assert result.warnings and "MISSING INPUT" in result.warnings[0]


def test_missing_chemistry_with_mitigations_feeds_the_verifier() -> None:
    """Check missing chemistry with mitigations feeds the verifier."""
    result = sc.scaling_check(
        ScalingRequest(gas_handling=GasHandling.DEGAS_USE, design_mitigations=ALL_DEGAS)
    )
    evidence = result.data.evidence
    assert evidence.status == "insufficient data"
    assert set(evidence.mitigations) == set(ALL_DEGAS)
    assert result.data.missing_mitigations == []
    reinject = sc.scaling_check(ScalingRequest(gas_handling=GasHandling.PRESSURISED_REINJECT)).data
    assert Mitigation.PRESSURISED_LOOP in reinject.required_mitigations
    assert Mitigation.CALCITE_INHIBITOR not in reinject.required_mitigations
    assert "CO2-release and minimum loop pressure" in reinject.missing_inputs


def _chemistry(**overrides) -> BrineChemistry:
    """Fixture:  chemistry."""
    data = {
        "temperature_degc": 85.0,
        "ph_actual": 6.5,
        "calcium_mol_per_l": 1e-3,
        "alkalinity_eq_per_l": 1e-3,
        "sulfate_mol_per_l": 1e-3,
        "chloride_mg_per_l": 1e5,
        "tds_mg_per_l": 1.6e5,
        "k_c": 0.0,
        "log_k_gypsum": -4.6,
    }
    data.update(overrides)
    return BrineChemistry.model_validate(data)


def test_ryznar_and_gypsum_hand_calculation() -> None:
    """Check ryznar and gypsum hand calculation."""
    ph_s = sc.saturation_ph(1e-3, 1e-3, 0.5)
    assert ph_s == pytest.approx(6.5)
    assert sc.ryznar_index(ph_s, 6.5) == pytest.approx(6.5)
    assert sc.gypsum_relative_saturation(1e-2, 1e-2, -4.6) == pytest.approx(10**0.6)


def test_with_chemistry_pass_and_fail() -> None:
    """Check with chemistry pass and fail."""
    ok = sc.scaling_check(
        ScalingRequest(
            gas_handling=GasHandling.DEGAS_USE,
            chemistry=_chemistry(k_c=0.4),
            design_mitigations=ALL_DEGAS,
        )
    ).data
    assert ok.status == "pass"
    assert ok.ryznar_index == pytest.approx(2 * 6.4 - 6.5)  # pH_s = 3 + 3 + 0.4
    assert ok.ryznar_class == "little or none"
    assert ok.ryznar_margin == pytest.approx(6.3 - 5.5)
    assert ok.gypsum_margin == pytest.approx(1.2 - 10 ** (-6 + 4.6))
    boundary = sc.scaling_check(
        ScalingRequest(
            gas_handling=GasHandling.DEGAS_USE, chemistry=_chemistry(), design_mitigations=ALL_DEGAS
        )
    ).data
    assert boundary.ryznar_index == pytest.approx(5.5) and boundary.ryznar_class == "slight"
    assert boundary.status == "pass"  # margin 0 passes; the inhibitor is in the design
    heavy = sc.scaling_check(
        ScalingRequest(
            gas_handling=GasHandling.DEGAS_USE,
            chemistry=_chemistry(k_c=-0.5),  # pH_s 5.5, RI 4.5
            design_mitigations=ALL_DEGAS,
        )
    ).data
    assert heavy.status == "fail" and heavy.ryznar_class == "heavy"
    assert heavy.evidence.status == "fail"
    gypsum = sc.scaling_check(
        ScalingRequest(
            gas_handling=GasHandling.DEGAS_USE,
            chemistry=_chemistry(calcium_mol_per_l=1e-2, sulfate_mol_per_l=1e-2),
            design_mitigations=ALL_DEGAS,
        )
    ).data
    assert gypsum.status == "fail" and gypsum.gypsum_margin < 0
    lacking = sc.scaling_check(
        ScalingRequest(gas_handling=GasHandling.DEGAS_USE, chemistry=_chemistry(k_c=0.4))
    ).data
    assert lacking.status == "fail" and lacking.missing_mitigations
    incomplete = sc.scaling_check(
        ScalingRequest(
            gas_handling=GasHandling.DEGAS_USE,
            chemistry=_chemistry(k_c=-1.0, tds_mg_per_l=None),
            design_mitigations=ALL_DEGAS,
        )
    ).data
    assert incomplete.status == "fail"  # a computed failure wins over missing inputs
    assert incomplete.missing_inputs == ["TDS"]


def test_slight_scaling_requires_inhibitor_on_pressurised_loop() -> None:
    """Check slight scaling requires inhibitor on pressurised loop."""
    base = [Mitigation.TITANIUM_PLATES, Mitigation.OXYGEN_EXCLUSION, Mitigation.NO_COPPER]
    request = ScalingRequest(
        gas_handling=GasHandling.PRESSURISED_REINJECT,
        chemistry=_chemistry(k_c=0.4),  # pH_s 6.4, RI 6.3 little; k_c 0.2 -> RI 5.9 slight
        design_mitigations=base + [Mitigation.PRESSURISED_LOOP],
        co2_release_pressure_barg=45.0,
        min_loop_pressure_barg=50.0,
    )
    assert sc.scaling_check(request).data.status == "pass"
    slight = sc.scaling_check(request.model_copy(update={"chemistry": _chemistry(k_c=0.2)})).data
    assert slight.ryznar_class == "slight"
    assert slight.missing_mitigations == [Mitigation.CALCITE_INHIBITOR]
    assert slight.status == "fail"
    low = sc.scaling_check(request.model_copy(update={"min_loop_pressure_barg": 20.0})).data
    assert low.co2_release_margin_bar == pytest.approx(-25.0)
    assert low.status == "fail"


def test_inputs_validated() -> None:
    """Check inputs validated."""
    with pytest.raises(ValueError):
        sc.saturation_ph(0.0, 1e-3, 0.0)
    assert math.isfinite(sc.gypsum_relative_saturation(1e-3, 1e-3, -4.6))


@pytest.mark.parametrize(
    ("handling", "mitigations", "expected"),
    [
        (GasHandling.DEGAS_USE, ALL_DEGAS, "pass"),
        (GasHandling.DEGAS_USE, ALL_DEGAS[:3], "fail"),
        (
            GasHandling.PRESSURISED_REINJECT,
            ALL_DEGAS[:3] + [Mitigation.PRESSURISED_LOOP],
            "pass",
        ),
    ],
)
def test_evidence_agrees_with_verifier_pd07(handling, mitigations, expected) -> None:
    """TL-22 → ScalingEvidence: PD-07 passes exactly when the tool lists no missing mitigation."""
    from gemini_agent.verify.design_verifier import CheckStatus, DesignVerifier
    from unit_test.gemini_agent.verify import evidence_factory as ef

    report = sc.scaling_check(
        ScalingRequest(gas_handling=handling, design_mitigations=mitigations)
    ).data
    evidence = ef.good_evidence(
        candidate=ef.candidate(gas_handling=handling), scaling=report.evidence
    )
    check = DesignVerifier.from_config().verify(evidence).check("PD-07")
    assert check.status == (CheckStatus.PASS if expected == "pass" else CheckStatus.FAIL)
    assert (report.missing_mitigations == []) is (expected == "pass")


def test_thresholds_match_register() -> None:
    """SCL-RULES in config/assumptions.yaml and the module constants must not drift apart."""
    from gemini_agent.schemas.config import load_config

    rules = load_config("assumptions").get("SCL-RULES").value
    assert rules == {
        "ryznar_fail_below": sc.RI_HEAVY_BELOW,
        "ryznar_inhibitor_below": sc.RI_SLIGHT_BELOW,
        "gypsum_rs_fail_above": sc.RS_AMBIGUOUS_HIGH,
        "gypsum_rs_inhibitor_from": sc.RS_AMBIGUOUS_LOW,
    }
