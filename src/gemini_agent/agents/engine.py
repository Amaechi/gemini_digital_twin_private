# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/agent/decision_engine.py @ d69507a. Modified by
# GeoAgent team, 2026-11-01: ADK engine replaced by Pydantic AI role agents on a local Ollama model
# (NativeOutput + output validators, <= 2 retries, UsageLimits); PolicyEngine covers phases
# P0–P10 and Decision Record choices; every LLM call goes through the LLM logger.
"""Decision engines: deterministic policy, Pydantic AI role agents and a resilient fallback.

``PolicyEngine``, ``LLMEngine`` and ``ResilientEngine`` (which falls back to the policy) follow
spec §5.1, §5.4, AG-03 and AG-12.

An engine chooses *within* a phase (next steps) or *among* workflow-offered options (Decision
Records). It never computes numbers and never marks a design ACCEPTED.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

from gemini_agent.agents.roles import ROLES
from gemini_agent.logging.llm_logger import LLMCallRecord, LLMLogger
from gemini_agent.prompts.system import choice_prompt, decision_prompt, system_prompt
from gemini_agent.schemas.config import LLMConfig
from gemini_agent.schemas.decision import (
    KIND_TOOLS,
    Decision,
    DecisionContext,
    DecisionQuestion,
    DecisionSource,
    NextStep,
    OptionChoice,
    Phase,
    RejectedOption,
    Role,
    StepKind,
)

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

T = TypeVar("T")


@dataclass
class EngineResult(Generic[T]):
    """An engine's output plus where it came from and what it cost."""

    value: T
    source: DecisionSource
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    error: str | None = None


class EngineFailure(RuntimeError):
    """The LLM produced no valid output after the bounded retries (AG-03)."""

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Keep the tokens the failed call used, so the phase budget counts them."""
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class EngineBudgetExceeded(RuntimeError):
    """A per-phase token/request budget was exceeded inside an LLM call (AG-04)."""

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Keep the tokens the call used, for the escalation summary."""
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def kind_for_tool(tool: str) -> StepKind:
    """Step kind that a tool needs (inverse of ``KIND_TOOLS``; default ``call_tool``)."""
    for kind, tools in KIND_TOOLS.items():
        if tools is not None and tool in tools:
            return kind  # type: ignore[return-value]
    return "call_tool"


class DecisionEngine(ABC):
    """Interface used by the workflow runner."""

    name: str = "engine"

    @abstractmethod
    def decide(self, context: DecisionContext) -> EngineResult[Decision]:
        """Next 1–3 steps for the current phase."""

    @abstractmethod
    def choose(
        self, question: DecisionQuestion, context: DecisionContext
    ) -> EngineResult[OptionChoice]:
        """One eligible option for a Decision Record."""


# --------------------------------------------------------------------------- policy
class PolicyEngine(DecisionEngine):
    """Deterministic engine for every phase (AG-12): no LLM, same public context as the agents.

    decide: P0 refuses off-topic requests; otherwise call the next required tool, then ask about
    each open gap, then finalise. choose: the option a deterministic tool ranking or the register
    default marked ``recommended``; else the first eligible option.
    """

    name = "policy"

    def decide(self, context: DecisionContext) -> EngineResult[Decision]:
        """Return the next step: refuse, next required tool, next open gap, or finalise."""
        phase = context.phase
        if phase == Phase.P0 and not context.in_scope:
            step = NextStep(
                kind="refuse",
                rationale=f"Off-topic request: {context.scope_reason}"[:300],
                expected_outcome="User is redirected to the geothermal heating/cooling design.",
            )
        elif context.required_tools_remaining:
            tool = context.required_tools_remaining[0]
            step = NextStep(
                kind=kind_for_tool(tool),
                tool=tool,
                args=dict(context.default_args.get(tool, {})),
                rationale=f"{tool} is required in {phase.value} and has not run yet.",
                expected_outcome=f"{tool} result recorded as evidence.",
            )
        elif context.open_gaps:
            gap = context.open_gaps[0]
            step = NextStep(
                kind="request_input",
                tool="request_missing_input",
                args={"assumption_id": gap},
                rationale=f"{gap} is an open design-basis gap; ask or record the default.",
                expected_outcome=f"Answer or labelled assumption for {gap}.",
            )
        else:
            step = NextStep(
                kind="finalise",
                rationale=f"All required {phase.value} steps are complete.",
                expected_outcome=f"Workflow moves past {phase.value}.",
            )
        return EngineResult(Decision(phase=phase, steps=[step]), source="policy")

    def choose(
        self, question: DecisionQuestion, context: DecisionContext
    ) -> EngineResult[OptionChoice]:
        """Return the recommended eligible option, else the first eligible one."""
        eligible = [option for option in question.options if option.eligible]
        chosen = next((option for option in eligible if option.recommended), eligible[0])
        if chosen.recommended:
            why = "Recommended by the deterministic tool ranking or the register default."
            other = "Ranked below the recommended option."
        else:
            why = "No ranking evidence yet; first eligible option in the offered order."
            other = "Not first in the offered order; no ranking evidence to prefer it."
        rejected = [
            RejectedOption(
                option_id=option.id,
                reason=(f"Not eligible: {option.notes}" if not option.eligible else other)[:300],
            )
            for option in question.options
            if option.id != chosen.id
        ]
        choice = OptionChoice(
            dr_id=question.dr_id, option_id=chosen.id, rationale=why, rejected=rejected
        )
        return EngineResult(choice, source="policy")


# --------------------------------------------------------------------------- LLM
@dataclass
class AgentDeps:
    """Run-time data the output validators need."""

    phase: Phase
    allowed_tools: tuple[str, ...]
    in_scope: bool = True
    open_gaps: tuple[str, ...] = ()
    validate_args: Callable[[str, dict[str, Any]], Any] | None = None
    dr_id: str | None = None
    option_ids: tuple[str, ...] = ()
    eligible_ids: tuple[str, ...] = ()


def check_decision(deps: AgentDeps, decision: Decision) -> str | None:
    """Return an error message for the model, or None if the decision is acceptable."""
    if decision.phase != deps.phase:
        return f"phase must be {deps.phase.value}, got {decision.phase.value}"
    for step in decision.steps:
        if step.tool is not None and step.tool not in deps.allowed_tools:
            return f"tool '{step.tool}' is not in your tool list {list(deps.allowed_tools)}"
        if step.kind == "refuse" and deps.phase != Phase.P0:
            return "refuse is only allowed in P0"
        if step.kind == "refuse" and deps.in_scope:
            return "the request is in scope (deterministic scope check); do not refuse"
        if step.kind == "request_input":
            gap = step.args.get("assumption_id")
            if gap not in deps.open_gaps:
                return f"request_input needs args.assumption_id in OPEN_GAPS {list(deps.open_gaps)}"
        elif step.tool is not None and deps.validate_args is not None:
            try:
                deps.validate_args(step.tool, dict(step.args))
            except ValueError as exc:
                return str(exc)
    return None


def check_choice(deps: AgentDeps, choice: OptionChoice) -> str | None:
    """Return an error message for the model, or None if the choice is acceptable."""
    if choice.dr_id != deps.dr_id:
        return f"dr_id must be {deps.dr_id}"
    if choice.option_id not in deps.eligible_ids:
        return f"option_id must be one of the eligible ids {list(deps.eligible_ids)}"
    unknown = [r.option_id for r in choice.rejected if r.option_id not in deps.option_ids]
    if unknown:
        return f"rejected option ids {unknown} are not offered"
    return None


def build_ollama_model(
    llm_config: LLMConfig, kind: Literal["orchestrator", "coder"] = "orchestrator", tag: str = ""
) -> Any:
    """Pydantic AI model for a local Ollama tag via its OpenAI-compatible endpoint.

    Constructing the model makes no network call; requests only happen on ``run``.
    """
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    profile = llm_config.active()
    model_tag = tag or (profile.coder if kind == "coder" else profile.orchestrator)
    provider = OllamaProvider(base_url=llm_config.ollama.base_url)
    return OllamaModel(model_tag, provider=provider)


def _messages_json(messages: list[Any]) -> list[Any]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    try:
        return list(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
    except Exception:  # noqa: BLE001  # pragma: no cover - logging must never break a run
        return [str(message) for message in messages]


_RETRY_PARTS = frozenset({"RetryPromptPart", "RetryFeedbackPart"})
"""Pydantic AI part classes that carry a validation error back to the model."""


def _last_feedback(messages: list[Any]) -> str:
    """Text of the last validation error sent back to the model ('' if none)."""
    for message in reversed(messages):
        for part in reversed(getattr(message, "parts", [])):
            if type(part).__name__ in _RETRY_PARTS:
                content = getattr(part, "content", "")
                return (content if isinstance(content, str) else str(content))[:300]
    return ""


def _retries_and_tool_calls(messages: list[Any]) -> tuple[int, list[dict[str, Any]]]:
    from pydantic_ai.messages import ToolCallPart

    retries = 0
    calls: list[dict[str, Any]] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if type(part).__name__ in _RETRY_PARTS:
                retries += 1
            elif isinstance(part, ToolCallPart):
                calls.append({"tool": part.tool_name, "args": part.args_as_dict()})
    return retries, calls


@dataclass
class LLMEngine(DecisionEngine):
    """Pydantic AI role agents (NativeOutput, validators, bounded retries, usage limits).

    ``model`` / ``coder_model`` override the Ollama models (tests pass ``TestModel`` or
    ``FunctionModel``; nothing here opens a network connection unless a real model is used).
    """

    llm_config: LLMConfig
    model: Any = None
    coder_model: Any = None
    logger: LLMLogger | None = None
    run_id: str = ""
    validate_args: Callable[[str, dict[str, Any]], Any] | None = None
    name: str = "llm"
    _agents: dict[tuple[Role, str], Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Every LLM call must be logged (MEM-04): a logger is mandatory."""
        if self.logger is None:
            raise ValueError("LLMEngine needs an LLMLogger: every LLM call is logged (MEM-04)")

    def _model_for(self, role: Role) -> Any:
        if ROLES[role].model == "coder":
            if self.coder_model is None:
                self.coder_model = self.model or build_ollama_model(self.llm_config, "coder")
            return self.coder_model
        if self.model is None:
            self.model = build_ollama_model(self.llm_config, "orchestrator")
        return self.model

    def _agent(self, role: Role, kind: Literal["decision", "choice"]) -> Any:
        from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

        key = (role, kind)
        if key in self._agents:
            return self._agents[key]
        output_type: type[BaseModel] = Decision if kind == "decision" else OptionChoice
        agent: Agent[AgentDeps, Any] = Agent(
            output_type=NativeOutput(output_type),
            instructions=system_prompt(role),
            deps_type=AgentDeps,
            retries=self.llm_config.max_output_retries,
            name=f"{role.value}.{kind}",
        )

        @agent.output_validator
        def _validate(ctx: RunContext[AgentDeps], output: Any) -> Any:
            error = (
                check_decision(ctx.deps, output)
                if isinstance(output, Decision)
                else check_choice(ctx.deps, output)
            )
            if error:
                raise ModelRetry(error)
            return output

        self._agents[key] = agent
        return agent

    def _run(
        self,
        role: Role,
        kind: Literal["decision", "choice"],
        prompt: str,
        deps: AgentDeps,
        budget: dict[str, int],
    ) -> EngineResult[Any]:
        from pydantic_ai import UsageLimits, capture_run_messages
        from pydantic_ai.exceptions import UsageLimitExceeded

        model = self._model_for(role)
        agent = self._agent(role, kind)
        tokens_left = budget.get("tokens")
        if tokens_left is not None and tokens_left <= 0:
            raise EngineBudgetExceeded(f"{role.value} {kind}: no tokens left in the phase budget")
        limits = UsageLimits(
            request_limit=self.llm_config.max_output_retries + 2,
            total_tokens_limit=tokens_left,
        )
        ollama = self.llm_config.ollama
        settings = {
            "temperature": self.llm_config.temperatures.planning,
            "seed": self.llm_config.seed,
            # Best effort: Ollama's OpenAI endpoint may ignore these; the server must also run
            # with OLLAMA_CONTEXT_LENGTH >= num_ctx (spec §5.4.7, PROGRESS L-1 note).
            "extra_body": {
                "keep_alive": ollama.keep_alive,
                "options": {"num_ctx": ollama.num_ctx},
            },
        }
        model_name = str(getattr(model, "model_name", type(model).__name__))
        start = time.perf_counter()
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    prompt, model=model, deps=deps, usage_limits=limits, model_settings=settings
                )
            except UsageLimitExceeded as exc:
                _, tok_in, tok_out = self._log(
                    role, deps.phase, model_name, messages, None, start, "escalated", str(exc)
                )
                raise EngineBudgetExceeded(f"{role.value} {kind}: {exc}", tok_in, tok_out) from exc
            except Exception as exc:  # any LLM failure falls back to the policy (AG-03)
                error = f"{type(exc).__name__}: {exc}"
                feedback = _last_feedback(list(messages))
                if feedback:
                    error = f"{error}; last validation error: {feedback}"
                _, tok_in, tok_out = self._log(
                    role, deps.phase, model_name, messages, None, start, "policy_fallback", error
                )
                raise EngineFailure(f"{role.value} {kind}: {error}", tok_in, tok_out) from exc
        usage = result.usage
        retries, _, _ = self._log(
            role,
            deps.phase,
            model_name,
            result.all_messages(),
            result.output,
            start,
            "llm",
            None,
            usage=usage,
        )
        return EngineResult(
            result.output,
            source="llm",
            input_tokens=int(usage.input_tokens or 0),
            output_tokens=int(usage.output_tokens or 0),
            retries=retries,
        )

    def _log(
        self,
        role: Role,
        phase: Phase,
        model_name: str,
        messages: list[Any],
        output: Any,
        start: float,
        source: str,
        error: str | None,
        usage: Any = None,
    ) -> tuple[int, int, int]:
        """Log one call; return (retries, input tokens, output tokens)."""
        retries, calls = _retries_and_tool_calls(list(messages))
        input_tokens = output_tokens = requests = 0
        if usage is not None:
            input_tokens = int(usage.input_tokens or 0)
            output_tokens = int(usage.output_tokens or 0)
            requests = int(usage.requests or 0)
        else:
            for message in messages:
                msg_usage = getattr(message, "usage", None)
                if msg_usage is not None:
                    input_tokens += int(getattr(msg_usage, "input_tokens", 0) or 0)
                    output_tokens += int(getattr(msg_usage, "output_tokens", 0) or 0)
                    requests += 1
        if self.logger is None:
            return retries, input_tokens, output_tokens
        self.logger.log(
            LLMCallRecord(
                run_id=self.run_id,
                agent=role.value,
                phase=phase.value,
                model=model_name,
                messages=_messages_json(list(messages)),
                response=output.model_dump(mode="json") if isinstance(output, BaseModel) else None,
                tool_calls=calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                requests=requests,
                latency_s=round(time.perf_counter() - start, 4),
                retries=retries,
                decision_source=source,
                error=error,
            )
        )
        return retries, input_tokens, output_tokens

    def decide(self, context: DecisionContext) -> EngineResult[Decision]:
        """Run the phase role's decision agent on the compact context."""
        deps = AgentDeps(
            phase=context.phase,
            allowed_tools=tuple(brief.name for brief in context.allowed_tools),
            in_scope=context.in_scope,
            open_gaps=tuple(context.open_gaps),
            validate_args=self.validate_args,
        )
        return self._run(
            context.role, "decision", decision_prompt(context), deps, context.budget_left
        )

    def choose(
        self, question: DecisionQuestion, context: DecisionContext
    ) -> EngineResult[OptionChoice]:
        """Run the DR role's choice agent on the workflow-offered options."""
        deps = AgentDeps(
            phase=question.phase,
            allowed_tools=(),
            dr_id=question.dr_id,
            option_ids=tuple(option.id for option in question.options),
            eligible_ids=tuple(option.id for option in question.options if option.eligible),
        )
        return self._run(
            question.role, "choice", choice_prompt(question, context), deps, context.budget_left
        )


class ResilientEngine(DecisionEngine):
    """Primary LLM engine with deterministic fallback (Plant Debugger ``ResilientDecisionEngine``).

    ``EngineFailure`` → PolicyEngine output recorded as ``policy_fallback``. Budget overruns are
    not masked: they propagate so the runner escalates (AG-04).
    """

    def __init__(self, primary: DecisionEngine, fallback: DecisionEngine | None = None) -> None:
        """Wrap ``primary``; ``fallback`` defaults to a new ``PolicyEngine``."""
        self.primary = primary
        self.fallback = fallback or PolicyEngine()
        self.name = f"{primary.name}+policy_fallback"

    def decide(self, context: DecisionContext) -> EngineResult[Decision]:
        """Primary decision, or the fallback's on ``EngineFailure``."""
        try:
            return self.primary.decide(context)
        except EngineFailure as exc:
            result = self.fallback.decide(context)
            return EngineResult(
                result.value,
                source="policy_fallback",
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                error=str(exc)[:500],
            )

    def choose(
        self, question: DecisionQuestion, context: DecisionContext
    ) -> EngineResult[OptionChoice]:
        """Primary choice, or the fallback's on ``EngineFailure``."""
        try:
            return self.primary.choose(question, context)
        except EngineFailure as exc:
            result = self.fallback.choose(question, context)
            return EngineResult(
                result.value,
                source="policy_fallback",
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                error=str(exc)[:500],
            )


def create_decision_engine(
    engine: Literal["policy", "llm"],
    llm_config: LLMConfig,
    *,
    logger: LLMLogger | None = None,
    run_id: str = "",
    model: Any = None,
    coder_model: Any = None,
    model_tag: str = "",
    validate_args: Callable[[str, dict[str, Any]], Any] | None = None,
) -> DecisionEngine:
    """Engine factory (Plant Debugger ``create_decision_engine``)."""
    if engine == "policy":
        return PolicyEngine()
    if engine == "llm":
        if logger is None:
            raise ValueError("engine 'llm' needs an LLMLogger: every LLM call is logged (MEM-04)")
        if model is None and model_tag:
            model = build_ollama_model(llm_config, "orchestrator", model_tag)
        primary = LLMEngine(
            llm_config,
            model=model,
            coder_model=coder_model,
            logger=logger,
            run_id=run_id,
            validate_args=validate_args,
        )
        return ResilientEngine(primary, PolicyEngine())
    raise ValueError(f"unsupported engine: {engine!r}")
