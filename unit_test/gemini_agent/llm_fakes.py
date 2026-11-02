"""Offline fake LLMs for tests (Pydantic AI ``FunctionModel``); never opens a network connection.

``policy_like`` reads the context JSON the runner put in the prompt and answers what the
PolicyEngine would, but through the real agent path (NativeOutput, validators, usage, logger).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from gemini_agent.agents.engine import PolicyEngine
from gemini_agent.schemas.decision import DecisionContext, DecisionQuestion


def user_prompt(messages: list[Any]) -> str:
    """First user prompt of the run."""
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def output_name(info: AgentInfo) -> str:
    """Name of the structured output the agent expects ('Decision' or 'OptionChoice')."""
    output_object = info.model_request_parameters.output_object
    return output_object.name if output_object is not None else ""


def policy_answer(messages: list[Any], info: AgentInfo) -> str:
    """JSON the PolicyEngine would return for this prompt."""
    prompt = user_prompt(messages)
    if output_name(info) == "Decision":
        context = DecisionContext.model_validate(json.loads(prompt.split("CONTEXT:\n", 1)[1]))
        return PolicyEngine().decide(context).value.model_dump_json()
    data = json.loads(prompt.split("DATA:\n", 1)[1])
    question = DecisionQuestion.model_validate(data["question"])
    return PolicyEngine().choose(question, None).value.model_dump_json()  # type: ignore[arg-type]


def policy_like(messages: list[Any], info: AgentInfo) -> ModelResponse:
    """Answer like the policy (valid on the first try)."""
    return ModelResponse(parts=[TextPart(policy_answer(messages, info))])


def kinds_as_call_tool(messages: list[Any], info: AgentInfo) -> ModelResponse:
    """Answer like the policy, but send every tool step as ``call_tool`` (the L-1 kind error)."""
    answer = json.loads(policy_answer(messages, info))
    for step in answer.get("steps", []):
        if step.get("tool"):
            step["kind"] = "call_tool"
    return ModelResponse(parts=[TextPart(json.dumps(answer))])


def always_invalid(messages: list[Any], info: AgentInfo) -> ModelResponse:
    """Answer with output that never validates."""
    return ModelResponse(parts=[TextPart('{"unexpected": "json"}')])


class Counting:
    """Wrap a FunctionModel function and count calls."""

    def __init__(self, func: Any) -> None:
        """Wrap ``func``."""
        self.func = func
        self.calls = 0

    def __call__(self, messages: list[Any], info: AgentInfo) -> ModelResponse:
        """Count and delegate."""
        self.calls += 1
        return self.func(messages, info)


def fake_model(func: Any = policy_like) -> FunctionModel:
    """Return a FunctionModel with a recognisable model name."""
    return FunctionModel(func, model_name="fake-llm")
