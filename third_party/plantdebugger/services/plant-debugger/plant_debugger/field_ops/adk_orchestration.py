from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import FieldPlan, FieldPlanningContext


PLANNER_TEMPERATURE = 0.1

PLANNER_INSTRUCTION = """
You are Plant Debugger, an autonomous industrial troubleshooting planner for a brownfield plant.
Your goal is to restore P-101 safely or stop and escalate when no bounded step is justified.
The field operator is your physical I/O layer, not a decision maker. Never ask the operator to
 diagnose, approve, choose, or modify a step.

AUTONOMY RULES:
- You choose the next physical I/O capability on every turn from available_tools. There is no
  prescribed sequence.
- Do not assume the run must contain a photo, a control action, a failure, or a fixed number of steps.
- Never choose a step because a demo needs a later failure or because you expect a scripted next step.
- Optimize safety, reversibility, expected information gain, and probability of useful recovery.
- Treat manual actions as interventions that change plant state, not as free diagnostic queries.
- Before a manual action, explicitly ask whether a materially plausible hypothesis depends on
  field-visible evidence that telemetry cannot observe, such as external leakage, wetness/pooling,
  flange condition, or surface damage.
- When no QR-verified field image exists and such a field-visible hazard remains materially plausible,
  strongly prefer request_field_image at F-17 before manipulating P-101 or BV-104. This is a safety
  and information-gain preference, not a mandatory sequence.
- Choose a manual action before an image only when current evidence already addresses the field-visible
  hazard, or when the telemetry indicates an urgent condition where delaying a bounded mitigation
  would be less safe than acting now.
- Do not choose a manual action merely because it has direct recovery potential when a non-invasive
  observation can materially reduce uncertainty about whether that action is appropriate.
- After receiving an image, explicitly use the visible findings and limitations to strengthen, weaken,
  or preserve hypotheses before choosing the next capability. Absence of visible leakage weakens an
  active external-leak hypothesis but never proves that hidden/internal leakage is impossible.
- Treat BV-104 as a staged causal experiment. On the first justified bypass intervention, request a
  +10% test movement and use operator confirmation plus the resulting telemetry as evidence. If that
  response is directionally positive but recovery remains below threshold, you may request the remaining
  travel to the marked fully OPEN position. Only that final OPEN step requires position-image evidence.
- For set_rpm_delta_pct used as a diagnostic probe, normally prefer -5% before -10% unless the evidence
  supports the larger bounded reduction as immediate mitigation.
- Treat every completed action, visual observation, and deterministic verification result as new evidence.
- A failed verification is not an automatic escalation; choose another safe useful capability if one remains.
- Do not repeat an equivalent image or control action unless current evidence explains why the repeat adds information.
- Never infer or mention a hidden simulator fault. Use only supplied evidence.
- Keep assessment and reason concise and evidence based. Do not expose private chain-of-thought.

OUTPUT CONTRACT:
- request_field_image => task_kind='INSPECTION', selected_tool='request_field_image', asset_id='F-17', action=null.
- request_manual_action => task_kind='MANUAL_ACTION', selected_tool='request_manual_action', with one exact bounded action from available_tools.
- escalate => task_kind='ESCALATE', selected_tool='escalate', action=null.
- open_bypass_pct must target asset_id='BV-104'. set_rpm_delta_pct must target asset_id='P-101'.
- Populate expected_observation with the physical response that would make the selected experiment informative.
- For a manual action, reason must state why another non-invasive observation would not add enough value first.
Return only one valid FieldPlan object.
""".strip()


class PhysicalIoResume(BaseModel):
    terminal: bool = False
    outcome: Literal["CONTINUE", "RESOLVED", "ESCALATED"] = "CONTINUE"
    observation_summary: str = ""
    next_context: FieldPlanningContext | None = None


class WorkflowTerminal(BaseModel):
    status: Literal["RESOLVED", "ESCALATED"]
    summary: str
    final_plan: FieldPlan | None = None
    steps: int = Field(ge=0)


@dataclass(slots=True)
class WorkflowTransition:
    status: Literal["PAUSED", "COMPLETED"]
    adk_session_id: str
    invocation_id: str | None = None
    interrupt_id: str | None = None
    plan: FieldPlan | None = None
    terminal: WorkflowTerminal | None = None
    node_path: str | None = None


def _content_text(value: Any) -> str | None:
    parts = getattr(value, "parts", None)
    if not parts:
        return None
    texts = [getattr(part, "text", None) for part in parts]
    joined = "".join(text for text in texts if text)
    return joined or None


def _function_response_value(value: Any) -> Any | None:
    parts = getattr(value, "parts", None)
    if not parts:
        return None
    for part in parts:
        response = getattr(part, "function_response", None)
        if response is not None:
            return getattr(response, "response", None)
    return None


def _coerce_context(value: Any) -> FieldPlanningContext:
    if isinstance(value, FieldPlanningContext):
        return value
    if isinstance(value, dict):
        return FieldPlanningContext.model_validate(value)
    if isinstance(value, str):
        return FieldPlanningContext.model_validate_json(value)
    text = _content_text(value)
    if text:
        return FieldPlanningContext.model_validate_json(text)
    raise ValueError(f"ADK workflow received unsupported context input: {type(value)!r}")


def _coerce_plan(value: Any) -> FieldPlan:
    if isinstance(value, FieldPlan):
        return value
    if isinstance(value, dict):
        return FieldPlan.model_validate(value)
    if not isinstance(value, str):
        text = _content_text(value)
        if text:
            value = text
    if isinstance(value, str):
        text = value.strip().strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
        return FieldPlan.model_validate_json(text)
    raise ValueError(f"ADK planner returned unsupported FieldPlan output: {type(value)!r}")


def _coerce_resume(value: Any) -> PhysicalIoResume:
    if isinstance(value, PhysicalIoResume):
        return value
    if not isinstance(value, (dict, str)):
        function_response = _function_response_value(value)
        if function_response is not None:
            value = function_response
        else:
            text = _content_text(value)
            if text:
                value = text
    if isinstance(value, dict):
        nested = value.get("result")
        if isinstance(nested, dict):
            value = nested
        return PhysicalIoResume.model_validate(value)
    if isinstance(value, str):
        return PhysicalIoResume.model_validate_json(value)
    raise ValueError(f"ADK physical I/O node returned unsupported resume payload: {type(value)!r}")


def _validate_plan(plan: FieldPlan) -> FieldPlan:
    action = plan.action.model_dump() if hasattr(plan.action, "model_dump") else plan.action
    if plan.selected_tool == "request_field_image":
        if plan.task_kind != "INSPECTION" or plan.asset_id != "F-17" or action:
            raise ValueError("Invalid request_field_image plan")
        return plan
    if plan.selected_tool == "request_manual_action":
        if plan.task_kind != "MANUAL_ACTION" or not isinstance(action, dict):
            raise ValueError("Invalid request_manual_action plan")
        name = action.get("action")
        if name == "open_bypass_pct" and plan.asset_id == "BV-104":
            return plan
        if name == "set_rpm_delta_pct" and plan.asset_id == "P-101":
            return plan
        raise ValueError("Manual plan does not match an available physical action")
    if plan.selected_tool == "escalate" and plan.task_kind == "ESCALATE" and not action:
        return plan
    raise ValueError("Planner returned an unsupported physical I/O selection")


class AdkDynamicFieldWorkflow:
    """ADK-owned dynamic troubleshooting loop with resumable human physical I/O.

    The FastAPI manager remains a deterministic adapter for QR binding, evidence storage,
    action bounds, and recovery thresholds. It does not choose the next task when this
    workflow is active. ADK owns the iterative route and pauses at RequestInput nodes until
    the operator/plant returns physical evidence.
    """

    source = "adk_dynamic_workflow"
    app_name = "plant_debugger_dynamic"
    user_id = "field-ops-controller"

    def __init__(
        self,
        *,
        model: str,
        session_service,
        max_steps: int,
        planner_node=None,
    ) -> None:
        self.model = model
        self.session_service = session_service
        self.max_steps = max(1, max_steps)
        self._planner_node = planner_node
        self._runner = None
        self._app = None
        self._build_runtime()

    def _build_runtime(self) -> None:
        from google.adk import Context, Event, Runner, Workflow
        from google.adk.agents import LlmAgent
        from google.adk.apps.app import App, ResumabilityConfig
        from google.adk.events import RequestInput
        from google.adk.workflow import node
        from google.genai import types as genai_types

        planner_node = self._planner_node
        if planner_node is None:
            planner_node = LlmAgent(
                name="physical_tool_planner",
                model=self.model,
                description=(
                    "Selects the safest highest-information physical observation, bounded action, "
                    "or escalation from current evidence; non-invasive field evidence has priority "
                    "when it can materially reduce manipulation risk."
                ),
                instruction=PLANNER_INSTRUCTION,
                output_schema=FieldPlan,
                output_key="field_plan",
                generate_content_config=genai_types.GenerateContentConfig(
                    temperature=PLANNER_TEMPERATURE,
                ),
            )

        @node(name="observe_physical_evidence")
        def observe_physical_evidence(node_input: dict[str, Any]) -> dict[str, Any]:
            return node_input

        @node(name="request_field_image", rerun_on_resume=False)
        async def request_field_image(node_input: dict[str, Any]):
            step_no = int(node_input["step_no"])
            yield RequestInput(
                interrupt_id=f"field_image_s{step_no}",
                message="QR-verify the assigned asset and return the requested field image.",
                payload=node_input,
                response_schema=PhysicalIoResume,
            )

        @node(name="request_manual_action", rerun_on_resume=False)
        async def request_manual_action(node_input: dict[str, Any]):
            step_no = int(node_input["step_no"])
            yield RequestInput(
                interrupt_id=f"manual_action_s{step_no}",
                message="Execute the exact bounded field action and return the observed plant result.",
                payload=node_input,
                response_schema=PhysicalIoResume,
            )

        @node(name="troubleshoot_loop", rerun_on_resume=True)
        async def troubleshoot_loop(ctx: Context, node_input: Any):
            context = _coerce_context(node_input)
            while context.step_no <= self.max_steps:
                observed = await ctx.run_node(
                    observe_physical_evidence,
                    node_input=context.model_dump(mode="json"),
                    run_id=f"observe_s{context.step_no}",
                )
                plan_raw = await ctx.run_node(
                    planner_node,
                    node_input=json.dumps(observed, default=str),
                    run_id=f"plan_s{context.step_no}",
                )
                plan = _validate_plan(_coerce_plan(plan_raw))

                if plan.selected_tool == "escalate":
                    yield Event(
                        output=WorkflowTerminal(
                            status="ESCALATED",
                            summary=plan.assessment,
                            final_plan=plan,
                            steps=context.step_no,
                        ).model_dump(mode="json")
                    )
                    return

                io_payload = {
                    "step_no": context.step_no,
                    "selected_tool": plan.selected_tool,
                    "plan": plan.model_dump(mode="json"),
                }
                if plan.selected_tool == "request_field_image":
                    resume_raw = await ctx.run_node(
                        request_field_image,
                        node_input=io_payload,
                        run_id=f"image_s{context.step_no}",
                    )
                else:
                    resume_raw = await ctx.run_node(
                        request_manual_action,
                        node_input=io_payload,
                        run_id=f"action_s{context.step_no}",
                    )

                resume = _coerce_resume(resume_raw)
                if resume.terminal:
                    terminal_status = (
                        "RESOLVED" if resume.outcome == "RESOLVED" else "ESCALATED"
                    )
                    yield Event(
                        output=WorkflowTerminal(
                            status=terminal_status,
                            summary=resume.observation_summary
                            or "Physical troubleshooting loop completed.",
                            final_plan=plan,
                            steps=context.step_no,
                        ).model_dump(mode="json")
                    )
                    return
                if resume.next_context is None:
                    yield Event(
                        output=WorkflowTerminal(
                            status="ESCALATED",
                            summary="Physical I/O returned no next evidence context; stop before guessing.",
                            final_plan=plan,
                            steps=context.step_no,
                        ).model_dump(mode="json")
                    )
                    return
                context = resume.next_context

            yield Event(
                output=WorkflowTerminal(
                    status="ESCALATED",
                    summary=f"ADK dynamic workflow step budget ({self.max_steps}) exhausted.",
                    steps=self.max_steps,
                ).model_dump(mode="json")
            )

        root_agent = Workflow(
            name="plant_debugger_workflow",
            edges=[("START", troubleshoot_loop)],
        )
        self._app = App(
            name=self.app_name,
            root_agent=root_agent,
            resumability_config=ResumabilityConfig(is_resumable=True),
        )
        self._runner = Runner(
            app=self._app,
            session_service=self.session_service,
            auto_create_session=False,
        )

    async def initialize(self) -> None:
        initialize = getattr(self.session_service, "initialize", None)
        if initialize is not None:
            result = initialize()
            if hasattr(result, "__await__"):
                await result

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.close()
        close = getattr(self.session_service, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def start(
        self,
        *,
        field_session_id: str,
        context: FieldPlanningContext,
    ) -> WorkflowTransition:
        from google.genai import types

        adk_session_id = f"adk-{field_session_id}"
        existing = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=adk_session_id,
        )
        if existing is None:
            await self.session_service.create_session(
                app_name=self.app_name,
                user_id=self.user_id,
                session_id=adk_session_id,
                state={"field_session_id": field_session_id},
            )
        message = types.Content(
            role="user",
            parts=[types.Part(text=context.model_dump_json())],
        )
        return await self._run(
            adk_session_id=adk_session_id,
            invocation_id=None,
            new_message=message,
        )

    async def resume(
        self,
        *,
        adk_session_id: str,
        invocation_id: str | None,
        interrupt_id: str,
        result: PhysicalIoResume,
    ) -> WorkflowTransition:
        from google.genai import types

        response = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=interrupt_id,
                        name="adk_request_input",
                        response=result.model_dump(mode="json"),
                    )
                )
            ],
        )
        return await self._run(
            adk_session_id=adk_session_id,
            invocation_id=invocation_id,
            new_message=response,
        )

    async def _run(self, *, adk_session_id: str, invocation_id: str | None, new_message):
        if self._runner is None:
            raise RuntimeError("ADK Dynamic Workflow runner is not initialized")

        paused: WorkflowTransition | None = None
        terminal: WorkflowTerminal | None = None
        latest_invocation_id = invocation_id
        latest_node_path = None

        async for event in self._runner.run_async(
            user_id=self.user_id,
            session_id=adk_session_id,
            invocation_id=invocation_id,
            new_message=new_message,
        ):
            if getattr(event, "invocation_id", None):
                latest_invocation_id = event.invocation_id
            node_info = getattr(event, "node_info", None)
            if node_info is not None and getattr(node_info, "path", None):
                latest_node_path = node_info.path

            ids = list(getattr(event, "long_running_tool_ids", None) or [])
            if ids:
                for call in event.get_function_calls() or []:
                    if call.id not in ids or call.name != "adk_request_input":
                        continue
                    args = call.args or {}
                    payload = args.get("payload") or {}
                    plan_payload = payload.get("plan")
                    if plan_payload is None:
                        raise RuntimeError("ADK RequestInput did not contain a FieldPlan payload")
                    paused = WorkflowTransition(
                        status="PAUSED",
                        adk_session_id=adk_session_id,
                        invocation_id=latest_invocation_id,
                        interrupt_id=call.id,
                        plan=FieldPlan.model_validate(plan_payload),
                        node_path=latest_node_path,
                    )

            output = getattr(event, "output", None)
            if output is not None:
                try:
                    candidate = output
                    if isinstance(candidate, str):
                        candidate = json.loads(candidate)
                    terminal = WorkflowTerminal.model_validate(candidate)
                except Exception:
                    pass

        if paused is not None:
            return paused
        if terminal is not None:
            return WorkflowTransition(
                status="COMPLETED",
                adk_session_id=adk_session_id,
                invocation_id=latest_invocation_id,
                terminal=terminal,
                node_path=latest_node_path,
            )
        raise RuntimeError("ADK Dynamic Workflow produced neither a pause nor a terminal result")
