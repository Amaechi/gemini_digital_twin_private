# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/tools/verify.py @ d69507a. Modified by GeoAgent team,
# 2026-11-01: the telemetry recovery check is replaced by the 4-layer design verification of spec
# §10.1 (syntactic, semantic, physical, performance & design, plus the exergy KPI); the structure of
# ``DeterministicVerifier.evaluate`` / ``VerifyResult`` (class-level thresholds, pass/fail, deltas,
# compact summary) is kept; no simulator client, no waiting.
"""DesignVerifier: deterministic verification of one candidate design (spec §10.1, VR-01).

Only this verifier can mark a design ACCEPTED (CLAUDE.md golden rule 3, AG-11). It computes no
design: it checks the evidence produced by the deterministic tools (GEMINI project JSON, daily
results, component operating points, subsurface and scaling screens) against the hard limits in
``config/limits.yaml``, the reference cards and the spec §10.1 table.

Every check returns pass/fail, the value, the limit and a **margin** in the check's unit
(``margin >= 0`` means satisfied; a floating-point tolerance of ``NUMERIC_TOL`` relative is
allowed, so a value exactly at its limit passes; the margins are the report's deltas, VR-01). A
check whose evidence is missing is ``not_evaluated``; a check that does not apply to the candidate
is ``not_applicable``. Verdict:

- any failed check → ``REJECTED`` (violations listed);
- otherwise any required check not evaluated, or evidence from a stub tool → ``UNVERIFIED``;
- otherwise → ``ACCEPTED``.

WP-14 closed the evidence gaps listed at hand-over (each check has a failing-case test): the TL-05
design point (PH-25 feasible, PH-26 flow solve error ≤ 1 %, PH-27 ESP-intake margin ≥ 0), the
cooling and heat-pump model validity warnings (PH-28), the daily ``DayResult.flags`` (PH-29), the
atmospheric letdown of degassed brine (PH-30, SUB-NPSH bound), TL-08 ``within_limit`` (PD-11) and
TL-07 ``breakthrough_after_horizon`` (PD-12).

The exergy layer is reported, never pass/fail (spec §10.1). Sources: spec §10.1; RC-03 (plate
HEX); RC-12 (scaling, materials); RC-01 items 6, 8, 9 (pumps, NPSH, CO₂ release); RC-04 (heat
pump defaults); DB-08/DB-33 (brine ρ, c_p); spec ENG-06 (spacing, interference, t_b); spec SIM-04
(fast vs full). Pressures named ``_barg`` are gauge.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

from gemini_agent.runner.toolbox import CandidateArgs, ToolContext, ToolSpec
from gemini_agent.schemas import config as cfg
from gemini_agent.schemas.candidate import EQUIPMENT_SIZE_SUFFIXES, Candidate
from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    SUMMARY_MAX_CHARS,
    GasHandling,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.decision import DesignVerdict
from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet
from gemini_agent.tools.design_basis import brine_heat_mw, temperature_at_depth_degc

VERIFIER_NAME = "DesignVerifier"
"""Value of ``verifier`` in every report (the runner records it with the verdict)."""

# --------------------------------------------------------------------------- thresholds
ENERGY_BALANCE_TOL_FRAC = 0.01
"""Daily energy balance error < 1 % (spec §10.1 physical layer)."""
HEX_MIN_APPROACH_K = 1.1
"""Smallest achievable plate-HEX approach (RC-03; Guidebook p.217–223)."""
HEX_TYPICAL_APPROACH_K = (2.0, 5.6)
"""Typical approach 2–5.6 K (spec §10.1; RC-03: economical 2.8–5.6 K, GNS 2–3 K) → warning only."""
PA_PER_PSI = 6894.757
"""1 psi in Pa (unit conversion)."""
HEX_RATING: dict[str, tuple[float, float]] = {
    "gasketed": (150.0, 20.0),
    "welded": ((400.0 - 32.0) / 1.8, 300.0 * PA_PER_PSI / 1.0e5),
}
"""Plate-HEX rating (max °C, max bar(g)): gasketed ~150 °C / ~20 bar (RC-03; GNS p.40); welded
400 °F / 300 psig = 204.4 °C / 20.68 bar(g) (RC-03; Guidebook p.218)."""
HEX_DP_PER_NTU_PA = (1.0 * PA_PER_PSI, 6.0 * PA_PER_PSI)
"""Δp per NTU 1–6 psi (≈ 6.9–41.4 kPa); typical 3.5 psi ≈ 24 kPa (RC-03 eq. 4; Guidebook p.221)."""
HEX_NTU_PER_PASS = (0.6, 4.0)
"""NTU per pass 0.6–4.0 (RC-03; Guidebook p.221)."""
HEX_CORRECTION_FACTOR_MIN = 0.85
"""LMTD correction factor F_c 0.85–1.0 (RC-03 eq. 2; GNS p.57–58): total NTU of a counter-flow
unit lies between the ideal NTU and NTU/0.85."""
HEX_MAX_UNIT_FLOW_M3H = 1360.0
"""Largest plate-HEX unit flow (RC-03 limits)."""
HEX_MAX_UNIT_AREA_M2 = 1208.0
"""Largest plate-HEX unit area (RC-03 limits)."""
ABSORPTION_PUMPING_MAX_FRAC = 0.5
"""Absorption pumping ≤ 50 % of the equivalent electric chiller's power (spec §10.1; Guidebook
p.270)."""
NPSH_MARGIN_M = 15.0
"""NPSH_A ≥ NPSH_R + 15 m (RC-01 item 8; DiPippo Problem 4.5 heuristic)."""
ESP_RELEASE_PRESSURE_ID = "VER-ESP-PB"
"""Register entry: ESP-intake / CO₂-release setpoint 1.2 · p_b = 60 bar(a) (RC-01 item 9, Henry's
law); used when no computed bubble point is passed as evidence."""
ESP_MARGIN_ID = "VER-ESP-MARGIN"
"""Register entry: factor 1.2 [A] on a computed bubble point, p ≥ 1.2 · p_b in bar(a) (RC-01
item 9). Applied by the verifier whenever a bare p_b is passed as evidence."""
P_ATM_BAR = 1.01325
"""Standard atmosphere (bar) to convert between bar(a) and the evidence's bar(g)."""
HP_CARNOT_FRACTION_ID = "VER-HP-ETA"
"""Register entry: COP ≤ 0.6 × Carnot without a datasheet (RC-04 [A])."""
HP_MAX_LIFT_ID = "VER-HP-LIFT"
"""Register entry: lift ≤ 60 K without a datasheet (RC-04 [A])."""
BRINE_CP_ID = "DB-33"
"""Register entry: brine specific heat 4100 J/(kg K) (spec §2.4 [A])."""
WELL_SPACING_MIN_M = 1200.0
"""Well spacing ≥ 1.2 km (spec ENG-06, §10.1)."""
INTERFERENCE_MAX_BAR = 1.0
"""Inter-doublet pressure interference ≤ 1 bar (spec ENG-06 [L Dogger]; TL-08)."""
BREAKTHROUGH_MIN_YR = 30.0
"""Thermal breakthrough time ≥ 30 yr (spec ENG-06, §10.1)."""
FAST_FULL_WARN_FRAC = 0.02
"""Fast vs full daily discrepancy > 2 % → warning (spec SIM-04)."""
FAST_FULL_FAIL_FRAC = 0.05
"""Fast vs full daily discrepancy > 5 % → fail (spec SIM-04)."""
SPLIT_FRACTION_TOL = 1e-6
"""Splitter fractions must sum to 1 within this tolerance (GEMINI ``Splitter``)."""
NUMERIC_TOL = 1e-6
"""Relative floating-point tolerance of every bound check; not an engineering coefficient."""
FLOW_SOLVE_TOL_FRAC = ENERGY_BALANCE_TOL_FRAC
"""TL-05 ESP-frequency solve: |Q_GEMINI − Q_target| / Q_target ≤ 1 % (same numerical tolerance as
the energy balance; WP-05 solves to ~1e-12)."""
VAPOUR_BOUND_ID = "SUB-NPSH"
"""Register entry: water vapour pressure bounded by 1.01325 bar(a) up to 100 °C (RC-01 item 8)."""
SUCTION_PRESSURE_ID = "SUB-PSUC"
"""Register entry: injection-pump suction (degasser letdown, CFG-SURFACE) pressure, bar(g)."""
MODEL_VALIDITY_FAIL = re.compile(
    r"(?i)freeze protection not modelled|at or below the zero-capacity|OUTSIDE RC-04 limits"
)
"""Model warnings that invalidate the operating point (PH-28): a wet tower below 0 °C water
(RC-07, no freeze protection), an absorption drive at/below the zero-capacity temperature (RC-05)
or a heat pump outside its RC-04 limits. Every other model warning (extrapolated correlation,
capped COP, multi-pass note, missing datasheet → register default) is reported, not failed."""
DAY_FLAG_FAIL = re.compile(r"(?i)OUTSIDE RC-04 limits|full mode invalid")
"""``DayResult.flags`` that fail PH-29 (WP-06/07: heat pump outside RC-04 limits; a full-mode day
GEMINI could not run). Other flags (e.g. HTHP datasheet MISSING → RC-04 defaults) are warnings."""
PUMP_POWER_TOL_FRAC = ENERGY_BALANCE_TOL_FRAC
"""Numerical consistency tolerance of the reported pump power vs Q·Δp/η (same 1 % as the energy
balance; not a card value)."""

PROJECT_KEYS = ("components", "boundary_inputs", "connections")
"""Top-level keys of a GEMINI project JSON (``_``-prefixed keys are comments)."""

_COPPER_FREE = re.compile(r"(?i)\b(?:copper|cu)[\s-]*free\b|\bno[nt]?[\s-]+(?:copper|cu)\b")
_COPPER_SYMBOL = re.compile(r"\bCu(?![a-z])")  # Cu, CuNi10Fe1Mn, CuZn37, Cu-Ni (case-sensitive)
_COPPER_WORDS = re.compile(
    r"(?i)\b(?:copper|brass|bronze|cupro\w*|monel|solder\w*|gunmetal|admiralty|muntz)\b"
    r"|\bUNS\s*C\d{5}\b"
)
_TITANIUM = re.compile(r"(?i)\b(?:titanium|ti)\b")
_NOT_TITANIUM_PLATE = re.compile(
    r"(?i)\bno[nt]?[\s-]+ti(?:tanium)?\b|stainless|\baisi\b|\bss\b|\bsteel\b|duplex"
    r"|stabili[sz]ed|\b(?:304|316|317|321|347|904)\w*"
)


def is_copper_alloy(material: str) -> bool:
    """Return True for copper, its alloys (CuNi, CuZn, brass, bronze, Monel, UNS C) and solder.

    RC-12: copper alloys and solder are unacceptable in brine contact (Guidebook p.152, 211).
    """
    if _COPPER_FREE.search(material):
        return False
    return bool(_COPPER_SYMBOL.search(material) or _COPPER_WORDS.search(material))


def is_titanium(material: str) -> bool:
    """Return True for titanium plates (Ti grades); False for Ti-stabilised steels or 'not Ti'."""
    return bool(_TITANIUM.search(material)) and not _NOT_TITANIUM_PLATE.search(material)


class Layer(StrEnum):
    """Verification layer (spec §10.1)."""

    SYNTACTIC = "syntactic"
    SEMANTIC = "semantic"
    PHYSICAL = "physical"
    PERFORMANCE = "performance_design"
    EXERGY = "exergy"


class CheckStatus(StrEnum):
    """Outcome of one check."""

    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"
    NOT_APPLICABLE = "not_applicable"
    INFO = "info"


class Mitigation(StrEnum):
    """Design mitigations the scaling/corrosion screen can rely on (RC-12 TL-22 behaviour)."""

    PRESSURISED_LOOP = "pressurised_loop"
    CALCITE_INHIBITOR = "calcite_inhibitor"
    TITANIUM_PLATES = "titanium_plates"
    OXYGEN_EXCLUSION = "oxygen_exclusion"
    NO_COPPER = "no_copper"


# --------------------------------------------------------------------------- evidence
class HexPoint(StrictModel):
    """Plate-HEX operating point (RC-03). Hot side = brine when ``brine_side``."""

    name: str
    hot_in_degc: float
    hot_out_degc: float
    cold_in_degc: float
    cold_out_degc: float
    construction: Literal["gasketed", "welded"] = "gasketed"
    passes: int = Field(default=1, ge=1, description="Number of passes in series.")
    design_pressure_barg: float | None = Field(
        default=None, description="Highest operating pressure on either side, bar(g)."
    )
    min_brine_pressure_barg: float | None = Field(
        default=None, description="Lowest brine-side pressure, bar(g) (CO₂ release, RC-12)."
    )
    ntu: float | None = Field(default=None, gt=0, description="NTU per pass.")
    pressure_drop_pa: float | None = Field(default=None, ge=0, description="Per pass.")
    flow_m3h: float | None = Field(default=None, ge=0, description="Larger side, per unit.")
    area_m2: float | None = Field(default=None, gt=0, description="Per unit.")
    plate_material: str | None = None
    brine_side: bool = True


class HeatPumpPoint(StrictModel):
    """Heat-pump operating point (RC-04).

    COP is bounded by Carnot at the refrigerant condensing/evaporating temperatures (RC-04 eq. 1);
    lift is sink outlet − source outlet. ``max_lift_k`` / ``max_carnot_fraction`` come from a
    cited datasheet; None → the RC-04 defaults (register VER-HP-LIFT, VER-HP-ETA).
    """

    name: str
    cop: float = Field(ge=1, description="Heating COP (≥ 1, RC-04 eq. 3).")
    condensing_degc: float
    evaporating_degc: float
    sink_out_degc: float
    source_out_degc: float
    max_lift_k: float | None = Field(default=None, gt=0)
    max_carnot_fraction: float | None = Field(default=None, gt=0, le=1)


class AbsorptionPoint(StrictModel):
    """Absorption-chiller operating point with the GM-04 model's bounds at that point."""

    name: str
    cop: float = Field(gt=0)
    cop_min: float | None = Field(default=None, ge=0)
    cop_max: float | None = Field(default=None, gt=0)
    cooling_mw: float = Field(ge=0)
    rated_cooling_mw: float | None = Field(default=None, gt=0)
    pumping_power_mw: float | None = Field(default=None, ge=0)
    electric_chiller_power_mw: float | None = Field(
        default=None,
        gt=0,
        description="Electric chiller power for the same duty (Guidebook p.270).",
    )


class PumpPoint(StrictModel):
    """Pump operating point; shaft power must equal Q·Δp/η (RC-01 item 6, DB-13/DB-40)."""

    name: str
    flow_m3h: float = Field(ge=0)
    pressure_rise_pa: float = Field(ge=0)
    shaft_power_w: float = Field(ge=0)


class EspPoint(StrictModel):
    """ESP intake conditions (RC-01 items 8–9)."""

    name: str
    intake_pressure_barg: float
    release_pressure_barg: float | None = Field(
        default=None,
        ge=-P_ATM_BAR,
        description=(
            "Computed bubble point p_b here, bar(g): bare bubble point; the verifier applies the "
            "margin (VER-ESP-MARGIN). None → evidence-level p_b or the VER-ESP-PB setpoint."
        ),
    )
    npsh_available_m: float | None = None
    npsh_required_m: float | None = Field(default=None, ge=0)


class SubsurfaceEvidence(StrictModel):
    """Subsurface screening results (TL-06, TL-07, TL-08)."""

    well_spacing_m: float | None = Field(default=None, gt=0)
    interference_bar: float | None = Field(default=None, ge=0)
    thermal_breakthrough_yr: float | None = Field(default=None, gt=0)
    interference_within_limit: bool | None = Field(
        default=None, description="TL-08 ``within_limit`` at the design separation (PD-11)."
    )
    breakthrough_after_horizon: bool | None = Field(
        default=None, description="TL-07 ``breakthrough_after_horizon`` (ENG-06, PD-12)."
    )
    horizon_yr: float | None = Field(default=None, gt=0, description="TL-07 analysis horizon.")


class DoubletPointEvidence(StrictModel):
    """TL-05 design operating point of one doublet (WP-05 ``OperatingPoint``; RC-01)."""

    name: str
    flow_m3h: float = Field(ge=0)
    feasible: bool
    reasons: list[str] = Field(default_factory=list, description="Why TL-05 found it infeasible.")
    flow_error_frac: float | None = Field(default=None, description="GEMINI flow vs target.")
    intake_margin_bar: float | None = Field(
        default=None, description="ESP intake − 1.2 · p_b setpoint (≥ 0 required)."
    )


class ScalingEvidence(StrictModel):
    """Result of the scaling/corrosion screen (TL-22, RC-12)."""

    status: Literal["pass", "fail", "insufficient data"]
    mitigations: list[Mitigation] = Field(default_factory=list)
    detail: str = ""


class VerificationEvidence(StrictModel):
    """Everything the verifier may look at for one candidate.

    Every part but the candidate is optional; a missing part makes its checks ``not_evaluated``.
    """

    candidate: Candidate
    project: dict[str, JsonValue] | str | None = Field(
        default=None, description="GEMINI project JSON (dict, or raw text to detect duplicates)."
    )
    annual: AnnualResult | None = Field(default=None, description="Full-mode daily results.")
    fast_annual: AnnualResult | None = Field(default=None, description="Fast-mode daily results.")
    kpis: KPISet | None = None
    hex_points: list[HexPoint] = Field(default_factory=list)
    heat_pumps: list[HeatPumpPoint] = Field(default_factory=list)
    absorption: list[AbsorptionPoint] = Field(default_factory=list)
    pumps: list[PumpPoint] = Field(default_factory=list)
    esps: list[EspPoint] = Field(default_factory=list)
    subsurface: SubsurfaceEvidence | None = None
    scaling: ScalingEvidence | None = None
    co2_release_pressure_barg: float | None = Field(
        default=None,
        ge=-P_ATM_BAR,
        description=(
            "Computed bubble point p_b, bar(g) (WP-05): bare bubble point; the verifier applies "
            "the margin (VER-ESP-MARGIN). None → the VER-ESP-PB setpoint (margin included)."
        ),
    )
    brine_contact_materials: dict[str, str] = Field(
        default_factory=dict, description="Component → material of brine-wetted parts."
    )
    doublet_points: list[DoubletPointEvidence] = Field(
        default_factory=list, description="TL-05 design points (PH-25…PH-27)."
    )
    model_warnings: list[str] = Field(
        default_factory=list,
        description="Validity warnings of the cooling / heat-pump models (TL-11/12, PH-28).",
    )
    letdown_pressure_barg: float | None = Field(
        default=None,
        description="Degasser letdown pressure, bar(g) (CFG-SURFACE); None → SUB-PSUC (PH-30).",
    )
    saline_brine: bool = Field(
        default=True, description="DB-08 brine (1030 kg/m³) is saline → titanium plates (RC-12)."
    )
    stub_evidence: bool = Field(
        default=False, description="Any part came from a stub tool → never ACCEPTED."
    )


# --------------------------------------------------------------------------- report
class CheckResult(StrictModel):
    """One check: pass/fail with value, limit and margin (``margin >= 0`` = satisfied)."""

    check_id: str
    layer: Layer
    name: str
    status: CheckStatus
    value: float | None = None
    limit: float | None = None
    margin: float | None = None
    unit: str = ""
    detail: str = ""
    warnings: list[str] = Field(default_factory=list)
    source: str = ""


class LayerResult(StrictModel):
    """Counts and status of one layer."""

    layer: Layer
    status: Literal["pass", "fail", "incomplete", "info"]
    passed: int = 0
    failed: int = 0
    not_evaluated: int = 0


class VerificationReport(StrictModel):
    """``verify_design`` output.

    ``candidate_id``/``verdict``/``violations``/``verifier`` are the runner's reducer contract
    (``runner/toolbox.py``).
    """

    candidate_id: str
    verdict: DesignVerdict
    violations: list[str] = Field(default_factory=list)
    verifier: str = VERIFIER_NAME
    layers: list[LayerResult]
    checks: list[CheckResult]
    not_evaluated: list[str] = Field(default_factory=list)
    margins: dict[str, float] = Field(
        default_factory=dict, description="Check id → margin in the check's unit (VR-01 deltas)."
    )
    assumption_ids: list[str] = Field(default_factory=list)
    stub_evidence: bool = False
    summary: str = Field(max_length=SUMMARY_MAX_CHARS)

    @model_validator(mode="after")
    def _check_verdict(self) -> VerificationReport:
        failed = any(c.status == CheckStatus.FAIL for c in self.checks)
        if (self.verdict == DesignVerdict.REJECTED) != failed:
            raise ValueError("verdict REJECTED if and only if a check failed")
        if self.verdict == DesignVerdict.ACCEPTED and (self.not_evaluated or self.stub_evidence):
            raise ValueError("ACCEPTED needs every required check evaluated and no stub evidence")
        return self

    def check(self, check_id: str) -> CheckResult:
        """Return one check by id (KeyError if absent)."""
        for item in self.checks:
            if item.check_id == check_id:
                return item
        raise KeyError(check_id)


# --------------------------------------------------------------------------- helpers
def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6) + 0.0  # no -0.0


@dataclass
class _Bound:
    """Worst item of a bound check: the smallest margin and its description."""

    margin: float = math.inf
    value: float | None = None
    detail: str = ""
    count: int = 0

    def add(self, margin: float, value: float | None, detail: str) -> None:
        self.count += 1
        if margin < self.margin:
            self.margin, self.value, self.detail = margin, value, detail


@dataclass
class _Checks:
    """Collects ``CheckResult``s for one layer."""

    layer: Layer
    items: list[CheckResult] = field(default_factory=list)

    def add(
        self,
        check_id: str,
        name: str,
        status: CheckStatus,
        *,
        detail: str = "",
        value: float | None = None,
        limit: float | None = None,
        margin: float | None = None,
        unit: str = "",
        source: str = "",
        warnings: Iterable[str] = (),
    ) -> None:
        self.items.append(
            CheckResult(
                check_id=check_id,
                layer=self.layer,
                name=name,
                status=status,
                value=_round(value),
                limit=_round(limit),
                margin=_round(margin),
                unit=unit,
                detail=detail[:400],
                warnings=list(warnings),
                source=source,
            )
        )

    def missing(self, check_id: str, name: str, what: str, source: str = "") -> None:
        self.add(
            check_id, name, CheckStatus.NOT_EVALUATED, detail=f"no evidence: {what}", source=source
        )

    def not_applicable(self, check_id: str, name: str, why: str, source: str = "") -> None:
        self.add(check_id, name, CheckStatus.NOT_APPLICABLE, detail=why, source=source)

    def bound(
        self,
        check_id: str,
        name: str,
        worst: _Bound,
        *,
        limit: float | None,
        unit: str,
        source: str,
        ok_detail: str = "",
        warnings: Iterable[str] = (),
        tolerance: float | None = None,
    ) -> None:
        """Pass if the worst margin ≥ −tolerance (default ``NUMERIC_TOL`` relative)."""
        if worst.count == 0:
            self.missing(check_id, name, "no evaluable item (see related checks)", source)
            return
        if tolerance is None:
            scale = max(1.0, abs(limit or 0.0), abs(worst.value or 0.0))
            tolerance = NUMERIC_TOL * scale
        status = CheckStatus.PASS if worst.margin >= -tolerance else CheckStatus.FAIL
        detail = worst.detail if status == CheckStatus.FAIL else (ok_detail or worst.detail)
        self.add(
            check_id,
            name,
            status,
            detail=detail,
            value=worst.value,
            limit=limit,
            margin=worst.margin,
            unit=unit,
            source=source,
            warnings=warnings,
        )


def _parse_project(raw: dict[str, Any] | str) -> tuple[dict[str, Any], list[str]]:
    """Parse the project; return (dict, duplicate component names). Raises ValueError."""
    if isinstance(raw, dict):
        return raw, []
    duplicates: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        return dict(pairs)

    try:
        project = json.loads(raw, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(project, dict):
        raise ValueError("project JSON must be an object")  # noqa: TRY004 - content error
    return project, sorted(set(duplicates))


def _component_types(project: dict[str, Any] | None) -> dict[str, str]:
    if not project or not isinstance(project.get("components"), dict):
        return {}
    return {
        name: str(spec.get("type", "")) if isinstance(spec, dict) else ""
        for name, spec in project["components"].items()
    }


def _has_equipment(candidate: Candidate, *markers: str) -> bool:
    return any(marker in key for key in candidate.equipment_sizes for marker in markers)


def _active_doublets(day: DayResult) -> Iterable[tuple[int, float]]:
    for index, flow in enumerate(day.doublet_flow_m3h):
        if flow > 0:
            yield index, flow


def _to_barg(value: float, unit: str | None) -> float:
    """Convert a register pressure in ``bar(a)`` or ``bar(g)`` to bar(g); other units raise."""
    if unit == "bar(a)":
        return value - P_ATM_BAR
    if unit == "bar(g)":
        return value
    raise ValueError(f"{ESP_RELEASE_PRESSURE_ID}: unit must be bar(a) or bar(g), got {unit!r}")


def _lmtd(dt_a: float, dt_b: float) -> float:
    if abs(dt_a - dt_b) <= 1e-9 * max(dt_a, dt_b):
        return dt_a
    return (dt_a - dt_b) / math.log(dt_a / dt_b)


def _note(ids: list[str], assumption_id: str) -> None:
    if assumption_id not in ids:
        ids.append(assumption_id)


# --------------------------------------------------------------------------- verifier
class DesignVerifier:
    """Deterministic 4-layer verifier (spec §10.1).

    Thresholds: ``config/limits.yaml`` (hard limits), the module constants (cards/spec, cited)
    and the assumptions register (``VER-ESP-PB``, ``VER-ESP-MARGIN``, ``VER-HP-ETA``,
    ``VER-HP-LIFT``, ``DB-33``).
    """

    def __init__(
        self,
        limits: cfg.HardLimits,
        design_basis: cfg.DesignBasis,
        register: cfg.AssumptionsRegister,
    ) -> None:
        """Bind the hard limits, the design basis and the assumptions register."""
        self.limits = limits
        self.design_basis = design_basis
        release = register.get(ESP_RELEASE_PRESSURE_ID)
        release_value = float(release.value)  # type: ignore[arg-type]
        self.release_pressure_default_barg = _to_barg(release_value, release.unit)
        self.release_default_note = (
            f"{ESP_RELEASE_PRESSURE_ID} {_fmt(release_value)} {release.unit}"
        )
        margin = register.get(ESP_MARGIN_ID)
        self.release_margin = float(margin.value)  # type: ignore[arg-type]
        if margin.unit != "-" or self.release_margin < 1.0:
            raise ValueError(
                f"{ESP_MARGIN_ID} must be a dimensionless factor >= 1, got "
                f"{self.release_margin} {margin.unit!r}"
            )
        self.hp_carnot_fraction_default = float(register.get(HP_CARNOT_FRACTION_ID).value)
        self.hp_max_lift_default_k = float(register.get(HP_MAX_LIFT_ID).value)
        self.brine_cp_j_per_kg_k = float(register.get(BRINE_CP_ID).value)
        vapour: dict[str, Any] = register.get(VAPOUR_BOUND_ID).value  # type: ignore[assignment]
        self.vapour_bound_bara = float(vapour["vapour_pressure_bound_bara"])
        self.vapour_bound_degc = float(vapour["vapour_pressure_bound_valid_to_degc"])
        self.suction_default_barg = float(register.get(SUCTION_PRESSURE_ID).value)  # type: ignore

    @classmethod
    def from_config(cls, config_dir: Path | str | None = None) -> DesignVerifier:
        """Build from ``config/limits.yaml``, ``design_basis.yaml`` and ``assumptions.yaml``."""
        return cls(
            cfg.load_config("limits", config_dir),
            cfg.load_config("design_basis", config_dir),
            cfg.load_config("assumptions", config_dir),
        )

    # ------------------------------------------------------------------ entry point
    def verify(self, evidence: VerificationEvidence) -> VerificationReport:
        """Run every layer and return the report (never raises on bad evidence)."""
        project, duplicates, load_error = None, [], ""
        if evidence.project is not None:
            try:
                project, duplicates = _parse_project(evidence.project)  # type: ignore[arg-type]
            except ValueError as exc:
                load_error = str(exc)
        types = _component_types(project)
        assumption_ids: list[str] = []
        checks = [
            *self._syntactic(evidence, project, load_error),
            *self._semantic(evidence, project, duplicates, load_error),
            *self._physical(evidence, types, assumption_ids),
            *self._performance(evidence, assumption_ids),
            *self._exergy(evidence),
        ]
        return self._report(evidence.candidate.id, checks, evidence.stub_evidence, assumption_ids)

    def required_pressure_barg(self, p_b_barg: float) -> float:
        """RC-01 item 9 setpoint for a bare bubble point: margin × p_b in bar(a), as bar(g)."""
        return self.release_margin * (p_b_barg + P_ATM_BAR) - P_ATM_BAR

    def _setpoint(
        self, p_b_barg: float | None, ev: VerificationEvidence, ids: list[str]
    ) -> tuple[float, str]:
        """Return the required pressure (bar(g)) and its basis.

        A bare p_b (argument, else ``ev.co2_release_pressure_barg``) gets the register margin;
        without one the VER-ESP-PB setpoint applies (it already includes the margin).
        """
        if p_b_barg is None:
            p_b_barg = ev.co2_release_pressure_barg
        if p_b_barg is not None:
            _note(ids, ESP_MARGIN_ID)
            basis = (
                f"{_fmt(self.release_margin)} × p_b(a), p_b {_fmt(p_b_barg)} bar(g) = "
                f"{_fmt(p_b_barg + P_ATM_BAR)} bar(a), {ESP_MARGIN_ID}"
            )
            return self.required_pressure_barg(p_b_barg), basis
        _note(ids, ESP_RELEASE_PRESSURE_ID)
        return self.release_pressure_default_barg, self.release_default_note

    # ------------------------------------------------------------------ layer 1: syntactic
    def _syntactic(
        self, ev: VerificationEvidence, project: dict[str, Any] | None, load_error: str
    ) -> list[CheckResult]:
        out = _Checks(Layer.SYNTACTIC)
        name = "GEMINI project JSON loads"
        if ev.project is None:
            out.missing("SY-01", name, "GEMINI project JSON", "spec §10.1")
        elif load_error:
            out.add("SY-01", name, CheckStatus.FAIL, detail=load_error, source="spec §10.1")
        else:
            assert project is not None
            unknown = [k for k in project if k not in PROJECT_KEYS and not k.startswith("_")]
            if not isinstance(project.get("components"), dict) or not project["components"]:
                problem = "'components' must be a non-empty object"
            elif not isinstance(project.get("connections", []), list):
                problem = "'connections' must be a list"
            elif not isinstance(project.get("boundary_inputs", []), list):
                problem = "'boundary_inputs' must be a list"
            elif unknown:
                problem = f"unknown top-level keys {unknown} (expected {list(PROJECT_KEYS)})"
            else:
                problem = _build_error(project)
            out.add(
                "SY-01",
                name,
                CheckStatus.FAIL if problem else CheckStatus.PASS,
                detail=problem or f"{len(project['components'])} components built",
                source="spec §10.1; gemini_simulator.project_loader",
            )

        parts = (("annual", ev.annual), ("fast_annual", ev.fast_annual), ("kpis", ev.kpis))
        mismatched = [
            label
            for label, part in parts
            if part is not None and part.candidate_id != ev.candidate.id
        ]
        out.add(
            "SY-02",
            "schemas valid and consistent",
            CheckStatus.FAIL if mismatched else CheckStatus.PASS,
            detail=(
                f"candidate_id of {mismatched} differs from '{ev.candidate.id}'"
                if mismatched
                else "candidate, results and KPIs validate"
            ),
            source="spec §10.1; gemini_agent.schemas",
        )

        bad_keys = [
            key for key in ev.candidate.equipment_sizes if not key.endswith(EQUIPMENT_SIZE_SUFFIXES)
        ]
        for part in (ev.annual, ev.fast_annual):
            for day in part.days if part else []:
                bad_keys += [k for k in day.margins if not k.endswith(EQUIPMENT_SIZE_SUFFIXES)]
        if ev.kpis is not None:
            bad_keys += [
                k for k in ev.kpis.min_constraint_margins if not k.endswith(EQUIPMENT_SIZE_SUFFIXES)
            ]
        bad_keys = sorted(set(bad_keys))
        out.add(
            "SY-03",
            "unit suffixes present",
            CheckStatus.FAIL if bad_keys else CheckStatus.PASS,
            detail=f"keys without unit suffix: {bad_keys}" if bad_keys else "all keys suffixed",
            source="CLAUDE.md rule 5; spec §10.1",
        )
        return out.items

    # ------------------------------------------------------------------ layer 2: semantic
    def _semantic(
        self,
        ev: VerificationEvidence,
        project: dict[str, Any] | None,
        duplicates: list[str],
        load_error: str,
    ) -> list[CheckResult]:
        out = _Checks(Layer.SEMANTIC)
        names = {
            "SE-01": "unique component names",
            "SE-02": "connections reference existing components and keys",
            "SE-03": "splitter fractions sum to 1",
            "SE-04": "each producer paired with an injector",
        }
        if project is None or load_error:
            why = "project did not load (SY-01)" if load_error else "GEMINI project JSON"
            for check_id, name in names.items():
                out.missing(check_id, name, why, "spec §10.1")
            return out.items
        components: dict[str, Any] = project.get("components") or {}
        connections: list[Any] = project.get("connections") or []
        boundary: list[Any] = project.get("boundary_inputs") or []

        out.add(
            "SE-01",
            names["SE-01"],
            CheckStatus.FAIL if duplicates else CheckStatus.PASS,
            detail=f"duplicate names {duplicates}" if duplicates else f"{len(components)} names",
            source="spec §10.1",
        )

        problems = _reference_problems(components, connections, boundary)
        if not problems:
            problems = _solve_problems(project)
        out.add(
            "SE-02",
            names["SE-02"],
            CheckStatus.FAIL if problems else CheckStatus.PASS,
            detail="; ".join(problems[:3]) or f"{len(connections)} connections resolve",
            source="spec §10.1; gemini_simulator.solver",
        )

        worst = _Bound()
        for name, spec in components.items():
            if not isinstance(spec, dict) or spec.get("type") != "splitter":
                continue
            fractions = (spec.get("parameters") or {}).get("split_fractions", [0.5, 0.5])
            try:
                values = [float(v) for v in fractions]
            except (TypeError, ValueError):
                worst.add(-1.0, None, f"{name}: split_fractions {fractions!r} not numeric")
                continue
            error = abs(sum(values) - 1.0)
            negative = [v for v in values if not 0.0 <= v <= 1.0]
            margin = -error if not negative else -max(abs(v) for v in negative)
            worst.add(margin, sum(values), f"{name}: fractions {values} sum to {_fmt(sum(values))}")
        if worst.count == 0:
            out.not_applicable("SE-03", names["SE-03"], "no splitter", "spec §10.1")
        else:
            out.bound(
                "SE-03",
                names["SE-03"],
                worst,
                limit=1.0,
                unit="-",
                source="spec §10.1",
                tolerance=SPLIT_FRACTION_TOL,
            )

        out.items.append(self._pairing(ev.candidate, components, connections))
        return out.items

    @staticmethod
    def _pairing(
        candidate: Candidate, components: dict[str, Any], connections: list[Any]
    ) -> CheckResult:
        """SE-04. The project may model one representative doublet of an N-doublet candidate."""
        out = _Checks(Layer.SEMANTIC)
        types = {n: (s.get("type") if isinstance(s, dict) else None) for n, s in components.items()}
        producers = sorted(n for n, t in types.items() if t == "producer_well")
        injectors = sorted(n for n, t in types.items() if t == "injector_well")
        name, source = "each producer paired with an injector", "spec §10.1; DB-14"
        if candidate.doublets == 0:
            ok = not producers and not injectors
            out.add(
                "SE-04",
                name,
                CheckStatus.PASS if ok else CheckStatus.FAIL,
                detail="no wells (C0)" if ok else "C0 candidate has wells",
                source=source,
            )
            return out.items[0]
        graph: dict[str, set[str]] = {}
        for entry in connections:
            if isinstance(entry, dict):
                graph.setdefault(str(entry.get("predecessor")), set()).add(
                    str(entry.get("successor"))
                )
        unpaired = [p for p in producers if not _reaches(graph, p, set(injectors))]
        problem = ""
        if not producers:
            problem = "no producer_well"
        elif len(producers) != len(injectors):
            problem = f"{len(producers)} producer(s) but {len(injectors)} injector(s)"
        elif unpaired:
            problem = f"no flow path to an injector from {unpaired}"
        out.add(
            "SE-04",
            name,
            CheckStatus.FAIL if problem else CheckStatus.PASS,
            detail=problem or f"{len(producers)} producer/injector pair(s)",
            value=len(producers),
            limit=len(injectors),
            unit="count",
            source=source,
        )
        return out.items[0]

    # ------------------------------------------------------------------ layer 3: physical
    def _physical(
        self, ev: VerificationEvidence, types: dict[str, str], assumption_ids: list[str]
    ) -> list[CheckResult]:
        out = _Checks(Layer.PHYSICAL)
        self._daily_checks(out, ev)
        self._brine_balance(out, ev, assumption_ids)
        self._hex_checks(out, ev, types)
        self._heat_pump_checks(out, ev, types, assumption_ids)
        self._absorption_checks(out, ev, types)
        self._pump_checks(out, ev, types)
        self._esp_checks(out, ev, types, assumption_ids)
        self._sign_checks(out, ev)
        self._design_point_checks(out, ev)
        self._model_validity_checks(out, ev)
        self._letdown_check(out, ev, assumption_ids)
        return out.items

    # ------------------------------------------------------------------ WP-14 checks
    @staticmethod
    def _design_point_checks(out: _Checks, ev: VerificationEvidence) -> None:
        """PH-25…PH-27: the TL-05 design operating point of every doublet (RC-01; WP-05)."""
        names = {
            "PH-25": "TL-05 design point feasible",
            "PH-26": "TL-05 ESP flow solve error ≤ 1 %",
            "PH-27": "ESP intake ≥ 1.2 · p_b at the design point (TL-05)",
        }
        source = "RC-01 items 6–9; WP-05 TL-05"
        if ev.candidate.doublets == 0:
            for check_id, name in names.items():
                out.not_applicable(check_id, name, "no doublets (C0)", source)
            return
        if not ev.doublet_points:
            for check_id, name in names.items():
                out.missing(check_id, name, "TL-05 design operating points", source)
            return
        bad = [p for p in ev.doublet_points if not p.feasible]
        out.add(
            "PH-25",
            names["PH-25"],
            CheckStatus.FAIL if bad else CheckStatus.PASS,
            detail=(
                "; ".join(
                    f"{p.name} at {_fmt(p.flow_m3h)} m³/h: {', '.join(p.reasons)}" for p in bad
                )
                if bad
                else f"{len(ev.doublet_points)} design point(s) feasible"
            ),
            value=float(len(bad)),
            limit=0.0,
            margin=-float(len(bad)),
            unit="infeasible points",
            source=source,
        )
        errors, intake = _Bound(), _Bound()
        for p in ev.doublet_points:
            if p.flow_error_frac is not None:
                err = abs(p.flow_error_frac)
                errors.add(
                    FLOW_SOLVE_TOL_FRAC - err, err, f"{p.name}: flow error {_fmt(100 * err)} %"
                )
            if p.intake_margin_bar is not None:
                intake.add(
                    p.intake_margin_bar,
                    p.intake_margin_bar,
                    f"{p.name}: ESP intake margin {_fmt(p.intake_margin_bar)} bar vs ≥ 0",
                )
        if any(p.flow_error_frac is None for p in ev.doublet_points):
            out.missing("PH-26", names["PH-26"], "TL-05 flow_error_frac", source)
        else:
            out.bound(
                "PH-26", names["PH-26"], errors, limit=FLOW_SOLVE_TOL_FRAC, unit="frac",
                source=source,
            )  # fmt: skip
        if any(p.intake_margin_bar is None for p in ev.doublet_points):
            out.missing("PH-27", names["PH-27"], "TL-05 intake_margin_bar", source)
        else:
            out.bound(
                "PH-27", names["PH-27"], intake, limit=0.0, unit="bar",
                source=f"RC-01 item 9; {ESP_MARGIN_ID}; WP-05 TL-05",
            )  # fmt: skip

    @staticmethod
    def _model_validity_checks(out: _Checks, ev: VerificationEvidence) -> None:
        """PH-28 model validity warnings, PH-29 daily flags (WP-02/03/06/07 outputs)."""
        name = "cooling and heat-pump models within validity (RC-04…RC-07)"
        source = "RC-04; RC-05; RC-06; RC-07; GM-04"
        if ev.annual is None:
            out.missing(
                "PH-28", name, "full-mode results (model warnings travel with them)", source
            )
        else:
            failing = [w for w in ev.model_warnings if MODEL_VALIDITY_FAIL.search(w)]
            others = [w for w in ev.model_warnings if not MODEL_VALIDITY_FAIL.search(w)]
            out.add(
                "PH-28",
                name,
                CheckStatus.FAIL if failing else CheckStatus.PASS,
                detail=(
                    "; ".join(failing[:3])
                    if failing
                    else f"{len(others)} model warning(s), none invalidates a point"
                ),
                value=float(len(failing)),
                limit=0.0,
                margin=-float(len(failing)),
                unit="invalid model points",
                source=source,
                warnings=others[:5],
            )
        name = "daily result flags (DayResult.flags)"
        source = "WP-06 simulate_day; WP-07 TL-12"
        if ev.annual is None:
            out.missing("PH-29", name, "full-mode daily results", source)
            return
        failing_days: list[str] = []
        other: dict[str, int] = {}
        for day in ev.annual.days:
            for flag in day.flags:
                if DAY_FLAG_FAIL.search(flag):
                    failing_days.append(f"{day.date}: {flag}")
                else:
                    other[flag] = other.get(flag, 0) + 1
        out.add(
            "PH-29",
            name,
            CheckStatus.FAIL if failing_days else CheckStatus.PASS,
            detail=(
                "; ".join(failing_days[:2])
                + (f" (+{len(failing_days) - 2} more)" if len(failing_days) > 2 else "")
                if failing_days
                else f"{sum(other.values())} informational flag(s) on {len(ev.annual.days)} days"
            ),
            value=float(len(failing_days)),
            limit=0.0,
            margin=-float(len(failing_days)),
            unit="failing day flags",
            source=source,
            warnings=[f"{flag} [×{count}]" for flag, count in list(other.items())[:5]],
        )

    def _letdown_check(
        self, out: _Checks, ev: VerificationEvidence, assumption_ids: list[str]
    ) -> None:
        """PH-30: degassed brine let down to ≤ 1 atm must stay at or below its boiling point.

        At a letdown pressure ≤ 1.01325 bar(a) water boils above 100 °C (SUB-NPSH vapour-pressure
        bound, RC-01 item 8): hotter brine flashes in the degasser and the injection-pump suction
        has no NPSH. Above 1 atm the cards give no saturation data → not evaluated.
        """
        name = "degassed brine below its boiling point at the letdown pressure"
        source = f"RC-01 item 8; {VAPOUR_BOUND_ID}; {SUCTION_PRESSURE_ID}; CFG-SURFACE"
        candidate = ev.candidate
        if candidate.doublets == 0 or candidate.gas_handling != GasHandling.DEGAS_USE:
            out.not_applicable("PH-30", name, "no degassing at atmospheric letdown", source)
            return
        letdown = ev.letdown_pressure_barg
        if letdown is None:
            letdown = self.suction_default_barg
            _note(assumption_ids, SUCTION_PRESSURE_ID)
        if letdown + P_ATM_BAR > self.vapour_bound_bara + 1e-9:
            out.missing(
                "PH-30", name,
                f"saturation pressure above {_fmt(self.vapour_bound_bara)} bar(a) (no card data)",
                source,
            )  # fmt: skip
            return
        if ev.annual is None:
            out.missing("PH-30", name, "full-mode production temperatures", source)
            return
        _note(assumption_ids, VAPOUR_BOUND_ID)
        worst = _Bound()
        for day in ev.annual.days:
            for i, flow in _active_doublets(day):
                t = day.production_temperature_degc[i]
                worst.add(
                    self.vapour_bound_degc - t,
                    t,
                    f"{day.date} doublet {i + 1}: {_fmt(t)} °C brine let down to "
                    f"{_fmt(letdown)} bar(g) > {_fmt(self.vapour_bound_degc)} °C (flashes)",
                )
        out.bound(
            "PH-30", name, worst, limit=self.vapour_bound_degc, unit="degC", source=source,
            ok_detail=f"hottest brine {_fmt(self.vapour_bound_degc - worst.margin)} °C ≤ "
            f"{_fmt(self.vapour_bound_degc)} °C at {_fmt(letdown)} bar(g)",
        )  # fmt: skip

    def _daily_checks(self, out: _Checks, ev: VerificationEvidence) -> None:
        lim = self.limits
        geothermal = ev.candidate.doublets > 0
        names = {
            "PH-01": "daily energy balance < 1 % (supplied + unmet = demand)",
            "PH-02": f"brine cooling 0–{_fmt(lim.brine_cooling_max_k)} K",
            "PH-03": (
                f"{_fmt(lim.flow_min_active_m3h)}–{_fmt(lim.flow_max_m3h)} m3/h per active "
                "doublet, 0 on outage"
            ),
            "PH-04": "Q_inj = Q_prod",
            "PH-05": f"heating supply {_fmt(lim.heating_supply_degc)} °C met",
            "PH-06": f"cooling supply {_fmt(lim.cooling_supply_degc)} °C met",
        }
        self._doublet_count(out, ev)
        if ev.annual is None:
            for check_id, name in names.items():
                if check_id in {"PH-02", "PH-03", "PH-04"} and not geothermal:
                    out.not_applicable(check_id, name, "no doublets (C0)", "limits.yaml")
                else:
                    out.missing(check_id, name, "full-mode daily results", "spec §10.1")
            return
        days = ev.annual.days

        balance = _Bound()
        for day in days:
            for label, demand_mw, supplied_mw, unmet_mwh in (
                ("heating", day.heating_demand_mw, day.heat_supplied_mw, day.unmet_heating_mwh),
                ("cooling", day.cooling_demand_mw, day.cool_supplied_mw, day.unmet_cooling_mwh),
            ):
                demand = demand_mw * HOURS_PER_DAY
                covered = supplied_mw * HOURS_PER_DAY + unmet_mwh
                scale = max(demand, covered)
                if scale <= 0:
                    continue
                error = abs(covered - demand) / scale
                balance.add(
                    ENERGY_BALANCE_TOL_FRAC - error,
                    error,
                    f"{day.date} {label}: supplied+unmet {_fmt(covered)} vs demand "
                    f"{_fmt(demand)} MWh ({_fmt(100 * error)} %)",
                )
        if balance.count == 0:
            out.not_applicable("PH-01", names["PH-01"], "no demand on any day", "spec §10.1")
        else:
            out.bound(
                "PH-01",
                names["PH-01"],
                balance,
                limit=ENERGY_BALANCE_TOL_FRAC,
                unit="frac",
                source="spec §10.1",
            )

        if not geothermal:
            for check_id in ("PH-02", "PH-03", "PH-04"):
                out.not_applicable(check_id, names[check_id], "no doublets (C0)", "limits.yaml")
        else:
            self._doublet_day_checks(out, ev, names)

        for check_id, attr, demand_attr, setpoint, upper in (
            ("PH-05", "heating_supply_degc", "heating_demand_mw", lim.heating_supply_degc, False),
            ("PH-06", "cooling_supply_degc", "cooling_demand_mw", lim.cooling_supply_degc, True),
        ):
            worst, missing = _Bound(), 0
            for day in days:
                if getattr(day, demand_attr) <= 0:
                    continue
                temperature = getattr(day, attr)
                if temperature is None:
                    missing += 1
                    continue
                margin = (setpoint - temperature) if upper else (temperature - setpoint)
                worst.add(
                    margin,
                    temperature,
                    f"{day.date}: supply {_fmt(temperature)} °C vs {'≤' if upper else '≥'} "
                    f"{_fmt(setpoint)} °C",
                )
            source = "DB-16/DB-17; limits.yaml"
            if missing:
                out.missing(
                    check_id, names[check_id], f"supply temperature on {missing} day(s)", source
                )
            elif worst.count == 0:
                out.not_applicable(check_id, names[check_id], "no demand", source)
            else:
                out.bound(
                    check_id, names[check_id], worst, limit=setpoint, unit="degC", source=source
                )

    def _doublet_count(self, out: _Checks, ev: VerificationEvidence) -> None:
        lim = self.limits
        name = f"{lim.doublet_count_min}–{lim.doublet_count_max} doublets, matching the results"
        count = ev.candidate.doublets
        if count == 0:
            out.not_applicable("PH-24", name, "no doublets (C0)", "limits.yaml DB-10")
            return
        worst = _Bound()
        worst.add(
            min(count - lim.doublet_count_min, lim.doublet_count_max - count),
            count,
            f"{count} doublets vs {lim.doublet_count_min}–{lim.doublet_count_max}",
        )
        for day in ev.annual.days if ev.annual else []:
            if day.doublet_flow_m3h and len(day.doublet_flow_m3h) != count:
                worst.add(
                    -abs(len(day.doublet_flow_m3h) - count),
                    len(day.doublet_flow_m3h),
                    f"{day.date}: {len(day.doublet_flow_m3h)} doublet flows for {count} doublets",
                )
        out.bound("PH-24", name, worst, limit=lim.doublet_count_max, unit="count", source="DB-10")

    def _doublet_day_checks(
        self, out: _Checks, ev: VerificationEvidence, names: dict[str, str]
    ) -> None:
        lim = self.limits
        assert ev.annual is not None
        days = ev.annual.days
        source = "limits.yaml (DB-11/12/14)"
        if not any(day.doublet_flow_m3h for day in days):
            for check_id in ("PH-02", "PH-03", "PH-04"):
                out.missing(check_id, names[check_id], "per-doublet flows", source)
            return

        cooling, cooling_missing = _Bound(), False
        flow = _Bound()
        balance, injection_missing = _Bound(), False
        for day in days:
            available = day.doublet_available or [True] * len(day.doublet_flow_m3h)
            for index, q in enumerate(day.doublet_flow_m3h):
                label = f"{day.date} doublet {index + 1}"
                if index < len(available) and not available[index]:
                    if q > 0:
                        flow.add(-q, q, f"{label}: {_fmt(q)} m3/h during planned outage")
                    continue
                if q > 0:
                    flow.add(
                        min(q - lim.flow_min_active_m3h, lim.flow_max_m3h - q),
                        q,
                        f"{label}: {_fmt(q)} m3/h vs {_fmt(lim.flow_min_active_m3h)}–"
                        f"{_fmt(lim.flow_max_m3h)} m3/h",
                    )
            for index, q in _active_doublets(day):
                label = f"{day.date} doublet {index + 1}"
                if day.production_temperature_degc and day.injection_temperature_degc:
                    dt_k = (
                        day.production_temperature_degc[index]
                        - day.injection_temperature_degc[index]
                    )
                    cooling.add(
                        lim.brine_cooling_max_k - dt_k,
                        dt_k,
                        f"{label}: brine cooled {_fmt(dt_k)} K vs ≤ "
                        f"{_fmt(lim.brine_cooling_max_k)} K",
                    )
                    if dt_k < 0:  # injection hotter than production: not a heat-extraction loop
                        cooling.add(dt_k, dt_k, f"{label}: injection hotter than production")
                else:
                    cooling_missing = True
                if day.injection_flow_m3h:
                    q_inj = day.injection_flow_m3h[index]
                    balance.add(
                        -abs(q_inj - q),
                        q_inj - q,
                        f"{label}: injection {_fmt(q_inj)} vs production {_fmt(q)} m3/h",
                    )
                else:
                    injection_missing = True
        if cooling_missing:
            out.missing("PH-02", names["PH-02"], "production/injection temperatures", source)
        elif cooling.count:
            out.bound(
                "PH-02",
                names["PH-02"],
                cooling,
                limit=lim.brine_cooling_max_k,
                unit="K",
                source=source,
            )
        else:
            out.not_applicable("PH-02", names["PH-02"], "no doublet produced", source)
        if flow.count:
            out.bound(
                "PH-03", names["PH-03"], flow, limit=lim.flow_max_m3h, unit="m3/h", source=source
            )
        else:
            out.add(
                "PH-03",
                names["PH-03"],
                CheckStatus.PASS,
                detail="no doublet produced on any day",
                source=source,
                warnings=["geothermal candidate without production"],
            )
        if injection_missing:
            out.missing("PH-04", names["PH-04"], "injection flows", source)
        elif balance.count:
            out.bound(
                "PH-04",
                names["PH-04"],
                balance,
                limit=0.0,
                unit="m3/h",
                source=source,
                ok_detail="injection equals production on every doublet-day",
                tolerance=NUMERIC_TOL * lim.flow_max_m3h,
            )
        else:
            out.not_applicable("PH-04", names["PH-04"], "no doublet produced", source)

    def _brine_balance(
        self, out: _Checks, ev: VerificationEvidence, assumption_ids: list[str]
    ) -> None:
        """PH-22: Σ heat drawn from the brine = Σ ρ·Q·c_p·(T_prod − T_inj) within 1 % per day.

        A user that both draws brine heat and delivers network heat obeys the first law:
        delivered = drawn + electricity (direct HEX: none; heat pump: Q_cond = Q_evap + W).
        """
        name = "brine-side energy balance < 1 % (heat drawn = ρ·Q·c_p·ΔT; first law per user)"
        # Users: see ``_user_balances`` (no heat without an input; drive heat matches COP).
        source = "spec §10.1; DB-08, DB-33; DB-23/DB-24"
        if ev.candidate.doublets == 0:
            out.not_applicable("PH-22", name, "no doublets (C0)", source)
            return
        if ev.annual is None:
            out.missing("PH-22", name, "full-mode daily results", source)
            return
        rho = self.design_basis.brine.density_kg_m3
        cp = self.brine_cp_j_per_kg_k
        _note(assumption_ids, BRINE_CP_ID)
        lows = [p.cop_min for p in ev.absorption if p.cop_min is not None]
        highs = [p.cop_max for p in ev.absorption if p.cop_max is not None]
        cop_range = (min(lows), max(highs)) if lows and highs else None
        worst, missing = _Bound(), 0
        for day in ev.annual.days:
            active = list(_active_doublets(day))
            if active and not (day.production_temperature_degc and day.injection_temperature_degc):
                missing += 1
                continue
            available = sum(
                brine_heat_mw(
                    q,
                    rho,
                    cp,
                    day.production_temperature_degc[i] - day.injection_temperature_degc[i],
                )
                for i, q in active
            )
            if available <= 0 and not day.brine_heat_drawn_mw:
                continue
            if not day.brine_heat_drawn_mw:
                missing += 1
                continue
            drawn = sum(day.brine_heat_drawn_mw.values())
            scale = max(abs(available), abs(drawn))
            error = abs(drawn - available) / scale if scale > 0 else 0.0
            worst.add(
                ENERGY_BALANCE_TOL_FRAC - error,
                error,
                f"{day.date}: heat drawn {_fmt(drawn)} MW vs brine ρQc_pΔT {_fmt(available)} MW "
                f"({_fmt(100 * error)} %)",
            )
            self._user_balances(day, cop_range, worst)
        if missing:
            out.missing(
                "PH-22", name, f"brine heat drawn or temperatures on {missing} day(s)", source
            )
        elif worst.count == 0:
            out.not_applicable("PH-22", name, "no doublet produced", source)
        else:
            out.bound(
                "PH-22", name, worst, limit=ENERGY_BALANCE_TOL_FRAC, unit="frac", source=source
            )

    @staticmethod
    def _user_balances(
        day: DayResult, cop_range: tuple[float, float] | None, worst: _Bound
    ) -> None:
        """First law per user: no heat source without an energy input.

        - a brine user that delivers network heat: delivered = drawn + its electricity (1 %);
        - a brine user that delivers no network heat drives cooling of the same key, with the
          implied COP (cooling / drawn heat) inside the GM-04 bounds of the absorption points;
        - every other network heat source needs fuel or electricity under the same key.
        """
        for key, drawn_k in day.brine_heat_drawn_mw.items():
            if key in day.heat_by_source_mw:
                delivered = day.heat_by_source_mw[key]
                expected = drawn_k + day.electricity_by_consumer_mw.get(key, 0.0)
                scale = max(abs(delivered), abs(expected))
                error = abs(delivered - expected) / scale if scale > 0 else 0.0
                worst.add(
                    ENERGY_BALANCE_TOL_FRAC - error,
                    error,
                    f"{day.date} '{key}': delivered {_fmt(delivered)} MW vs drawn + electricity "
                    f"{_fmt(expected)} MW",
                )
            elif drawn_k > 0 and key not in day.cool_by_source_mw:
                worst.add(-1.0, drawn_k, f"{day.date} '{key}': brine heat drawn but not used")
            elif drawn_k > 0 and cop_range is not None:
                cop = day.cool_by_source_mw[key] / drawn_k
                lo, hi = cop_range
                worst.add(
                    min(cop / lo - 1.0, 1.0 - cop / hi),
                    cop,
                    f"{day.date} '{key}': cooling/drive heat = COP {_fmt(cop)} vs "
                    f"{_fmt(lo)}–{_fmt(hi)} (GM-04)",
                )
        for key, heat in day.heat_by_source_mw.items():
            fed = (
                key in day.brine_heat_drawn_mw
                or key in day.gas_fuel_by_consumer_mw
                or key in day.electricity_by_consumer_mw
            )
            if heat > 0 and not fed:
                worst.add(
                    -1.0,
                    heat,
                    f"{day.date} '{key}': {_fmt(heat)} MW of heat without brine, fuel or "
                    "electricity input",
                )

    def _hex_checks(self, out: _Checks, ev: VerificationEvidence, types: dict[str, str]) -> None:
        names = {
            "PH-07": f"plate-HEX approach ≥ {_fmt(HEX_MIN_APPROACH_K)} K",
            "PH-08": "plate-HEX temperature within rating (gasketed 150 °C, welded 204 °C)",
            "PH-09": "plate-HEX pressure within rating (gasketed 20 bar, welded 20.7 bar(g))",
            "PH-10": "plate-HEX Δp 1–6 psi per NTU (≈ 24 kPa/NTU)",
            "PH-11": "plate-HEX NTU per pass 0.6–4.0",
            "PH-12": "plate-HEX unit ≤ 1360 m3/h and ≤ 1208 m2",
            "PH-23": "plate-HEX NTU consistent with its terminal temperatures (F_c 0.85–1)",
        }
        source = "RC-03"
        required = (
            bool(ev.hex_points) or "heat_exchanger" in types.values() or ev.candidate.doublets > 0
        )
        if not ev.hex_points:
            for check_id, name in names.items():
                if required:
                    out.missing(check_id, name, "plate-HEX operating points", source)
                else:
                    out.not_applicable(check_id, name, "no heat exchanger", source)
            return
        bounds = {check_id: _Bound() for check_id in names}
        missing: dict[str, list[str]] = {key: [] for key in ("PH-09", "PH-10", "PH-11", "PH-12")}
        warnings: list[str] = []
        for point in ev.hex_points:
            _hex_point(point, bounds, missing, warnings)
        out.bound(
            "PH-07",
            names["PH-07"],
            bounds["PH-07"],
            limit=HEX_MIN_APPROACH_K,
            unit="K",
            source="RC-03; Guidebook p.217–223",
            warnings=warnings,
        )
        out.bound("PH-08", names["PH-08"], bounds["PH-08"], limit=None, unit="degC", source=source)
        for check_id, limit, unit, src in (
            ("PH-09", None, "bar", "RC-03; GNS p.40; Guidebook p.218"),
            ("PH-10", HEX_DP_PER_NTU_PA[1] / 1000.0, "kPa", "RC-03 eq. 4; Guidebook p.221"),
            ("PH-11", HEX_NTU_PER_PASS[1], "-", "RC-03; Guidebook p.221"),
            ("PH-12", 1.0, "frac", "RC-03 limits"),
        ):
            if missing[check_id]:
                out.missing(check_id, names[check_id], f"data for {missing[check_id]}", src)
            else:
                out.bound(
                    check_id, names[check_id], bounds[check_id], limit=limit, unit=unit, source=src
                )
        src = "RC-03 eq. 1–2 (ε-NTU / LMTD, counter-flow)"
        if missing["PH-11"]:
            out.missing("PH-23", names["PH-23"], f"NTU for {missing['PH-11']}", src)
        else:
            out.bound("PH-23", names["PH-23"], bounds["PH-23"], limit=None, unit="-", source=src)

    def _heat_pump_checks(
        self,
        out: _Checks,
        ev: VerificationEvidence,
        types: dict[str, str],
        assumption_ids: list[str],
    ) -> None:
        names = {
            "PH-13": "heat-pump COP ≤ Carnot (condensing/evaporating temperatures)",
            "PH-14": "heat-pump lift and COP within datasheet (or RC-04 defaults)",
        }
        required = (
            bool(ev.heat_pumps)
            or "heatpump" in types.values()
            or _has_equipment(ev.candidate, "heat_pump", "hthp")
        )
        if not ev.heat_pumps:
            for check_id, name in names.items():
                if required:
                    out.missing(check_id, name, "heat-pump operating points", "RC-04")
                else:
                    out.not_applicable(check_id, name, "no heat pump", "RC-04")
            return
        carnot, datasheet = _Bound(), _Bound()
        for p in ev.heat_pumps:
            cond_k, evap_k = p.condensing_degc + 273.15, p.evaporating_degc + 273.15
            if (
                cond_k <= evap_k
                or p.condensing_degc <= p.sink_out_degc
                or p.evaporating_degc >= p.source_out_degc
            ):
                carnot.add(
                    -1.0,
                    p.cop,
                    f"{p.name}: configuration error (condensing {_fmt(p.condensing_degc)} °C must "
                    f"exceed the evaporating {_fmt(p.evaporating_degc)} °C and the sink outlet; "
                    "evaporating must be below the source outlet: approaches > 0)",
                )
                continue
            cop_carnot = cond_k / (cond_k - evap_k)
            carnot.add(
                cop_carnot - p.cop,
                p.cop,
                f"{p.name}: COP {_fmt(p.cop)} vs Carnot {_fmt(cop_carnot)}",
            )
            fraction = p.max_carnot_fraction
            if fraction is None:
                fraction = self.hp_carnot_fraction_default
                _note(assumption_ids, HP_CARNOT_FRACTION_ID)
            max_lift = p.max_lift_k
            if max_lift is None:
                max_lift = self.hp_max_lift_default_k
                _note(assumption_ids, HP_MAX_LIFT_ID)
            cap = fraction * cop_carnot
            datasheet.add(
                (cap - p.cop) / cap,
                p.cop / cap,
                f"{p.name}: COP {_fmt(p.cop)} vs ≤ {_fmt(fraction)} × Carnot = {_fmt(cap)}",
            )
            lift = p.sink_out_degc - p.source_out_degc
            datasheet.add(
                (max_lift - lift) / max_lift,
                lift / max_lift,
                f"{p.name}: lift {_fmt(lift)} K (outlet to outlet) vs ≤ {_fmt(max_lift)} K",
            )
        out.bound(
            "PH-13", names["PH-13"], carnot, limit=None, unit="-", source="spec §10.1; RC-04 eq. 1"
        )
        out.bound(
            "PH-14",
            names["PH-14"],
            datasheet,
            limit=1.0,
            unit="frac",
            source=f"RC-04 ({HP_CARNOT_FRACTION_ID}, {HP_MAX_LIFT_ID} without a datasheet)",
        )

    def _absorption_checks(
        self, out: _Checks, ev: VerificationEvidence, types: dict[str, str]
    ) -> None:
        names = {
            "PH-15": "absorption COP and capacity within GM-04 bounds",
            "PH-16": "absorption pumping ≤ 50 % of electric-chiller power",
        }
        required = (
            bool(ev.absorption)
            or any("absorption" in t for t in types.values())
            or _has_equipment(ev.candidate, "absorption")
        )
        if not ev.absorption:
            for check_id, name in names.items():
                if required:
                    out.missing(check_id, name, "absorption operating points", "GM-04")
                else:
                    out.not_applicable(check_id, name, "no absorption chiller", "GM-04")
            return
        bounds, pumping = _Bound(), _Bound()
        missing: dict[str, list[str]] = {"PH-15": [], "PH-16": []}
        for p in ev.absorption:
            if p.cop_min is None or p.cop_max is None or p.rated_cooling_mw is None:
                missing["PH-15"].append(p.name)
            else:
                bounds.add(
                    min(p.cop - p.cop_min, p.cop_max - p.cop),
                    p.cop,
                    f"{p.name}: COP {_fmt(p.cop)} vs {_fmt(p.cop_min)}–{_fmt(p.cop_max)}",
                )
                bounds.add(
                    (p.rated_cooling_mw - p.cooling_mw) / p.rated_cooling_mw,
                    p.cooling_mw,
                    f"{p.name}: {_fmt(p.cooling_mw)} MW vs rated {_fmt(p.rated_cooling_mw)} MW",
                )
            if p.pumping_power_mw is None or p.electric_chiller_power_mw is None:
                missing["PH-16"].append(p.name)
            else:
                limit = ABSORPTION_PUMPING_MAX_FRAC * p.electric_chiller_power_mw
                pumping.add(
                    limit - p.pumping_power_mw,
                    p.pumping_power_mw,
                    f"{p.name}: pumping {_fmt(p.pumping_power_mw)} MW vs ≤ {_fmt(limit)} MW",
                )
        for check_id, worst, unit, src in (
            ("PH-15", bounds, "-", "spec GM-04"),
            ("PH-16", pumping, "MW", "spec §10.1; Guidebook p.270"),
        ):
            if missing[check_id]:
                out.missing(check_id, names[check_id], f"data for {missing[check_id]}", src)
            else:
                out.bound(check_id, names[check_id], worst, limit=None, unit=unit, source=src)

    def _pump_checks(self, out: _Checks, ev: VerificationEvidence, types: dict[str, str]) -> None:
        eta = self.limits.pump_hydraulic_efficiency_frac
        name = f"pump shaft power = QΔp/{_fmt(eta)}"
        source = "RC-01 item 6; DB-13/DB-40"
        required = (
            bool(ev.pumps)
            or ev.candidate.doublets > 0
            or bool({"injector_pump", "booster_pump"} & set(types.values()))
        )
        if not ev.pumps:
            if required:
                out.missing("PH-17", name, "pump operating points", source)
            else:
                out.not_applicable("PH-17", name, "no pumps", source)
            return
        worst = _Bound()
        for p in ev.pumps:
            expected = p.flow_m3h / 3600.0 * p.pressure_rise_pa / eta
            if expected > 0:
                error = abs(p.shaft_power_w - expected) / expected
            else:
                error = 0.0 if p.shaft_power_w == 0 else 1.0
            worst.add(
                PUMP_POWER_TOL_FRAC - error,
                error,
                f"{p.name}: shaft {_fmt(p.shaft_power_w)} W vs QΔp/η {_fmt(expected)} W",
            )
        out.bound("PH-17", name, worst, limit=PUMP_POWER_TOL_FRAC, unit="frac", source=source)

    def _esp_checks(
        self,
        out: _Checks,
        ev: VerificationEvidence,
        types: dict[str, str],
        assumption_ids: list[str],
    ) -> None:
        names = {
            "PH-18": (
                f"ESP intake ≥ {_fmt(self.release_margin)} · bubble point (RC-01 item 9 setpoint)"
            ),
            "PH-19": f"NPSH_A ≥ NPSH_R + {_fmt(NPSH_MARGIN_M)} m",
        }
        required = bool(ev.esps) or ev.candidate.doublets > 0 or "esp" in types.values()
        if not ev.esps:
            for check_id, name in names.items():
                if required:
                    out.missing(check_id, name, "ESP operating points", "RC-01")
                else:
                    out.not_applicable(check_id, name, "no ESP", "RC-01")
            return
        intake, npsh, missing, warnings = _Bound(), _Bound(), [], []
        no_npshr: list[str] = []
        for p in ev.esps:
            limit, basis = self._setpoint(p.release_pressure_barg, ev, assumption_ids)
            if p.release_pressure_barg is None and ev.co2_release_pressure_barg is None:
                warnings.append(
                    f"{p.name}: no computed bubble point; default setpoint {basis} = "
                    f"{_fmt(limit)} bar(g)"
                )
            intake.add(
                p.intake_pressure_barg - limit,
                p.intake_pressure_barg,
                f"{p.name}: intake {_fmt(p.intake_pressure_barg)} bar(g) vs ≥ {_fmt(limit)} "
                f"bar(g) ({basis})",
            )
            if p.npsh_available_m is None:
                missing.append(p.name)
            else:
                # SUB-NPSH (WP-14): NPSH_R not given (no pump selected) → NPSH_A ≥ 15 m alone.
                required = p.npsh_required_m
                if required is None:
                    required = 0.0
                    no_npshr.append(p.name)
                npsh.add(
                    p.npsh_available_m - required - NPSH_MARGIN_M,
                    p.npsh_available_m,
                    f"{p.name}: NPSH_A {_fmt(p.npsh_available_m)} m vs NPSH_R "
                    f"{_fmt(required)} + {_fmt(NPSH_MARGIN_M)} m",
                )
        out.bound(
            "PH-18",
            names["PH-18"],
            intake,
            limit=None,
            unit="bar",
            source=f"RC-01 item 9; {ESP_MARGIN_ID} / {ESP_RELEASE_PRESSURE_ID}",
            warnings=warnings,
        )
        if missing:
            out.missing("PH-19", names["PH-19"], f"NPSH_A for {missing}", "RC-01 item 8")
        else:
            npsh_notes = []
            if no_npshr:
                _note(assumption_ids, VAPOUR_BOUND_ID)
                names_ = sorted(set(no_npshr))
                npsh_notes.append(
                    f"NPSH_R not given for {names_} ({VAPOUR_BOUND_ID}): checked NPSH_A ≥ "
                    f"{_fmt(NPSH_MARGIN_M)} m; add NPSH_R when the pump is selected"
                )
            out.bound(
                "PH-19", names["PH-19"], npsh, limit=NPSH_MARGIN_M, unit="m",
                source=f"RC-01 item 8; {VAPOUR_BOUND_ID}", warnings=npsh_notes,
            )  # fmt: skip

    def _sign_checks(self, out: _Checks, ev: VerificationEvidence) -> None:
        name = "no negative flows or powers"
        if ev.annual is None:
            out.missing("PH-20", name, "full-mode daily results", "spec §10.1")
        else:
            worst = _Bound()
            for day in ev.annual.days:
                for label, values in (
                    ("heat", day.heat_by_source_mw),
                    ("cooling", day.cool_by_source_mw),
                    ("electricity", day.electricity_by_consumer_mw),
                    ("gas fuel", day.gas_fuel_by_consumer_mw),
                    ("brine heat drawn", day.brine_heat_drawn_mw),
                ):
                    for key, value in values.items():
                        worst.add(value, value, f"{day.date} {label} '{key}' = {_fmt(value)} MW")
            if worst.count == 0:
                out.add(
                    "PH-20",
                    name,
                    CheckStatus.PASS,
                    detail="no flows or powers reported",
                    source="spec §10.1",
                )
            else:
                out.bound(
                    "PH-20",
                    name,
                    worst,
                    limit=0.0,
                    unit="MW",
                    source="spec §10.1",
                    ok_detail="all source/consumer powers ≥ 0",
                )
        name = "simulation constraint margins ≥ 0"
        worst = _Bound()
        for day in ev.annual.days if ev.annual else []:
            for key, margin in day.margins.items():
                worst.add(margin, margin, f"{day.date} {key} margin {_fmt(margin)}")
        if worst.count == 0:
            out.not_applicable("PH-21", name, "no margins reported by the simulation", "spec §8")
        else:
            out.bound("PH-21", name, worst, limit=0.0, unit="suffix", source="DayResult.margins")

    # ------------------------------------------------------------------ layer 4: performance
    def _performance(
        self, ev: VerificationEvidence, assumption_ids: list[str]
    ) -> list[CheckResult]:
        out = _Checks(Layer.PERFORMANCE)
        candidate, geothermal = ev.candidate, ev.candidate.doublets > 0

        name = "unmet demand = 0"
        unmet: float | None = None
        if ev.annual is not None:
            unmet = ev.annual.unmet_heating_mwh + ev.annual.unmet_cooling_mwh
        elif (
            ev.kpis
            and ev.kpis.unmet_heating_mwh is not None
            and ev.kpis.unmet_cooling_mwh is not None
        ):
            unmet = ev.kpis.unmet_heating_mwh + ev.kpis.unmet_cooling_mwh
        if unmet is None:
            out.missing("PD-01", name, "daily results or unmet-demand KPIs", "spec §10.1")
        else:
            out.add(
                "PD-01",
                name,
                CheckStatus.PASS if unmet <= 0 else CheckStatus.FAIL,
                detail=f"unmet heating + cooling {_fmt(unmet)} MWh/yr",
                value=unmet,
                limit=0.0,
                margin=0.0 - unmet,
                unit="MWh",
                source="spec §10.1; DB-30",
            )

        name = "planned outages do not overlap"
        if not geothermal:
            out.not_applicable("PD-02", name, "no doublets (C0)", "DB-30")
        elif ev.annual is None or not any(day.doublet_available for day in ev.annual.days):
            out.missing("PD-02", name, "daily doublet availability", "DB-30")
        else:
            worst = _Bound()
            for day in ev.annual.days:
                off = sum(1 for available in day.doublet_available if not available)
                worst.add(1 - off, off, f"{day.date}: {off} doublets on outage at once")
            out.bound(
                "PD-02",
                name,
                worst,
                limit=1.0,
                unit="doublets",
                source="DB-30",
                ok_detail=f"at most {_fmt(1 - worst.margin)} doublet off on any day",
            )

        sub = ev.subsurface
        breakthrough = sub.thermal_breakthrough_yr if sub else None
        if breakthrough is None and ev.kpis is not None:
            breakthrough = ev.kpis.thermal_breakthrough_yr
        subsurface_checks: tuple[tuple[str, str, float | None, float, bool, str, bool], ...] = (
            ("PD-03", "well spacing ≥ 1.2 km", sub.well_spacing_m if sub else None,
             WELL_SPACING_MIN_M, False, "m", geothermal),
            ("PD-04", "inter-doublet interference ≤ 1 bar", sub.interference_bar if sub else None,
             INTERFERENCE_MAX_BAR, True, "bar", candidate.doublets > 1),
            ("PD-05", "thermal breakthrough ≥ 30 yr", breakthrough, BREAKTHROUGH_MIN_YR, False,
             "yr", geothermal),
        )  # fmt: skip
        for check_id, name, value, limit, upper, unit, applies in subsurface_checks:
            src = "spec ENG-06; RC-02"
            if not applies:
                out.not_applicable(check_id, name, f"{candidate.doublets} doublet(s)", src)
            elif value is None:
                out.missing(check_id, name, "subsurface screening result", src)
            else:
                worst = _Bound()
                worst.add(
                    limit - value if upper else value - limit,
                    value,
                    f"{_fmt(value)} {unit} vs {'≤' if upper else '≥'} {_fmt(limit)} {unit}",
                )
                out.bound(check_id, name, worst, limit=limit, unit=unit, source=src)

        for check_id, name, flag, applies in (
            ("PD-11", "TL-08 interference within limit at the design separation",
             sub.interference_within_limit if sub else None, candidate.doublets > 1),
            ("PD-12", "TL-07 thermal breakthrough after the analysis horizon",
             sub.breakthrough_after_horizon if sub else None, geothermal),
        ):  # fmt: skip
            src = "spec ENG-06; RC-02; WP-05 TL-07/TL-08"
            if not applies:
                out.not_applicable(check_id, name, f"{candidate.doublets} doublet(s)", src)
            elif flag is None:
                out.missing(check_id, name, "TL-07/TL-08 screening flag", src)
            else:
                horizon = f" (horizon {_fmt(sub.horizon_yr)} yr)" if sub and sub.horizon_yr else ""
                out.add(
                    check_id,
                    name,
                    CheckStatus.PASS if flag else CheckStatus.FAIL,
                    detail=f"{'yes' if flag else 'no'}{horizon if check_id == 'PD-12' else ''}",
                    value=1.0 if flag else 0.0,
                    limit=1.0,
                    margin=0.0 if flag else -1.0,
                    unit="flag",
                    source=src,
                )

        _fast_full(out, ev)
        _scaling(out, ev)
        _materials(out, ev)
        self._envelope(out, ev)
        self._pressurised_loop(out, ev, assumption_ids)
        return out.items

    def _envelope(self, out: _Checks, ev: VerificationEvidence) -> None:
        """PD-09. Heat per doublet ≤ ρQc_pΔT follows from PH-02/03 and the PH-22 balance."""
        name = "sanity envelope: production temperature ≤ reservoir temperature"
        source = "spec §2.2 DB-20/DB-29; RC-01"
        candidate = ev.candidate
        if candidate.doublets == 0:
            out.not_applicable("PD-09", name, "no doublets (C0)", source)
            return
        if ev.annual is None or not any(d.production_temperature_degc for d in ev.annual.days):
            out.missing("PD-09", name, "daily production temperatures", source)
            return
        reservoir = self.design_basis.reservoir
        assert candidate.depth_m is not None
        t_res = temperature_at_depth_degc(
            candidate.depth_m,
            reservoir.surface_temperature_degc,
            reservoir.geothermal_gradient_k_per_km,
        )
        worst = _Bound()
        for day in ev.annual.days:
            for index, _ in _active_doublets(day):
                if day.production_temperature_degc:
                    t = day.production_temperature_degc[index]
                    worst.add(
                        t_res - t,
                        t,
                        f"{day.date} doublet {index + 1}: {_fmt(t)} °C vs ≤ reservoir "
                        f"{_fmt(t_res)} °C at {_fmt(candidate.depth_m)} m",
                    )
        if worst.count == 0:
            out.not_applicable("PD-09", name, "no doublet produced", source)
        else:
            out.bound("PD-09", name, worst, limit=t_res, unit="degC", source=source)

    def _pressurised_loop(
        self, out: _Checks, ev: VerificationEvidence, assumption_ids: list[str]
    ) -> None:
        """PD-10 (RC-12): without degassing, brine stays above the CO₂-release pressure."""
        name = "pressurised brine loop ≥ CO₂-release setpoint (RC-01 item 9)"
        source = f"RC-12; RC-01 item 9; {ESP_MARGIN_ID} / {ESP_RELEASE_PRESSURE_ID}"
        candidate = ev.candidate
        if candidate.doublets == 0 or candidate.gas_handling != GasHandling.PRESSURISED_REINJECT:
            out.not_applicable("PD-10", name, "loop is degassed or has no brine", source)
            return
        brine_hex = [p for p in ev.hex_points if p.brine_side]
        lacking = [p.name for p in brine_hex if p.min_brine_pressure_barg is None]
        if not brine_hex or lacking:
            what = f"minimum brine pressure of {lacking or 'the brine HEX'}"
            out.missing("PD-10", name, what, source)
            return
        release, basis = self._setpoint(None, ev, assumption_ids)
        worst = _Bound()
        for p in brine_hex:
            assert p.min_brine_pressure_barg is not None
            detail = (
                f"{p.name}: min brine pressure {_fmt(p.min_brine_pressure_barg)} bar(g) vs ≥ "
                f"{_fmt(release)} bar(g) CO₂-release setpoint ({basis})"
            )
            rating = HEX_RATING[p.construction][1]
            if rating < release:
                detail += (
                    f"; {p.construction} plate HEX rated {_fmt(rating)} bar(g) cannot hold the "
                    "loop above release: degas (inhibitor) or use a shell-and-tube unit "
                    "(RC-01 item 9, RC-03)"
                )
            worst.add(p.min_brine_pressure_barg - release, p.min_brine_pressure_barg, detail)
        out.bound("PD-10", name, worst, limit=release, unit="bar", source=source)

    # ------------------------------------------------------------------ layer 5: exergy
    @staticmethod
    def _exergy(ev: VerificationEvidence) -> list[CheckResult]:
        out = _Checks(Layer.EXERGY)
        kpis = ev.kpis
        value = kpis.exergy_efficiency_frac if kpis else None
        dead_state = kpis.exergy_dead_state if kpis else None
        if value is None:
            detail = "exergy utilisation not reported (WP-08); reported, not pass/fail"
        else:
            detail = f"η_u {_fmt(value)} (dead state: {dead_state or 'NOT STATED'})"
        out.add(
            "EX-01",
            "exergy utilisation reported",
            CheckStatus.INFO,
            detail=detail,
            value=value,
            unit="frac",
            source="spec §10.1; RC-11; DB-34d",
            warnings=[] if value is None or dead_state else ["dead state not stated (DB-34d)"],
        )
        return out.items

    # ------------------------------------------------------------------ report
    @staticmethod
    def _report(
        candidate_id: str,
        checks: list[CheckResult],
        stub_evidence: bool,
        assumption_ids: list[str],
    ) -> VerificationReport:
        failed = [c for c in checks if c.status == CheckStatus.FAIL]
        missing = [c.check_id for c in checks if c.status == CheckStatus.NOT_EVALUATED]
        if failed:
            verdict = DesignVerdict.REJECTED
        elif missing or stub_evidence:
            verdict = DesignVerdict.UNVERIFIED
        else:
            verdict = DesignVerdict.ACCEPTED
        layers = []
        for layer in Layer:
            items = [c for c in checks if c.layer == layer]
            n_fail = sum(c.status == CheckStatus.FAIL for c in items)
            n_miss = sum(c.status == CheckStatus.NOT_EVALUATED for c in items)
            n_pass = sum(c.status == CheckStatus.PASS for c in items)
            status: Literal["pass", "fail", "incomplete", "info"]
            if layer == Layer.EXERGY:
                status = "info"
            else:
                status = "fail" if n_fail else ("incomplete" if n_miss else "pass")
            layers.append(
                LayerResult(
                    layer=layer, status=status, passed=n_pass, failed=n_fail, not_evaluated=n_miss
                )
            )
        return VerificationReport(
            candidate_id=candidate_id,
            verdict=verdict,
            violations=[f"{c.check_id} {c.name}: {c.detail}" for c in failed],
            layers=layers,
            checks=checks,
            not_evaluated=missing,
            margins={c.check_id: c.margin for c in checks if c.margin is not None},
            assumption_ids=assumption_ids,
            stub_evidence=stub_evidence,
            summary=_summary(candidate_id, verdict, checks, failed, missing, stub_evidence),
        )


# --------------------------------------------------------------------------- stateless checks
def _hex_point(
    p: HexPoint, bounds: dict[str, _Bound], missing: dict[str, list[str]], warnings: list[str]
) -> None:
    """Add one plate-HEX operating point to the RC-03 bound checks."""
    duty_hot = p.hot_in_degc - p.hot_out_degc
    duty_cold = p.cold_out_degc - p.cold_in_degc
    dt_a = p.hot_in_degc - p.cold_out_degc
    dt_b = p.hot_out_degc - p.cold_in_degc
    approach = min(dt_a, dt_b)
    if duty_hot < 0 or duty_cold < 0 or (duty_hot > 0) != (duty_cold > 0):
        bounds["PH-07"].add(
            -max(-duty_hot, -duty_cold, 1.0),
            approach,
            f"{p.name}: inconsistent temperatures (hot ΔT {_fmt(duty_hot)} K, cold ΔT "
            f"{_fmt(duty_cold)} K)",
        )
    bounds["PH-07"].add(
        approach - HEX_MIN_APPROACH_K,
        approach,
        f"{p.name}: approach {_fmt(approach)} K vs ≥ {_fmt(HEX_MIN_APPROACH_K)} K",
    )
    lo_typ, hi_typ = HEX_TYPICAL_APPROACH_K
    if approach >= HEX_MIN_APPROACH_K and not lo_typ <= approach <= hi_typ:
        warnings.append(f"{p.name}: approach {_fmt(approach)} K outside typical 2–5.6 K")
    t_rating, p_rating = HEX_RATING[p.construction]
    t_max = max(p.hot_in_degc, p.hot_out_degc, p.cold_in_degc, p.cold_out_degc)
    bounds["PH-08"].add(
        t_rating - t_max,
        t_max,
        f"{p.name}: {_fmt(t_max)} °C vs ≤ {_fmt(t_rating)} °C ({p.construction})",
    )
    if p.design_pressure_barg is None:
        missing["PH-09"].append(p.name)
    else:
        bounds["PH-09"].add(
            p_rating - p.design_pressure_barg,
            p.design_pressure_barg,
            f"{p.name}: {_fmt(p.design_pressure_barg)} bar(g) vs ≤ {_fmt(p_rating)} bar(g) "
            f"({p.construction})",
        )
    if p.ntu is None or p.pressure_drop_pa is None:
        missing["PH-10"].append(p.name)
    else:
        per_ntu = p.pressure_drop_pa / p.ntu
        lo, hi = HEX_DP_PER_NTU_PA
        bounds["PH-10"].add(
            min(per_ntu - lo, hi - per_ntu) / 1000.0,
            per_ntu / 1000.0,
            f"{p.name}: Δp {_fmt(per_ntu / 1000.0)} kPa per NTU vs {_fmt(lo / 1000.0)}–"
            f"{_fmt(hi / 1000.0)} kPa",
        )
    if p.ntu is None:
        missing["PH-11"].append(p.name)
    else:
        lo, hi = HEX_NTU_PER_PASS
        bounds["PH-11"].add(
            min(p.ntu - lo, hi - p.ntu),
            p.ntu,
            f"{p.name}: NTU {_fmt(p.ntu)} per pass vs {_fmt(lo)}–{_fmt(hi)}",
        )
        if dt_a > 0 and dt_b > 0 and max(duty_hot, duty_cold) > 0:
            ideal = max(duty_hot, duty_cold) / _lmtd(dt_a, dt_b)  # counter-flow, C_min side
            total = p.ntu * p.passes
            upper = ideal / HEX_CORRECTION_FACTOR_MIN
            bounds["PH-23"].add(
                min(total - ideal, upper - total),
                total,
                f"{p.name}: total NTU {_fmt(total)} ({p.passes} pass(es)) vs {_fmt(ideal)}–"
                f"{_fmt(upper)} from the temperatures",
            )
    if p.flow_m3h is None and p.area_m2 is None:
        missing["PH-12"].append(p.name)
    if p.flow_m3h is not None:
        bounds["PH-12"].add(
            (HEX_MAX_UNIT_FLOW_M3H - p.flow_m3h) / HEX_MAX_UNIT_FLOW_M3H,
            p.flow_m3h / HEX_MAX_UNIT_FLOW_M3H,
            f"{p.name}: {_fmt(p.flow_m3h)} m3/h vs ≤ {_fmt(HEX_MAX_UNIT_FLOW_M3H)} m3/h per unit",
        )
    if p.area_m2 is not None:
        bounds["PH-12"].add(
            (HEX_MAX_UNIT_AREA_M2 - p.area_m2) / HEX_MAX_UNIT_AREA_M2,
            p.area_m2 / HEX_MAX_UNIT_AREA_M2,
            f"{p.name}: {_fmt(p.area_m2)} m2 vs ≤ {_fmt(HEX_MAX_UNIT_AREA_M2)} m2 per unit",
        )


def _fast_full(out: _Checks, ev: VerificationEvidence) -> None:
    """PD-06 (SIM-04): every day, per source, unmet demand and electricity within 5 %."""
    name = "fast vs full simulation agree (≤ 5 % daily, per source)"
    source = "spec SIM-04"
    if ev.annual is None or ev.fast_annual is None:
        missing = "fast-mode" if ev.annual is not None else "full-mode"
        out.missing("PD-06", name, f"{missing} daily results", source)
        return
    fast = {day.day_index: day for day in ev.fast_annual.days}
    absent = [day.day_index for day in ev.annual.days if day.day_index not in fast]
    if absent:
        what = f"fast-mode results for {len(absent)} of {len(ev.annual.days)} days"
        out.missing("PD-06", name, what, source)
        return
    worst, warnings = _Bound(), []

    def compare(label: str, full: float, quick: float, scale: float) -> None:
        if scale <= 0:
            return
        error = abs(full - quick) / scale
        if FAST_FULL_WARN_FRAC < error <= FAST_FULL_FAIL_FRAC:
            warnings.append(f"{label}: {_fmt(100 * error)} % (> 2 %)")
        worst.add(
            FAST_FULL_FAIL_FRAC - error,
            error,
            f"{label}: fast {_fmt(quick)} vs full {_fmt(full)} ({_fmt(100 * error)} %)",
        )

    for day in ev.annual.days:
        other = fast[day.day_index]
        sides = (
            ("heat", day.heat_by_source_mw, other.heat_by_source_mw, day.heating_demand_mw,
             day.unmet_heating_mwh, other.unmet_heating_mwh),
            ("cooling", day.cool_by_source_mw, other.cool_by_source_mw, day.cooling_demand_mw,
             day.unmet_cooling_mwh, other.unmet_cooling_mwh),
        )  # fmt: skip
        for kind, full_src, fast_src, demand, full_unmet, fast_unmet in sides:
            scale = max(demand, sum(full_src.values()))
            for key in sorted(set(full_src) | set(fast_src)):
                compare(
                    f"{day.date} {kind} '{key}' MW",
                    full_src.get(key, 0.0),
                    fast_src.get(key, 0.0),
                    scale,
                )
            compare(f"{day.date} unmet {kind} MWh", full_unmet, fast_unmet, scale * HOURS_PER_DAY)
        full_el, fast_el = day.electricity_consumed_mw, other.electricity_consumed_mw
        compare(f"{day.date} electricity MW", full_el, fast_el, max(full_el, fast_el))
    if worst.count == 0:
        out.add(
            "PD-06",
            name,
            CheckStatus.PASS,
            detail="no demand or supply on the compared days",
            source=source,
        )
    else:
        out.bound(
            "PD-06",
            name,
            worst,
            limit=FAST_FULL_FAIL_FRAC,
            unit="frac",
            source=source,
            warnings=warnings[:5],
        )


def _scaling(out: _Checks, ev: VerificationEvidence) -> None:
    """PD-07. A computed scaling 'fail' fails even with mitigations: it needs a design change."""
    name = "scaling screen passed or mitigated"
    source = "RC-12 (TL-22)"
    candidate = ev.candidate
    if candidate.doublets == 0:
        out.not_applicable("PD-07", name, "no brine loop (C0)", source)
        return
    if ev.scaling is None:
        out.missing("PD-07", name, "scaling/corrosion screen", source)
        return
    status = ev.scaling.status
    if status != "insufficient data":
        out.add(
            "PD-07",
            name,
            CheckStatus.PASS if status == "pass" else CheckStatus.FAIL,
            detail=f"screen {status}. {ev.scaling.detail}".strip(),
            source=source,
        )
        return
    required = {Mitigation.TITANIUM_PLATES, Mitigation.OXYGEN_EXCLUSION, Mitigation.NO_COPPER}
    if candidate.gas_handling == GasHandling.DEGAS_USE:
        required.add(Mitigation.CALCITE_INHIBITOR)  # degassing raises pH: dose downhole
    else:
        required.add(Mitigation.PRESSURISED_LOOP)  # keep pressure above CO₂ release
    lacking = sorted(m.value for m in required - set(ev.scaling.mitigations))
    out.add(
        "PD-07",
        name,
        CheckStatus.FAIL if lacking else CheckStatus.PASS,
        detail=(
            f"brine chemistry missing and mitigations {lacking} not in the design"
            if lacking
            else "brine chemistry missing; required mitigations are in the design"
        ),
        value=len(lacking),
        limit=0.0,
        margin=-len(lacking),
        unit="count",
        source=source,
        warnings=["MISSING INPUT: brine chemistry (Ca, alkalinity, pH, Cl, SO4, TDS)"],
    )


def _materials(out: _Checks, ev: VerificationEvidence) -> None:
    """PD-08: no copper alloy in brine contact; titanium plates for saline brine (RC-12)."""
    name = "materials compatible (no Cu in brine; Ti plates for saline brine)"
    source = "RC-12; RC-03; spec §10.1"
    if ev.candidate.doublets == 0:
        out.not_applicable("PD-08", name, "no brine loop (C0)", source)
        return
    brine_hex = [p for p in ev.hex_points if p.brine_side]
    materials = dict(ev.brine_contact_materials)
    unknown = [p.name for p in brine_hex if p.plate_material is None]
    materials.update({p.name: p.plate_material for p in brine_hex if p.plate_material})
    if unknown or not materials:
        what = f"plate material of {unknown}" if unknown else "brine-contact materials"
        out.missing("PD-08", name, what, source)
        return
    problems = [
        f"{part}: copper alloy '{material}'"
        for part, material in materials.items()
        if is_copper_alloy(material)
    ]
    if ev.saline_brine:
        problems += [
            f"{p.name}: plates '{p.plate_material}' not titanium"
            for p in brine_hex
            if p.plate_material and not is_titanium(p.plate_material)
        ]
    out.add(
        "PD-08",
        name,
        CheckStatus.FAIL if problems else CheckStatus.PASS,
        detail="; ".join(problems) or f"{len(materials)} brine-contact materials compatible",
        value=len(problems),
        limit=0.0,
        margin=-len(problems),
        unit="count",
        source=source,
    )


def _summary(
    candidate_id: str,
    verdict: DesignVerdict,
    checks: list[CheckResult],
    failed: list[CheckResult],
    missing: list[str],
    stub_evidence: bool,
) -> str:
    evaluated = sum(c.status in (CheckStatus.PASS, CheckStatus.FAIL) for c in checks)
    head = f"{VERIFIER_NAME} {candidate_id}: {verdict.value}; "
    if verdict == DesignVerdict.REJECTED:
        body = f"{len(failed)}/{evaluated} failed: " + "; ".join(
            f"{c.check_id} {c.detail}" for c in failed
        )
    elif verdict == DesignVerdict.UNVERIFIED:
        body = f"{evaluated} checks pass, {len(missing)} lack evidence ({', '.join(missing)})"
        if stub_evidence:
            body += "; evidence from stub tools"
    else:
        tightest = min(
            (
                c
                for c in checks
                if c.status == CheckStatus.PASS
                and c.margin is not None
                and c.unit not in ("count", "-")
                and c.limit
            ),
            key=lambda c: (c.margin or 0.0) / abs(c.limit or 1.0),
            default=None,
        )
        body = f"all {evaluated} checks pass"
        if tightest is not None:
            body += (
                f"; tightest {tightest.check_id} margin {_fmt(tightest.margin or 0.0)} "
                f"{tightest.unit}"
            )
    text = head + body
    return text if len(text) <= SUMMARY_MAX_CHARS else text[: SUMMARY_MAX_CHARS - 1] + "…"


# --------------------------------------------------------------------------- GEMINI helpers
def _build_error(project: dict[str, Any]) -> str:
    """Return '' if ``build_project`` instantiates the project, else the error text."""
    from gemini_simulator.project_loader import build_project

    try:
        build_project(project)
    except Exception as exc:  # noqa: BLE001 - any build error is a syntactic failure
        return f"build_project failed: {type(exc).__name__}: {exc}"[:200]
    return ""


def _reference_problems(
    components: dict[str, Any], connections: list[Any], boundary: list[Any]
) -> list[str]:
    problems = []
    for i, entry in enumerate(connections):
        if not isinstance(entry, dict):
            problems.append(f"connection {i} is not an object")
            continue
        for role in ("predecessor", "successor"):
            if entry.get(role) not in components:
                problems.append(f"connection {i}: unknown {role} '{entry.get(role)}'")
        ports = entry.get("ports")
        if ports is None:
            pairs = [(entry.get("predecessor_output"), entry.get("successor_input"))]
        else:
            pairs = [tuple(p) if isinstance(p, list | tuple) else (None, None) for p in ports]
        for pair in pairs:
            if len(pair) != 2 or not all(isinstance(k, str) and k for k in pair):
                problems.append(f"connection {i}: invalid key pair {pair!r}")
    for i, entry in enumerate(boundary):
        if not isinstance(entry, dict) or entry.get("component") not in components:
            name = entry.get("component") if isinstance(entry, dict) else entry
            problems.append(f"boundary input {i}: unknown component '{name}'")
        elif not isinstance(entry.get("key"), str) or "value" not in entry:
            problems.append(f"boundary input {i}: needs 'key' and 'value'")
    return problems


def _solve_problems(project: dict[str, Any]) -> list[str]:
    """Solve one steady-state step; every connection's output key must exist."""
    from gemini_simulator.project_loader import build_project

    try:
        solver = build_project(project)
        solver.run()
    except KeyError as exc:
        return [f"steady-state step: missing key {exc}"]
    except Exception as exc:  # noqa: BLE001 - a project that does not solve is not valid
        return [f"steady-state step failed (not a key error): {type(exc).__name__}: {exc}"[:200]]
    return [
        f"'{pred}' has no output '{out_key}'"
        for pred, _, out_key, _ in solver.connections
        if out_key not in solver.components[pred].output
    ]


def _reaches(graph: dict[str, set[str]], start: str, targets: set[str]) -> bool:
    seen, stack = {start}, [start]
    while stack:
        node = stack.pop()
        for nxt in graph.get(node, ()):
            if nxt in targets:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


# --------------------------------------------------------------------------- toolbox binding
EvidenceProvider = Callable[[Candidate, ToolContext], VerificationEvidence]
"""Collects the evidence for one candidate from the run (WP-14 passes the real collector)."""


def context_evidence(candidate: Candidate, context: ToolContext) -> VerificationEvidence:
    """Evidence available in the runner's ``ToolContext``: the candidate and its KPIs.

    Without the GEMINI project and daily results most checks stay ``not_evaluated``, so this
    provider can never yield ACCEPTED; WP-14 supplies a collector of the real tool outputs.
    """
    return VerificationEvidence(candidate=candidate, kpis=context.kpis.get(candidate.id))


@dataclass
class VerifyDesignTool:
    """``verify_design`` tool (TL-16) bound to an evidence provider (toolbox signature)."""

    evidence_provider: EvidenceProvider = context_evidence
    verifier: DesignVerifier | None = None
    _cache: dict[str, DesignVerifier] = field(default_factory=dict, repr=False)

    def _verifier(self, context: ToolContext) -> DesignVerifier:
        if self.verifier is not None:
            return self.verifier
        key = str(context.config_dir)
        if key not in self._cache:
            self._cache[key] = DesignVerifier.from_config(context.config_dir)
        return self._cache[key]

    def __call__(self, args: CandidateArgs, context: ToolContext) -> ToolResult[VerificationReport]:
        """Verify the requested (or selected) candidate."""
        candidate_id = args.candidate_id
        if candidate_id == "all":
            candidate_id = context.selected_id or (
                context.shortlist[0] if context.shortlist else ""
            )
        if not candidate_id:
            raise ValueError("no candidate selected for verification")
        by_id = {c.id: c for c in context.candidates}
        if candidate_id not in by_id:
            raise ValueError(f"unknown candidate '{candidate_id}'")
        evidence = self.evidence_provider(by_id[candidate_id], context)
        report = self._verifier(context).verify(evidence)
        warnings = [w for c in report.checks for w in c.warnings][:10]
        if report.stub_evidence:
            warnings.append("evidence from stub tools: the design cannot be ACCEPTED")
        return ToolResult[VerificationReport](
            data=report,
            summary=report.summary,
            warnings=warnings,
            assumption_ids=report.assumption_ids,
            sources=["spec §10.1", "RC-01", "RC-03", "RC-04", "RC-12", "limits.yaml"],
        )


def verify_design_spec(
    evidence_provider: EvidenceProvider = context_evidence,
    verifier: DesignVerifier | None = None,
) -> ToolSpec:
    """``ToolSpec`` binding the real verifier (``stub=False``) under the name ``verify_design``.

    Register it with ``Toolbox.register`` (WP-14 integration). The runner lets an ACCEPTED verdict
    stand only from a ``stub=False`` binding in a run without stub tool results (AG-11).
    """
    return ToolSpec(
        "verify_design",
        "Run the 4-layer DesignVerifier on a candidate.",
        CandidateArgs,
        VerifyDesignTool(evidence_provider, verifier),
        owner="WP-09",
        stub=False,
    )
