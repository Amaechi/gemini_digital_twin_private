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
(``margin >= 0`` means satisfied; the margins are the report's deltas, VR-01). A check whose
evidence is missing is ``not_evaluated``; a check that does not apply to the candidate is
``not_applicable``. Verdict:

- any failed check → ``REJECTED`` (violations listed);
- otherwise any required check not evaluated, or evidence from a stub tool → ``UNVERIFIED``;
- otherwise → ``ACCEPTED``.

The exergy layer is reported, never pass/fail (spec §10.1). Sources: spec §10.1; RC-03 (plate
HEX limits); RC-12 (scaling, materials); RC-01 items 6, 8, 9 (pumps, NPSH, CO₂ release); spec
ENG-06 (spacing, interference, t_b); spec SIM-04 (fast vs full).
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
from gemini_agent.tools.design_basis import temperature_at_depth_degc

VERIFIER_NAME = "DesignVerifier"
"""Value of ``verifier`` in every report (the runner records it with the verdict)."""

# --------------------------------------------------------------------------- thresholds
ENERGY_BALANCE_TOL_FRAC = 0.01
"""Daily energy balance error < 1 % (spec §10.1 physical layer)."""
HEX_MIN_APPROACH_K = 1.1
"""Smallest achievable plate-HEX approach (RC-03; Guidebook p.217–223)."""
HEX_TYPICAL_APPROACH_K = (2.0, 5.6)
"""Typical approach 2–5.6 K (spec §10.1; RC-03: economical 2.8–5.6 K, GNS 2–3 K) → warning only."""
HEX_MAX_TEMPERATURE_DEGC = 150.0
"""Gasketed plate-HEX temperature rating ~150 °C (RC-03; GNS p.40)."""
HEX_MAX_PRESSURE_BAR = 20.0
"""Gasketed plate-HEX pressure rating ~20 bar (RC-03; GNS p.40)."""
PA_PER_PSI = 6894.757
"""1 psi in Pa (unit conversion)."""
HEX_DP_PER_NTU_PA = (1.0 * PA_PER_PSI, 6.0 * PA_PER_PSI)
"""Δp per NTU 1–6 psi (≈ 6.9–41.4 kPa); typical 3.5 psi ≈ 24 kPa (RC-03 eq. 4; Guidebook p.221)."""
HEX_NTU_PER_PASS = (0.6, 4.0)
"""NTU per pass 0.6–4.0 (RC-03; Guidebook p.221)."""
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
"""Register entry for the default bubble/CO₂-release pressure (RC-01 item 9 [A], 20 bar(g))."""
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
"""Relative floating-point tolerance of the equality checks (Q_inj = Q_prod, supply temperatures
met); not an engineering coefficient. Such a check passes when ``margin >= -tolerance``."""
PUMP_POWER_TOL_FRAC = ENERGY_BALANCE_TOL_FRAC
"""Reported pump shaft power must match Q·Δp/η within the 1 % balance tolerance."""

_COPPER = re.compile(r"\b(cu|cuni|cu-ni|copper|brass|bronze|cupro\w*)\b", re.IGNORECASE)
"""Copper alloys and solder are unacceptable in brine contact (RC-12; Guidebook p.152, 211)."""
_TITANIUM = re.compile(r"\b(ti|titanium)\b", re.IGNORECASE)
"""Titanium plates for saline brine (RC-12; RC-03 materials; DiPippo Ch22)."""

PROJECT_KEYS = ("components", "boundary_inputs", "connections")
"""Top-level keys of a GEMINI project JSON (``_``-prefixed keys are comments)."""


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
    design_pressure_bar: float | None = Field(
        default=None, description="Highest operating pressure on either side, bar(g)."
    )
    ntu: float | None = Field(default=None, gt=0, description="NTU per pass.")
    pressure_drop_pa: float | None = Field(default=None, ge=0, description="Per pass.")
    flow_m3h: float | None = Field(default=None, ge=0, description="Larger side, per unit.")
    area_m2: float | None = Field(default=None, gt=0, description="Per unit.")
    plate_material: str | None = None
    brine_side: bool = True


class HeatPumpPoint(StrictModel):
    """Heat-pump operating point.

    ``sink_degc``/``source_degc`` are the condensing/evaporating temperatures or the
    entropic-mean stream temperatures (both give an upper bound on the COP).
    """

    name: str
    cop: float = Field(gt=0, description="Heating COP.")
    sink_degc: float
    source_degc: float
    max_lift_k: float | None = Field(default=None, gt=0, description="Datasheet lift limit.")


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
    intake_pressure_bar: float = Field(description="bar(g).")
    release_pressure_bar: float | None = Field(
        default=None, description="Bubble/CO₂-release pressure, bar(g); None → VER-ESP-PB."
    )
    npsh_available_m: float | None = None
    npsh_required_m: float | None = Field(default=None, ge=0)


class SubsurfaceEvidence(StrictModel):
    """Subsurface screening results (TL-06, TL-07, TL-08)."""

    well_spacing_m: float | None = Field(default=None, gt=0)
    interference_bar: float | None = Field(default=None, ge=0)
    thermal_breakthrough_yr: float | None = Field(default=None, gt=0)


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
    brine_contact_materials: dict[str, str] = Field(
        default_factory=dict, description="Component → material of brine-wetted parts."
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
                detail=detail[:240],
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
        tolerance: float = 0.0,
    ) -> None:
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


# --------------------------------------------------------------------------- verifier
class DesignVerifier:
    """Deterministic 4-layer verifier (spec §10.1).

    Thresholds: ``config/limits.yaml`` (hard limits), the module constants (cards/spec, cited)
    and the assumptions register (``VER-ESP-PB``).
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
        self.release_pressure_default_bar = float(register.get(ESP_RELEASE_PRESSURE_ID).value)

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
        candidate = evidence.candidate
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
            *self._performance(evidence),
            *self._exergy(evidence),
        ]
        return self._report(candidate.id, checks, evidence.stub_evidence, assumption_ids)

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
            problem = ""
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

        mismatched = [
            label
            for label, part in (
                ("annual", ev.annual),
                ("fast_annual", ev.fast_annual),
                ("kpis", ev.kpis),
            )
            if part is not None and part.candidate_id != ev.candidate.id
        ]
        try:
            Candidate.model_validate(ev.candidate.model_dump())
            schema_error = ""
        except ValueError as exc:  # pragma: no cover - the model is validated on construction
            schema_error = str(exc)
        problem = schema_error or (
            f"candidate_id of {mismatched} differs from '{ev.candidate.id}'" if mismatched else ""
        )
        out.add(
            "SY-02",
            "schemas valid and consistent",
            CheckStatus.FAIL if problem else CheckStatus.PASS,
            detail=problem or "candidate, results and KPIs validate",
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
            problems = _solve_problems(project, connections)
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
            source=source,
        )
        return out.items[0]

    # ------------------------------------------------------------------ layer 3: physical
    def _physical(
        self, ev: VerificationEvidence, types: dict[str, str], assumption_ids: list[str]
    ) -> list[CheckResult]:
        out = _Checks(Layer.PHYSICAL)
        self._daily_checks(out, ev)
        self._hex_checks(out, ev, types)
        self._heat_pump_checks(out, ev, types)
        self._absorption_checks(out, ev, types)
        self._pump_checks(out, ev, types)
        self._esp_checks(out, ev, types, assumption_ids)
        self._sign_checks(out, ev)
        return out.items

    def _daily_checks(self, out: _Checks, ev: VerificationEvidence) -> None:
        lim = self.limits
        geothermal = ev.candidate.doublets > 0
        names = {
            "PH-01": "daily energy balance < 1 %",
            "PH-02": f"brine cooling ≤ {_fmt(lim.brine_cooling_max_k)} K",
            "PH-03": (
                f"{lim.doublet_count_min}–{lim.doublet_count_max} doublets, "
                f"{_fmt(lim.flow_min_active_m3h)}–{_fmt(lim.flow_max_m3h)} m3/h per active doublet"
            ),
            "PH-04": "Q_inj = Q_prod",
            "PH-05": f"heating supply {_fmt(lim.heating_supply_degc)} °C met",
            "PH-06": f"cooling supply {_fmt(lim.cooling_supply_degc)} °C met",
        }
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
            if missing:
                out.missing(
                    check_id,
                    names[check_id],
                    f"supply temperature on {missing} day(s)",
                    "DB-16/DB-17",
                )
            elif worst.count == 0:
                out.not_applicable(check_id, names[check_id], "no demand", "DB-16/DB-17")
            else:
                out.bound(
                    check_id,
                    names[check_id],
                    worst,
                    limit=setpoint,
                    unit="degC",
                    source="DB-16/DB-17; limits.yaml",
                    tolerance=NUMERIC_TOL * max(1.0, abs(setpoint)),
                )

    def _doublet_day_checks(
        self, out: _Checks, ev: VerificationEvidence, names: dict[str, str]
    ) -> None:
        lim = self.limits
        assert ev.annual is not None
        days = ev.annual.days
        if not any(day.doublet_flow_m3h for day in days):
            for check_id in ("PH-02", "PH-03", "PH-04"):
                out.missing(check_id, names[check_id], "per-doublet flows", "spec §10.1")
            return

        cooling, cooling_missing = _Bound(), False
        flow = _Bound()
        balance, injection_missing = _Bound(), False
        count = ev.candidate.doublets
        if not lim.doublet_count_min <= count <= lim.doublet_count_max:
            flow.add(
                -1.0,
                count,
                f"{count} doublets vs {lim.doublet_count_min}–{lim.doublet_count_max}",
            )
        for day in days:
            if day.doublet_flow_m3h and len(day.doublet_flow_m3h) != count:
                flow.add(
                    -1.0,
                    len(day.doublet_flow_m3h),
                    f"{day.date}: {len(day.doublet_flow_m3h)} doublet flows for {count} "
                    "doublets",
                )
            available = day.doublet_available or [True] * len(day.doublet_flow_m3h)
            for index, q in enumerate(day.doublet_flow_m3h):
                label = f"{day.date} doublet {index + 1}"
                if not available[index]:
                    if q > 0:
                        flow.add(-q, q, f"{label}: {_fmt(q)} m3/h during planned outage")
                    continue
                if q > 0:
                    margin = min(q - lim.flow_min_active_m3h, lim.flow_max_m3h - q)
                    flow.add(
                        margin,
                        q,
                        f"{label}: {_fmt(q)} m3/h vs "
                        f"{_fmt(lim.flow_min_active_m3h)}–"
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
        source = "limits.yaml (DB-10/11/12/14)"
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
                detail="no doublet produced",
                source=source,
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
                tolerance=NUMERIC_TOL * self.limits.flow_max_m3h,
            )
        else:
            out.not_applicable("PH-04", names["PH-04"], "no doublet produced", source)

    def _hex_checks(self, out: _Checks, ev: VerificationEvidence, types: dict[str, str]) -> None:
        names = {
            "PH-07": f"plate-HEX approach ≥ {_fmt(HEX_MIN_APPROACH_K)} K",
            "PH-08": f"plate-HEX temperature ≤ {_fmt(HEX_MAX_TEMPERATURE_DEGC)} °C",
            "PH-09": f"plate-HEX pressure ≤ {_fmt(HEX_MAX_PRESSURE_BAR)} bar",
            "PH-10": "plate-HEX Δp 1–6 psi per NTU (≈ 24 kPa/NTU)",
            "PH-11": "plate-HEX NTU per pass 0.6–4.0",
            "PH-12": "plate-HEX unit ≤ 1360 m3/h and ≤ 1208 m2",
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
        approach, temperature, pressure = _Bound(), _Bound(), _Bound()
        dp, ntu, size = _Bound(), _Bound(), _Bound()
        missing: dict[str, list[str]] = {"PH-09": [], "PH-10": [], "PH-11": [], "PH-12": []}
        warnings: list[str] = []
        for point in ev.hex_points:
            terminal = min(
                point.hot_in_degc - point.cold_out_degc, point.hot_out_degc - point.cold_in_degc
            )
            if point.hot_out_degc > point.hot_in_degc or point.cold_out_degc < point.cold_in_degc:
                approach.add(
                    -abs(point.hot_out_degc - point.hot_in_degc) - 1.0,
                    terminal,
                    f"{point.name}: hot side heats up or cold side cools down",
                )
            approach.add(
                terminal - HEX_MIN_APPROACH_K,
                terminal,
                f"{point.name}: approach {_fmt(terminal)} K vs ≥ {_fmt(HEX_MIN_APPROACH_K)} K",
            )
            if HEX_MIN_APPROACH_K <= terminal < HEX_TYPICAL_APPROACH_K[0]:
                warnings.append(f"{point.name}: approach {_fmt(terminal)} K below typical 2–5.6 K")
            t_max = max(
                point.hot_in_degc, point.hot_out_degc, point.cold_in_degc, point.cold_out_degc
            )
            temperature.add(
                HEX_MAX_TEMPERATURE_DEGC - t_max,
                t_max,
                f"{point.name}: {_fmt(t_max)} °C vs ≤ {_fmt(HEX_MAX_TEMPERATURE_DEGC)} °C",
            )
            if point.design_pressure_bar is None:
                missing["PH-09"].append(point.name)
            else:
                pressure.add(
                    HEX_MAX_PRESSURE_BAR - point.design_pressure_bar,
                    point.design_pressure_bar,
                    f"{point.name}: {_fmt(point.design_pressure_bar)} bar vs ≤ "
                    f"{_fmt(HEX_MAX_PRESSURE_BAR)} bar",
                )
            if point.ntu is None or point.pressure_drop_pa is None:
                missing["PH-10"].append(point.name)
            else:
                per_ntu = point.pressure_drop_pa / point.ntu
                lo, hi = HEX_DP_PER_NTU_PA
                dp.add(
                    min(per_ntu - lo, hi - per_ntu) / 1000.0,
                    per_ntu / 1000.0,
                    f"{point.name}: Δp {_fmt(per_ntu / 1000.0)} kPa per NTU vs "
                    f"{_fmt(lo / 1000.0)}–{_fmt(hi / 1000.0)} kPa",
                )
            if point.ntu is None:
                missing["PH-11"].append(point.name)
            else:
                lo, hi = HEX_NTU_PER_PASS
                ntu.add(
                    min(point.ntu - lo, hi - point.ntu),
                    point.ntu,
                    f"{point.name}: NTU {_fmt(point.ntu)} vs {_fmt(lo)}–{_fmt(hi)}",
                )
            if point.flow_m3h is None and point.area_m2 is None:
                missing["PH-12"].append(point.name)
            if point.flow_m3h is not None:
                size.add(
                    (HEX_MAX_UNIT_FLOW_M3H - point.flow_m3h) / HEX_MAX_UNIT_FLOW_M3H,
                    point.flow_m3h / HEX_MAX_UNIT_FLOW_M3H,
                    f"{point.name}: {_fmt(point.flow_m3h)} m3/h vs ≤ "
                    f"{_fmt(HEX_MAX_UNIT_FLOW_M3H)} m3/h per unit",
                )
            if point.area_m2 is not None:
                size.add(
                    (HEX_MAX_UNIT_AREA_M2 - point.area_m2) / HEX_MAX_UNIT_AREA_M2,
                    point.area_m2 / HEX_MAX_UNIT_AREA_M2,
                    f"{point.name}: {_fmt(point.area_m2)} m2 vs ≤ "
                    f"{_fmt(HEX_MAX_UNIT_AREA_M2)} m2 per unit",
                )
        out.bound(
            "PH-07",
            names["PH-07"],
            approach,
            limit=HEX_MIN_APPROACH_K,
            unit="K",
            source="RC-03; Guidebook p.217–223",
            warnings=warnings,
        )
        out.bound(
            "PH-08",
            names["PH-08"],
            temperature,
            limit=HEX_MAX_TEMPERATURE_DEGC,
            unit="degC",
            source="RC-03; GNS p.40",
        )
        for check_id, worst, limit, unit, src in (
            ("PH-09", pressure, HEX_MAX_PRESSURE_BAR, "bar", "RC-03; GNS p.40"),
            ("PH-10", dp, HEX_DP_PER_NTU_PA[1] / 1000.0, "kPa", "RC-03 eq. 4; Guidebook p.221"),
            ("PH-11", ntu, HEX_NTU_PER_PASS[1], "-", "RC-03; Guidebook p.221"),
            ("PH-12", size, 1.0, "frac", "RC-03 limits"),
        ):
            if missing[check_id]:
                out.missing(check_id, names[check_id], f"data for {missing[check_id]}", src)
            else:
                out.bound(check_id, names[check_id], worst, limit=limit, unit=unit, source=src)

    def _heat_pump_checks(
        self, out: _Checks, ev: VerificationEvidence, types: dict[str, str]
    ) -> None:
        names = {"PH-13": "heat-pump COP ≤ Carnot", "PH-14": "heat-pump lift within datasheet"}
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
        carnot, lift, missing = _Bound(), _Bound(), []
        for point in ev.heat_pumps:
            sink_k, source_k = point.sink_degc + 273.15, point.source_degc + 273.15
            if sink_k <= source_k:
                carnot.add(
                    -point.cop,
                    point.cop,
                    f"{point.name}: sink {_fmt(point.sink_degc)} °C not above source "
                    f"{_fmt(point.source_degc)} °C",
                )
            else:
                limit = sink_k / (sink_k - source_k)
                carnot.add(
                    limit - point.cop,
                    point.cop,
                    f"{point.name}: COP {_fmt(point.cop)} vs Carnot {_fmt(limit)}",
                )
            if point.max_lift_k is None:
                missing.append(point.name)
            else:
                value = point.sink_degc - point.source_degc
                lift.add(
                    point.max_lift_k - value,
                    value,
                    f"{point.name}: lift {_fmt(value)} K vs datasheet ≤ {_fmt(point.max_lift_k)} K",
                )
        out.bound(
            "PH-13",
            names["PH-13"],
            carnot,
            limit=None,
            unit="-",
            source="spec §10.1; COP_Carnot = T_sink/(T_sink − T_source)",
        )
        if missing:
            out.missing("PH-14", names["PH-14"], f"datasheet lift for {missing}", "RC-04")
        else:
            out.bound("PH-14", names["PH-14"], lift, limit=None, unit="K", source="RC-04")

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
            error = (
                abs(p.shaft_power_w - expected) / expected
                if expected > 0
                else (0.0 if p.shaft_power_w == 0 else 1.0)
            )
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
            "PH-18": "ESP intake above bubble/CO₂-release pressure",
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
        for p in ev.esps:
            release = p.release_pressure_bar
            if release is None:
                release = self.release_pressure_default_bar
                if ESP_RELEASE_PRESSURE_ID not in assumption_ids:
                    assumption_ids.append(ESP_RELEASE_PRESSURE_ID)
                warnings.append(
                    f"{p.name}: release pressure not given; default "
                    f"{_fmt(release)} bar(g) ({ESP_RELEASE_PRESSURE_ID})"
                )
            intake.add(
                p.intake_pressure_bar - release,
                p.intake_pressure_bar,
                f"{p.name}: intake {_fmt(p.intake_pressure_bar)} bar(g) vs release ≥ "
                f"{_fmt(release)} bar(g)",
            )
            if p.npsh_available_m is None or p.npsh_required_m is None:
                missing.append(p.name)
            else:
                margin = p.npsh_available_m - p.npsh_required_m - NPSH_MARGIN_M
                npsh.add(
                    margin,
                    p.npsh_available_m,
                    f"{p.name}: NPSH_A {_fmt(p.npsh_available_m)} m vs NPSH_R "
                    f"{_fmt(p.npsh_required_m)} + {_fmt(NPSH_MARGIN_M)} m",
                )
        out.bound(
            "PH-18",
            names["PH-18"],
            intake,
            limit=None,
            unit="bar",
            source=f"RC-01 item 9; {ESP_RELEASE_PRESSURE_ID}",
            warnings=warnings,
        )
        if missing:
            out.missing("PH-19", names["PH-19"], f"NPSH for {missing}", "RC-01 item 8")
        else:
            out.bound(
                "PH-19", names["PH-19"], npsh, limit=NPSH_MARGIN_M, unit="m", source="RC-01 item 8"
            )

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
    def _performance(self, ev: VerificationEvidence) -> list[CheckResult]:
        out = _Checks(Layer.PERFORMANCE)
        candidate, geothermal = ev.candidate, ev.candidate.doublets > 0

        name = "unmet demand = 0"
        if ev.annual is not None:
            unmet = ev.annual.unmet_heating_mwh + ev.annual.unmet_cooling_mwh
        elif (
            ev.kpis
            and ev.kpis.unmet_heating_mwh is not None
            and (ev.kpis.unmet_cooling_mwh is not None)
        ):
            unmet = ev.kpis.unmet_heating_mwh + ev.kpis.unmet_cooling_mwh
        else:
            unmet = None
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
        for check_id, name, value, limit, upper, unit, applies in (
            (
                "PD-03",
                "well spacing ≥ 1.2 km",
                sub.well_spacing_m if sub else None,
                WELL_SPACING_MIN_M,
                False,
                "m",
                geothermal,
            ),
            (
                "PD-04",
                "inter-doublet interference ≤ 1 bar",
                sub.interference_bar if sub else None,
                INTERFERENCE_MAX_BAR,
                True,
                "bar",
                candidate.doublets > 1,
            ),
            (
                "PD-05",
                "thermal breakthrough ≥ 30 yr",
                breakthrough,
                BREAKTHROUGH_MIN_YR,
                False,
                "yr",
                geothermal,
            ),
        ):
            src = "spec ENG-06; RC-02"
            if not applies:
                out.not_applicable(check_id, name, f"{candidate.doublets} doublet(s)", src)
            elif value is None:
                out.missing(check_id, name, "subsurface screening result", src)
            else:
                margin = limit - value if upper else value - limit
                out.add(
                    check_id,
                    name,
                    CheckStatus.PASS if margin >= 0 else CheckStatus.FAIL,
                    detail=f"{_fmt(value)} {unit} vs {'≤' if upper else '≥'} {_fmt(limit)}",
                    value=value,
                    limit=limit,
                    margin=margin,
                    unit=unit,
                    source=src,
                )

        self._fast_full(out, ev)
        self._scaling(out, ev)
        self._materials(out, ev)
        self._envelope(out, ev)
        return out.items

    @staticmethod
    def _fast_full(out: _Checks, ev: VerificationEvidence) -> None:
        name = "fast vs full simulation agree (≤ 5 % daily)"
        source = "spec SIM-04"
        if ev.annual is None or ev.fast_annual is None:
            missing = "fast-mode" if ev.annual is not None else "full-mode"
            out.missing("PD-06", name, f"{missing} daily results", source)
            return
        fast = {day.day_index: day for day in ev.fast_annual.days}
        worst, warnings, compared = _Bound(), [], 0
        for day in ev.annual.days:
            other = fast.get(day.day_index)
            if other is None:
                continue
            compared += 1
            for label, full_mw, fast_mw, demand_mw in (
                ("heat", day.heat_supplied_mw, other.heat_supplied_mw, day.heating_demand_mw),
                ("cooling", day.cool_supplied_mw, other.cool_supplied_mw, day.cooling_demand_mw),
            ):
                scale = max(demand_mw, full_mw)
                if scale <= 0:
                    continue
                error = abs(full_mw - fast_mw) / scale
                if FAST_FULL_WARN_FRAC < error <= FAST_FULL_FAIL_FRAC:
                    warnings.append(f"{day.date} {label}: {_fmt(100 * error)} % (> 2 %)")
                worst.add(
                    FAST_FULL_FAIL_FRAC - error,
                    error,
                    f"{day.date} {label}: fast {_fmt(fast_mw)} vs full {_fmt(full_mw)} MW "
                    f"({_fmt(100 * error)} %)",
                )
        if compared == 0:
            out.missing("PD-06", name, "days present in both fast and full results", source)
        elif worst.count == 0:
            out.add(
                "PD-06",
                name,
                CheckStatus.PASS,
                detail="no demand on the compared days",
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

    @staticmethod
    def _scaling(out: _Checks, ev: VerificationEvidence) -> None:
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

    @staticmethod
    def _materials(out: _Checks, ev: VerificationEvidence) -> None:
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
            f"{part}: copper alloy '{m}'" for part, m in materials.items() if _COPPER.search(m)
        ]
        if ev.saline_brine:
            problems += [
                f"{p.name}: plates '{p.plate_material}' not titanium"
                for p in brine_hex
                if p.plate_material and not _TITANIUM.search(p.plate_material)
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

    def _envelope(self, out: _Checks, ev: VerificationEvidence) -> None:
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
                        f"{day.date} doublet {index + 1}: {_fmt(t)} °C vs ≤ "
                        f"reservoir {_fmt(t_res)} °C at "
                        f"{_fmt(candidate.depth_m)} m",
                    )
        if worst.count == 0:
            out.not_applicable("PD-09", name, "no doublet produced", source)
        else:
            out.bound("PD-09", name, worst, limit=t_res, unit="degC", source=source)

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
        violations = [f"{c.check_id} {c.name}: {c.detail}" for c in failed]
        margins = {c.check_id: c.margin for c in checks if c.margin is not None}
        return VerificationReport(
            candidate_id=candidate_id,
            verdict=verdict,
            violations=violations,
            layers=layers,
            checks=checks,
            not_evaluated=missing,
            margins=margins,
            assumption_ids=assumption_ids,
            stub_evidence=stub_evidence,
            summary=_summary(candidate_id, verdict, checks, failed, missing, stub_evidence),
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
    """'' if ``build_project`` instantiates the project, else the error text."""
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


def _solve_problems(project: dict[str, Any], connections: list[Any]) -> list[str]:
    """Solve one steady-state step; every connection's output key must exist."""
    from gemini_simulator.project_loader import build_project

    try:
        solver = build_project(project)
        solver.run()
    except KeyError as exc:
        return [f"steady-state step: missing key {exc}"]
    except Exception as exc:  # noqa: BLE001 - a project that does not solve is not valid
        return [f"steady-state step failed: {type(exc).__name__}: {exc}"[:200]]
    problems = []
    for pred, _, out_key, _ in solver.connections:
        if out_key not in solver.components[pred].output:
            problems.append(f"'{pred}' has no output '{out_key}'")
    return problems


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
            sources=["spec §10.1", "RC-01", "RC-03", "RC-12", "limits.yaml"],
        )


def verify_design_spec(
    evidence_provider: EvidenceProvider = context_evidence,
    verifier: DesignVerifier | None = None,
) -> ToolSpec:
    """``ToolSpec`` binding the real verifier (``stub=False``) under the name ``verify_design``.

    Register it with ``Toolbox.register`` (WP-14 integration); the runner then lets an ACCEPTED
    verdict stand (AG-11), while the same verdict from a ``stub=True`` binding is downgraded.
    """
    return ToolSpec(
        "verify_design",
        "Run the 4-layer DesignVerifier on a candidate.",
        CandidateArgs,
        VerifyDesignTool(evidence_provider, verifier),
        owner="WP-09",
        stub=False,
    )
