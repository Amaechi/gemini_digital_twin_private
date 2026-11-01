# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/safety/action_guard.py @ d69507a. Modified by
# GeoAgent team, 2026-11-01: the allow-listed operator actions become candidate designs checked
# against the hard limits of config/limits.yaml (DB-10…17, DB-36, DB-40) and the GEMINI project
# structure; the normalised-action signature becomes the SHA-256 of the canonical project JSON plus
# the candidate design, and the prior-signature rejection becomes duplicate-configuration
# detection; no simulator call, no async.
"""DesignGuard (TL-10): hard limits before simulation and duplicate configurations (spec §6, §10.1).

Golden rule 4 (CLAUDE.md): a design that violates a hard limit is never simulated silently. The
guard checks a :class:`Candidate` and, when given, its TL-09 :class:`GeneratedConfig` (or a raw
GEMINI project) and returns ``APPROVED`` or ``REJECTED`` with one specific reason per violation:

* DB-10 doublet count 1–3 (0 only for C0); DB-11 50–300 m³/h per active doublet (design and
  dispatch flows); DB-14 Q_inj = Q_prod — every injector's flow traced back through the brine
  train (splitters × fractions, mixers summed) must be exactly its own producer's flow;
* DB-12 brine cooling ≤ 40 K — plate-HEX design ΔT, heat-pump source floors ≥ T_wh − 40 K,
  absorption generator ΔT; DB-13/DB-40 injection-pump efficiency = 0.70 × η_motor × η_VFD;
* DB-16 heat-pump sink target 100 °C; DB-17 chiller/free-cooling supply 20 °C;
* DB-36 no CH₄ venting: every separator's gas must reach a burner (``gas_flow_rate`` of a boiler
  or CHP); a pressurised-reinjection candidate must not separate gas;
* duplicates: the hash of the canonical project JSON plus the candidate's design fields is
  already registered for another candidate (a renamed copy; ActionGuard's prior signatures).

Design flags that are not hard limits (e.g. a pressurised loop above the plate-HEX rating) are
returned as warnings; the DesignVerifier fails them after simulation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from gemini_agent.memory.design_cache import config_hash
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import GasHandling, StrictModel, ToolResult
from gemini_agent.schemas.config import AssumptionsRegister, HardLimits, load_config
from gemini_agent.tools.config_generator import GeneratedConfig, project_hash
from gemini_simulator.registry import COMPONENT_REGISTRY

TOOL_ID = "TL-10"
GUARD_VERSION = 1
REL_TOL = 1e-9
"""Relative tolerance on equalities (efficiency, set points)."""
FLOW_TOL_FRAC = 1e-9
"""Relative tolerance of the injection/production flow trace."""
TEMPERATURE_TOL_K = 1e-6

_FLOW_PASS: dict[str, dict[str, str]] = {
    "filter": {"flow_rate": "flow_rate", "flow_rate_m3h": "flow_rate"},
    "separator": {"flow_rate": "flow_rate", "flow_rate_m3h": "flow_rate"},
    "heat_exchanger": {"primary_flow_rate": "primary_flow_rate"},
    "heatpump": {
        "source_flow_rate": "source_flow_rate",
        "source_flow_rate_m3h": "source_flow_rate",
    },
    "absorption_chiller": {"drive_flow_rate_m3s": "drive_flow_rate_m3s"},
    "injector_well": {"flow_rate_m3s": "flow_rate"},
}
"""Brine-flow pass-through: component type → {output key: input key carrying the same flow}."""
_M3S_OUTPUTS = {"flow_rate_m3s", "flow_rate", "primary_flow_rate", "source_flow_rate"}
_BURNER_TYPES = {"boiler", "chp"}
_COOLING_TYPES = ("free_cooling", "absorption_chiller", "electric_chiller")


class GuardViolation(StrictModel):
    """One hard-limit violation with its specific reason."""

    code: str = Field(description="Design-basis item, e.g. 'DB-11'.")
    limit: str = Field(description="config/limits.yaml key (or 'duplicate').")
    component: str | None = None
    value: float | None = None
    bound: float | None = None
    reason: str


class GuardResult(StrictModel):
    """Outcome of one candidate (Plant Debugger ``GuardResult`` shape)."""

    candidate_id: str
    approved: bool
    status: Literal["APPROVED", "REJECTED"]
    reason: str = Field(max_length=300)
    violations: list[GuardViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    signature: str | None = Field(
        default=None, description="SHA-256 of the canonical project + candidate design."
    )
    duplicate_of: str | None = None


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class DesignGuard:
    """Hard-limit gate before simulation (see module doc)."""

    def __init__(self, limits: HardLimits, register: AssumptionsRegister) -> None:
        """Bind the hard limits and the DB-40 motor/VFD efficiencies."""
        self.limits = limits
        db40 = register.get("DB-40").value
        if not isinstance(db40, dict):
            raise TypeError(f"DB-40 must be a mapping, got {db40!r}")
        self.motor_vfd_frac = float(db40["motor_efficiency_frac"]) * float(
            db40["vfd_efficiency_frac"]
        )
        self.injection_pump_efficiency_frac = (
            limits.pump_hydraulic_efficiency_frac * self.motor_vfd_frac
        )
        self.signatures: dict[str, str] = {}

    @classmethod
    def from_config(cls, config_dir: Path | str | None = None) -> DesignGuard:
        """Build from ``config/limits.yaml`` and the assumptions register (DB-40)."""
        return cls(load_config("limits", config_dir), load_config("assumptions", config_dir))

    # ------------------------------------------------------------------ entry points
    @staticmethod
    def signature(candidate: Candidate, project: Mapping[str, Any]) -> str:
        """SHA-256 of the canonical {project, candidate design} JSON.

        The design part is the candidate without ``id``, ``description`` and ``parent_id``, so a
        C6 (storage dispatched by WP-07) is not a duplicate of its parent's identical GEMINI plant,
        while a renamed copy of a candidate is.
        """
        design = candidate.model_dump(mode="json", exclude={"id", "description", "parent_id"})
        return config_hash(
            {"project": project_hash(dict(project)), "design": design},
            namespace=f"design_guard:v{GUARD_VERSION}",
        )

    def validate(
        self,
        candidate: Candidate,
        config: GeneratedConfig | None = None,
        *,
        project: Mapping[str, Any] | None = None,
        prior_signatures: Mapping[str, str] | None = None,
    ) -> GuardResult:
        """Check one candidate (and its project); never raises on a bad design."""
        violations: list[GuardViolation] = []
        warnings: list[str] = []
        self._candidate_checks(candidate, violations)
        if project is None and config is not None:
            project = config.project
        signature = duplicate_of = None
        if config is not None:
            self._config_checks(config, violations, warnings)
        if project is None:
            warnings.append("no GEMINI project: structural checks (DB-12…17, DB-36) not run")
        else:
            self._project_checks(candidate, dict(project), violations, warnings)
            signature = self.signature(candidate, project)
            known = dict(self.signatures)
            known.update(prior_signatures or {})
            other = known.get(signature)
            if other is not None and other != candidate.id:
                duplicate_of = other
                violations.append(
                    GuardViolation(
                        code="duplicate",
                        limit="duplicate",
                        reason=(
                            f"same GEMINI project and design as candidate '{other}' "
                            f"(signature {signature[:12]}): simulate it once"
                        ),
                    )
                )
            elif other == candidate.id:
                warnings.append("same project already approved for this candidate")
        approved = not violations
        if approved:
            reason = "within all hard limits (limits.yaml DB-10…17, DB-36)"
            if signature is not None:
                reason += f"; signature {signature[:12]} is new"
        else:
            reason = "; ".join(v.reason for v in violations)
        return GuardResult(
            candidate_id=candidate.id,
            approved=approved,
            status="APPROVED" if approved else "REJECTED",
            reason=reason[:300],
            violations=violations,
            warnings=warnings,
            signature=signature,
            duplicate_of=duplicate_of,
        )

    def register(self, result: GuardResult) -> None:
        """Remember an approved project's signature for duplicate detection."""
        if result.approved and result.signature is not None:
            self.signatures.setdefault(result.signature, result.candidate_id)

    def validate_batch(
        self, items: Iterable[tuple[Candidate, GeneratedConfig | None]]
    ) -> list[GuardResult]:
        """Validate several candidates in order; later duplicates of approved ones are rejected."""
        results = []
        for candidate, config in items:
            result = self.validate(candidate, config)
            self.register(result)
            results.append(result)
        return results

    # ------------------------------------------------------------------ candidate level
    def _candidate_checks(self, c: Candidate, out: list[GuardViolation]) -> None:
        lim = self.limits
        if c.concept == "C0":
            return
        if not lim.doublet_count_min <= c.doublets <= lim.doublet_count_max:
            out.append(
                GuardViolation(
                    code="DB-10",
                    limit="doublet_count_min/max",
                    value=c.doublets,
                    bound=(
                        lim.doublet_count_max
                        if c.doublets > lim.doublet_count_max
                        else lim.doublet_count_min
                    ),
                    reason=(
                        f"DB-10: {c.doublets} doublets outside {lim.doublet_count_min}–"
                        f"{lim.doublet_count_max}"
                    ),
                )
            )
        self._flow_check(c.design_flow_m3h or 0.0, "design flow", None, out)
        if c.gas_handling is None:
            out.append(
                GuardViolation(
                    code="DB-36",
                    limit="ch4_venting_allowed",
                    reason="DB-36: a candidate with doublets must state its gas handling",
                )
            )

    def _flow_check(
        self, flow: float, label: str, component: str | None, out: list[GuardViolation]
    ) -> None:
        lim = self.limits
        if lim.flow_min_active_m3h <= flow <= lim.flow_max_m3h:
            return
        low = flow < lim.flow_min_active_m3h
        out.append(
            GuardViolation(
                code="DB-11",
                limit="flow_min_active_m3h" if low else "flow_max_m3h",
                component=component,
                value=flow,
                bound=lim.flow_min_active_m3h if low else lim.flow_max_m3h,
                reason=(
                    f"DB-11: {label} {flow:g} m³/h outside {lim.flow_min_active_m3h:g}–"
                    f"{lim.flow_max_m3h:g} m³/h per active doublet"
                ),
            )
        )

    def _config_checks(
        self, config: GeneratedConfig, out: list[GuardViolation], warnings: list[str]
    ) -> None:
        lim = self.limits
        for plan in config.doublets:
            if plan.active:
                self._flow_check(
                    plan.flow_m3h, f"doublet {plan.index} flow", f"producer_well_{plan.index}", out
                )
            elif plan.flow_m3h != 0.0:
                out.append(
                    GuardViolation(
                        code="DB-11",
                        limit="flow_min_active_m3h",
                        value=plan.flow_m3h,
                        reason=f"DB-11: doublet {plan.index} is off but has a flow",
                    )
                )
        hex_design = config.hex_design
        if hex_design is not None:
            cooling = hex_design.brine_in_degc - hex_design.brine_out_degc
            if cooling > lim.brine_cooling_max_k + TEMPERATURE_TOL_K:
                out.append(
                    GuardViolation(
                        code="DB-12",
                        limit="brine_cooling_max_k",
                        value=cooling,
                        bound=lim.brine_cooling_max_k,
                        reason=(
                            f"DB-12: plate-HEX design brine cooling {cooling:.2f} K > "
                            f"{lim.brine_cooling_max_k:g} K"
                        ),
                    )
                )
        warnings.extend(config.flags)

    # ------------------------------------------------------------------ project level
    def _project_checks(
        self,
        candidate: Candidate,
        project: dict[str, Any],
        out: list[GuardViolation],
        warnings: list[str],
    ) -> None:
        components = project.get("components")
        if not isinstance(components, dict):
            out.append(
                GuardViolation(code="SY-01", limit="project", reason="project has no components")
            )
            return
        types = {
            name: spec.get("type") for name, spec in components.items() if isinstance(spec, dict)
        }
        unknown = sorted(n for n, t in types.items() if t not in COMPONENT_REGISTRY)
        if unknown:
            warnings.append(f"unregistered component types in {unknown}")
        params = {
            name: (spec.get("parameters") or {})
            for name, spec in components.items()
            if isinstance(spec, dict)
        }
        incoming = _incoming(project)
        boundary = {
            (e.get("component"), e.get("key")): e.get("value")
            for e in project.get("boundary_inputs") or []
            if isinstance(e, dict)
        }
        producers = sorted(n for n, t in types.items() if t == "producer_well")
        injectors = sorted(n for n, t in types.items() if t == "injector_well")
        if candidate.doublets == 0 and (producers or injectors):
            out.append(
                GuardViolation(
                    code="DB-10",
                    limit="doublet_count_min/max",
                    reason="DB-10: C0 candidate has wells in its project",
                )
            )
        if candidate.doublets and len(producers) > candidate.doublets:
            out.append(
                GuardViolation(
                    code="DB-10",
                    limit="doublet_count_max",
                    value=len(producers),
                    bound=candidate.doublets,
                    reason=(
                        f"DB-10: project has {len(producers)} producers for "
                        f"{candidate.doublets} doublet(s)"
                    ),
                )
            )
        self._injection_balance(types, params, incoming, producers, injectors, out)
        self._pump_efficiency(params, injectors, out)
        self._brine_cooling(types, params, incoming, boundary, out)
        self._set_points(types, params, out)
        self._venting(candidate, types, params, project, out, warnings)

    def _injection_balance(
        self,
        types: dict[str, Any],
        params: dict[str, Any],
        incoming: dict[tuple[str, str], tuple[str, str]],
        producers: list[str],
        injectors: list[str],
        out: list[GuardViolation],
    ) -> None:
        """DB-14: each injector receives exactly one producer's full flow."""
        if len(producers) != len(injectors):
            out.append(
                GuardViolation(
                    code="DB-14",
                    limit="injection_equals_production",
                    value=len(injectors),
                    bound=len(producers),
                    reason=(
                        f"DB-14: {len(producers)} producer(s) but {len(injectors)} injector(s) "
                        "(Q_inj = Q_prod per doublet)"
                    ),
                )
            )
        used: dict[str, str] = {}
        for injector in injectors:
            try:
                shares = _trace_flow(injector, "flow_rate", types, params, incoming)
            except ValueError as exc:
                out.append(
                    GuardViolation(
                        code="DB-14",
                        limit="injection_equals_production",
                        component=injector,
                        reason=f"DB-14: {injector} flow cannot be traced: {exc}",
                    )
                )
                continue
            sources = {k: v for k, v in shares.items() if abs(v) > FLOW_TOL_FRAC}
            if len(sources) == 1:
                ((producer, share),) = sources.items()
                if abs(share - 1.0) <= FLOW_TOL_FRAC and producer not in used:
                    used[producer] = injector
                    continue
            detail = ", ".join(f"{k} × {v:.4g}" for k, v in sorted(sources.items())) or "nothing"
            out.append(
                GuardViolation(
                    code="DB-14",
                    limit="injection_equals_production",
                    component=injector,
                    reason=(
                        f"DB-14: {injector} injects {detail}, not exactly one producer's flow "
                        "(Q_inj = Q_prod)"
                    ),
                )
            )

    def _pump_efficiency(
        self, params: dict[str, Any], injectors: list[str], out: list[GuardViolation]
    ) -> None:
        """DB-13/DB-40: injection pump at 0.70 × η_motor × η_VFD (wire to water)."""
        expected = self.injection_pump_efficiency_frac
        for injector in injectors:
            pump = params[injector].get("injector_pump")
            value = _num(pump.get("efficiency_factor")) if isinstance(pump, dict) else None
            if value is not None and math.isclose(value, expected, rel_tol=REL_TOL):
                continue
            out.append(
                GuardViolation(
                    code="DB-13",
                    limit="pump_hydraulic_efficiency_frac",
                    component=injector,
                    value=value,
                    bound=expected,
                    reason=(
                        f"DB-13: {injector} pump efficiency "
                        f"{value if value is not None else 'unset'}"
                        f" ≠ {self.limits.pump_hydraulic_efficiency_frac:g} × motor × VFD = "
                        f"{expected:.4f} (DB-40)"
                    ),
                )
            )

    def _brine_cooling(
        self,
        types: dict[str, Any],
        params: dict[str, Any],
        incoming: dict[tuple[str, str], tuple[str, str]],
        boundary: dict[tuple[Any, Any], Any],
        out: list[GuardViolation],
    ) -> None:
        """DB-12: heat-pump source floors and absorption generator ΔT within 40 K of T_wh."""
        limit = self.limits.brine_cooling_max_k
        for name, kind in types.items():
            if kind == "heatpump":
                floor = _num(params[name].get("Ts_in_minimum"))
                t_wh = _producer_temperature(name, types, params, incoming, boundary)
                if t_wh is None:
                    continue
                if floor is None or floor < t_wh - limit - TEMPERATURE_TOL_K:
                    out.append(
                        GuardViolation(
                            code="DB-12",
                            limit="brine_cooling_max_k",
                            component=name,
                            value=floor,
                            bound=t_wh - limit,
                            reason=(
                                f"DB-12: {name} may cool the brine to {floor} °C, below T_wh "
                                f"{t_wh:.2f} − {limit:g} K"
                            ),
                        )
                    )
            elif kind == "absorption_chiller":
                delta = _num(
                    params[name].get(
                        "generator_delta_t_k", _default("absorption_chiller", "generator_delta_t_k")
                    )
                )
                if delta is not None and delta > limit:
                    out.append(
                        GuardViolation(
                            code="DB-12",
                            limit="brine_cooling_max_k",
                            component=name,
                            value=delta,
                            bound=limit,
                            reason=f"DB-12: {name} generator ΔT {delta:g} K > {limit:g} K",
                        )
                    )

    def _set_points(
        self, types: dict[str, Any], params: dict[str, Any], out: list[GuardViolation]
    ) -> None:
        """DB-16 heat-pump sink target; DB-17 cooling supply set points."""
        lim = self.limits
        for name, kind in types.items():
            if kind == "heatpump":
                value = _num(params[name].get("Th_out_target", _default(kind, "Th_out_target")))
                if value is None or not math.isclose(
                    value, lim.heating_supply_degc, rel_tol=REL_TOL
                ):
                    out.append(
                        GuardViolation(
                            code="DB-16",
                            limit="heating_supply_degc",
                            component=name,
                            value=value,
                            bound=lim.heating_supply_degc,
                            reason=(
                                f"DB-16: {name} sink target {value} °C ≠ "
                                f"{lim.heating_supply_degc:g} °C"
                            ),
                        )
                    )
            elif kind in _COOLING_TYPES:
                key = "chilled_water_temperature_out_degc"
                value = _num(params[name].get(key, _default(kind, key)))
                if value is None or not math.isclose(
                    value, lim.cooling_supply_degc, rel_tol=REL_TOL
                ):
                    out.append(
                        GuardViolation(
                            code="DB-17",
                            limit="cooling_supply_degc",
                            component=name,
                            value=value,
                            bound=lim.cooling_supply_degc,
                            reason=(
                                f"DB-17: {name} supply {value} °C ≠ {lim.cooling_supply_degc:g} °C"
                            ),
                        )
                    )

    def _venting(
        self,
        candidate: Candidate,
        types: dict[str, Any],
        params: dict[str, Any],
        project: dict[str, Any],
        out: list[GuardViolation],
        warnings: list[str],
    ) -> None:
        """DB-36: separated gas must be burned; pressurised reinjection separates none."""
        outgoing: dict[str, list[tuple[str, str, str]]] = {}
        for entry in project.get("connections") or []:
            if not isinstance(entry, dict):
                continue
            ports = entry.get("ports") or [
                [entry.get("predecessor_output"), entry.get("successor_input")]
            ]
            for pair in ports:
                outgoing.setdefault(str(entry.get("predecessor")), []).append(
                    (str(pair[0]), str(entry.get("successor")), str(pair[1]))
                )
        separators = sorted(n for n, t in types.items() if t == "separator")
        gassy = [s for s in separators if (_num(params[s].get("gas_water_ratio")) or 0.0) > 0]
        if candidate.gas_handling == GasHandling.PRESSURISED_REINJECT and gassy:
            out.append(
                GuardViolation(
                    code="DB-36",
                    limit="ch4_venting_allowed",
                    reason=(
                        f"DB-36: pressurised reinjection but {gassy} separate gas "
                        "(it must stay dissolved, DR-07)"
                    ),
                )
            )
        if candidate.gas_handling == GasHandling.DEGAS_USE and candidate.doublets and not gassy:
            warnings.append("degas_use candidate without a gas separator: gas stays dissolved")
        for separator in gassy:
            vented = _unburned_gas(separator, types, params, outgoing)
            if vented:
                out.append(
                    GuardViolation(
                        code="DB-36",
                        limit="ch4_venting_allowed",
                        component=separator,
                        reason=(
                            f"DB-36: gas from {separator} is not burned ({vented}); venting CH₄ is "
                            "not allowed"
                        ),
                    )
                )


# --------------------------------------------------------------------------- graph helpers
def _incoming(project: dict[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    """(successor, input key) → (predecessor, output key)."""
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for entry in project.get("connections") or []:
        if not isinstance(entry, dict):
            continue
        ports = entry.get("ports") or [
            [entry.get("predecessor_output"), entry.get("successor_input")]
        ]
        for pair in ports:
            if isinstance(pair, list | tuple) and len(pair) == 2:
                result[(str(entry.get("successor")), str(pair[1]))] = (
                    str(entry.get("predecessor")),
                    str(pair[0]),
                )
    return result


def _default(component_type: str, key: str) -> JsonValue:
    model = COMPONENT_REGISTRY[component_type]()
    return getattr(model, "parameters", {}).get(key)


def _trace_flow(
    name: str,
    key: str,
    types: dict[str, Any],
    params: dict[str, Any],
    incoming: dict[tuple[str, str], tuple[str, str]],
    depth: int = 0,
) -> dict[str, float]:
    """Express the flow arriving at ``name.key`` as {producer: share of its flow}."""
    if depth > 200:
        raise ValueError("flow trace too deep (cycle?)")
    source = incoming.get((name, key))
    if source is None:
        raise ValueError(f"'{name}.{key}' has no upstream connection")
    pred, out_key = source
    kind = types.get(pred)
    if kind == "producer_well":
        if out_key in ("flow_rate", "flow_rate_m3s"):
            return {pred: 1.0}
        raise ValueError(f"'{pred}.{out_key}' is not a flow")
    if kind == "splitter":
        if not out_key.startswith("output_"):
            raise ValueError(f"'{pred}.{out_key}' is not a splitter output")
        fractions = params[pred].get("split_fractions") or []
        share = _num(fractions[int(out_key.split("_")[1])])
        if share is None:
            raise ValueError(f"'{pred}' split fraction is not a number")
        upstream = _trace_flow(pred, "input", types, params, incoming, depth + 1)
        return {k: v * share for k, v in upstream.items()}
    if kind == "mixer":
        if out_key != "flow":
            raise ValueError(f"'{pred}.{out_key}' is not the mixer flow")
        total: dict[str, float] = {}
        for i in range(int(params[pred].get("num_inputs", 0))):
            for k, v in _trace_flow(
                pred, f"input_{i}_flow", types, params, incoming, depth + 1
            ).items():
                total[k] = total.get(k, 0.0) + v
        return total
    passthrough = _FLOW_PASS.get(str(kind), {})
    if out_key not in passthrough:
        raise ValueError(f"'{pred}' ({kind}) output '{out_key}' does not carry the brine flow")
    return _trace_flow(pred, passthrough[out_key], types, params, incoming, depth + 1)


def _producer_temperature(
    name: str,
    types: dict[str, Any],
    params: dict[str, Any],
    incoming: dict[tuple[str, str], tuple[str, str]],
    boundary: dict[tuple[Any, Any], Any],
) -> float | None:
    """Wellhead temperature of the producer that feeds a heat pump's source (boundary input)."""
    try:
        shares = _trace_flow(name, "source_flow_rate", types, params, incoming)
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    temperatures = [_num(boundary.get((producer, "wellhead_temperature"))) for producer in shares]
    known = [t for t in temperatures if t is not None]
    return max(known) if known else None


def _unburned_gas(
    separator: str,
    types: dict[str, Any],
    params: dict[str, Any],
    outgoing: dict[str, list[tuple[str, str, str]]],
) -> str:
    """Return '' if all of the separator's gas reaches a burner, else what is lost."""
    links = [
        o for o in outgoing.get(separator, []) if o[0] in ("gas_mass_flow_rate", "gas_flow_rate")
    ]
    if not links:
        return "gas output not connected"
    problems = []
    for _, successor, input_key in links:
        problem = _gas_reaches_burner(successor, input_key, types, params, outgoing, 0)
        if problem:
            problems.append(problem)
    return "; ".join(problems)


def _gas_reaches_burner(
    name: str,
    input_key: str,
    types: dict[str, Any],
    params: dict[str, Any],
    outgoing: dict[str, list[tuple[str, str, str]]],
    depth: int,
) -> str:
    kind = types.get(name)
    if kind in _BURNER_TYPES:
        if input_key in ("gas_flow_rate", "gas_volumetric_flow_rate"):
            return ""
        return f"{name}.{input_key} is not a fuel input"
    if depth > 50:
        return "gas path too deep"
    if kind == "adder":
        targets = [o for o in outgoing.get(name, []) if o[0] == "output"]
        if not targets:
            return f"{name} output not connected"
        problems = [
            _gas_reaches_burner(succ, key, types, params, outgoing, depth + 1)
            for _, succ, key in targets
        ]
        return "; ".join(p for p in problems if p)
    if kind == "splitter":
        problems = []
        for i, fraction in enumerate(params[name].get("split_fractions") or []):
            if (_num(fraction) or 0.0) <= 0.0:
                continue
            targets = [o for o in outgoing.get(name, []) if o[0] == f"output_{i}"]
            if not targets:
                problems.append(f"{name} output_{i} ({fraction}) not connected")
            for _, succ, key in targets:
                problem = _gas_reaches_burner(succ, key, types, params, outgoing, depth + 1)
                if problem:
                    problems.append(problem)
        return "; ".join(problems)
    return f"gas goes to {name} ({kind}), not a burner"


# --------------------------------------------------------------------------- tool
class GuardRequest(StrictModel):
    """TL-10 input: candidates with their generated configs (None → candidate-level only)."""

    candidates: list[Candidate]
    configs: dict[str, GeneratedConfig] = Field(
        default_factory=dict, description="candidate id → TL-09 config."
    )
    prior_signatures: dict[str, str] = Field(
        default_factory=dict, description="guard signature → candidate id already simulated."
    )


class GuardReport(StrictModel):
    """TL-10 output."""

    results: list[GuardResult]

    @property
    def failed_ids(self) -> list[str]:
        """Ids of rejected candidates."""
        return [r.candidate_id for r in self.results if not r.approved]


def design_guard(
    request: GuardRequest, *, config_dir: Path | str | None = None
) -> ToolResult[GuardReport]:
    """TL-10: hard limits and duplicate configurations before simulation (limits.yaml)."""
    guard = DesignGuard.from_config(config_dir)
    guard.signatures.update(request.prior_signatures)
    results = guard.validate_batch(
        (candidate, request.configs.get(candidate.id)) for candidate in request.candidates
    )
    report = GuardReport(results=results)
    failed = report.failed_ids
    summary = f"{TOOL_ID}: {len(results) - len(failed)}/{len(results)} approved"
    if failed:
        first = next(r for r in results if not r.approved)
        summary += f"; rejected {', '.join(failed)} — {first.candidate_id}: {first.reason}"
    return ToolResult[GuardReport](
        data=report,
        summary=summary[:300],
        warnings=[w for r in results for w in r.warnings][:10],
        assumption_ids=["DB-40"],
        sources=["config/limits.yaml", "DB-10…DB-17", "DB-36", "Plant Debugger ActionGuard"],
    )


__all__ = [
    "TOOL_ID",
    "DesignGuard",
    "GuardReport",
    "GuardRequest",
    "GuardResult",
    "GuardViolation",
    "design_guard",
]
