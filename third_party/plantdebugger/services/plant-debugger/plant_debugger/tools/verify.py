from __future__ import annotations

import asyncio
from statistics import mean

from ..schemas import TelemetryPoint, TelemetryResponse, VerifyResult
from .simulator import SimulatorClient


class DeterministicVerifier:
    FLOW_RATIO = 0.92
    SAFE_VIBRATION = 4.0
    SAFE_CURRENT = 40.0
    SAFE_TEMPERATURE = 70.0
    MIN_STABLE_SAMPLES = 3

    def __init__(self, simulator: SimulatorClient, wait_seconds: float = 2.0) -> None:
        self.simulator = simulator
        self.wait_seconds = max(0.0, wait_seconds)

    async def wait_and_verify(
        self,
        before: TelemetryPoint,
    ) -> tuple[VerifyResult, TelemetryResponse]:
        if self.wait_seconds:
            await asyncio.sleep(self.wait_seconds)
        # Sample only the post-action tail. A wider window can contain healthy current
        # telemetry but too few consecutive post-action points, which would trigger an
        # unnecessary third action instead of simply completing verification.
        tail_window = max(0.25, self.wait_seconds * 0.6)
        telemetry = await self.simulator.get_telemetry(window_s=tail_window, samples=5)
        result = self.evaluate(before=before, telemetry=telemetry)
        return result, telemetry

    @classmethod
    def evaluate(cls, before: TelemetryPoint, telemetry: TelemetryResponse) -> VerifyResult:
        if not telemetry.samples:
            raise ValueError("Verifier requires at least one telemetry sample")
        baseline_flow = float(telemetry.baseline.get("flow", 100.0))
        threshold_flow = cls.FLOW_RATIO * baseline_flow
        stable = [
            point
            for point in telemetry.samples
            if point.flow >= threshold_flow
            and point.vibration <= cls.SAFE_VIBRATION
            and point.motor_current <= cls.SAFE_CURRENT
            and point.temperature <= cls.SAFE_TEMPERATURE
        ]
        # Require consecutive healthy samples at the tail, not any healthy samples in the window.
        stable_tail = 0
        for point in reversed(telemetry.samples):
            healthy = (
                point.flow >= threshold_flow
                and point.vibration <= cls.SAFE_VIBRATION
                and point.motor_current <= cls.SAFE_CURRENT
                and point.temperature <= cls.SAFE_TEMPERATURE
            )
            if not healthy:
                break
            stable_tail += 1
        after = telemetry.samples[-1]
        resolved = stable_tail >= cls.MIN_STABLE_SAMPLES
        before_dict = cls._metrics(before)
        after_dict = cls._metrics(after)
        deltas = {key: round(after_dict[key] - before_dict[key], 3) for key in before_dict}
        if resolved:
            summary = (
                f"Recovery verified across {stable_tail} consecutive samples: flow "
                f"{after.flow:.1f}, vibration {after.vibration:.1f}, current "
                f"{after.motor_current:.1f}."
            )
        else:
            summary = (
                f"Not resolved: {stable_tail} consecutive healthy samples; flow {after.flow:.1f}, "
                f"vibration {after.vibration:.1f}, current {after.motor_current:.1f}."
            )
        return VerifyResult(
            resolved=resolved,
            health_before=before.health_score,
            health_after=after.health_score,
            stable_samples=stable_tail,
            thresholds={
                "flow_min": threshold_flow,
                "vibration_max": cls.SAFE_VIBRATION,
                "motor_current_max": cls.SAFE_CURRENT,
                "temperature_max": cls.SAFE_TEMPERATURE,
            },
            before=before_dict,
            after=after_dict,
            deltas=deltas,
            summary=summary,
        )

    @staticmethod
    def _metrics(point: TelemetryPoint) -> dict[str, float]:
        return {
            "flow": point.flow,
            "vibration": point.vibration,
            "motor_current": point.motor_current,
            "temperature": point.temperature,
            "differential_pressure": point.differential_pressure,
            "health_score": point.health_score,
        }
