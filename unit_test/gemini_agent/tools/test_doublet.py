"""Tests for TL-05 ``doublet_operating_point`` and TL-06 ``doublet_performance_map`` (RC-01).

Hand calculations (RC-01 eq 4–6, 12) are independent of GEMINI; the acceptance criteria of
WP-05 are checked on the full 50…300 m³/h map: target flow within 1 % (GEMINI ProducerWell) or
an explicit infeasible flag, Q_inj = Q_prod, cached map reloaded, runtime < 60 s.
"""

import json
import math
import time

import pytest
from pydantic import ValidationError
from scipy.optimize import brentq

from gemini_agent.runner.toolbox import FlowArgs
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import SUMMARY_MAX_CHARS
from gemini_agent.schemas.config import REPO_ROOT, load_config
from gemini_agent.tools import doublet as doublet_module
from gemini_agent.tools.design_basis import productivity_index_m3h_per_bar
from gemini_agent.tools.doublet import (
    DEFAULT_CACHE_DIR,
    FLOW_TOLERANCE_FRAC,
    HENRY_CONSTANTS,
    DoubletDesign,
    EspEvidence,
    OperatingPointRequest,
    PerformanceMapRequest,
    brine_bubble_point,
    bubble_point_estimate,
    build_performance_map,
    curve_runout_and_bep,
    dissolved_gas_mol_m3,
    doublet_operating_point,
    doublet_performance_map,
    henry_constant_mol_m3_pa,
    map_flows,
    operating_point,
    resolve_doublet,
)
from gemini_agent.tools.resource import load_subsurface_basis
from gemini_agent.verify.design_verifier import (
    ESP_MARGIN_ID,
    ESP_RELEASE_PRESSURE_ID,
    CheckStatus,
    DesignVerifier,
    EspPoint,
    VerificationEvidence,
)
from gemini_model.well.correlation.darcyweisbach import DarcyWeisbach
from gemini_simulator.well_flow import solve_injector_wellhead_pressure_bar

G = 9.81
U_GEMINI_W_M2K = 10.0  # GEMINI DPDT hard-coded wellbore heat-transfer coefficient (RC-01 note)


@pytest.fixture(scope="module")
def basis():
    """Subsurface inputs from the repository config."""
    return load_subsurface_basis()


@pytest.fixture(scope="module")
def doublet(basis):
    """Resolve the default design: register defaults, ESP sized."""
    return resolve_doublet(DoubletDesign(), basis)


@pytest.fixture(scope="module")
def perf_map(basis, doublet):
    """Build the default 50…300 m³/h map, uncached."""
    return build_performance_map(doublet, map_flows(basis.flow_min_m3h, basis.flow_max_m3h, 10.0))


@pytest.fixture(scope="module")
def full_flow(perf_map):
    """Operating point at the maximum flow."""
    return perf_map.points[-1]


def _friction_bar(basis, flow_m3h):
    """RC-01 eq 5 by hand (Darcy–Weisbach, Swamee–Jain) over the full well depth."""
    d, eps = basis.well_inner_diameter_m, basis.well_roughness_m
    rho, mu = basis.density_kg_m3, basis.viscosity_pa_s
    velocity = flow_m3h / 3600.0 / (math.pi / 4.0 * d**2)
    reynolds = rho * velocity * d / mu
    f = 0.25 / math.log10(eps / (3.7 * d) + 5.74 / reynolds**0.9) ** 2
    return f * basis.design_depth_m / d * rho * velocity**2 / 2.0 / 1e5


def _pi(basis):
    return productivity_index_m3h_per_bar(
        basis.permeability_md,
        basis.thickness_m,
        basis.viscosity_pa_s,
        basis.drainage_radius_m,
        basis.wellbore_radius_m,
        basis.skin,
    )


# --------------------------------------------------------------------------- RC-01 card checks
def test_swamee_jain_card_validation():
    """RC-01 validation: Swamee–Jain at Re = 1e6, ε/D = 0.0003 gives f = 0.0158 (±1 %)."""
    rho, mu, diameter, length = 1000.0, 1.0e-3, 0.1, 1.0
    velocity = 1.0e6 * mu / (rho * diameter)
    dp_fric, _ = DarcyWeisbach.calculate_dp(
        velocity, rho, math.pi / 2, mu, diameter, 0.0003 * diameter, length
    )
    friction_factor = dp_fric * diameter / (length * rho * velocity**2 / 2.0)
    assert friction_factor == pytest.approx(0.0158, rel=0.01)


def test_henry_constant_card_validation():
    """RC-01 item 9: H_CH₄(360.15 K, sea water) = 0.636 mol m⁻³ bar⁻¹ (±1 %)."""
    h0, b_k = HENRY_CONSTANTS["sea_water"]["CH4"]
    assert henry_constant_mol_m3_pa(h0, b_k, 360.15) * 1e5 == pytest.approx(0.636, rel=0.01)
    assert henry_constant_mol_m3_pa(h0, b_k, 298.15) == h0  # reference temperature


def test_bubble_point_card_validation(basis):
    """RC-01 item 9 at 87 °C: p_b 46.1 (sea) / 52.2 (pure) bar(a); CO₂ 1.1 bar; setpoint 55.3."""
    assert dissolved_gas_mol_m3(0.8, 0.8) == pytest.approx(28.55, abs=0.01)
    assert dissolved_gas_mol_m3(0.8, 0.2) == pytest.approx(7.14, abs=0.01)
    bubble = brine_bubble_point(basis, 87.0)
    assert bubble.basis.solvent == "sea_water" and bubble.second_bound.solvent == "pure_water"
    assert bubble.release_pressure_bara == pytest.approx(46.1, abs=0.5)
    assert bubble.second_bound.bubble_pressure_bara == pytest.approx(52.2, abs=0.5)
    co2 = next(g for g in bubble.basis.gases if g.gas == "CO2")
    assert co2.partial_pressure_bara == pytest.approx(1.1, abs=0.1)
    assert bubble.intake_setpoint_bara == pytest.approx(55.3, abs=0.1)
    assert bubble.release_pressure_barg == pytest.approx(bubble.release_pressure_bara - 1.01325)
    assert not bubble.notes  # 87 °C is inside the card's 25–100 °C range


def test_bubble_point_matches_register_record(basis):
    """The p_b values recorded in SUB-PBUB are the ones the tool computes at DB-01 (87 °C)."""
    record = load_config("assumptions").get("SUB-PBUB").value
    bubble = brine_bubble_point(basis, record["at_temperature_degc"])
    assert bubble.margin_factor == record["intake_margin_factor"] == 1.2
    assert bubble.release_pressure_bara == pytest.approx(record["bubble_pressure_bara"], abs=0.05)
    assert bubble.second_bound.bubble_pressure_bara == pytest.approx(
        record["bubble_pressure_pure_water_bara"], abs=0.05
    )
    assert bubble.intake_setpoint_bara == pytest.approx(record["intake_setpoint_bara"], abs=0.05)


def test_bubble_point_monotonic():
    """RC-01 item 9: p_b rises with GLR and, for CH₄ over 25–100 °C, with temperature."""
    by_glr = [
        bubble_point_estimate(87.0, glr, 0.8, 0.2, "sea_water").bubble_pressure_bara
        for glr in (0.4, 0.8, 1.2)
    ]
    assert by_glr == sorted(by_glr) and by_glr[1] == pytest.approx(2 * by_glr[0])
    by_temperature = [
        bubble_point_estimate(t, 0.8, 1.0, 0.0, solvent).bubble_pressure_bara
        for solvent in ("sea_water", "pure_water")
        for t in (25.0, 50.0, 75.0, 100.0)
    ]
    assert by_temperature[:4] == sorted(by_temperature[:4])
    assert by_temperature[4:] == sorted(by_temperature[4:])


def test_bubble_point_outside_checked_range_is_noted(basis):
    """Above 100 °C (or below 25 °C) the van 't Hoff extrapolation is flagged."""
    assert brine_bubble_point(basis, 102.0).notes
    assert brine_bubble_point(basis, 24.0).notes
    with pytest.raises(ValueError, match="solvent"):
        bubble_point_estimate(87.0, 0.8, 0.8, 0.2, "brine")


# --------------------------------------------------------------------------- ESP sizing
def test_gemini_stage_curve_matches_register_statement(basis):
    """SUB-ESP: GEMINI stage curve BEP ≈ 183 m³/h (72 %), run-out ≈ 280 m³/h at 60 Hz."""
    runout, bep, efficiency = curve_runout_and_bep(
        basis.esp_stage_head_coeff, basis.esp_stage_power_coeff, 60.0
    )
    assert runout == pytest.approx(280.0, abs=1.0)
    assert bep == pytest.approx(183.0, abs=1.0)
    assert efficiency == pytest.approx(0.72, abs=0.005)


def test_esp_sized_by_register_rules(basis, doublet):
    """BEP at Q_max; fewest stages reaching Q_max at ≤ 60 Hz; shallowest 10 m setting depth."""
    esp = doublet.esp
    assert set(esp.sized) == {"flow_scale", "stages", "setting_depth"}
    assert esp.best_efficiency_flow_m3h == pytest.approx(basis.flow_max_m3h, rel=1e-4)
    assert esp.runout_flow_m3h > basis.flow_max_m3h
    assert esp.stages == 16
    full = operating_point(doublet, basis.flow_max_m3h)
    assert full.esp_frequency_hz <= esp.frequency_nominal_hz
    fewer = resolve_doublet(DoubletDesign(esp_stages=esp.stages - 1), basis)
    assert operating_point(fewer, basis.flow_max_m3h).esp_frequency_hz > 60.0
    assert esp.setting_depth_m == 840.0 and esp.setting_depth_m % 10.0 == 0.0
    assert full.intake_margin_bar >= 0.0
    shallower = resolve_doublet(
        DoubletDesign(esp_setting_depth_m=esp.setting_depth_m - 10.0), basis
    )
    assert operating_point(shallower, basis.flow_max_m3h).intake_margin_bar < 0.0


def test_scaled_curve_keeps_efficiency(basis, doublet):
    """Pump-family scaling (Q × k, H × 1, P × k) leaves the BEP efficiency unchanged."""
    _, _, base_efficiency = curve_runout_and_bep(
        basis.esp_stage_head_coeff, basis.esp_stage_power_coeff, 60.0
    )
    assert doublet.esp.best_efficiency_frac == pytest.approx(base_efficiency, rel=1e-6)


# --------------------------------------------------------------------------- acceptance
def test_map_hits_target_flow_or_flags_infeasible(perf_map):
    """Every point is within 1 % of its target via GEMINI ProducerWell, or infeasible + reason."""
    assert perf_map.points[0].flow_m3h == 50.0
    assert perf_map.points[-1].flow_m3h == 300.0
    for point in perf_map.points:
        if point.feasible:
            assert point.flow_error_frac <= FLOW_TOLERANCE_FRAC
            assert point.producer_flow_check_m3h == pytest.approx(point.flow_m3h, rel=0.01)
        else:
            assert point.reasons
    assert perf_map.feasible_flow_min_m3h == 50.0 and perf_map.feasible_flow_max_m3h == 300.0


def test_injection_equals_production(perf_map):
    """DB-14: Q_inj = Q_prod at every point."""
    assert all(p.injection_flow_m3h == p.flow_m3h for p in perf_map.points)


def test_map_runtime_under_60_s(basis, doublet):
    """WP-05 acceptance: a full map in < 60 s."""
    start = time.perf_counter()
    build_performance_map(doublet, map_flows(basis.flow_min_m3h, basis.flow_max_m3h, 10.0))
    assert time.perf_counter() - start < 60.0


def test_rc01_envelope_at_full_flow(full_flow):
    """RC-01 sanity envelope at 300 m³/h."""
    assert 20.0 <= full_flow.drawdown_bar <= 25.0
    assert 30.0 <= full_flow.esp_dp_bar <= 40.0
    assert full_flow.esp_shaft_power_w == pytest.approx(0.4e6, rel=0.1)
    assert full_flow.injection_pump_shaft_power_w == pytest.approx(0.3e6, rel=0.1)
    assert 85.0 <= full_flow.wellhead_temperature_degc <= 86.0


# --------------------------------------------------------------------------- hand calculations
def test_esp_dp_and_power_by_hand(basis, full_flow):
    """RC-01 eq 4–7: Δp_ESP = P_wh + ρgz + ΔP_fric − P_wf; P_shaft = QΔp/0.70 (DB-13)."""
    q = basis.flow_max_m3h
    p_wf = basis.design_pressure_barg - q / _pi(basis)
    hydrostatic = basis.density_kg_m3 * G * basis.design_depth_m / 1e5
    dp = basis.producer_wellhead_pressure_barg + hydrostatic + _friction_bar(basis, q) - p_wf
    assert full_flow.bottomhole_pressure_barg == pytest.approx(p_wf, abs=1e-6)
    assert full_flow.producer_friction_bar == pytest.approx(_friction_bar(basis, q), rel=1e-6)
    assert full_flow.esp_dp_bar == pytest.approx(dp, abs=1e-4)
    shaft = q / 3600.0 * dp * 1e5 / 0.70
    assert full_flow.esp_shaft_power_w == pytest.approx(shaft, rel=1e-5)
    assert full_flow.esp_electric_power_w == pytest.approx(shaft / (0.93 * 0.97), rel=1e-5)
    assert full_flow.esp_curve_shaft_power_w == pytest.approx(shaft, rel=0.10)


def test_injector_by_hand_rc01_eq12(basis, full_flow):
    """RC-01 eq 12: P_inj,wh = P_res + Q/II − ρgH + ΔP_fric; pump P_el = QΔp/(0.7·0.93·0.97)."""
    q = basis.flow_max_m3h
    hydrostatic = basis.density_kg_m3 * G * basis.design_depth_m / 1e5
    friction = _friction_bar(basis, q)
    whp = basis.design_pressure_barg + q / _pi(basis) - hydrostatic + friction
    assert full_flow.injector_wellhead_pressure_barg == pytest.approx(whp, abs=1e-4)
    assert full_flow.injector_friction_bar == pytest.approx(friction, rel=1e-6)
    assert full_flow.injection_pump_required
    assert full_flow.injection_pump_dp_bar == pytest.approx(whp - 0.0, abs=1e-4)  # SUB-PSUC 0
    electric = q / 3600.0 * whp * 1e5 / (0.70 * 0.93 * 0.97)
    assert full_flow.injection_pump_electric_power_w == pytest.approx(electric, rel=1e-5)
    assert full_flow.total_pump_electric_power_w == pytest.approx(
        full_flow.esp_electric_power_w + full_flow.injection_pump_electric_power_w
    )


def test_injector_uses_corrected_friction_path(basis, doublet, full_flow):
    """The tool's injector WHP is the friction-as-loss GEMINI path, 2·ΔP_fric above legacy."""
    config = {
        "reservoir_pressure_bar": doublet.reservoir_pressure_barg,
        "productivity_index_m3h_per_bar": doublet.injectivity_index_m3h_per_bar,
        "welltrajectory": [
            {"angle": 90.0, "depth": doublet.depth_m, "diameter": doublet.well_inner_diameter_m}
        ],
        "roughness_m": doublet.well_roughness_m,
        "pvt": doublet.pvt,
    }
    legacy = solve_injector_wellhead_pressure_bar(config, 300.0, 45.0)
    assert full_flow.injector_wellhead_pressure_barg - legacy == pytest.approx(
        2.0 * full_flow.injector_friction_bar, abs=1e-4
    )


def test_both_wells_use_brine_pvt(basis, doublet):
    """WP-01 follow-up: brine ρ, μ, c_p (DB-08, DB-33) on both wells."""
    assert doublet.pvt == {
        "liquid_density_kg_m3": basis.density_kg_m3,
        "liquid_viscosity_pa_s": basis.viscosity_pa_s,
        "liquid_specific_heat_j_kgk": basis.cp_j_per_kg_k,
    }
    assert doublet.well_roughness_m == pytest.approx(0.0457e-3)


def test_wellhead_temperature_matches_analytic_profile(basis, full_flow):
    """DPDT 'up' vs the exact solution for a linear ambient: T_wh = T_s + (g/a)(1 − e^(−aL))."""
    mass_flow = basis.flow_max_m3h / 3600.0 * basis.density_kg_m3
    a = U_GEMINI_W_M2K * math.pi * basis.well_inner_diameter_m / (mass_flow * basis.cp_j_per_kg_k)
    gradient = basis.gradient_k_per_km / 1000.0
    exact = basis.surface_temperature_degc + gradient / a * (
        1.0 - math.exp(-a * basis.design_depth_m)
    )
    assert full_flow.wellhead_temperature_degc == pytest.approx(exact, abs=0.05)
    assert full_flow.injection_temperature_degc == pytest.approx(
        full_flow.wellhead_temperature_degc - basis.brine_cooling_max_k
    )


def test_trends_across_the_map(perf_map):
    """Frequency, Δp, pump power and wellhead temperature all rise with flow."""
    points = perf_map.points
    for field in (
        "esp_frequency_hz",
        "esp_dp_bar",
        "total_pump_electric_power_w",
        "wellhead_temperature_degc",
        "injector_wellhead_pressure_barg",
    ):
        values = [getattr(p, field) for p in points]
        assert values == sorted(values), field


def test_gas_release_and_npsh_checks(basis, doublet, full_flow):
    """RC-01 items 8–9: intake ≥ 1.2·p_b, NPSHA ≥ 15 m, release depth above the pump."""
    bubble = doublet.bubble_point
    assert full_flow.release_pressure_barg == bubble.release_pressure_barg
    assert full_flow.intake_setpoint_barg == pytest.approx(
        1.2 * bubble.release_pressure_bara - 1.01325
    )
    assert full_flow.esp_intake_pressure_barg >= full_flow.intake_setpoint_barg
    assert full_flow.npsh_available_m >= basis.npsh_margin_m
    assert 0.0 < full_flow.gas_release_depth_m < doublet.esp.setting_depth_m
    assert full_flow.setting_depth_margin_m > 0.0
    gradient_bar_per_m = basis.density_kg_m3 * G / 1e5 + _friction_bar(basis, 300.0) / (
        basis.design_depth_m
    )
    assert full_flow.tubing_gas_release_depth_m == pytest.approx(
        (bubble.release_pressure_barg - basis.producer_wellhead_pressure_barg) / gradient_bar_per_m,
        rel=1e-3,
    )
    assert any("RC-12" in note for note in full_flow.notes)


def test_esp_evidence_carries_bubble_point(full_flow):
    """p_b goes to the verifier explicitly (WP-09 EspPoint.release_pressure_barg)."""
    evidence = full_flow.esp_evidence
    assert isinstance(evidence, EspEvidence)
    assert evidence.release_pressure_barg == full_flow.release_pressure_barg
    assert evidence.intake_pressure_barg == full_flow.esp_intake_pressure_barg
    assert evidence.npsh_available_m == full_flow.npsh_available_m
    assert evidence.npsh_required_m is None  # NPSHR not given (SUB-NPSH)


def test_esp_evidence_matches_verifier_esp_point(full_flow):
    """Contract with WP-09: the evidence is an ``EspPoint`` carrying the *bare* p_b in bar(g)."""
    point = EspPoint(**full_flow.esp_evidence.model_dump())
    assert set(EspEvidence.model_fields) == set(EspPoint.model_fields)
    assert point.release_pressure_barg == full_flow.release_pressure_barg
    assert point.release_pressure_barg < full_flow.intake_setpoint_barg  # not pre-multiplied


def test_margin_agrees_with_verifier_register():
    """SUB-PBUB (TL-05 sizing) and VER-ESP-MARGIN (verifier PH-18) state the same 1.2."""
    register = load_config("assumptions")
    assert register.get("SUB-PBUB").value["intake_margin_factor"] == 1.2
    assert register.get(ESP_MARGIN_ID).value == 1.2


def _setting_depth_for_intake(basis, target_barg, flow_m3h=300.0):
    """ESP setting depth (m) at which the intake at ``flow_m3h`` equals ``target_barg``."""

    def residual(depth_m):
        design = DoubletDesign(esp_setting_depth_m=depth_m)
        point = operating_point(resolve_doublet(design, basis), flow_m3h)
        return point.esp_intake_pressure_barg - target_barg

    return brentq(residual, 300.0, 2400.0, xtol=1e-9)


def _ph18(point):
    candidate = Candidate.model_validate(
        {
            "id": "C2-wp05",
            "concept": "C2",
            "doublets": 1,
            "depth_m": 2500.0,
            "design_flow_m3h": point.flow_m3h,
            "gas_handling": "degas_use",
            "heating_return_degc": 55.0,
            "cooling_return_degc": 26.0,
        }
    )
    evidence = VerificationEvidence(
        candidate=candidate, esps=[EspPoint(**point.esp_evidence.model_dump())]
    )
    return DesignVerifier.from_config().verify(evidence)


def test_end_to_end_verifier_ph18_at_and_below_setpoint(basis, doublet):
    """TL-05 → verifier PH-18 with the bare p_b: 1.2·p_b passes, between p_b and 1.2·p_b fails.

    The verifier applies VER-ESP-MARGIN itself (limit = 1.2 · (p_b + 1.01325) − 1.01325 bar(g)),
    which is exactly TL-05's setpoint, so both margins agree (the verifier rounds to 1e-6 bar).
    """
    bubble = doublet.bubble_point
    p_b, setpoint = bubble.release_pressure_barg, bubble.intake_setpoint_barg
    # Intake at the setpoint (1e-6 m deeper than the exact root, so the float margin is ≥ 0).
    at_setpoint = operating_point(
        resolve_doublet(
            DoubletDesign(esp_setting_depth_m=_setting_depth_for_intake(basis, setpoint) + 1e-6),
            basis,
        ),
        300.0,
    )
    assert at_setpoint.feasible
    assert at_setpoint.esp_intake_pressure_barg == pytest.approx(setpoint, abs=1e-6)
    assert at_setpoint.esp_evidence.release_pressure_barg == p_b  # bare p_b, not 1.2·p_b
    report = _ph18(at_setpoint)
    check = report.check("PH-18")
    assert check.status == CheckStatus.PASS, check
    assert check.margin == pytest.approx(at_setpoint.intake_margin_bar, abs=1e-6)  # rounded 1e-6
    assert ESP_MARGIN_ID in report.assumption_ids
    assert ESP_RELEASE_PRESSURE_ID not in report.assumption_ids  # register default not used

    # Intake halfway between p_b and 1.2·p_b: TL-05 flags it and the verifier fails PH-18.
    between = operating_point(
        resolve_doublet(
            DoubletDesign(
                esp_setting_depth_m=_setting_depth_for_intake(basis, 0.5 * (p_b + setpoint))
            ),
            basis,
        ),
        300.0,
    )
    assert p_b < between.esp_intake_pressure_barg < setpoint
    assert not between.feasible and any("1.2 × bubble point" in r for r in between.reasons)
    check = _ph18(between).check("PH-18")
    assert check.status == CheckStatus.FAIL, check
    assert check.margin == pytest.approx(between.intake_margin_bar, abs=1e-6)
    # The default sized design (840 m) passes as well.
    assert _ph18(operating_point(doublet, 300.0)).check("PH-18").status == CheckStatus.PASS


def test_pressurised_loop_has_no_tubing_release(basis, doublet):
    """C7: wellhead held above p_b (≈ 45 bar(g)) ⇒ no tubing release; Δp rises with the WHP."""
    whp = 50.0
    assert whp > doublet.bubble_point.release_pressure_barg
    design = DoubletDesign(producer_wellhead_pressure_barg=whp)
    point = operating_point(resolve_doublet(design, basis), 300.0)
    base = operating_point(doublet, 300.0)
    assert point.feasible
    assert point.tubing_gas_release_depth_m is None
    assert point.esp_dp_bar - base.esp_dp_bar == pytest.approx(
        whp - basis.producer_wellhead_pressure_barg, abs=1e-6
    )


# --------------------------------------------------------------------------- other modes
def test_choke_mode_below_minimum_frequency(basis):
    """With many stages the ESP stays at 30 Hz and a wellhead choke takes the excess head."""
    doublet = resolve_doublet(DoubletDesign(esp_stages=30), basis)
    point = operating_point(doublet, 50.0)
    assert point.feasible
    assert point.esp_mode == "min_frequency_choked"
    assert point.esp_frequency_hz == doublet.esp.frequency_min_hz
    assert point.wellhead_choke_dp_bar > 0.0
    assert point.producer_wellhead_pressure_barg == pytest.approx(
        doublet.producer_wellhead_pressure_barg + point.wellhead_choke_dp_bar
    )
    assert point.flow_error_frac <= FLOW_TOLERANCE_FRAC


@pytest.mark.parametrize(
    ("design", "flow", "reason"),
    [
        (DoubletDesign(), 40.0, "DB-11"),
        (DoubletDesign(), 320.0, "DB-11"),
        (DoubletDesign(esp_stages=5), 300.0, "70 Hz"),
        (DoubletDesign(esp_setting_depth_m=450.0), 300.0, "bubble"),
        (DoubletDesign(esp_setting_depth_m=300.0), 300.0, "fluid level"),
        (DoubletDesign(esp_flow_scale=1.0, esp_stages=16), 300.0, "run-out"),
    ],
)
def test_infeasible_points_are_flagged(basis, design, flow, reason):
    """Explicit infeasible flag with a reason instead of a wrong number."""
    point = operating_point(resolve_doublet(design, basis), flow)
    assert not point.feasible
    assert any(reason in text for text in point.reasons)
    for field in (
        "esp_shaft_power_w",
        "esp_electric_power_w",
        "esp_curve_shaft_power_w",
        "injection_pump_shaft_power_w",
        "injection_pump_electric_power_w",
        "total_pump_electric_power_w",
    ):
        assert getattr(point, field) is None, field


def test_shallow_design_sets_pump_at_reservoir_top(basis):
    """If no depth keeps the intake ≥ 1.2·p_b at Q_max the pump sits at the reservoir top."""
    doublet = resolve_doublet(DoubletDesign(depth_m=400.0), basis)
    assert doublet.esp.setting_depth_m == pytest.approx(400.0 - 0.5 * basis.thickness_m)
    assert any("reservoir top" in note for note in doublet.esp.notes)
    perf = build_performance_map(doublet, map_flows(50.0, 300.0, 50.0))
    assert perf.points[0].feasible and not perf.points[-1].feasible
    assert any("DB-04" in note for note in perf.points[0].notes)  # T_inj below 12 °C


def test_lineshaft_range_is_noted(basis, doublet):
    """RC-01 item 11: settings below ≈ 730 m are lineshaft-economical (informational note)."""
    assert not any("lineshaft" in note for note in doublet.esp.notes)  # 840 m
    shallow = resolve_doublet(DoubletDesign(esp_setting_depth_m=600.0), basis)
    assert any("lineshaft" in note for note in shallow.esp.notes)


def test_esp_flow_to_affinity_bep_ratio(full_flow, doublet):
    """Q / (Q_BEP · f/60) is reported; ≈ 1 at full flow (BEP placed at Q_max)."""
    expected = 300.0 / (doublet.esp.best_efficiency_flow_m3h * full_flow.esp_frequency_hz / 60.0)
    assert full_flow.esp_flow_to_bep_ratio == pytest.approx(expected)
    assert 1.0 < full_flow.esp_flow_to_bep_ratio < 1.05


def test_injector_on_gravity_reports_signed_whp(basis):
    """Skin −5 at 50 m³/h: required WHP = P_res + Q/II − ρgH + ΔP_fric < 0 ⇒ no pump, choke."""
    doublet = resolve_doublet(DoubletDesign(skin=-5.0), basis)
    point = operating_point(doublet, 50.0)
    hydrostatic = basis.density_kg_m3 * G * basis.design_depth_m / 1e5
    expected = (
        basis.design_pressure_barg
        + 50.0 / doublet.injectivity_index_m3h_per_bar
        - hydrostatic
        + _friction_bar(basis, 50.0)
    )
    assert expected < 0.0
    assert point.injector_wellhead_pressure_barg == pytest.approx(expected, abs=1e-6)
    assert not point.injection_pump_required
    assert point.injection_pump_electric_power_w == 0.0
    assert any("gravity" in note for note in point.notes)


def test_injectivity_factor_cold_injection(basis, full_flow):
    """II = 0.8 × PI (WP-11 SUB-II case): ≈ +6 bar on the 26.5 bar(g) injection WHP (LIM-SUB-02)."""
    doublet = resolve_doublet(DoubletDesign(injectivity_factor=0.8), basis)
    assert doublet.injectivity_index_m3h_per_bar == pytest.approx(0.8 * _pi(basis))
    point = operating_point(doublet, 300.0)
    extra = 300.0 / doublet.injectivity_index_m3h_per_bar - 300.0 / _pi(basis)
    assert point.injector_wellhead_pressure_barg == pytest.approx(
        full_flow.injector_wellhead_pressure_barg + extra, abs=1e-6
    )
    assert extra == pytest.approx(6.0, abs=0.2)
    assert point.injection_pump_electric_power_w > full_flow.injection_pump_electric_power_w
    assert resolve_doublet(DoubletDesign(), basis) == resolve_doublet(
        DoubletDesign(injectivity_factor=None), basis
    )


def test_suction_above_whp_needs_no_pump(basis):
    """Pump required only when the injector WHP exceeds the suction pressure."""
    doublet = resolve_doublet(DoubletDesign(injection_pump_suction_barg=30.0), basis)
    point = operating_point(doublet, 300.0)
    assert point.injector_wellhead_pressure_barg < 30.0
    assert not point.injection_pump_required
    assert point.injection_pump_dp_bar == 0.0 and point.injection_pump_electric_power_w == 0.0


def test_hot_wellhead_flags_flashing_not_checked(basis):
    """Above 100 °C at the wellhead the saturation check cannot be done: noted, not hidden."""
    point = operating_point(resolve_doublet(DoubletDesign(depth_m=5000.0), basis), 300.0)
    assert point.wellhead_temperature_degc > 100.0
    assert any("flashing" in note for note in point.notes)


def test_deep_design_skips_npsh_above_100c(basis):
    """At 3000 m (T_res 102 °C) the vapour-pressure bound does not hold: NPSH not evaluated."""
    point = operating_point(resolve_doublet(DoubletDesign(depth_m=3000.0), basis), 300.0)
    assert point.feasible
    assert point.npsh_available_m is None
    assert any("NPSH not evaluated" in note for note in point.notes)


def test_design_breaking_hard_limit_raises(basis):
    """DB-12: brine cooling above 40 K is refused, never used silently."""
    with pytest.raises(ValueError, match="DB-12"):
        resolve_doublet(DoubletDesign(brine_cooling_k=45.0), basis)


def test_unsizeable_esp_raises(basis):
    """A curve that cannot reach Q_max cannot be sized."""
    with pytest.raises(ValueError, match="no head"):
        resolve_doublet(DoubletDesign(esp_flow_scale=0.5), basis)


# --------------------------------------------------------------------------- tool envelope
def test_operating_point_tool_result():
    """TL-05 tool: summary ≤ 300 chars, register ids, explicit infeasible summary."""
    result = doublet_operating_point()
    assert result.data.point.feasible
    assert 0 < len(result.summary) <= SUMMARY_MAX_CHARS
    register = load_config("assumptions")
    for assumption_id in result.assumption_ids:
        register.get(assumption_id)
    assert {"SUB-WELL", "SUB-ESP", "SUB-PBUB", "DB-40"} <= set(result.assumption_ids)
    bad = doublet_operating_point(OperatingPointRequest(flow_m3h=320.0))
    assert not bad.data.point.feasible
    assert "INFEASIBLE" in bad.summary and len(bad.summary) <= SUMMARY_MAX_CHARS
    assert bad.warnings


def test_flow_args_bounded_to_hard_limits():
    """WP-10 FlowArgs bounds equal the DB-11 limits in config/limits.yaml."""
    limits = load_config("limits")
    assert FlowArgs(flow_m3h=limits.flow_min_active_m3h).flow_m3h == 50.0
    assert FlowArgs(flow_m3h=limits.flow_max_m3h).flow_m3h == 300.0
    assert FlowArgs().flow_m3h is None
    for flow in (limits.flow_min_active_m3h - 1.0, limits.flow_max_m3h + 1.0):
        with pytest.raises(ValidationError):
            FlowArgs(flow_m3h=flow)


# --------------------------------------------------------------------------- TL-06 map + cache
def test_map_flows_grid():
    """Grid includes both ends of DB-11."""
    assert len(map_flows(50.0, 300.0, 10.0)) == 26
    assert map_flows(50.0, 300.0, 40.0) == [50.0, 90.0, 130.0, 170.0, 210.0, 250.0, 290.0, 300.0]
    assert map_flows(50.0, 300.0, 250.0) == [50.0, 300.0]


def test_map_is_cached_and_reloaded(tmp_path):
    """TL-06: first call computes and writes, second call reloads the same map."""
    first = doublet_performance_map(cache_dir=tmp_path)
    assert not first.data.from_cache
    files = list(tmp_path.glob("*.json"))
    assert [f.stem for f in files] == [first.data.cache_key]
    assert "from_cache" not in json.loads(files[0].read_text(encoding="utf-8"))
    second = doublet_performance_map(cache_dir=tmp_path)
    assert second.data.from_cache
    assert second.data.points == first.data.points
    assert second.data.doublet == first.data.doublet
    assert len(second.summary) <= SUMMARY_MAX_CHARS and "cached" in second.summary


def test_cache_key_follows_inputs(tmp_path):
    """A different design or grid gets its own cache file."""
    base = doublet_performance_map(cache_dir=tmp_path).data.cache_key
    other_design = doublet_performance_map(
        PerformanceMapRequest(design=DoubletDesign(producer_wellhead_pressure_barg=10.0)),
        cache_dir=tmp_path,
    ).data
    other_grid = doublet_performance_map(
        PerformanceMapRequest(step_m3h=25.0), cache_dir=tmp_path
    ).data
    assert len({base, other_design.cache_key, other_grid.cache_key}) == 3
    assert not other_design.from_cache and not other_grid.from_cache
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_corrupt_cache_is_rebuilt(tmp_path):
    """An unreadable cache file is recomputed and overwritten."""
    key = doublet_performance_map(cache_dir=tmp_path).data.cache_key
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
    rebuilt = doublet_performance_map(cache_dir=tmp_path)
    assert not rebuilt.data.from_cache
    assert doublet_performance_map(cache_dir=tmp_path).data.from_cache


def test_cache_can_be_disabled(tmp_path):
    """``use_cache=False`` neither reads nor writes."""
    result = doublet_performance_map(cache_dir=tmp_path, use_cache=False)
    assert not result.data.from_cache
    assert not list(tmp_path.iterdir())


def test_default_cache_lives_in_ignored_runs_dir():
    """Maps are cached under runs/ (git-ignored except runs/final/)."""
    assert DEFAULT_CACHE_DIR.is_relative_to(REPO_ROOT / "runs")
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "runs/*" in gitignore


def test_map_interpolation(perf_map):
    """Linear interpolation between feasible grid points; outside the range raises."""
    p150, p160 = perf_map.points[10], perf_map.points[11]
    assert perf_map.interpolate("esp_dp_bar", 150.0) == pytest.approx(p150.esp_dp_bar)
    assert perf_map.interpolate("esp_dp_bar", 155.0) == pytest.approx(
        0.5 * (p150.esp_dp_bar + p160.esp_dp_bar)
    )
    with pytest.raises(ValueError, match="outside"):
        perf_map.interpolate("esp_dp_bar", 310.0)
    with pytest.raises(ValueError, match="numeric"):
        perf_map.interpolate("esp_mode", 150.0)


def test_interpolation_never_bridges_infeasible_points(basis):
    """A flow next to an infeasible grid point raises instead of bridging the gap."""
    perf = build_performance_map(
        resolve_doublet(DoubletDesign(depth_m=400.0), basis), map_flows(50.0, 300.0, 50.0)
    )
    first_bad = next(i for i, p in enumerate(perf.points) if not p.feasible)
    assert first_bad > 0 and perf.points[first_bad - 1].feasible
    low, high = perf.points[first_bad - 1].flow_m3h, perf.points[first_bad].flow_m3h
    assert perf.interpolate("esp_dp_bar", low) == perf.points[first_bad - 1].esp_dp_bar
    with pytest.raises(ValueError, match="infeasible"):
        perf.interpolate("esp_dp_bar", 0.5 * (low + high))
    with pytest.raises(ValueError, match="infeasible"):
        perf.interpolate("esp_dp_bar", high)


def test_cache_key_covers_source_code(monkeypatch, basis, doublet):
    """A change in the GEMINI/WP-05 source changes the key, so stale maps are not reused."""
    flows = map_flows(basis.flow_min_m3h, basis.flow_max_m3h, 10.0)
    fingerprint = doublet_module.code_fingerprint()
    assert len(fingerprint) == 64
    key = doublet_module.map_cache_key(doublet, flows)
    monkeypatch.setattr(doublet_module, "code_fingerprint", lambda: "edited source")
    assert doublet_module.map_cache_key(doublet, flows) != key
