from __future__ import annotations

import hashlib
import json
from typing import Any

from ..schemas import GuardResult, IncidentRecord
from ..tools.simulator import SimulatorClient


class ActionGuard:
    ALLOWED_ACTIONS = {
        "set_rpm_delta_pct": {"min": -10.0, "max": 0.0},
        "open_bypass_pct": {"min": 0.0, "max": 20.0},
        "switch_standby": {},
        "reset_to_previous": {},
    }

    def __init__(self, simulator: SimulatorClient) -> None:
        self.simulator = simulator

    async def validate_and_execute(
        self,
        *,
        incident: IncidentRecord,
        step_no: int,
        arguments: dict[str, Any],
        prior_signatures: set[str],
    ) -> GuardResult:
        preview = await self.validate(
            incident=incident,
            step_no=step_no,
            arguments=arguments,
            prior_signatures=prior_signatures,
        )
        if not preview.approved or not preview.action_id or not preview.normalized_action:
            return preview
        result = await self.simulator.execute_action(
            action_id=preview.action_id,
            incident_id=incident.incident_id,
            step_no=step_no,
            action=preview.normalized_action,
        )
        return preview.model_copy(update={"execution_result": result})

    async def validate(
        self,
        *,
        incident: IncidentRecord,
        step_no: int,
        arguments: dict[str, Any],
        prior_signatures: set[str],
    ) -> GuardResult:
        action_name = str(arguments.get("action", ""))
        if action_name not in self.ALLOWED_ACTIONS:
            return GuardResult(
                approved=False,
                status="REJECTED",
                reason=f"Action '{action_name or '<missing>'}' is not allow-listed.",
            )
        try:
            normalized = await self._normalize(action_name, arguments)
        except ValueError as exc:
            return GuardResult(approved=False, status="REJECTED", reason=str(exc))
        signature = self.signature(normalized)
        if signature in prior_signatures:
            return GuardResult(
                approved=False,
                status="REJECTED",
                normalized_action=normalized,
                reason="The same action and parameters already failed under unchanged conditions.",
            )
        action_id = self.action_id(incident.incident_id, step_no, normalized)
        return GuardResult(
            approved=True,
            status="APPROVED",
            action_id=action_id,
            normalized_action=normalized,
            reason="Action is allow-listed, within limits, distinct, and reversible in the demo.",
        )

    async def _normalize(self, action_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if action_name in {"set_rpm_delta_pct", "open_bypass_pct"}:
            if "delta_pct" not in arguments:
                raise ValueError(f"{action_name} requires delta_pct")
            delta = float(arguments["delta_pct"])
            limits = self.ALLOWED_ACTIONS[action_name]
            if delta < limits["min"] or delta > limits["max"]:
                raise ValueError(
                    f"{action_name} delta_pct={delta:g} is outside "
                    f"[{limits['min']:g}, {limits['max']:g}]"
                )
            return {"action": action_name, "delta_pct": round(delta, 3)}
        if action_name == "switch_standby":
            state = await self.simulator.get_public_state()
            if not state.standby_ready:
                raise ValueError("Standby pump is not ready")
            return {"action": action_name, "enabled": bool(arguments.get("enabled", True))}
        if action_name == "reset_to_previous":
            return {"action": action_name}
        raise ValueError(f"Unsupported action: {action_name}")

    @staticmethod
    def signature(normalized_action: dict[str, Any]) -> str:
        return json.dumps(normalized_action, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def action_id(incident_id: str, step_no: int, normalized_action: dict[str, Any]) -> str:
        raw = f"{incident_id}:{step_no}:{ActionGuard.signature(normalized_action)}"
        return hashlib.sha256(raw.encode()).hexdigest()
