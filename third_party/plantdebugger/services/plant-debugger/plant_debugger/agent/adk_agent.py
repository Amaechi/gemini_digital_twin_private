from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import suppress
from typing import Any

from .prompt import SYSTEM_PROMPT
from .schemas import DecisionContext, NextStepDecision


class AdkDecisionEngine:
    """Google ADK-backed structured decision engine.

    Imports are deliberately lazy: local policy mode and unit tests remain runnable without GCP
    packages. In Cloud Run, install google-adk and set GOOGLE_GENAI_USE_VERTEXAI=TRUE.
    """

    def __init__(self, *, model: str, project: str, location: str) -> None:
        self.model_name = model
        self.project = project
        self.location = location
        self._initialized = False
        self._runner: Any | None = None
        self._session_service: Any | None = None
        self._types: Any | None = None
        self._app_name = "plant_debugger"
        self._output_key = "next_step_decision"
        # InMemorySessionService is used only as an ephemeral transport for one model turn.
        # Serialize calls within a Cloud Run instance so its mutable in-process session map is
        # never accessed by overlapping incident requests. Durable workflow state remains in
        # Firestore and is provided explicitly in every DecisionContext.
        self._run_lock = asyncio.Lock()
        self.last_source = "adk"

    def _initialize(self) -> None:
        if self._initialized:
            return
        if not self.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for DECISION_ENGINE=adk")
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.location)
        try:
            from google.adk import Runner
            from google.adk.agents import LlmAgent
            from google.adk.sessions import InMemorySessionService
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - requires ADK dependency
            raise RuntimeError("google-adk is not installed") from exc

        agent = LlmAgent(
            name="plant_debugger",
            model=self.model_name,
            description="Chooses the next bounded troubleshooting step for an industrial incident.",
            instruction=SYSTEM_PROMPT,
            output_schema=NextStepDecision,
            output_key=self._output_key,
        )
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            app_name=self._app_name,
            agent=agent,
            session_service=self._session_service,
        )
        self._types = types
        self._initialized = True

    async def decide(self, context: DecisionContext) -> NextStepDecision:
        async with self._run_lock:
            return await self._decide_serialized(context)

    async def _decide_serialized(self, context: DecisionContext) -> NextStepDecision:
        self._initialize()
        assert self._runner is not None
        assert self._session_service is not None
        assert self._types is not None

        user_id = "incident-runner"
        session_id = f"decision-{uuid.uuid4().hex}"
        await self._session_service.create_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
        )
        try:
            prompt = (
                "Select exactly one next step from the following incident context.\n\n"
                + context.model_dump_json(indent=2)
            )
            message = self._types.Content(role="user", parts=[self._types.Part(text=prompt)])
            parsed = await self._run_and_parse(
                user_id=user_id,
                session_id=session_id,
                message=message,
            )
            if parsed is not None:
                self.last_source = "adk"
                return parsed

            # One bounded repair turn. The runner still applies its own fallback/escalation policy
            # if this second response cannot be validated.
            schema = json.dumps(NextStepDecision.model_json_schema(), separators=(",", ":"))
            repair = self._types.Content(
                role="user",
                parts=[
                    self._types.Part(
                        text=(
                            "Your previous response did not validate. Return only one JSON object "
                            "matching this schema, with no Markdown or commentary: " + schema
                        )
                    )
                ],
            )
            parsed = await self._run_and_parse(
                user_id=user_id,
                session_id=session_id,
                message=repair,
            )
            if parsed is not None:
                self.last_source = "adk_repaired"
                return parsed
            raise RuntimeError("ADK returned no valid NextStepDecision after one repair turn")
        finally:
            # Every turn receives its complete context from Firestore-backed workflow state, so
            # retaining the ephemeral ADK session would only create an in-process memory leak.
            with suppress(Exception):
                await self._session_service.delete_session(
                    app_name=self._app_name,
                    user_id=user_id,
                    session_id=session_id,
                )

    async def _run_and_parse(
        self, *, user_id: str, session_id: str, message: Any
    ) -> NextStepDecision | None:
        """Run one ADK turn and parse both event output and persisted output_key state.

        ADK normally exposes the final response in event content. Because an ``output_key`` is
        configured as well, the same result can also be persisted into the session state. Reading
        both paths makes the integration resilient to event-shape differences across ADK releases
        without weakening schema validation.
        """

        candidates = await self._collect_candidates(
            user_id=user_id,
            session_id=session_id,
            message=message,
        )
        session_candidate = await self._get_session_output_candidate(
            user_id=user_id,
            session_id=session_id,
        )
        if session_candidate is not None:
            candidates.append(session_candidate)
        return self._parse_candidates(candidates)

    async def _collect_candidates(
        self, *, user_id: str, session_id: str, message: Any
    ) -> list[Any]:
        assert self._runner is not None
        candidates: list[Any] = []
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            output = getattr(event, "output", None)
            if output is not None:
                candidates.append(output)
            content = getattr(event, "content", None)
            if content and getattr(content, "parts", None):
                for part in content.parts:
                    text = getattr(part, "text", None)
                    if text:
                        candidates.append(text)
        return candidates

    async def _get_session_output_candidate(
        self, *, user_id: str, session_id: str
    ) -> Any | None:
        assert self._session_service is not None
        session = await self._session_service.get_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            return None
        state = getattr(session, "state", None)
        if state is None:
            return None
        if hasattr(state, "get"):
            return state.get(self._output_key)
        if hasattr(state, "to_dict"):
            return state.to_dict().get(self._output_key)
        return None

    @classmethod
    def _parse_candidates(cls, candidates: list[Any]) -> NextStepDecision | None:
        for candidate in reversed(candidates):
            parsed = cls._parse_candidate(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_candidate(candidate: Any) -> NextStepDecision | None:
        if isinstance(candidate, NextStepDecision):
            return candidate
        if isinstance(candidate, dict):
            try:
                return NextStepDecision.model_validate(candidate)
            except Exception:
                return None
        if isinstance(candidate, str):
            text = candidate.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:].lstrip()
            try:
                return NextStepDecision.model_validate(json.loads(text))
            except Exception:
                return None
        return None
