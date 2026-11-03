"""Model Builder agent: gap → reference card → model + test → pytest → register (spec §5.1, §7.1).

The coder (a Pydantic AI agent on the ``coder`` model profile, spec §4.3) returns a
``ModelDraft``: model code, a reference-example test (REF-02) and the REFERENCE.md copied from the
card (REF-01). Everything else is deterministic: the ``ModelBuilder`` writes the files through the
sandboxed TL-18 tools, runs the tests in a subprocess and feeds failures back to the coder (at
most ``max_attempts`` drafts). Only a passing pytest run on unchanged files registers the class,
and only in the in-memory registry: the LLM never decides that a model is valid.

With ``--engine policy`` there is no coder: P4 calls ``model_gap_analysis`` only and missing
types stay as reported gaps. S7 (spec §12): :func:`run_s7_demo` removes a component type from a
COPY of the registry, detects the gap, builds the model in a sandbox and registers it in the copy.
Tests drive it with a ``FunctionModel``; the real-LLM demo runs locally::

    PYTHONPATH=src python -m gemini_agent.agents.model_builder --sandbox runs/s7_demo

The coder call uses the engine's settings path (``engine.model_settings``) with the
``calls.model_builder`` limits of ``config/llm.yaml`` (long answers: 1200 s, 8192 tokens by
default). A timed-out draft is a failed attempt that the loop retries up to ``max_attempts``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from gemini_agent.logging.llm_logger import LLMCallRecord, LLMLogger
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import LLMConfig, load_config
from gemini_agent.tools.model_builder_tools import (
    ALLOWED_MODEL_IMPORTS,
    ALLOWED_TEST_IMPORTS,
    DEFAULT_PYTEST_TIMEOUT_S,
    MAX_FILE_BYTES,
    REQUIRED_OUTPUTS,
    MissingComponent,
    ModelBuilderError,
    ModelGapReport,
    ModelSandbox,
    PytestReport,
    RegistrationRecord,
    check_sandbox_root,
    model_file_paths,
    model_gap_analysis,
    read_reference_card,
    register_model,
    run_model_tests,
    screen_python,
)

AGENT_NAME = "model_builder"
CLASS_NAME_PATTERN = r"^[A-Z][A-Za-z0-9]{2,63}$"
FEEDBACK_CHARS = 2000
DEFAULT_TOKEN_BUDGET = 40_000
"""P4 phase budget (``runner/phases.py``): 40 k tokens."""

CODER_INSTRUCTIONS = (
    "You are the Model Builder of a geothermal digital twin (GEMINI). Write ONE new GEMINI "
    "component model from the reference card in the request, as JSON matching ModelDraft.\n"
    "Rules:\n"
    "- Implement only equations, defaults and limits written on the card, in your own words; "
    "cite the card id and sections in docstrings (REF-01, REF-03). Never invent coefficients.\n"
    "- model_code: one class named class_name that subclasses "
    "gemini_model.model_abstract.StaticModel; __init__ calls super().__init__() and sets "
    "self.parameters (card defaults, SI units, unit-suffixed keys such as _degc, _k, _w) and "
    "self.output = {}; calculate_output(self, u, x=None) reads inputs from the dict u, raises "
    "ValueError on invalid inputs and sets self.output including 'power_th' (W), 'power_el' (W) "
    "and 'emission' (kg/s).\n"
    "- test_code: pytest tests that import the class from gemini_model.<model_name>.<model_name>; "
    "at least one test reproduces the card's worked example or validation test within the "
    "stated tolerance (REF-02).\n"
    "- reference_md: the card content the model uses, starting with the card id.\n"
    "- Only the allowed imports; no file, network, process or exec access.\n"
    "- If feedback from a failed test run is given, fix the cause and return all three files."
)


class CoderError(RuntimeError):
    """The coder produced no valid draft (budget, validation or model error).

    ``timed_out`` marks a request that hit ``request_timeout_s`` (or the HTTP timeout): the
    build loop retries those up to ``max_attempts``; every other failure ends the build.
    """

    def __init__(
        self,
        message: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        *,
        timed_out: bool = False,
    ) -> None:
        """Keep the tokens spent before the failure."""
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.timed_out = timed_out


def is_timeout(exc: BaseException) -> bool:
    """Return True for a pydantic-ai request deadline or HTTP timeout (the loop retries).

    ``ModelRequestTimeout`` is the ``request_timeout`` deadline, ``ModelTimeoutError`` the HTTP
    ``timeout`` of one attempt (pydantic-ai 2.64); neither subclasses ``TimeoutError``.
    """
    from pydantic_ai.exceptions import ModelRequestTimeout, ModelTimeoutError

    return isinstance(exc, (ModelRequestTimeout, ModelTimeoutError))


# --------------------------------------------------------------------------- schemas
class ModelDraft(StrictModel):
    """What the coder returns: three files (the builder writes the two ``__init__.py``)."""

    class_name: str = Field(pattern=CLASS_NAME_PATTERN, description="Must equal the request's.")
    model_code: str = Field(max_length=MAX_FILE_BYTES, description="src/gemini_model/<n>/<n>.py")
    test_code: str = Field(max_length=MAX_FILE_BYTES, description="test_<n>.py (pytest)")
    reference_md: str = Field(max_length=MAX_FILE_BYTES, description="REFERENCE.md from the card")
    notes: str = Field(default="", max_length=600)


class ModelBuildRequest(StrictModel):
    """Everything the coder sees for one draft (no other context)."""

    component_type: str
    model_name: str
    class_name: str
    reference_card: str
    card_text: str
    gm_id: str | None = None
    needed_by: list[str] = Field(default_factory=list)
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    feedback: str = Field(default="", description="Why the previous draft failed.")
    files: dict[str, str] = Field(description="Where each file is written (sandbox-relative).")
    allowed_model_imports: list[str]
    allowed_test_imports: list[str]


@dataclass
class CoderReply:
    """A validated draft and what it cost."""

    draft: ModelDraft
    source: str = "llm"
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0


Coder = Callable[[ModelBuildRequest], CoderReply]
"""Anything that turns a request into a validated draft (``LLMCoder`` in production)."""


class BuildAttempt(StrictModel):
    """One draft → write → pytest (→ register) cycle."""

    attempt: int
    decision_source: str
    written: list[str] = Field(default_factory=list)
    pytest: PytestReport | None = None
    error: str | None = None
    timed_out: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_s: float = Field(default=0.0, ge=0, description="Coder call wall time.")


class ModelBuildResult(StrictModel):
    """Outcome of building one missing component type."""

    component_type: str
    model_name: str
    class_name: str
    reference_card: str | None
    status: Literal["registered", "tests_failed", "coder_failed", "not_buildable", "no_coder"]
    attempts: list[BuildAttempt] = Field(default_factory=list)
    registration: RegistrationRecord | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    summary: str = Field(max_length=300)


class ModelBuilderRun(StrictModel):
    """Gap analysis before and after the builds."""

    before: ModelGapReport
    results: list[ModelBuildResult]
    after: ModelGapReport
    closed: list[str]
    still_missing: list[str]


class S7DemoReport(StrictModel):
    """Scenario S7: a cooling model removed from the registry is rebuilt (spec §12)."""

    removed_type: str
    gap_detected: bool
    gap_closed: bool
    registered_class: str | None
    sandbox: str
    run: ModelBuilderRun


@dataclass
class S7Demo:
    """S7 report plus the registry copy the model was registered in."""

    report: S7DemoReport
    registry: dict[str, Any]


# --------------------------------------------------------------------------- draft checks
def class_name_for(model_name: str) -> str:
    """Return the class name of a model folder ('free_cooling_rc07' → 'FreeCoolingRc07')."""
    return "".join(part[:1].upper() + part[1:] for part in model_name.split("_") if part)


def _string_constants(tree: ast.AST) -> set[str]:
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def check_draft(request: ModelBuildRequest, draft: ModelDraft) -> str | None:
    """Return why a draft is unusable (sent back to the coder), or None."""
    if draft.class_name != request.class_name:
        return f"class_name must be '{request.class_name}', got '{draft.class_name}'"
    try:
        screen_python(draft.model_code, "model")
        screen_python(draft.test_code, "test")
    except ModelBuilderError as exc:
        return f"code refused: {exc}"
    model_tree = ast.parse(draft.model_code)
    if not any(
        isinstance(node, ast.ClassDef) and node.name == draft.class_name for node in model_tree.body
    ):
        return f"model_code must define class {draft.class_name} at module level"
    absent = [key for key in REQUIRED_OUTPUTS if key not in _string_constants(model_tree)]
    if absent:
        return f"model output must include {', '.join(absent)} (W, W, kg/s)"
    test_tree = ast.parse(draft.test_code)
    tests = [
        node
        for node in ast.walk(test_tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    if not tests:
        return "test_code has no test_ function (REF-02 needs a reference-example test)"
    module = f"gemini_model.{request.model_name}.{request.model_name}"
    if not any(
        isinstance(node, ast.ImportFrom) and node.module == module for node in ast.walk(test_tree)
    ):
        return f"test_code must import the model with 'from {module} import {draft.class_name}'"
    if request.reference_card not in draft.reference_md:
        return f"reference_md must cite {request.reference_card} (REF-01)"
    return None


def coder_prompt(request: ModelBuildRequest) -> str:
    """User prompt for one draft: the request as JSON."""
    payload = request.model_dump(mode="json")
    return (
        f"Write the GEMINI model '{request.model_name}' (class {request.class_name}) for "
        f"component type '{request.component_type}' from {request.reference_card}. "
        f"Attempt {request.attempt} of {request.max_attempts}.\n"
        f"BUILD REQUEST:\n{json.dumps(payload, ensure_ascii=False)}"
    )


# --------------------------------------------------------------------------- LLM coder
@dataclass
class LLMCoder:
    """Pydantic AI coder agent (NativeOutput, output validator, retries, usage limits, logged).

    ``model`` is a Pydantic AI model (tests: ``FunctionModel``/``TestModel``); None builds the
    Ollama ``coder`` model of the active profile. Every call is logged (MEM-04). Model settings
    come from ``engine.model_settings`` with ``calls.model_builder`` (``config/llm.yaml``).
    """

    llm_config: LLMConfig
    logger: LLMLogger
    model: Any = None
    run_id: str = ""
    phase: str = "P4"
    token_budget: int | None = DEFAULT_TOKEN_BUDGET
    tokens_used: int = 0
    _agent: Any = field(default=None, repr=False)

    def model_name(self) -> str:
        """Name of the coder model (built on first use)."""
        model = self._get_model()
        return str(getattr(model, "model_name", type(model).__name__))

    def _get_model(self) -> Any:
        if self.model is None:
            from gemini_agent.agents import engine

            self.model = engine.build_ollama_model(self.llm_config, "coder")
        return self.model

    @property
    def call_limits(self) -> Any:
        """``calls.model_builder`` of ``config/llm.yaml`` (timeout, output cap, thinking)."""
        return self.llm_config.calls.model_builder

    def model_settings(self) -> dict[str, Any]:
        """Return the settings of one coder call (engine path, Model Builder limits)."""
        from gemini_agent.agents import engine

        return engine.model_settings(
            self.llm_config,
            self.call_limits,
            temperature=self.llm_config.temperatures.extraction,
        )

    def _get_agent(self) -> Any:
        from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

        if self._agent is not None:
            return self._agent
        agent: Agent[ModelBuildRequest, ModelDraft] = Agent(
            output_type=NativeOutput(ModelDraft),
            instructions=CODER_INSTRUCTIONS,
            deps_type=ModelBuildRequest,
            retries=self.llm_config.max_output_retries,
            name=f"{AGENT_NAME}.coder",
        )

        @agent.output_validator
        def _validate(ctx: RunContext[ModelBuildRequest], output: ModelDraft) -> ModelDraft:
            error = check_draft(ctx.deps, output)
            if error:
                raise ModelRetry(error)
            return output

        self._agent = agent
        return agent

    def _log(
        self,
        model_name: str,
        messages: Sequence[Any],
        output: BaseModel | None,
        usage: Any,
        start: float,
        source: str,
        error: str | None,
    ) -> LLMCallRecord:
        record = LLMCallRecord.from_messages(
            run_id=self.run_id,
            agent=AGENT_NAME,
            phase=self.phase,
            model=model_name,
            messages=list(messages),
            output=output,
            usage=usage,
            latency_s=time.perf_counter() - start,
            decision_source=source,
            error=error,
        )
        self.logger.log(record)
        return record

    def __call__(self, request: ModelBuildRequest) -> CoderReply:
        """Return a validated draft or raise ``CoderError``."""
        from pydantic_ai import UsageLimits, capture_run_messages

        left = None if self.token_budget is None else self.token_budget - self.tokens_used
        if left is not None and left <= 0:
            raise CoderError("model builder token budget exhausted")
        model = self._get_model()
        agent = self._get_agent()
        settings = self.model_settings()
        limits = UsageLimits(
            request_limit=self.llm_config.max_output_retries + 2, total_tokens_limit=left
        )
        model_name = self.model_name()
        start = time.perf_counter()
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    coder_prompt(request),
                    model=model,
                    deps=request,
                    usage_limits=limits,
                    model_settings=settings,
                )
            except Exception as exc:  # timeouts are retried by the loop; anything else ends it
                error = f"{type(exc).__name__}: {exc}"[:400]
                timed_out = is_timeout(exc)
                if timed_out:
                    error = (
                        f"{error} (after {time.perf_counter() - start:.0f} s; "
                        f"calls.model_builder.request_timeout_s = "
                        f"{self.call_limits.request_timeout_s:g})"
                    )[:500]
                # No response was captured, so the record counts 0 tokens even though the
                # server processed the prompt; the budget is charged on answered calls only.
                record = self._log(model_name, messages, None, None, start, "failed", error)
                self.tokens_used += record.total_tokens
                raise CoderError(
                    error, record.input_tokens, record.output_tokens, timed_out=timed_out
                ) from exc
        record = self._log(
            model_name, result.all_messages(), result.output, result.usage, start, "llm", None
        )
        self.tokens_used += record.total_tokens
        return CoderReply(
            draft=result.output,
            source="llm",
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            retries=record.retries,
        )


# --------------------------------------------------------------------------- builder
def _feedback(report: PytestReport) -> str:
    head = report.error or (
        f"{report.failures} failed, {report.errors} errors of {report.tests} tests: "
        f"{', '.join(report.failed_tests[:5])}"
    )
    return f"{head}\n{report.output_tail[-FEEDBACK_CHARS:]}"[:FEEDBACK_CHARS]


def _init_text(component_type: str, card: str, *, test: bool = False) -> str:
    what = "Tests of the" if test else "The"
    return (
        f'"""{what} {component_type} model written by the GeoAgent Model Builder from {card}."""\n'
    )


@dataclass
class ModelBuilder:
    """Deterministic build loop around a coder (spec §5.1 Model Builder, §7.1)."""

    sandbox: ModelSandbox
    coder: Coder | None
    registry: MutableMapping[str, Any] | None = None
    max_attempts: int = 3
    pytest_timeout_s: float = DEFAULT_PYTEST_TIMEOUT_S

    def build(
        self,
        gap: MissingComponent,
        *,
        model_name: str | None = None,
        class_name: str | None = None,
    ) -> ModelBuildResult:
        """Build, test and register one missing component type."""
        name = model_name or gap.suggested_model_name
        cls_name = class_name or class_name_for(name)
        common = {
            "component_type": gap.component_type,
            "model_name": name,
            "class_name": cls_name,
            "reference_card": gap.reference_card,
        }
        if not gap.buildable or gap.reference_card is None:
            return ModelBuildResult(
                status="not_buildable",
                summary=f"{gap.component_type}: no reference card; gap stays open (escalate).",
                **common,
            )
        if self.coder is None:
            return ModelBuildResult(
                status="no_coder",
                summary=f"{gap.component_type}: no coder (policy engine); gap reported only.",
                **common,
            )
        if not re.fullmatch(CLASS_NAME_PATTERN, cls_name):
            raise ModelBuilderError(f"class name must match {CLASS_NAME_PATTERN}, got {cls_name!r}")
        self.sandbox.check_model_name(name)
        card = read_reference_card(gap.reference_card).data
        paths = model_file_paths(name)
        attempts: list[BuildAttempt] = []
        tokens = [0, 0]
        feedback = ""
        registration: RegistrationRecord | None = None
        status: Literal["registered", "tests_failed", "coder_failed"] = "tests_failed"
        for number in range(1, self.max_attempts + 1):
            request = ModelBuildRequest(
                component_type=gap.component_type,
                model_name=name,
                class_name=cls_name,
                reference_card=card.card_id,
                card_text=card.text,
                gm_id=gap.gm_id,
                needed_by=gap.needed_by,
                attempt=number,
                max_attempts=self.max_attempts,
                feedback=feedback,
                files=dict(paths),
                allowed_model_imports=sorted(ALLOWED_MODEL_IMPORTS),
                allowed_test_imports=sorted(ALLOWED_TEST_IMPORTS),
            )
            started = time.perf_counter()
            try:
                reply = self.coder(request)
            except CoderError as exc:
                tokens[0] += exc.input_tokens
                tokens[1] += exc.output_tokens
                attempts.append(
                    BuildAttempt(
                        attempt=number,
                        decision_source="failed",
                        error=str(exc),
                        timed_out=exc.timed_out,
                        input_tokens=exc.input_tokens,
                        output_tokens=exc.output_tokens,
                        elapsed_s=time.perf_counter() - started,
                    )
                )
                status = "coder_failed"
                if exc.timed_out:  # a timeout is one failed attempt; the next draft retries
                    feedback = (
                        f"Attempt {number} timed out before a complete answer: {exc}. "
                        "Answer with compact code and tests (no prose outside the JSON)."
                    )
                    continue
                break
            tokens[0] += reply.input_tokens
            tokens[1] += reply.output_tokens
            status = "tests_failed"  # a draft arrived; a timed-out earlier attempt no longer counts
            attempt = BuildAttempt(
                attempt=number,
                decision_source=reply.source,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                elapsed_s=time.perf_counter() - started,
            )
            attempts.append(attempt)
            error = check_draft(request, reply.draft)
            if error is not None:  # a coder that skipped validation still cannot write
                attempt.error = error
                feedback = error
                continue
            files = {
                "model_init": _init_text(gap.component_type, card.card_id),
                "model": reply.draft.model_code,
                "reference": reply.draft.reference_md,
                "test_init": _init_text(gap.component_type, card.card_id, test=True),
                "test": reply.draft.test_code,
            }
            try:
                for kind, content in files.items():
                    attempt.written.append(self.sandbox.write(paths[kind], content).path)
            except ModelBuilderError as exc:
                attempt.error = f"write refused: {exc}"
                feedback = attempt.error
                continue
            report = run_model_tests(self.sandbox, name, timeout_s=self.pytest_timeout_s)
            attempt.pytest = report
            if not report.passed:
                feedback = _feedback(report)
                attempt.error = report.error or "tests failed"
                continue
            try:
                registration = register_model(
                    self.sandbox,
                    name,
                    component_type=gap.component_type,
                    class_name=cls_name,
                    registry=self.registry,
                )
            except ModelBuilderError as exc:
                attempt.error = f"registration refused: {exc}"
                feedback = attempt.error
                continue
            status = "registered"
            break
        last = attempts[-1] if attempts else None
        if status == "registered" and registration is not None:
            summary = (
                f"{gap.component_type}: {cls_name} registered at runtime after "
                f"{len(attempts)} draft(s); {registration.tests_passed} tests passed."
            )
        else:
            reason = (last.error if last else None) or status
            summary = (
                f"{gap.component_type}: not registered after {len(attempts)} draft(s): {reason}"
            )
        return ModelBuildResult(
            status=status,
            attempts=attempts,
            registration=registration,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            summary=summary[:300],
            **common,
        )

    def close_gaps(self, candidates: Sequence[Candidate] | None = None) -> ModelBuilderRun:
        """Gap analysis → build every buildable missing type → gap analysis again."""
        before = model_gap_analysis(candidates, registry=self.registry).data
        results = [self.build(gap) for gap in before.missing]
        after = model_gap_analysis(candidates, registry=self.registry).data
        return ModelBuilderRun(
            before=before,
            results=results,
            after=after,
            closed=sorted(set(before.missing_component_types) - set(after.missing_component_types)),
            still_missing=after.missing_component_types,
        )


# --------------------------------------------------------------------------- S7 demo
def run_s7_demo(
    sandbox_root: Path | str,
    coder: Coder | None,
    *,
    removed_type: str = "free_cooling",
    registry: MutableMapping[str, Any] | None = None,
    max_attempts: int = 3,
    pytest_timeout_s: float = DEFAULT_PYTEST_TIMEOUT_S,
) -> S7Demo:
    """Spec §12 S7 on a registry COPY: remove a type, detect the gap, rebuild, register.

    The live ``COMPONENT_REGISTRY`` and the repository are never modified; the model is written
    under a new folder name in ``sandbox_root``.
    """
    if registry is None:
        from gemini_simulator import registry as registry_module

        registry = registry_module.COMPONENT_REGISTRY
    if removed_type not in registry:
        raise ValueError(f"'{removed_type}' is not in the registry")
    reduced = {key: value for key, value in registry.items() if key != removed_type}
    sandbox = ModelSandbox(sandbox_root)
    builder = ModelBuilder(
        sandbox,
        coder,
        registry=reduced,
        max_attempts=max_attempts,
        pytest_timeout_s=pytest_timeout_s,
    )
    run = builder.close_gaps()
    registered = reduced.get(removed_type)
    report = S7DemoReport(
        removed_type=removed_type,
        gap_detected=removed_type in run.before.missing_component_types,
        gap_closed=removed_type in run.closed,
        registered_class=registered.__name__ if registered is not None else None,
        sandbox=str(sandbox.root),
        run=run,
    )
    return S7Demo(report=report, registry=reduced)


def main(argv: Sequence[str] | None = None) -> int:
    """Local real-LLM S7 demo (needs Ollama; never used in tests or cloud sessions)."""
    parser = argparse.ArgumentParser(prog="python -m gemini_agent.agents.model_builder")
    parser.add_argument("--sandbox", default="runs/model_builder_s7", help="new sandbox folder")
    parser.add_argument("--remove", default="free_cooling", help="registry type to rebuild")
    parser.add_argument("--model", default="", help="Ollama coder tag (default: llm.yaml)")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    from gemini_agent.agents import engine

    llm_config = load_config("llm", args.config_dir)
    sandbox_root = check_sandbox_root(Path(args.sandbox))  # before anything is created
    logger = LLMLogger(sandbox_root)
    coder = LLMCoder(
        llm_config=llm_config,
        logger=logger,
        model=engine.build_ollama_model(llm_config, "coder", args.model),
        run_id=sandbox_root.name,
    )
    started = time.perf_counter()
    try:
        demo = run_s7_demo(
            sandbox_root, coder, removed_type=args.remove, max_attempts=args.max_attempts
        )
    finally:
        engine.close_model_clients(coder.model)  # orderly shutdown (no kqueue traceback)
    elapsed = time.perf_counter() - started
    (sandbox_root / "s7_report.json").write_text(
        demo.report.model_dump_json(indent=1), encoding="utf-8"
    )
    for result in demo.report.run.results:
        print(result.summary)
    print(f"S7 gap detected: {demo.report.gap_detected}; closed: {demo.report.gap_closed}")
    if not demo.report.gap_closed:
        print(failure_report(demo.report, coder, elapsed))
    return 0 if demo.report.gap_closed else 1


def failure_report(report: S7DemoReport, coder: LLMCoder, elapsed_s: float) -> str:
    """Why the demo failed: elapsed time, the coder limits in use and every attempt."""
    limits = coder.call_limits
    header = (
        f"FAILED after {elapsed_s:.0f} s (coder {coder.model_name()}; "
        f"calls.model_builder: request_timeout_s = {limits.request_timeout_s:g}, "
        f"max_output_tokens = {limits.max_output_tokens}, thinking = {limits.thinking})"
    )
    lines = [header]
    for result in report.run.results:
        if result.status == "registered":
            continue
        lines.append(
            f"{result.component_type}: {result.status}, {len(result.attempts)} attempt(s), "
            f"{result.input_tokens} in / {result.output_tokens} out tokens"
        )
        for attempt in result.attempts:
            state = "timed out" if attempt.timed_out else attempt.decision_source
            lines.append(
                f"  attempt {attempt.attempt}: {state} after {attempt.elapsed_s:.0f} s "
                f"({attempt.input_tokens} in / {attempt.output_tokens} out): "
                f"{(attempt.error or 'ok')[:200]}"
            )
    timed_out = any(a.timed_out for r in report.run.results for a in r.attempts)
    if timed_out:
        lines.append(
            "Raise calls.model_builder.request_timeout_s in config/llm.yaml (or --max-attempts) "
            "if the model needs longer per draft."
        )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - local demo entry point
    sys.exit(main())
