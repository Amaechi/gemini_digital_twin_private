from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..config import Settings
from .adk_agent import AdkDecisionEngine
from .schemas import DecisionContext, Hypothesis, NextStep, NextStepDecision

logger = logging.getLogger(__name__)


class DecisionEngine(ABC):
    last_source: str = "unknown"

    @abstractmethod
    async def decide(self, context: DecisionContext) -> NextStepDecision: ...


class PolicyDecisionEngine(DecisionEngine):
    """Deterministic offline policy used for tests, local demos, and ADK failure recovery.

    It has no access to hidden simulator state. It only interprets the same public telemetry and
    incident history supplied to the ADK agent.
    """

    async def decide(self, context: DecisionContext) -> NextStepDecision:
        self.last_source = "policy"
        telemetry = context.telemetry
        current = telemetry.get("current", {})
        history = context.history
        action_results = [item for item in history if item.get("type") == "ACTION_RESULT"]
        actions = [
            item
            for item in action_results
            if item.get("payload", {}).get("approved") is True
        ]
        verifies = [item for item in history if item.get("type") == "VERIFY"]

        flow = float(current.get("flow", 0.0))
        vibration = float(current.get("vibration", 0.0))
        dp = float(current.get("differential_pressure", 0.0))
        temperature = float(current.get("temperature", 0.0))

        if verifies and verifies[-1].get("payload", {}).get("resolved") is True:
            return NextStepDecision(
                assessment="Deterministic verification confirms sustained recovery.",
                hypotheses=[
                    Hypothesis(
                        cause="unknown",
                        rank=1,
                        supporting_evidence=["Verifier resolved=true"],
                    )
                ],
                next_step=NextStep(
                    kind="resolve",
                    tool="wait_and_verify",
                    arguments={},
                    expected_observation="Health remains inside all deterministic limits.",
                    reason="Close the incident only after verified recovery.",
                ),
            )

        action_names = [
            item.get("payload", {}).get("normalized_action", {}).get("action") for item in actions
        ]
        current_is_healthy = (
            flow >= 92.0
            and vibration <= 4.0
            and float(current.get("motor_current", 999.0)) <= 40.0
            and temperature <= 70.0
        )
        if actions and current_is_healthy and (not verifies or verifies[-1].get("payload", {}).get("resolved") is not True):
            return NextStepDecision(
                assessment=(
                    "Current telemetry is inside recovery limits, but the prior verification window "
                    "did not yet contain three consecutive healthy samples."
                ),
                hypotheses=[
                    Hypothesis(
                        cause="unknown",
                        rank=1,
                        supporting_evidence=["All current deterministic health limits are satisfied"],
                    )
                ],
                next_step=NextStep(
                    kind="observe",
                    tool="wait_and_verify",
                    arguments={"seconds": 1},
                    expected_observation="Three consecutive healthy samples confirm sustained recovery.",
                    reason="Complete verification without adding another control action.",
                ),
            )
        if not actions:
            if temperature >= 70.0 and vibration >= 6.0:
                return NextStepDecision(
                    assessment=(
                        "High vibration with abnormal temperature is more consistent with rotating "
                        "equipment degradation than a hydraulic restriction."
                    ),
                    hypotheses=[
                        Hypothesis(
                            cause="bearing_degradation",
                            rank=1,
                            supporting_evidence=[
                                f"temperature={temperature:.1f} C",
                                f"vibration={vibration:.1f} mm/s",
                            ],
                        ),
                        Hypothesis(
                            cause="valve_obstruction",
                            rank=2,
                            contradicting_evidence=["Temperature rise is unusually strong"],
                        ),
                    ],
                    next_step=NextStep(
                        kind="act",
                        tool="request_control_action",
                        arguments={"action": "switch_standby", "enabled": True},
                        expected_observation=(
                            "Vibration and temperature should fall while flow returns toward baseline."
                        ),
                        reason="A ready standby pump provides a reversible low-exposure failover.",
                    ),
                )

            return NextStepDecision(
                assessment=(
                    "Low flow and elevated vibration fit both cavitation and valve restriction; "
                    "a small RPM probe is the safest discriminating action."
                ),
                hypotheses=[
                    Hypothesis(
                        cause="cavitation",
                        rank=1,
                        supporting_evidence=[
                            f"flow={flow:.1f} below baseline",
                            f"vibration={vibration:.1f} elevated",
                        ],
                        contradicting_evidence=(
                            [f"differential pressure={dp:.1f} is high"] if dp > 3.2 else []
                        ),
                    ),
                    Hypothesis(
                        cause="valve_obstruction",
                        rank=2,
                        supporting_evidence=(
                            [f"differential pressure={dp:.1f} is elevated"] if dp > 3.2 else []
                        ),
                    ),
                ],
                next_step=NextStep(
                    kind="act",
                    tool="request_control_action",
                    arguments={"action": "set_rpm_delta_pct", "delta_pct": -5.0},
                    expected_observation=(
                        "If cavitation dominates, vibration should drop materially and health should "
                        "recover; a weak response shifts evidence toward restriction."
                    ),
                    reason="Use the lowest-risk reversible probe before opening the bypass.",
                ),
            )

        if "set_rpm_delta_pct" in action_names and "open_bypass_pct" not in action_names:
            last_verify = verifies[-1].get("payload", {}) if verifies else {}
            vib_delta = float(last_verify.get("deltas", {}).get("vibration", 0.0))
            return NextStepDecision(
                assessment=(
                    "The RPM probe did not satisfy recovery thresholds; the weak vibration response "
                    "and elevated differential pressure weaken cavitation and strengthen restriction."
                ),
                hypotheses=[
                    Hypothesis(
                        cause="valve_obstruction",
                        rank=1,
                        supporting_evidence=[
                            f"differential pressure={dp:.1f}",
                            f"post-probe vibration delta={vib_delta:.2f}",
                            "RPM probe did not resolve the incident",
                        ],
                    ),
                    Hypothesis(
                        cause="cavitation",
                        rank=2,
                        contradicting_evidence=["Expected material response to RPM reduction was absent"],
                    ),
                ],
                next_step=NextStep(
                    kind="act",
                    tool="request_control_action",
                    arguments={"action": "open_bypass_pct", "delta_pct": 15.0},
                    expected_observation=(
                        "Flow should return above 92 percent of baseline, differential pressure should "
                        "fall, and vibration should normalize."
                    ),
                    reason="Test and relieve the leading valve-restriction hypothesis.",
                ),
            )

        if "open_bypass_pct" in action_names and "switch_standby" not in action_names:
            return NextStepDecision(
                assessment="Hydraulic actions did not restore a stable healthy state.",
                hypotheses=[
                    Hypothesis(
                        cause="bearing_degradation",
                        rank=1,
                        supporting_evidence=["Hydraulic corrective actions were insufficient"],
                    ),
                    Hypothesis(
                        cause="unknown",
                        rank=2,
                        supporting_evidence=["Residual symptoms remain after two bounded actions"],
                    ),
                ],
                next_step=NextStep(
                    kind="act",
                    tool="request_control_action",
                    arguments={"action": "switch_standby", "enabled": True},
                    expected_observation="Vibration and temperature should normalize on standby pump.",
                    reason="Use the final allow-listed reversible recovery action.",
                ),
            )

        return NextStepDecision(
            assessment="No additional distinct allow-listed action remains within the bounded loop.",
            hypotheses=[
                Hypothesis(
                    cause="unknown",
                    rank=1,
                    supporting_evidence=["All safe candidate actions were exhausted"],
                )
            ],
            next_step=NextStep(
                kind="escalate",
                tool="escalate",
                arguments={"reason": "Safe action set exhausted"},
                expected_observation="A human operator receives the complete incident record.",
                reason="Escalate instead of repeating an ineffective action.",
            ),
        )


class ResilientDecisionEngine(DecisionEngine):
    def __init__(
        self,
        primary: DecisionEngine,
        fallback: DecisionEngine | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_source = "unknown"

    async def decide(self, context: DecisionContext) -> NextStepDecision:
        try:
            result = await self.primary.decide(context)
            self.last_source = getattr(self.primary, "last_source", "primary")
            return result
        except Exception:
            if self.fallback is None:
                raise
            logger.exception("primary_decision_engine_failed_using_policy_fallback")
            result = await self.fallback.decide(context)
            self.last_source = "policy_fallback"
            return result


def create_decision_engine(settings: Settings) -> DecisionEngine:
    policy = PolicyDecisionEngine()
    if settings.decision_engine == "policy":
        return policy
    if settings.decision_engine == "adk":
        adk = AdkDecisionEngine(
            model=settings.gemini_model,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        return ResilientDecisionEngine(adk, policy if settings.adk_fallback_policy else None)
    raise ValueError(f"Unsupported DECISION_ENGINE: {settings.decision_engine}")
