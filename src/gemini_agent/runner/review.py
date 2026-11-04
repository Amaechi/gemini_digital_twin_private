"""Interactive design review after the P9 verdict (``cli run --review``; FINAL_SPRINT §E).

The runner asks the user "Any objection to the recommended design?" once the P9 verifier verdict
is in. Free text (``"the boiler is too big"``, ``"I'd prefer 3 doublets"``) goes to the Lead
Engineer role, which maps it to **exactly one** bounded what-if lever and value
(``tools.whatif.LEVERS``; ``LeverChoice`` output; the policy engine uses keyword patterns,
``agents.objections``). The runner then calls :func:`gemini_agent.tools.whatif.whatif` with that
lever — the same deterministic tools as ``cli whatif``, in ``runs/<id>/whatif/<n>/`` — shows the
before → after KPI table with the DesignGuard / verifier verdicts and asks "adopt the change?".
Adopting reruns P6–P9 on the changed design (the verifier loop-back path, counted against the ≤ 3
loop limit); DR-12 "User review: <objection> → <lever>" records adopted or kept with the evidence
of the what-if. Golden rule 1 holds: the LLM chooses a lever, the tools compute.

This module holds the bounded pieces the runner uses: the lever and candidate briefs the engine
sees, the question texts, the what-if call and the DR-12 record. The state machine
(objection → mapping → what-if → adopt, pause/resume, the P6–P9 rerun) is in
``runner/design_runner.py``.

Adoptable levers: ``DB-31``, ``DB-32``, ``DB-30`` (recorded as user answers), ``doublets`` and
``concept`` (TL-13 re-sizes from the adopted design, ``optimise_design`` ``candidate_id``) and
``OPT-ROBUST-BACKUP`` (P8 re-prices; ``off`` makes the DR-11 robust option ineligible).
``design_flow_m3h`` is a TL-13 size variable (OPT-SPACE) and ``ECO-CO2-GAS`` / ``CHP-EL-FRAC``
are scenario values of the register: their what-if is shown but a run cannot adopt them (the
register is changed in ``config/`` and a new run started).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from gemini_agent.runner.state import ReviewState, RunState, ToolRecord
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.decision import (
    INT_STEPS,
    REVIEW_DR_ID,
    REVIEW_DR_TITLE,
    CandidateBrief,
    DecisionCriterion,
    DecisionOption,
    DecisionRecord,
    InputRequest,
    LeverBrief,
    Phase,
    RejectedOption,
    ReviewQuestion,
    Role,
    key_tokens,
    reject_llm_quantities,
)
from gemini_agent.tools import whatif as whatif_tool
from gemini_agent.tools.whatif import WhatIfResult

REVIEW_PREFIX = "REVIEW"
"""Request ids of the review questions: ``REVIEW-<loop>-<round>`` and ``REVIEW-ADOPT-<loop>``."""
MAX_REVIEW_ROUNDS = 3
"""Objections mapped per pass before the review closes as kept (FINAL_SPRINT §E)."""
NO_OBJECTION = frozenset({"", "none", "no", "n", "ok", "fine", "accept", "approve", "-"})
"""Answers that mean "no objection" (Enter = none)."""
KEEP_OPTION = "kept"
"""DR-12 option id of keeping the recommended design."""
WHATIF_TOOL = "whatif"
"""Tool name of the what-if evidence record in the run (``ToolRecord.tool``)."""

ADOPTABLE: frozenset[str] = frozenset(
    {"DB-31", "DB-32", "DB-30", "OPT-ROBUST-BACKUP", "doublets", "concept"}
)
NOT_ADOPTABLE_REASON: dict[str, str] = {
    "design_flow_m3h": "the design flow is a TL-13 size variable (OPT-SPACE) that the P6 "
    "optimiser sets; the what-if shows its effect, a run cannot pin it",
    "ECO-CO2-GAS": "a scenario value of the register, not a design lever: change "
    "config/assumptions.yaml and start a new run",
    "CHP-EL-FRAC": "a scenario value of the register, not a design lever: change "
    "config/assumptions.yaml and start a new run",
}
_WHATIF_ID = re.compile(r"-w\d+(?:-opt)?(?:-robust)?$")
"""Ids of what-if designs and the designs TL-13 / P8 derive from them (not re-pickable)."""
ANSWER_LEVERS: frozenset[str] = frozenset({"DB-31", "DB-32", "DB-30"})
"""Adopted levers recorded as user answers (register ids) so every tool of the rerun reads them."""

whatif = whatif_tool.whatif
"""The what-if entry point the runner calls (module attribute so tests can replace it)."""


# --------------------------------------------------------------------------- briefs
def lever_briefs() -> list[LeverBrief]:
    """The bounded levers as the review agent sees them (catalogue order)."""
    return [
        LeverBrief(
            id=spec.id,
            kind=spec.kind,
            description=spec.description,
            unit=spec.unit,
            choices=list(spec.choices),
            bound=spec.bound,
            adoptable=spec.id in ADOPTABLE,
        )
        for spec in whatif_tool.LEVERS.values()
    ]


def _brief(candidate: Candidate) -> CandidateBrief:
    return CandidateBrief(
        id=candidate.id,
        concept=candidate.concept,
        doublets=candidate.doublets,
        depth_m=candidate.depth_m,
        description=candidate.description[:120],
    )


def candidate_briefs(state: RunState) -> list[CandidateBrief]:
    """Screened geothermal candidates the ``concept`` lever may re-pick (none excluded)."""
    excluded = state.excluded_ids()
    sized_suffixes = ("-opt", whatif_tool.ROBUST_SUFFIX)
    return [
        _brief(c)
        for c in state.candidates
        if c.concept != "C0"
        and c.id not in excluded
        and not c.id.endswith(sized_suffixes)
        and _WHATIF_ID.search(c.id) is None
    ]


def selected_candidate(state: RunState) -> Candidate | None:
    """The recommended design of the run (None before P6)."""
    return next((c for c in state.candidates if c.id == state.selected_id), None)


def build_review_question(state: RunState, review: ReviewState) -> ReviewQuestion:
    """The objection of the current round with the design and the bounded levers."""
    selected = selected_candidate(state)
    if selected is None:
        raise ValueError("no selected design to review")
    backup = state.robust_backup
    installed: bool | None = None
    if backup is not None:
        installed = backup.robust_candidate_id == state.selected_id
    return ReviewQuestion(
        run_id=state.run_id,
        round=review.round,
        objection=review.objection[:600],
        selected=_brief(selected),
        selected_returns_degc={
            "heating_return_degc": selected.heating_return_degc,
            "cooling_return_degc": selected.cooling_return_degc,
        },
        robust_backup_installed=installed,
        levers=lever_briefs(),
        candidates=candidate_briefs(state),
        feedback=review.feedback[:600],
    )


# --------------------------------------------------------------------------- questions
def objection_request(state: RunState, review: ReviewState) -> InputRequest:
    """``REVIEW-<loop>-<round>``: "Any objection to the recommended design? (Enter = none)"."""
    verdict = state.verdict_value() or "not verified"
    text = (
        f"Any objection to the recommended design {state.selected_id} (verifier verdict "
        f"{verdict})? Free text, e.g. 'the boiler is too big' or 'I'd prefer three doublets'; "
        f"it is mapped to one bounded design lever and computed as a what-if "
        f"(round {review.round + 1} of {MAX_REVIEW_ROUNDS}). (Enter = none)"
    )
    if review.feedback:
        text = f"{review.feedback} {text}"
    return InputRequest(
        request_id=f"{REVIEW_PREFIX}-{state.loop_count}-{review.round + 1}",
        phase=Phase.P9,
        question=text,
        default="none",
    )


def adopt_request(state: RunState, review: ReviewState, result: WhatIfResult) -> InputRequest:
    """``REVIEW-ADOPT-<loop>``: the before → after table, the verdicts and "adopt? yes/no"."""
    lever = review.lever or ""
    adoptable = lever in ADOPTABLE
    header = (
        f"What-if {result.index} for your objection '{review.objection}': "
        f"{whatif_tool.format_levers(result.levers)} applied to {result.before.candidate.id} "
        f"(what-if design {result.after.candidate.id})."
    )
    lines = [header, "", whatif_tool.render_table(result), ""]
    for outcome in (result.before, result.after):
        line = f"{outcome.label}: {outcome.status}"
        if outcome.reason:
            line += f" - {outcome.reason}"
        if outcome.verifier_summary:
            line += f"; {outcome.verifier_summary}"
        lines.append(line)
    lines += [f"warning: {w}" for w in result.warnings]
    if adoptable:
        ask = (
            "Adopt the change? yes = rerun P6-P9 on the changed design (TL-13 re-sizes from it; "
            f"counted against the {state.loop_count} of 3 verification loops used), no = keep "
            "the recommended design. yes/no"
        )
    else:
        ask = (
            f"This lever cannot be adopted in a run ({NOT_ADOPTABLE_REASON.get(lever, lever)}); "
            "answer no to keep the recommended design (yes is recorded as kept). yes/no"
        )
    lines += ["", ask]
    return InputRequest(
        request_id=f"{REVIEW_PREFIX}-ADOPT-{state.loop_count}",
        phase=Phase.P9,
        question="\n".join(lines),
        default="no",
    )


# --------------------------------------------------------------------------- what-if
def resolve_value(state: RunState, lever: str, value: str | None) -> str | None:
    """Turn an engine's ``more`` / ``fewer`` for an integer lever into the stepped count.

    The engine never works out the number: the selected design's count ± 1 is computed here
    (``schemas.decision.INT_STEPS``); the DesignGuard bounds it in the what-if.
    """
    spec = whatif_tool.LEVERS.get(lever)
    if value is None or spec is None or spec.kind != "int":
        return value
    step = INT_STEPS.get(value.strip().lower())
    if step is None:
        return value
    selected = selected_candidate(state)
    if selected is None:
        return value
    return str(getattr(selected, spec.id, 0) + step)


def run_whatif(
    run_dir: Path, lever: str, value: Any, budget: str, config_dir: Path | None
) -> WhatIfResult:
    """Call the what-if tool with one lever on the live run folder (``whatif/<n>/`` is written)."""
    return whatif(run_dir, {lever: value}, budget, config_dir=config_dir)


def load_whatif(run_dir: Path, index: int) -> WhatIfResult:
    """Re-read ``whatif/<n>/whatif.json`` (after a pause the result is not in memory)."""
    path = run_dir / whatif_tool.WHATIF_DIR / str(index) / "whatif.json"
    return WhatIfResult.model_validate_json(path.read_text(encoding="utf-8"))


def whatif_record(state: RunState, result: WhatIfResult, tr_id: str, *, stub: bool) -> ToolRecord:
    """Evidence record of a what-if in the run (``TR-…`` id quoted by DR-12)."""
    from gemini_agent.runner.decision_records import result_keys

    return ToolRecord(
        id=tr_id,
        tool=WHATIF_TOOL,
        phase=Phase.P9,
        args={"lever": next(iter(result.levers)), "value": _json_scalar(result)},
        ok=True,
        summary=result.summary,
        warnings=list(result.warnings),
        stub=stub,
        candidate_ids=[result.before.candidate.id, result.after.candidate.id],
        source="user",
        path=result.outputs.get("json"),
        signature="",
        loop=state.loop_count,
        data_keys=result_keys(result),
    )


def _json_scalar(result: WhatIfResult) -> str | int | float | bool | None:
    value = next(iter(result.levers.values()))
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------- DR-12
def review_record(
    state: RunState,
    review: ReviewState,
    result: WhatIfResult | None,
    *,
    adopted: bool,
    note: str,
    evidence_ids: list[str],
    evidence_keys: list[str],
    recommended_id: str | None = None,
) -> DecisionRecord:
    """DR-12 "User review: <objection> → <lever>": adopted or kept, with the what-if evidence.

    Options come from the what-if result (the run's recommended design and the what-if design);
    ``recommended`` follows the optimiser's rule in the what-if record (lower LCOH + LCOC among
    options meeting demand and not REJECTED). The choice is the user's. The criterion key is
    the lever (an evidence key of the what-if), or the verifier verdict when there was no
    objection. Numbers in the rationale are the user's words only (``decision_source`` user).
    """
    selected = recommended_id or state.selected_id or "none"
    verification = state.latest_verification(selected)
    verdict = verification.verdict.value if verification is not None else "not verified"
    lever = review.lever
    options = [
        DecisionOption(
            id=KEEP_OPTION,
            label=f"Keep the recommended design {selected}",
            evidence_ids=evidence_ids,
            recommended=True,
            eligible=True,
            notes=f"verifier verdict {verdict}",
        )
    ]
    adopt_id = None
    if result is not None:
        after = result.after
        after_option = next(o for o in result.record.options if o.id == "after")
        adopt_id = after.candidate.id
        eligible = after.status == "ok" and after.guard_approved and (lever or "") in ADOPTABLE
        after_verdict = after.verdict.value if after.verdict is not None else "not run"
        options[0].recommended = not after_option.recommended
        options.append(
            DecisionOption(
                id=adopt_id,
                label=f"Adopt what-if {result.index}: "
                f"{whatif_tool.format_levers(result.levers)} → {adopt_id}",
                evidence_ids=evidence_ids,
                recommended=after_option.recommended and eligible,
                eligible=eligible,
                notes=(
                    f"DesignGuard {'APPROVED' if after.guard_approved else 'REJECTED'}; verifier "
                    f"{after_verdict}; {after_option.notes}"
                    + (
                        ""
                        if (lever or "") in ADOPTABLE
                        else f"; not adoptable: {NOT_ADOPTABLE_REASON.get(lever or '', '')}"
                    )
                )[:500],
            )
        )
    choice = options[1] if adopted and adopt_id else options[0]
    criterion = _criterion(review, result, choice.id, evidence_ids, evidence_keys)
    objection = review.objection or "none"
    mapped = f"{lever}={_fmt_value(review.value)}" if lever else "no lever"
    title = f"{REVIEW_DR_TITLE}: {objection} → {mapped}"[:200]
    rationale = f"User review ({review.mapping_source or 'none'}): objection '{objection}'"
    if lever:
        rationale += f" mapped to lever {mapped}"
    if result is not None:
        rationale += (
            f"; what-if {result.index} ({result.record.dr_id}) computed by simulate_annual, "
            f"economics and verify_design on the same basis for both designs: {result.summary}"
        )
    rationale += f". Outcome: {'adopted' if adopted else 'kept'} — {note}"
    history = {k: v for k, v in state.review_levers.items() if not adopted or k != lever}
    if history:
        rationale += "; levers adopted earlier in this run: " + ", ".join(
            f"{k}={_fmt_value(v)}" for k, v in history.items()
        )
    rejected = [
        RejectedOption(
            option_id=o.id,
            reason=("Not chosen by the user." if o.eligible else f"Not eligible: {o.notes}"[:300]),
        )
        for o in options
        if o.id != choice.id
    ]
    return DecisionRecord(
        dr_id=REVIEW_DR_ID,  # type: ignore[arg-type]
        title=title,
        run_id=state.run_id,
        phase=Phase.P9,
        role=Role.LEAD_ENGINEER,
        question=f"Any objection to the recommended design {selected}? ({objection})",
        options=options,
        evidence_ids=sorted(set(evidence_ids)),
        choice=choice.id,
        choice_label=choice.label,
        criterion=criterion,
        rationale=rationale[:1500],
        rejected=rejected,
        decision_source="user",
        stub_evidence=any(r.stub for r in state.tool_results if r.id in set(evidence_ids)),
    )


def _fmt_value(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _criterion(
    review: ReviewState,
    result: WhatIfResult | None,
    best: str,
    evidence_ids: list[str],
    keys: list[str],
) -> DecisionCriterion | None:
    """The user's decision as a criterion: key = the lever (or the verdict without objection)."""
    allowed = set(keys)
    if review.lever and review.lever in allowed and result is not None:
        text = f"user review: objection mapped to lever {review.lever}; the what-if evidence " + (
            "was adopted by the user" if best != KEEP_OPTION else "was shown and the user kept"
        )
        key, source = review.lever, WHATIF_TOOL
    elif "verdict" in allowed:
        text = "user review: no objection to the recommended design and its verifier verdict"
        key, source = "verdict", "verify_design"
    else:
        return None
    text = reject_llm_quantities(text)[:200]
    if any(token not in allowed | {key} for token in key_tokens(text)):
        return None
    return DecisionCriterion(
        id="user-review",
        key=key,
        source=source,
        text=text,
        best_option_ids=[best],
        evidence_ids=list(evidence_ids),
    )


def evidence_keys_for(
    state: RunState, evidence_ids: list[str], levers: Mapping[str, Any]
) -> list[str]:
    """Citable keys of DR-12: the what-if / verification result keys, their tools, the levers."""
    keys: dict[str, None] = {}
    wanted = set(evidence_ids)
    for record in state.tool_results:
        if record.id in wanted and record.ok:
            keys[record.tool] = None
            keys.update(dict.fromkeys(record.data_keys))
    keys.update(dict.fromkeys(levers))
    return sorted(keys)


__all__ = [
    "ADOPTABLE",
    "ANSWER_LEVERS",
    "KEEP_OPTION",
    "MAX_REVIEW_ROUNDS",
    "NOT_ADOPTABLE_REASON",
    "NO_OBJECTION",
    "REVIEW_PREFIX",
    "WHATIF_TOOL",
    "adopt_request",
    "build_review_question",
    "candidate_briefs",
    "evidence_keys_for",
    "lever_briefs",
    "load_whatif",
    "objection_request",
    "resolve_value",
    "review_record",
    "run_whatif",
    "selected_candidate",
    "whatif_record",
]
