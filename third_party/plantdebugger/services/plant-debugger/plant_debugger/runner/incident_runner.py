from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ..agent import DecisionContext, DecisionEngine
from ..persistence import IncidentStore
from ..safety import ActionGuard
from ..schemas import (
    AlertEvent,
    EventType,
    IncidentEvent,
    IncidentRecord,
    IncidentRunResult,
    IncidentStatus,
    TelemetryResponse,
    VerifyResult,
)
from ..tools import DeterministicVerifier, RunbookSearch, SimulatorClient

logger = logging.getLogger(__name__)


class IncidentRunner:
    def __init__(
        self,
        *,
        store: IncidentStore,
        simulator: SimulatorClient,
        decision_engine: DecisionEngine,
        runbooks: RunbookSearch,
        guard: ActionGuard,
        verifier: DeterministicVerifier,
        max_steps: int = 6,
        max_control_actions: int = 4,
    ) -> None:
        self.store = store
        self.simulator = simulator
        self.decision_engine = decision_engine
        self.runbooks = runbooks
        self.guard = guard
        self.verifier = verifier
        self.max_steps = max_steps
        self.max_control_actions = max_control_actions
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def handle_incident(self, event: AlertEvent) -> IncidentRunResult:
        incident, created = await self.store.get_or_create_incident(event)
        if not created:
            logger.info(
                "duplicate_event_ignored",
                extra={
                    "incident_id": incident.incident_id,
                    "event_id": event.event_id,
                    "trace_id": event.trace_id,
                },
            )
            return IncidentRunResult(
                incident_id=incident.incident_id,
                status=incident.status,
                duplicate=True,
                summary="Duplicate event acknowledged without a second actuation.",
            )

        lock = await self._get_lock(incident.incident_id)
        async with lock:
            try:
                return await self._run_new_incident(incident, event)
            except Exception as exc:
                logger.exception(
                    "incident_runner_unhandled_error",
                    extra={
                        "incident_id": incident.incident_id,
                        "event_id": event.event_id,
                        "trace_id": event.trace_id,
                    },
                )
                await self._append(
                    incident,
                    EventType.ERROR,
                    step_no=incident.current_step,
                    payload={"error": type(exc).__name__, "message": str(exc)},
                )
                return await self._escalate(
                    incident,
                    reason=f"Unhandled runner failure: {type(exc).__name__}",
                    step_no=incident.current_step,
                )

    async def _run_new_incident(
        self, incident: IncidentRecord, event: AlertEvent
    ) -> IncidentRunResult:
        await self._append(
            incident,
            EventType.ALERT,
            step_no=0,
            payload={
                "rule": event.rule,
                "snapshot": event.snapshot,
                "message": "Flow low + vibration high detected; autonomous workflow started.",
            },
        )
        telemetry = await self.simulator.get_telemetry(window_s=8.0, samples=12)
        current = telemetry.samples[-1]
        incident = await self.store.update_incident(
            incident.incident_id,
            status=IncidentStatus.INVESTIGATING,
            health_before=current.health_score,
            health_after=current.health_score,
        )
        await self._append_observation(incident, telemetry, step_no=0)

        query = self._runbook_query(event, telemetry)
        snippets = self.runbooks.search(query, limit=3)
        await self._append(
            incident,
            EventType.RUNBOOK,
            step_no=0,
            payload={
                "query": query,
                "sources": [snippet.as_dict() for snippet in snippets],
                "message": f"Loaded {len(snippets)} bounded runbook snippets.",
            },
        )

        for step_no in range(1, self.max_steps + 1):
            incident = await self.store.update_incident(
                incident.incident_id,
                status=IncidentStatus.INVESTIGATING,
                current_step=step_no,
            )
            telemetry = await self.simulator.get_telemetry(window_s=5.0, samples=8)
            await self._append_observation(incident, telemetry, step_no=step_no)
            events = await self.store.list_events(incident.incident_id)
            context = self._build_context(incident, telemetry, events, snippets)

            try:
                decision = await self.decision_engine.decide(context)
            except Exception as exc:
                await self._append(
                    incident,
                    EventType.ERROR,
                    step_no=step_no,
                    payload={
                        "error": "decision_engine_failure",
                        "message": str(exc),
                    },
                )
                return await self._escalate(
                    incident,
                    reason="Decision engine failed after bounded retry/fallback.",
                    step_no=step_no,
                )

            await self._append(
                incident,
                EventType.HYPOTHESIS,
                step_no=step_no,
                payload={
                    "assessment": decision.assessment,
                    "hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
                    "next_step": decision.next_step.model_dump(mode="json"),
                    "decision_source": getattr(self.decision_engine, "last_source", "unknown"),
                    "message": decision.assessment,
                },
            )

            next_step = decision.next_step
            if next_step.kind == "act":
                if incident.control_action_count >= self.max_control_actions:
                    return await self._escalate(
                        incident,
                        reason="Control-action limit reached.",
                        step_no=step_no,
                    )
                before = telemetry.samples[-1]
                await self._append(
                    incident,
                    EventType.ACTION_REQUEST,
                    step_no=step_no,
                    payload={
                        "arguments": next_step.arguments,
                        "expected_observation": next_step.expected_observation,
                        "reason": next_step.reason,
                        "message": self._action_message(next_step.arguments),
                    },
                )
                prior_signatures = self._prior_action_signatures(events)
                guard_result = await self.guard.validate_and_execute(
                    incident=incident,
                    step_no=step_no,
                    arguments=next_step.arguments,
                    prior_signatures=prior_signatures,
                )
                await self._append(
                    incident,
                    EventType.ACTION_RESULT,
                    step_no=step_no,
                    payload={
                        **guard_result.model_dump(mode="json"),
                        "message": (
                            f"Action executed: {self._action_message(guard_result.normalized_action or {})}"
                            if guard_result.approved
                            else f"Action rejected: {guard_result.reason}"
                        ),
                    },
                )
                if not guard_result.approved:
                    continue

                incident = await self.store.update_incident(
                    incident.incident_id,
                    status=IncidentStatus.VERIFYING,
                    control_action_count=incident.control_action_count + 1,
                )
                verify, post_telemetry = await self.verifier.wait_and_verify(before)
                await self._append_verify(incident, verify, step_no)
                incident = await self.store.update_incident(
                    incident.incident_id,
                    health_after=verify.health_after,
                )
                if verify.resolved:
                    return await self._resolve(incident, verify, step_no)
                continue

            if next_step.kind == "resolve":
                latest_verify = self._latest_verify(events)
                if latest_verify and latest_verify.get("resolved") is True:
                    verify = VerifyResult.model_validate(latest_verify)
                    return await self._resolve(incident, verify, step_no)
                await self._append(
                    incident,
                    EventType.ERROR,
                    step_no=step_no,
                    payload={
                        "error": "premature_resolve_rejected",
                        "message": "Resolve was rejected because deterministic verification is not healthy.",
                    },
                )
                continue

            if next_step.kind == "observe":
                if next_step.tool == "wait_and_verify":
                    incident = await self.store.update_incident(
                        incident.incident_id, status=IncidentStatus.VERIFYING
                    )
                    verify, _ = await self.verifier.wait_and_verify(telemetry.samples[-1])
                    await self._append_verify(incident, verify, step_no)
                    incident = await self.store.update_incident(
                        incident.incident_id, health_after=verify.health_after
                    )
                    if verify.resolved:
                        return await self._resolve(incident, verify, step_no)
                    continue
                if next_step.tool == "search_runbook":
                    requested_query = str(next_step.arguments.get("query", "")).strip()
                    requested_query = requested_query or self._runbook_query(event, telemetry)
                    snippets = self.runbooks.search(requested_query, limit=3)
                    await self._append(
                        incident,
                        EventType.RUNBOOK,
                        step_no=step_no,
                        payload={
                            "query": requested_query,
                            "sources": [snippet.as_dict() for snippet in snippets],
                            "message": (
                                f"Loaded {len(snippets)} additional bounded runbook snippets."
                            ),
                        },
                    )
                    continue
                if next_step.tool == "get_recent_telemetry":
                    window_s = self._bounded_float(
                        next_step.arguments.get("window_s"), default=8.0, minimum=3.0, maximum=30.0
                    )
                    samples = self._bounded_int(
                        next_step.arguments.get("samples"), default=12, minimum=4, maximum=30
                    )
                    requested_telemetry = await self.simulator.get_telemetry(
                        window_s=window_s,
                        samples=samples,
                    )
                    await self._append_observation(
                        incident,
                        requested_telemetry,
                        step_no=step_no,
                    )
                    continue
                continue

            if next_step.kind == "escalate":
                return await self._escalate(
                    incident,
                    reason=next_step.reason,
                    step_no=step_no,
                )

        return await self._escalate(
            incident,
            reason=f"Step limit reached ({self.max_steps}).",
            step_no=self.max_steps,
        )

    async def _resolve(
        self, incident: IncidentRecord, verify: VerifyResult, step_no: int
    ) -> IncidentRunResult:
        now = datetime.now(UTC)
        summary = (
            f"Recovered P-101 after {incident.control_action_count} guarded actions. "
            f"Health {incident.health_before:.1f} -> {verify.health_after:.1f}; "
            f"flow {verify.after['flow']:.1f}, vibration {verify.after['vibration']:.1f}."
        )
        incident = await self.store.update_incident(
            incident.incident_id,
            status=IncidentStatus.RESOLVED,
            resolved_at=now,
            health_after=verify.health_after,
            final_summary=summary,
        )
        await self._append(
            incident,
            EventType.RESOLVE,
            step_no=step_no,
            payload={
                "summary": summary,
                "before": verify.before,
                "after": verify.after,
                "stable_samples": verify.stable_samples,
                "message": "Recovery verified; incident closed.",
            },
        )
        logger.info(
            "incident_resolved",
            extra={
                "incident_id": incident.incident_id,
                "event_id": incident.trigger_event_id,
                "trace_id": incident.trace_id,
                "step_no": step_no,
            },
        )
        return IncidentRunResult(
            incident_id=incident.incident_id,
            status=IncidentStatus.RESOLVED,
            summary=summary,
        )

    async def _escalate(
        self, incident: IncidentRecord, *, reason: str, step_no: int
    ) -> IncidentRunResult:
        summary = f"Incident escalated safely: {reason}"
        incident = await self.store.update_incident(
            incident.incident_id,
            status=IncidentStatus.ESCALATED,
            final_summary=summary,
        )
        await self._append(
            incident,
            EventType.ESCALATE,
            step_no=step_no,
            payload={"reason": reason, "message": summary},
        )
        return IncidentRunResult(
            incident_id=incident.incident_id,
            status=IncidentStatus.ESCALATED,
            summary=summary,
        )

    async def _append_observation(
        self, incident: IncidentRecord, telemetry: TelemetryResponse, *, step_no: int
    ) -> None:
        current = telemetry.samples[-1]
        await self._append(
            incident,
            EventType.OBSERVE,
            step_no=step_no,
            payload={
                "current": current.model_dump(mode="json"),
                "controls": telemetry.controls,
                "message": (
                    f"Observed flow {current.flow:.1f}, vibration {current.vibration:.1f}, "
                    f"dP {current.differential_pressure:.1f}, health {current.health_score:.0f}."
                ),
            },
        )

    async def _append_verify(
        self, incident: IncidentRecord, verify: VerifyResult, step_no: int
    ) -> None:
        await self._append(
            incident,
            EventType.VERIFY,
            step_no=step_no,
            payload={
                **verify.model_dump(mode="json"),
                "message": verify.summary,
            },
        )

    async def _append(
        self,
        incident: IncidentRecord,
        event_type: EventType,
        *,
        step_no: int,
        payload: dict[str, Any],
    ) -> IncidentEvent:
        event = IncidentEvent(
            event_id=f"iev-{uuid.uuid4().hex}",
            incident_id=incident.incident_id,
            ts=datetime.now(UTC),
            type=event_type,
            step_no=step_no,
            payload=payload,
            trace_id=incident.trace_id,
        )
        await self.store.append_event(event)
        return event

    def _build_context(
        self,
        incident: IncidentRecord,
        telemetry: TelemetryResponse,
        events: list[IncidentEvent],
        snippets: list[Any],
    ) -> DecisionContext:
        current = telemetry.samples[-1]
        previous = telemetry.samples[max(0, len(telemetry.samples) - 4)]
        compact_history = [
            {
                "type": event.type.value,
                "step_no": event.step_no,
                "payload": self._compact_payload(event),
            }
            for event in events
            if event.type
            in {
                EventType.HYPOTHESIS,
                EventType.ACTION_RESULT,
                EventType.VERIFY,
                EventType.ERROR,
            }
        ][-14:]
        return DecisionContext(
            incident={
                "incident_id": incident.incident_id,
                "asset_id": incident.asset_id,
                "status": incident.status.value,
                "current_step": incident.current_step,
                "control_action_count": incident.control_action_count,
                "max_steps": self.max_steps,
                "max_control_actions": self.max_control_actions,
                "trigger_rule": incident.trigger_payload.get("rule"),
            },
            telemetry={
                "current": current.model_dump(mode="json"),
                "recent_delta": {
                    "flow": round(current.flow - previous.flow, 3),
                    "vibration": round(current.vibration - previous.vibration, 3),
                    "differential_pressure": round(
                        current.differential_pressure - previous.differential_pressure, 3
                    ),
                    "motor_current": round(current.motor_current - previous.motor_current, 3),
                    "temperature": round(current.temperature - previous.temperature, 3),
                    "health_score": round(current.health_score - previous.health_score, 3),
                },
                "controls": telemetry.controls,
                "asset_state": telemetry.asset_state,
                "baseline": telemetry.baseline,
            },
            history=compact_history,
            runbook_snippets=[snippet.as_dict() for snippet in snippets],
            safety={
                "allowed_actions": {
                    "set_rpm_delta_pct": {"delta_pct": [-10, 0]},
                    "open_bypass_pct": {"delta_pct": [0, 20]},
                    "switch_standby": {"requires": "standby_ready=true"},
                    "reset_to_previous": {},
                },
                "shutdown_exposed": False,
                "deterministic_verifier_required": True,
            },
        )

    @staticmethod
    def _compact_payload(event: IncidentEvent) -> dict[str, Any]:
        payload = event.payload
        if event.type == EventType.HYPOTHESIS:
            return {
                "assessment": payload.get("assessment"),
                "hypotheses": payload.get("hypotheses"),
                "next_step": payload.get("next_step"),
            }
        if event.type == EventType.ACTION_RESULT:
            return {
                "approved": payload.get("approved"),
                "status": payload.get("status"),
                "normalized_action": payload.get("normalized_action"),
                "reason": payload.get("reason"),
            }
        if event.type == EventType.VERIFY:
            return {
                "resolved": payload.get("resolved"),
                "health_before": payload.get("health_before"),
                "health_after": payload.get("health_after"),
                "stable_samples": payload.get("stable_samples"),
                "deltas": payload.get("deltas"),
                "after": payload.get("after"),
            }
        return {"message": payload.get("message"), "error": payload.get("error")}

    @staticmethod
    def _prior_action_signatures(events: list[IncidentEvent]) -> set[str]:
        signatures: set[str] = set()
        for event in events:
            if event.type != EventType.ACTION_RESULT:
                continue
            action = event.payload.get("normalized_action")
            if action and event.payload.get("approved"):
                signatures.add(ActionGuard.signature(action))
        return signatures

    @staticmethod
    def _latest_verify(events: list[IncidentEvent]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.type == EventType.VERIFY:
                return event.payload
        return None

    @staticmethod
    def _runbook_query(event: AlertEvent, telemetry: TelemetryResponse) -> str:
        current = telemetry.samples[-1]
        return (
            f"{event.rule} low flow high vibration differential pressure "
            f"{current.differential_pressure:.1f} temperature {current.temperature:.1f} "
            "cavitation valve obstruction bearing degradation safe action"
        )

    @staticmethod
    def _action_message(arguments: dict[str, Any]) -> str:
        action = arguments.get("action", "unknown")
        if action == "set_rpm_delta_pct":
            return f"Reduce RPM by {abs(float(arguments.get('delta_pct', 0))):g}%"
        if action == "open_bypass_pct":
            return f"Open bypass by {float(arguments.get('delta_pct', 0)):g}%"
        if action == "switch_standby":
            return "Switch to standby pump"
        if action == "reset_to_previous":
            return "Roll back previous action"
        return str(action)

    async def _get_lock(self, incident_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(incident_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[incident_id] = lock
            return lock

    @staticmethod
    def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))
