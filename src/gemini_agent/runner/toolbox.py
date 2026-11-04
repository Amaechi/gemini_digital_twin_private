"""Tool catalogue as the workflow sees it (spec §6, §5.4.4).

Each entry has a name, a one-sentence description, a flat argument model, a callable, the
owning WP and whether the current binding is a stub.

Reducer contract (``DesignRunner``): the runner reads these ``ToolResult.data`` fields by tool
name — ``load_design_basis.open_gaps``, ``generate_candidates.candidates``,
``design_guard.checks``, ``simulate_annual.kpis`` / ``economics.kpis`` / ``uncertainty.kpis``
(``KPISet`` list), ``optimise_design.shortlist`` (+ optional ``candidates``: sized designs that
join the run, and ``kpis``), ``robust_backup`` (pre-L-2: ``candidate_id``, the variant in
``candidates`` + ``kpis`` and the DR-11 evidence fields of ``RunState.robust_backup``),
``verify_design.verdict`` / ``violations`` / ``verifier``. Real tools keep these field names.

``default_toolbox`` binds the real tools (WP-14, ``runner/real_tools.py``, ``stub=False``);
the labelled stubs in ``runner/tool_stubs.py`` (``stub_toolbox``) remain for tests that exercise
the workflow mechanics without the engineering tools.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.decision import ToolBrief
from gemini_agent.schemas.results import KPISet


@dataclass(frozen=True)
class ToolContext:
    """Read-only view of the run that a tool may use (no LLM text, only typed state)."""

    run_id: str
    run_dir: Path
    config_dir: Path | None
    candidates: tuple[Candidate, ...] = ()
    kpis: dict[str, KPISet] = field(default_factory=dict)
    shortlist: tuple[str, ...] = ()
    selected_id: str | None = None
    decision_records: tuple[dict[str, Any], ...] = ()
    answers: dict[str, Any] = field(default_factory=dict)
    guard_failed: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def excluded_ids(self) -> frozenset[str]:
        """Candidates that failed DesignGuard or were REJECTED by the verifier."""
        return frozenset(self.guard_failed) | frozenset(self.rejected_ids)


ToolFunc = Callable[[BaseModel, ToolContext], ToolResult[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """One catalogue entry."""

    name: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc | None
    owner: str
    stub: bool = True
    runner_handled: bool = False


class ToolArgsError(ValueError):
    """Arguments failed the tool's input schema (sent back to the engine)."""


# --------------------------------------------------------------------------- argument models
class NoArgs(StrictModel):
    """Tool without arguments."""


class KnowledgeArgs(StrictModel):
    """Arguments of ``knowledge_search``."""

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=3, ge=1, le=5)


class RequestInputArgs(StrictModel):
    """Arguments of ``request_missing_input``."""

    assumption_id: str | None = Field(default=None, description="Register id, e.g. 'DB-31'.")
    question: str | None = Field(default=None, max_length=300)


class DepthArgs(StrictModel):
    """Depth argument (TL-03)."""

    depth_m: float | None = Field(default=None, gt=0, description="None: design-basis depth.")


class FlowArgs(StrictModel):
    """Flow argument (TL-05); bounded to the DB-11 hard limit (``config/limits.yaml``)."""

    flow_m3h: float | None = Field(
        default=None,
        ge=50.0,
        le=300.0,
        description="50–300 m³/h per active doublet (DB-11); None: design-basis maximum.",
    )


class CandidateArgs(StrictModel):
    """Candidate selector."""

    candidate_id: str = Field(default="all", description="Candidate id or 'all'.")


class SimulateArgs(StrictModel):
    """Arguments of ``simulate_annual``."""

    candidate_id: str = Field(default="all", description="Candidate id or 'all'.")
    mode: Literal["fast", "full"] = "fast"


RUNNER_ONLY = "runner_only"
"""``json_schema_extra`` flag of an argument the workflow runner sets itself: left out of the
agent's tool brief, and any value an agent sends is replaced by the runner's."""


class OptimiseArgs(StrictModel):
    """Arguments of ``optimise_design``."""

    top_n: int = Field(default=3, ge=1, le=3, description="Size the top 2–3 candidates.")
    candidate_id: str | None = Field(
        default=None,
        description="Review loop (DR-12): size this adopted design only, at its doublets, depth "
        "and return temperatures; None: the top-ranked concepts. Set by the runner only "
        "(``DesignRunner._pin_args``), never offered to or taken from an agent.",
        json_schema_extra={RUNNER_ONLY: True},
    )


class CardArgs(StrictModel):
    """Reference card id."""

    card_id: str = Field(pattern=r"^RC-\d{2}$")


def _runner_only(info: Any) -> bool:
    extra = info.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get(RUNNER_ONLY))


def _arg_hint(info: Any) -> str:
    if get_origin(info.annotation) is Literal:
        annotation = "one of " + "|".join(str(v) for v in get_args(info.annotation))
    else:
        annotation = getattr(info.annotation, "__name__", None) or str(info.annotation)
        annotation = annotation.replace("typing.", "")
    if info.is_required():
        return f"{annotation} (required)"
    return f"{annotation} = {info.default!r}"


class Toolbox:
    """Name → ``ToolSpec`` registry with argument validation."""

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        """Register the given specs."""
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        """Add or replace a tool binding."""
        self._specs[spec.name] = spec

    def __contains__(self, name: object) -> bool:
        """Return True if a tool with this name is registered."""
        return name in self._specs

    def names(self) -> list[str]:
        """All tool names."""
        return sorted(self._specs)

    def spec(self, name: str) -> ToolSpec:
        """Return the ``ToolSpec`` registered under ``name`` (KeyError if unknown)."""
        return self._specs[name]

    def brief(self, name: str) -> ToolBrief:
        """One-sentence description plus flat argument hints for the agent prompt."""
        spec = self._specs[name]
        args = {
            key: _arg_hint(info)
            for key, info in spec.args_model.model_fields.items()
            if not _runner_only(info)
        }
        return ToolBrief(name=name, description=spec.description, args=args)

    def validate_args(self, name: str, args: dict[str, Any]) -> BaseModel:
        """Validate flat arguments against the tool's input model."""
        spec = self._specs[name]
        try:
            return spec.args_model.model_validate(args)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}"
                for err in exc.errors()[:3]
            )
            raise ToolArgsError(f"{name}: {detail}") from exc

    def execute(self, name: str, args: dict[str, Any], context: ToolContext) -> ToolResult[Any]:
        """Validate arguments and run the tool."""
        spec = self._specs[name]
        if spec.runner_handled or spec.func is None:
            raise ValueError(f"tool '{name}' is handled by the workflow runner")
        parsed = self.validate_args(name, args)
        return spec.func(parsed, context)


def default_toolbox(profile: str | None = None, *, config_dir: Path | None = None) -> Toolbox:
    """Catalogue bound to the real tools (WP-14).

    ``profile`` = search budget of TL-13/TL-15 (register RUN-BUDGET: full / quick / smoke);
    None → the ``GEOAGENT_BUDGET`` environment variable, else ``full``.
    """
    from gemini_agent.runner import real_tools

    return real_tools.real_toolbox(profile, config_dir=config_dir)
