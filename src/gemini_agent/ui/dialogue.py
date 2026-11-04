"""Post-run voice dialogue: what-if by voice (post-submission, FINAL_SPRINT §I).

After the P10 approval of a ``cli talk`` run — or when ``cli talk --run-id <id>`` points at a
finished run — the assistant asks "What would you like to change?" and loops, at most
:data:`MAX_ROUNDS` times:

1. **record** the change request (push-to-talk or ``--typed``), echo ``I heard: "…"`` and confirm
   it on the keyboard; "stop" (or nothing usable) ends the dialogue;
2. **map** it with the review loop's Lead Engineer schema (``ReviewQuestion`` → ``LeverChoice``,
   ``DecisionEngine.map_objection``: the LLM with ``--engine llm``, the keyword policy otherwise
   and as the fallback; every call logged) to exactly one bounded what-if lever and value, echo
   "I understood: change … to …" and confirm before anything is computed;
3. **compute** the what-if with ``tools.whatif`` (the same deterministic tools as ``cli whatif``
   and the review loop; ``runs/<id>/whatif/<n>/``) and **speak** a three-sentence before → after
   summary whose numbers are read from the ``WhatIfResult`` table and rounded for speech;
4. **ask** "Adopt it, try another, or stop?": *adopt* hands the what-if to
   ``DesignRunner.adopt_whatif`` — the review loop's adoption path (P6–P9 rerun on the adopted
   design, TL-13 pinned to it, DR-12 "User review", the new P10 approval asked by voice);
   *another* starts the next round; *stop* ends.

Golden rule 1 holds: the engine only picks a lever, the what-if tool computes, this module reads
and formats. Without an adoption the dialogue writes only under ``runs/<id>/whatif/``: the
what-if folders and :data:`DIALOGUE_FILE` (one JSON line per round); on a finished run the LLM
calls of the dialogue are logged there too (``cli talk``), so the run's own files stay as they
were. Adopting rewrites the run's P6–P10 artefacts exactly as a review-loop adoption does — never
do that on the submitted runs (``runs/final*``).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from gemini_agent.agents.engine import (
    DecisionEngine,
    EngineBudgetExceeded,
    PolicyEngine,
)
from gemini_agent.runner import review as review_mod
from gemini_agent.runner.design_runner import DesignRunner, RunResult
from gemini_agent.runner.phases import PHASE_SPECS
from gemini_agent.runner.state import ReviewState, RunState
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import REPO_ROOT
from gemini_agent.schemas.decision import (
    PHASE_TITLES,
    DecisionContext,
    InputRequest,
    LeverChoice,
    Phase,
    Role,
    RunStatus,
    check_lever_choice,
)
from gemini_agent.tools import whatif as whatif_tool
from gemini_agent.tools.whatif import WhatIfResult
from gemini_agent.ui.voice import (
    DIALOGUE_DECISION_ID,
    DIALOGUE_DECISIONS,
    NOTHING_HEARD,
    VoiceSession,
    answer_spec,
    canonical_value,
)

MAX_ROUNDS = 5
"""Change requests per dialogue (FINAL_SPRINT §I)."""
DIALOGUE_FILE = "dialogue.jsonl"
"""``runs/<id>/whatif/dialogue.jsonl``: one :class:`DialogueRound` per line."""
CHANGE_PROMPT = (
    "What would you like to change? Name one design lever, for example the heating return "
    "temperature, the number of doublets or the backup boiler. Say stop to end."
)
NEXT_CHANGE_PROMPT = "What else would you like to change? Say stop to end."
DECISION_PROMPT = "Adopt it, try another, or stop?"
NOT_ADOPTABLE_PROMPT = "This change cannot be adopted in the run: {reason}. Try another, or stop?"
SUBMITTED_RUNS_PREFIX = "final"
"""Run directories under the repository's ``runs/`` whose path has a component starting with this
(``runs/final/…``, ``runs/final_policy``) are the submitted evidence: never adopted into."""
Decision = Literal["adopt", "another", "stop"]

_STOP_WORDS = frozenset(
    {"stop", "quit", "exit", "done", "finished", "finish", "enough", "bye", "goodbye", "end"}
)
_STOP_PHRASES = frozenset(
    {
        "no",
        "none",
        "nothing",
        "no change",
        "no changes",
        "nothing else",
        "thats all",
        "that is all",
        "im done",
        "were done",
    }
)
_ADOPT_WORDS = frozenset({"adopt", "adopted", "accept", "apply", "take"})
_ANOTHER_WORDS = frozenset({"another", "again", "next", "other", "retry", "different", "try"})
"""Decision vocabulary. "yes" and "no" are deliberately absent: after "Adopt it, try another, or
stop?" they are ambiguous and the question is repeated instead."""
_NON_WORD = re.compile(r"[^a-z0-9]+")
_APOSTROPHE = re.compile(r"['\u2019]")

SPOKEN_LEVERS: dict[str, str] = {
    "DB-31": "the heating return temperature",
    "DB-32": "the cooling return temperature",
    "DB-30": "the doublet availability interpretation",
    "ECO-CO2-GAS": "the grid-gas CO2 factor",
    "CHP-EL-FRAC": "the CHP electrical share",
    "OPT-ROBUST-BACKUP": "the robust backup",
    "design_flow_m3h": "the design flow per doublet",
    "doublets": "the number of doublets",
    "concept": "the concept",
}
"""Short spoken names of the levers (``tools.whatif.LEVERS`` descriptions are for the help)."""
SPOKEN_UNITS: dict[str, str] = {
    "USD/MWh": "dollars per megawatt-hour",
    "MUSD": "million dollars",
    "MUSD/yr": "million dollars per year",
    "t/yr": "tonnes per year",
    "d": "days",
    "MWh": "megawatt-hours",
    "degC": "degrees Celsius",
    "m3/h": "cubic metres per hour",
    "frac": "",
    "-": "",
}
COST_METRICS = ("lcoh_per_mwh", "lcoc_per_mwh", "capex_million", "co2_t_per_yr")
"""Table rows of the second sentence (costs and CO2)."""
DEMAND_METRICS = ("unmet_heating_days", "unmet_cooling_days", "probability_demand_met")
"""Table rows of the third sentence (demand coverage), with the guard / verifier verdicts."""


def submitted_run_reason(run_dir: Path) -> str:
    """Why ``run_dir`` must not be rewritten ('' when it may): the submitted ``runs/final*``."""
    try:
        relative = run_dir.resolve().relative_to((REPO_ROOT / "runs").resolve())
    except (OSError, ValueError):
        return ""
    if any(part.startswith(SUBMITTED_RUNS_PREFIX) for part in relative.parts):
        return (
            f"runs/{relative.as_posix()} is a submitted run (FINAL_SPRINT rule 1); copy it to "
            "another runs directory to adopt changes"
        )
    return ""


# --------------------------------------------------------------------------- words
def _words(text: str) -> list[str]:
    """Lower-case words; apostrophes are dropped ("I'd" → "id", "that's" → "thats")."""
    return [word for word in _NON_WORD.sub(" ", _APOSTROPHE.sub("", text.lower())).split() if word]


def is_stop(text: str) -> bool:
    """True when a change request means "stop" (a short utterance with a stop word, or a no)."""
    words = _words(text)
    if not words:
        return False
    joined = " ".join(words)
    if joined in _STOP_PHRASES:
        return True
    if any(" " in phrase and f" {phrase} " in f" {joined} " for phrase in _STOP_PHRASES):
        return True  # "that's all, thanks": a multi-word stop phrase inside the utterance
    return len(words) <= 4 and bool(set(words) & _STOP_WORDS)


def map_decision(text: str) -> Decision | None:
    """Keyword reading of the adopt / another / stop answer (None when ambiguous or absent)."""
    words = set(_words(text))
    hits: list[Decision] = []
    for decision, bag in (
        ("adopt", _ADOPT_WORDS),
        ("another", _ANOTHER_WORDS),
        ("stop", _STOP_WORDS),
    ):
        if words & bag:
            hits.append(decision)
    return hits[0] if len(hits) == 1 else None


def decision_request() -> InputRequest:
    """The "Adopt it, try another, or stop?" question in the normalisers' ``InputRequest`` form."""
    return InputRequest(
        request_id=DIALOGUE_DECISION_ID,
        phase=Phase.P10,
        question=DECISION_PROMPT,
        default=None,
    )


# --------------------------------------------------------------------------- speech
def spoken_value(value: JsonValue, unit: str = "") -> str:
    """A lever value for speech: ``on``/``off`` for flags, ``:g`` for numbers, with the unit."""
    if isinstance(value, bool):
        text = "on" if value else "off"
    elif isinstance(value, int | float):
        text = f"{value:g}"
    else:
        text = str(value)
    spoken_unit = SPOKEN_UNITS.get(unit, unit)
    return f"{text} {spoken_unit}".strip() if spoken_unit else text


def spoken_number(value: float | None, metric: str) -> str:
    """One table number rounded for speech (days and tonnes whole, fractions 2 d.p., else 1)."""
    if value is None:
        return "not computed"
    if metric.endswith("_days") or metric == "co2_t_per_yr":
        return f"{value:.0f}"
    if metric.endswith("_frac") or metric == "probability_demand_met":
        return f"{value:.2f}"
    return f"{value:.1f}"


def spoken_summary(result: WhatIfResult) -> str:
    """Three sentences before → after, every number read from ``result.table`` and rounded.

    1. the lever, its old and new value, the two design ids; 2. LCOH, LCOC, CAPEX and CO2 before
    and after with units; 3. unmet days (and P(demand met) when TL-15 ran) with the DesignGuard
    and verifier verdicts of the what-if design.
    """
    lever = next(iter(result.levers))
    value = result.levers[lever]
    spec = whatif_tool.LEVERS.get(lever)
    unit = spec.unit if spec else ""
    applied = next((item for item in result.applied if item.lever == lever), None)
    base = applied.base_value if applied is not None else None
    name = SPOKEN_LEVERS.get(lever, spec.description if spec else lever)
    before, after = result.before, result.after
    change = (
        f"from {spoken_value(base, unit)} to {spoken_value(value, unit)}"
        if base is not None
        else f"to {spoken_value(value, unit)}"
    )
    first = (
        f"What-if {result.index}: changing {name} {change} turns the recommended design "
        f"{before.candidate.id} into {after.candidate.id}."
    )
    rows = {row.metric: row for row in result.table}
    if after.kpis is None:  # infeasible / failed evaluation: say why instead of "not computed"
        reason = " ".join((after.reason or after.status).split())[:200]
        guard = "approved" if after.guard_approved else "rejected"
        return (
            f"{first} The what-if design could not be evaluated: {reason}. "
            f"The DesignGuard {guard} it and the verifier did not run."
        )
    costs = []
    for metric in COST_METRICS:
        row = rows.get(metric)
        if row is None:
            continue
        unit_text = SPOKEN_UNITS.get(row.unit, row.unit)
        costs.append(
            f"{row.label} from {spoken_number(row.before, metric)} to "
            f"{spoken_number(row.after, metric)} {unit_text}".rstrip()
        )
    second = ("Before to after: " + ", ".join(costs) + ".") if costs else "No KPI was computed."
    demand = []
    for metric in DEMAND_METRICS:
        row = rows.get(metric)
        if row is None or (row.before is None and row.after is None):
            continue
        if metric == "probability_demand_met" and row.after is None:
            demand.append(  # TL-15 reruns only with --budget full
                f"{row.label} {spoken_number(row.before, metric)} before, not recomputed with "
                f"the {result.budget} budget"
            )
            continue
        demand.append(
            f"{row.label} from {spoken_number(row.before, metric)} to "
            f"{spoken_number(row.after, metric)}"
        )
    guard = "approved" if after.guard_approved else "rejected"
    verdict = after.verdict.value if after.verdict is not None else "not run"
    third = (
        ", ".join(demand) + "; " if demand else ""
    ) + f"the DesignGuard {guard} and the verifier says {verdict} for the what-if design."
    return f"{first} {second} {third[0].upper() + third[1:]}"


# --------------------------------------------------------------------------- lever mapping
def dialogue_context(state: RunState) -> DecisionContext:
    """The compact engine context of a dialogue mapping (P9, Lead Engineer, no tools).

    The run is finished, so no phase budget is left: ``tokens`` 0 makes the engine use the
    floor of a review call (its own token ceiling), exactly as a Decision Record call does.
    """
    spec = PHASE_SPECS[Phase.P9]
    return DecisionContext(
        run_id=state.run_id,
        phase=Phase.P9,
        phase_title=PHASE_TITLES[Phase.P9],
        role=Role.LEAD_ENGINEER,
        objective=spec.objective,
        user_prompt=state.user_prompt[:500],
        in_scope=state.in_scope,
        scope_reason=state.scope_reason,
        allowed_tools=[],
        loop_count=state.loop_count,
        budget_left={"steps": 0, "tool_calls": 0, "tokens": 0},
    )


@dataclass
class LeverMapper:
    """Objection text → one bounded lever with the review loop's schema, policy fallback.

    ``engine`` is the runner's (``PolicyEngine`` → keywords; ``ResilientEngine`` → the Lead
    Engineer agent, logged, keyword fallback on failure). An invalid choice or a usage-limit
    overrun also falls back to the keywords (``source`` = ``policy_fallback``).
    """

    engine: DecisionEngine
    policy: PolicyEngine = field(default_factory=PolicyEngine)

    def map(
        self, state: RunState, objection: str, round_: int, feedback: str = ""
    ) -> tuple[LeverChoice, str]:
        """Return the ``LeverChoice`` (lever None = no match) and its decision source."""
        review = ReviewState(round=max(round_, 1), objection=objection[:600], feedback=feedback)
        question = review_mod.build_review_question(state, review)
        context = dialogue_context(state)
        try:
            result = self.engine.map_objection(question, context)
            choice, source = result.value, result.source
        except EngineBudgetExceeded:
            choice, source = self.policy.map_objection(question, context).value, "policy_fallback"
        if check_lever_choice(question, choice):
            choice, source = self.policy.map_objection(question, context).value, "policy_fallback"
        return choice, source


# --------------------------------------------------------------------------- the loop
class DialogueRound(StrictModel):
    """One round of the dialogue (a line of :data:`DIALOGUE_FILE`)."""

    round: int
    heard: str
    lever: str | None = None
    value: JsonValue = None
    mapping_source: str = ""
    whatif_index: int | None = None
    candidate_id: str | None = None
    decision: Decision | None = None
    rerun_status: str | None = None
    note: str = ""


@dataclass
class DialogueSummary:
    """What the dialogue did (returned by :meth:`DialogueLoop.run`)."""

    rounds: list[DialogueRound] = field(default_factory=list)
    whatif_indices: list[int] = field(default_factory=list)
    adopted: int = 0
    last_result: RunResult | None = None
    stopped_by: str = ""


@dataclass
class DialogueLoop:
    """The what-if dialogue around a finished run (installed as ``VoiceSession.dialogue_hook``).

    ``session`` listens, speaks, normalises and confirms; ``runner`` holds the run's engine (the
    lever mapping) and performs an adoption; ``budget`` is the what-if budget
    (``tools.whatif.BUDGETS``); ``log_path`` receives one JSON line per round.
    """

    session: VoiceSession
    runner: DesignRunner
    run_id: str
    budget: str = whatif_tool.DEFAULT_BUDGET
    max_rounds: int = MAX_ROUNDS
    log_path: Path | None = None
    mapper: LeverMapper | None = None
    adopt_block: str = ""
    """Non-empty: adoption is never offered, for this reason (the submitted ``runs/final*``)."""
    summary: DialogueSummary = field(default_factory=DialogueSummary)

    def __post_init__(self) -> None:
        """Check the budget; the mapper defaults to the runner's engine."""
        if self.budget not in whatif_tool.BUDGETS:
            raise ValueError(f"budget must be one of {whatif_tool.BUDGETS}")
        if self.mapper is None:
            self.mapper = LeverMapper(self.runner.engine)

    # ------------------------------------------------------------------ helpers
    def _print(self, text: str) -> None:
        print(text, file=self.session.out or sys.stdout)

    def _log(self, record: DialogueRound) -> None:
        self.summary.rounds.append(record)
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def _adoptable(self, state: RunState, result: WhatIfResult, lever: str) -> tuple[bool, str]:
        """Whether "adopt" is offered, with the reason when it is not (the runner re-checks)."""
        if self.adopt_block:
            return False, self.adopt_block
        if lever not in review_mod.ADOPTABLE:
            return False, review_mod.NOT_ADOPTABLE_REASON.get(lever, "not a design lever")
        after = result.after
        if after.status != "ok" or not after.guard_approved:
            return False, f"the what-if design is not usable ({after.reason or after.status})"
        if state.status != RunStatus.COMPLETED:
            return False, f"the run is {state.status.value}, not completed"
        if state.loop_count >= self.runner.max_verification_loops:
            return False, (
                f"the {self.runner.max_verification_loops} verification loops of the run are used"
            )
        return True, ""

    # ------------------------------------------------------------------ questions
    def ask_change(self, round_: int) -> str | None:
        """Speak the change prompt, record, echo and confirm; None = stop or nothing usable."""
        session = self.session
        session.say(CHANGE_PROMPT if round_ == 1 else NEXT_CHANGE_PROMPT)
        for _ in range(session.max_attempts):
            heard = session.hear()
            if not heard:
                session.say(NOTHING_HEARD)
                continue
            self._print(f'I heard: "{heard}"')
            if is_stop(heard):
                if session.confirm("Stop the dialogue?"):
                    self.summary.stopped_by = "user"
                    return None
                session.say("Let's try again.")
                continue
            if session.confirm("Use it?"):
                return heard
            session.say("Let's try again.")
        session.say("No change was confirmed; the dialogue ends.")
        self.summary.stopped_by = "no change confirmed"
        return None

    def understand_decision(self, request: InputRequest, heard: str) -> Decision | None:
        """Normaliser first (one logged LLM call with ``--engine llm``), then the keywords."""
        spec = answer_spec(request)
        understood = self.session.normaliser.normalise(request, heard)
        if understood.interpretation == "value" and understood.value:
            try:
                value = canonical_value(spec, understood.value)
            except ValueError:
                value = None
            if value in DIALOGUE_DECISIONS:
                return value  # type: ignore[return-value]
        return map_decision(heard)

    def ask_decision(self, adoptable: bool, reason: str) -> Decision | None:
        """Ask "Adopt it, try another, or stop?": record, understand, echo, confirm (None = give up)."""
        session = self.session
        session.say(DECISION_PROMPT if adoptable else NOT_ADOPTABLE_PROMPT.format(reason=reason))
        request = decision_request()
        for _ in range(session.max_attempts):
            heard = session.hear()
            if not heard:
                session.say(NOTHING_HEARD)
                continue
            decision = self.understand_decision(request, heard)
            if decision is None:
                session.say(
                    f'I heard "{heard}" but expected adopt, another or stop. Please repeat.'
                )
                continue
            if decision == "adopt" and not adoptable:
                session.say(f"It cannot be adopted: {reason}. Say another or stop.")
                continue
            self._print(f'I heard: "{heard}"')
            if session.confirm(f"I understood {decision} - apply?"):
                return decision
            session.say("Let's try again.")
        session.say("No decision was confirmed; nothing is adopted and the dialogue ends.")
        return None

    # ------------------------------------------------------------------ the loop
    def run(self, result: RunResult) -> DialogueSummary:
        """Run the dialogue on the finished run ``result`` describes; return what happened."""
        session = self.session
        self.summary.last_result = result
        if result.status != RunStatus.COMPLETED:
            session.say(f"The run {result.status.value}; there is no recommended design to change.")
            self.summary.stopped_by = "run not completed"
            return self.summary
        session.say(
            f"We can now explore changes to the recommended design {result.selected_id} as "
            f"what-ifs; nothing is adopted without your confirmation."
        )
        run_dir = self.runner.run_dir(self.run_id)
        for round_ in range(1, self.max_rounds + 1):
            heard = self.ask_change(round_)
            if heard is None:
                break
            state = self.runner.load_state(self.run_id)
            record = DialogueRound(round=round_, heard=heard)
            assert self.mapper is not None
            choice, source = self.mapper.map(state, heard, round_)
            record.mapping_source = source
            if choice.lever is None:
                record.note = f"no lever: {choice.rationale}"
                session.say(f"No design lever matches that. {choice.rationale} Try another change.")
                self._log(record)
                continue
            lever = choice.lever
            value = review_mod.resolve_value(state, lever, choice.value)
            spec = whatif_tool.LEVERS.get(lever)
            name = SPOKEN_LEVERS.get(lever, spec.description if spec else lever)
            spoken = spoken_value("" if value is None else value, spec.unit if spec else "")
            echo = f"I understood: change {name} ({lever}) to {spoken}."
            self._print(echo)
            session.speaker.say(echo)
            if not session.confirm("Compute the what-if?"):
                record.note = "mapping declined"
                session.say("Not computed. Let's try another change.")
                self._log(record)
                continue
            try:
                whatif = review_mod.run_whatif(
                    run_dir, lever, value, self.budget, self.runner.config_dir
                )
            except whatif_tool.WhatIfRefused as exc:
                record.note = f"refused: {exc}"[:500]
                session.say(f"That change is refused: {exc}. Try another change.")
                self._log(record)
                continue
            except (whatif_tool.UnknownLever, ValueError, OSError) as exc:
                record.note = f"failed: {type(exc).__name__}: {exc}"[:500]
                session.say(f"The what-if failed: {exc}. Try another change.")
                self._log(record)
                continue
            record.lever = next(iter(whatif.levers))
            record.value = whatif.levers[record.lever]
            record.whatif_index = whatif.index
            record.candidate_id = whatif.after.candidate.id
            self.summary.whatif_indices.append(whatif.index)
            self._print(whatif_tool.render_table(whatif))
            session.say(spoken_summary(whatif))
            adoptable, reason = self._adoptable(state, whatif, record.lever)
            decision = self.ask_decision(adoptable, reason)
            record.decision = decision
            if decision == "adopt":
                self._print(
                    "Adopting: P6-P9 rerun on the what-if design (DR-12); the run's records are "
                    "rewritten."
                )
                rerun = self.runner.adopt_whatif(
                    self.run_id, whatif, objection=heard, mapping_source=source
                )
                self.summary.last_result = rerun
                self.summary.adopted += 1
                record.rerun_status = rerun.status.value
                session.say(
                    f"Adopted. Phases P6 to P9 were rerun on {rerun.selected_id}: the run "
                    f"{rerun.status.value} with verdict {rerun.verdict or 'none'} and approval "
                    f"{rerun.approval or 'pending'}."
                )
                self._print(rerun.summary)
                if rerun.status != RunStatus.COMPLETED:
                    self._log(record)
                    self.summary.stopped_by = f"rerun {rerun.status.value}"
                    session.say("The rerun did not complete; the dialogue ends.")
                    break
            self._log(record)
            if decision is None or decision == "stop":
                self.summary.stopped_by = "user" if decision == "stop" else "no decision confirmed"
                break
        else:
            self.summary.stopped_by = "round limit"
            session.say(
                f"That was round {self.max_rounds} of {self.max_rounds}; the dialogue ends."
            )
        session.say(
            f"Dialogue over: {len(self.summary.whatif_indices)} what-ifs computed, "
            f"{self.summary.adopted} adopted."
        )
        return self.summary


__all__ = [
    "CHANGE_PROMPT",
    "DECISION_PROMPT",
    "DIALOGUE_FILE",
    "MAX_ROUNDS",
    "DialogueLoop",
    "DialogueRound",
    "DialogueSummary",
    "LeverMapper",
    "decision_request",
    "dialogue_context",
    "is_stop",
    "map_decision",
    "spoken_number",
    "spoken_summary",
    "spoken_value",
    "submitted_run_reason",
]
