"""Decision Records DR-01…DR-10 (AG-07): options offered by the workflow, written as JSON + MD.

Options come from tool results and the configuration, never from an LLM. ``recommended`` is set
only by a deterministic rule: the option holding the top candidate of
``ranking.rank_candidates`` (the optimiser's rule, so DRs agree with the selected design), a
register default (DR-06, DR-08) or a spec rule (DR-09). Evidence ids are
tool-result ids (``TR-…``) or config references (``config:…``).

Criteria (L-1 follow-up): each question also offers decisive criteria evaluated here on the tool
results — the recommendation rule first, then KPI criteria that discriminate between the
eligible options — and the evidence keys a rationale may cite (field names of the evidence tool
results, the tools, assumption ids). A choice names one criterion and must be one of its best
options, so its rationale is grounded and cannot contradict itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from gemini_agent.runner import ranking
from gemini_agent.runner.state import RunState
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    DR_TITLES,
    DecisionCriterion,
    DecisionOption,
    DecisionQuestion,
    DecisionRecord,
    DecisionSource,
    DesignVerdict,
    OptionChoice,
    Phase,
    RejectedOption,
    Role,
    key_tokens,
)

DR_QUESTIONS: dict[str, tuple[str, Role]] = {
    "DR-01": ("How many doublets should the plant have?", Role.SYSTEM_ARCHITECT),
    "DR-02": ("At which reservoir depth should the wells be drilled?", Role.SUBSURFACE),
    "DR-03": ("Which technology lifts heat to the 100 degC supply?", Role.SYSTEM_ARCHITECT),
    "DR-04": ("Which cooling technology mix supplies 20 degC?", Role.SYSTEM_ARCHITECT),
    "DR-05": ("Should the plant include thermal storage?", Role.SYSTEM_ARCHITECT),
    "DR-06": ("How are doublet maintenance windows scheduled?", Role.SYSTEM_ARCHITECT),
    "DR-07": ("How is the associated gas handled?", Role.SUBSURFACE),
    "DR-08": ("Which district-heating return temperature is designed for?", Role.SYSTEM_ARCHITECT),
    "DR-09": ("Which materials are used in the brine loop?", Role.SUBSURFACE),
    "DR-10": ("Which design is recommended?", Role.LEAD_ENGINEER),
}

_HEAT_TOPUP = {
    "C1": ("gas-boiler-chp-topup", "Plate HEX + gas boiler/CHP top-up (C1)"),
    "C2": ("hthp-series", "Plate HEX + HTHP in series + CHP + peak boiler (C2/C3)"),
    "C3": ("hthp-series", "Plate HEX + HTHP in series + CHP + peak boiler (C2/C3)"),
    "C4": ("deeper-direct-hex", "Deeper wells, direct plate HEX + small peak boiler (C4)"),
    "C5": ("shallow-large-hp", "Shallow wells + large heat pumps (C5)"),
}
_COOLING = {
    "C1": ("electric-chillers", "Electric chillers (C1)"),
    "C2": ("free-cooling-electric", "Free cooling + electric chillers (C2, C5)"),
    "C3": ("free-cooling-absorption", "Free cooling + absorption + electric peak (C3, C4)"),
    "C4": ("free-cooling-absorption", "Free cooling + absorption + electric peak (C3, C4)"),
    "C5": ("free-cooling-electric", "Free cooling + electric chillers (C2, C5)"),
}


def _base_concept(candidate: Candidate, by_id: dict[str, Candidate]) -> str:
    """Concept that defines heat/cooling technology (C6–C8 inherit from their parent)."""
    seen = set()
    while candidate.parent_id and candidate.parent_id in by_id and candidate.id not in seen:
        seen.add(candidate.id)
        candidate = by_id[candidate.parent_id]
    return candidate.concept


Members = dict[str, list[str]]
"""Option id → candidate ids it stands for (usable ones only; empty for non-candidate DRs)."""

KPI_CRITERIA: tuple[tuple[str, Literal["lowest", "highest"]], ...] = (
    ("lcoh_per_mwh", "lowest"),
    ("lcoc_per_mwh", "lowest"),
    ("unmet_heating_mwh", "lowest"),
    ("unmet_cooling_mwh", "lowest"),
    ("co2_t_per_yr", "lowest"),
    ("capex_million", "lowest"),
    ("exergy_efficiency_frac", "highest"),
)
"""``KPISet`` fields offered as decisive criteria when they discriminate between options."""

_KPI_TOOLS = ("economics", "simulate_annual")
"""Tools whose ``KPISet`` results fill ``RunState.kpis`` (preferred source first)."""


def result_keys(data: Any) -> list[str]:
    """Field names in a tool result's data, nested models included (the citable evidence keys).

    Lists and mappings are sampled by their first item (results are homogeneous); keys of a
    top-level mapping count, keys of nested mappings (equipment names) do not.
    """
    keys: dict[str, None] = {}

    def walk(value: Any, depth: int) -> None:
        if depth > 5:
            return
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                keys[name] = None
                walk(getattr(value, name), depth + 1)
        elif isinstance(value, Mapping):
            for name, item in list(value.items())[: 1 if depth else None]:
                if depth == 0 and isinstance(name, str):
                    keys[name] = None
                walk(item, depth + 1)
        elif isinstance(value, list | tuple) and value:
            walk(value[0], depth + 1)

    walk(data, 0)
    return list(keys)


def _group_options(
    state: RunState,
    key_fn: Callable[[Candidate], tuple[str, str] | None],
    usable_by_option: Members | None = None,
) -> tuple[list[DecisionOption], str | None]:
    """Group geothermal candidates into options; return the options and the top-ranked key.

    ``usable_by_option`` (if given) receives option id → usable candidate ids.
    """
    groups: dict[str, tuple[str, list[str]]] = {}
    for candidate in state.candidates:
        if candidate.concept == "C0":
            continue
        key = key_fn(candidate)
        if key is None:
            continue
        option_id, label = key
        groups.setdefault(option_id, (label, []))[1].append(candidate.id)
    # Same deterministic rule as the optimiser, so DR-01…08 agree with the selected design.
    top_key = None
    excluded = state.excluded_ids()
    ranked = ranking.rank_candidates(state.candidates, state.kpis, exclude_ids=excluded)
    by_id = {c.id: c for c in state.candidates}
    if ranked:
        top = key_fn(by_id[ranked[0]])
        top_key = top[0] if top else None
    basis = (
        "LCOH ranking"
        if ranking.has_kpi_evidence(state.kpis)
        else "generation order, no KPI evidence yet"
    )
    options = []
    for option_id, (label, members) in groups.items():
        evidence = [r.id for r in state.evidence_for(candidate_ids=set(members))]
        usable = [m for m in members if m not in excluded]
        dropped = [m for m in members if m in excluded]
        if usable_by_option is not None:
            usable_by_option[option_id] = usable
        notes = f"candidates: {', '.join(usable) or 'none'}"
        if dropped:
            notes += f"; excluded by DesignGuard/verifier: {', '.join(dropped)}"
        if option_id == top_key:
            notes += f"; top-ranked ({basis})"
        options.append(
            DecisionOption(
                id=option_id,
                label=label,
                evidence_ids=evidence,
                recommended=option_id == top_key,
                eligible=bool(usable),
                notes=notes,
            )
        )
    return options, top_key


def _tool_evidence(state: RunState, *tools: str) -> list[str]:
    return [r.id for r in state.evidence_for(tools=set(tools))]


def build_options(dr_id: str, state: RunState, config_dir: Path | None) -> list[DecisionOption]:
    """Options for one Decision Record from the current run state."""
    return _build_options(dr_id, state, config_dir, {})


def _build_options(
    dr_id: str, state: RunState, config_dir: Path | None, members: Members
) -> list[DecisionOption]:
    """``build_options``; ``members`` receives option id → usable candidate ids."""
    by_id = {c.id: c for c in state.candidates}
    if dr_id == "DR-01":
        options, _ = _group_options(
            state, lambda c: (f"{c.doublets}-doublets", f"{c.doublets} doublet(s)"), members
        )
        return sorted(options, key=lambda o: o.id)
    if dr_id == "DR-02":
        basis = load_config("design_basis", config_dir)
        options, _ = _group_options(
            state,
            lambda c: (f"depth-{c.depth_m:g}m", f"Reservoir at {c.depth_m:g} m TVD"),
            members,
        )
        default = f"depth-{basis.reservoir.mid_depth_m:g}m"
        for option in options:
            if option.id == default:
                option.evidence_ids.append("config:design_basis.yaml#DB-01")
        return options
    if dr_id == "DR-03":
        return _group_options(state, lambda c: _HEAT_TOPUP.get(_base_concept(c, by_id)), members)[0]
    if dr_id == "DR-04":
        return _group_options(state, lambda c: _COOLING.get(_base_concept(c, by_id)), members)[0]
    if dr_id == "DR-05":
        return _group_options(
            state,
            lambda c: (
                ("seasonal-ht-ates", "Seasonal HT-ATES storage (C6)")
                if c.concept == "C6"
                else ("no-storage", "No seasonal storage")
            ),
            members,
        )[0]
    if dr_id == "DR-07":
        return _group_options(
            state,
            lambda c: (
                (c.gas_handling.value.replace("_", "-"), f"Gas handling: {c.gas_handling.value}")
                if c.gas_handling
                else None
            ),
            members,
        )[0]
    if dr_id == "DR-06":
        evidence = _tool_evidence(state, "optimise_design") + ["config:assumptions.yaml#DB-30"]
        return [
            DecisionOption(
                id="optimiser-lowest-load",
                label="Outages placed by the optimiser in the lowest-load windows, never "
                "overlapping (DB-30)",
                evidence_ids=evidence,
                recommended=True,
            ),
            DecisionOption(
                id="common-summer-block",
                label="One common summer block for all doublets",
                evidence_ids=evidence,
                eligible=False,
                notes="outages would overlap; DB-30 requires non-overlapping outages",
            ),
        ]
    if dr_id == "DR-08":
        register = load_config("assumptions", config_dir)
        entry = register.get("DB-31")
        answered = state.answer_values().get("DB-31", entry.value)
        values = [entry.value] + [v for v in (entry.sensitivity or []) if v != entry.value]
        if answered not in values:
            values.insert(0, answered)
        evidence = ["config:assumptions.yaml#DB-31"] + _tool_evidence(state, "simulate_annual")
        return [
            DecisionOption(
                id=f"dh-return-{float(v):g}",
                label=f"District-heating return {float(v):g} degC",
                evidence_ids=evidence,
                recommended=v == answered,
                notes="user answer" if v == answered and answered != entry.value else "",
            )
            for v in values
        ]
    if dr_id == "DR-09":
        evidence = _tool_evidence(state, "load_design_basis") + ["spec:§10.1"]
        return [
            DecisionOption(
                id="titanium-plates-no-cu",
                label="Titanium HEX plates, no copper alloys in contact with brine",
                evidence_ids=evidence,
                recommended=True,
                notes="spec §10.1 materials check (saline brine)",
            ),
            DecisionOption(
                id="stainless-plates",
                label="Stainless-steel HEX plates",
                evidence_ids=evidence,
                eligible=False,
                notes="fails the §10.1 materials check for saline brine",
            ),
        ]
    if dr_id == "DR-10":
        options = []
        for candidate_id in state.shortlist or ([state.selected_id] if state.selected_id else []):
            verification = state.latest_verification(candidate_id)
            evidence = [r.id for r in state.evidence_for(candidate_ids={candidate_id})]
            # A design whose latest verdict is REJECTED (or that failed DesignGuard) is never the
            # final selection: DR-10 is skipped and the P9 gate loops back or escalates.
            rejected = verification is not None and verification.verdict == DesignVerdict.REJECTED
            selected = (
                candidate_id == state.selected_id
                and not rejected
                and candidate_id not in state.guard_failed
            )
            verdict = verification.verdict.value if verification else "not verified"
            options.append(
                DecisionOption(
                    id=candidate_id,
                    label=(
                        f"{candidate_id} ({by_id[candidate_id].description})"
                        if candidate_id in by_id
                        else candidate_id
                    ),
                    evidence_ids=evidence,
                    recommended=selected,
                    eligible=selected,
                    notes=(
                        f"verifier verdict: {verdict}"
                        if selected or candidate_id == state.selected_id
                        else "not the verified design (only the verified design can be selected)"
                    ),
                )
            )
        return options
    raise KeyError(dr_id)


def evidence_keys(state: RunState, options: list[DecisionOption]) -> list[str]:
    """Citable keys of the options' evidence: result field names, tools and assumption ids."""
    records = {record.id: record for record in state.tool_results}
    keys: dict[str, None] = {}
    for option in options:
        for evidence_id in option.evidence_ids:
            record = records.get(evidence_id)
            if record is not None:
                keys[record.tool] = None
                keys.update(dict.fromkeys(record.data_keys))
            elif evidence_id.startswith("config:") and "#" in evidence_id:
                keys[evidence_id.rsplit("#", 1)[1]] = None
    return sorted(keys)


def _evidence_with(state: RunState, options: list[DecisionOption], key: str) -> list[str]:
    """Evidence ids of the options whose tool result holds ``key`` (or is the tool ``key``)."""
    wanted = {e for option in options for e in option.evidence_ids}
    return [
        r.id for r in state.tool_results if r.id in wanted and (key in r.data_keys or r.tool == key)
    ]


def _rule_criterion(
    dr_id: str,
    state: RunState,
    options: list[DecisionOption],
    keys: list[str],
) -> DecisionCriterion | None:
    """Return the workflow's recommendation rule as a criterion (best = recommended option)."""
    best = [o.id for o in options if o.eligible and o.recommended]
    if not best:
        best = [next(o.id for o in options if o.eligible)]
        recommended = False
    else:
        recommended = True

    def pick(*preferred: str) -> str | None:
        return next((key for key in preferred if key in keys), None)

    def make(cid: str, key: str, source: str, text: str) -> DecisionCriterion:
        evidence = _evidence_with(state, options, key) or sorted(
            {e for o in options if o.id in best for e in o.evidence_ids}
        )
        return DecisionCriterion(
            id=cid, key=key, source=source, text=text, best_option_ids=best, evidence_ids=evidence
        )

    if dr_id == "DR-06" and recommended and "DB-30" in keys:
        text = "only option meeting DB-30: non-overlapping outages in the lowest-load windows"
        return make("db30-non-overlapping", "DB-30", "assumptions register", text)
    if dr_id == "DR-08" and "DB-31" in keys:
        answered = any(a.assumption_id == "DB-31" and a.source == "user" for a in state.answers)
        how = "user answer" if answered else "register default kept"
        text = f"{how} for the district-heating return temperature (DB-31)"
        return make("db31-answer", "DB-31", "assumptions register", text)
    if dr_id == "DR-09":
        key = pick("brine", "load_design_basis")
        if key is not None:
            text = f"materials check of spec §10.1 for saline brine ({key})"
            return make("spec-materials-check", key, "load_design_basis", text)
    if dr_id == "DR-10":
        key = pick("verdict")
        if key is not None:
            text = "selected design whose latest verifier verdict is not REJECTED"
            return make("selected-not-rejected", key, "verify_design", text)
        key = next(iter(keys), None)
        if key is None:
            return None
        return make(
            "selected-design", key, key, f"only eligible option: the selected design ({key})"
        )
    if dr_id in _CANDIDATE_DRS and recommended and ranking.has_kpi_evidence(state.kpis):
        source = pick(*_KPI_TOOLS)
        if source is not None and "lcoh_per_mwh" in keys:
            text = (
                f"lowest lcoh_per_mwh among guard-passed candidates without unmet demand "
                f"in {source}"
            )
            return make("ranking-lowest-lcoh_per_mwh", "lcoh_per_mwh", source, text)
    if dr_id in _CANDIDATE_DRS and "passed" in keys:
        text = "first option in generation order whose candidates passed the guard; no cost yet"
        return make("generation-order-passed", "passed", "design_guard", text)
    key = next(iter(keys), None)
    if key is None:
        return None
    text = f"first eligible option in the offered order ({key})"
    return make("offered-order", key, key, text)


_CANDIDATE_DRS = frozenset({"DR-01", "DR-02", "DR-03", "DR-04", "DR-05", "DR-07"})
"""DRs whose options group screened candidates (DR-10 offers the shortlist itself)."""


def _kpi_criteria(
    state: RunState,
    options: list[DecisionOption],
    members: Members,
    keys: list[str],
) -> list[DecisionCriterion]:
    """KPI criteria that discriminate between the eligible options (values from ``state.kpis``)."""
    eligible = [o for o in options if o.eligible]
    if len(eligible) < 2:
        return []
    criteria = []
    for key, direction in KPI_CRITERIA:
        if key not in keys:
            continue
        choose = min if direction == "lowest" else max
        values: dict[str, float] = {}
        for option in eligible:
            found = [
                getattr(state.kpis[c], key)
                for c in members.get(option.id, [])
                if c in state.kpis and getattr(state.kpis[c], key) is not None
            ]
            if found:
                values[option.id] = choose(found)
        if not values:
            continue
        target = choose(values.values())
        best = [option_id for option_id, value in values.items() if value == target]
        if len(best) == len(eligible):
            continue  # every option ties: the criterion cannot decide
        source = next(
            (r.tool for r in reversed(state.tool_results) if r.tool in _KPI_TOOLS and r.ok), None
        )
        where = f" in {source}" if source in keys else ""
        criteria.append(
            DecisionCriterion(
                id=f"{direction}-{key}",
                key=key,
                source=source or "tool results",
                text=f"{direction} {key} among the eligible options{where}",
                best_option_ids=best,
                evidence_ids=_evidence_with(state, options, key),
            )
        )
    return criteria


def build_criteria(
    dr_id: str,
    state: RunState,
    options: list[DecisionOption],
    members: Members,
    keys: list[str],
) -> list[DecisionCriterion]:
    """Decisive criteria for a question: the recommendation rule first, then KPI criteria."""
    rule = _rule_criterion(dr_id, state, options, keys)
    criteria = [rule] if rule is not None else []
    for criterion in _kpi_criteria(state, options, members, keys):
        duplicate = any(
            c.key == criterion.key and c.best_option_ids == criterion.best_option_ids
            for c in criteria
        )
        if not duplicate:
            criteria.append(criterion)
    allowed = set(keys) | {o.id for o in options}
    # A criterion may only cite keys the evidence holds (DecisionQuestion re-checks this).
    return [c for c in criteria if all(k in allowed for k in [c.key, *key_tokens(c.text)])]


def build_question(dr_id: str, state: RunState, phase: Phase, config_dir: Path | None):
    """``DecisionQuestion`` for a DR, or None if the run has no options for it yet."""
    members: Members = {}
    options = _build_options(dr_id, state, config_dir, members)
    if not options or not any(option.eligible for option in options):
        return None
    if dr_id == "DR-10":
        members = {option.id: [option.id] for option in options}
    keys = evidence_keys(state, options)
    question, role = DR_QUESTIONS[dr_id]
    return DecisionQuestion(
        dr_id=dr_id,  # type: ignore[arg-type]
        title=DR_TITLES[dr_id],
        question=question,
        phase=phase,
        role=role,
        options=options,
        criteria=build_criteria(dr_id, state, options, members, keys),
        evidence_keys=keys,
    )


def make_record(
    question: DecisionQuestion,
    choice: OptionChoice,
    source: DecisionSource,
    state: RunState,
) -> DecisionRecord:
    """Combine question and choice into an AG-07 Decision Record."""
    chosen = next(option for option in question.options if option.id == choice.option_id)
    evidence = sorted({e for option in question.options for e in option.evidence_ids})
    stub_ids = {r.id for r in state.tool_results if r.stub}
    rejected = list(choice.rejected)
    missing = {o.id for o in question.options} - {r.option_id for r in rejected} - {chosen.id}
    for option in question.options:
        if option.id in missing:
            rejected.append(RejectedOption(option_id=option.id, reason="Not chosen."))
    return DecisionRecord(
        dr_id=question.dr_id,
        title=question.title,
        run_id=state.run_id,
        phase=question.phase,
        role=question.role,
        question=question.question,
        options=question.options,
        evidence_ids=evidence,
        choice=chosen.id,
        choice_label=chosen.label,
        criterion=question.criterion(choice.criterion),
        rationale=choice.rationale,
        rejected=rejected,
        decision_source=source,
        stub_evidence=bool(stub_ids & set(evidence)),
    )


def render_markdown(record: DecisionRecord) -> str:
    """Human-readable Decision Record."""
    lines = [
        f"# {record.dr_id} {record.title}",
        "",
        f"- Run: `{record.run_id}` · phase {record.phase.value} · role {record.role.value}",
        f"- Decision source: **{record.decision_source}**",
        f"- Question: {record.question}",
        f"- **Choice: {record.choice}** — {record.choice_label}",
    ]
    if record.criterion is not None:
        evidence = ", ".join(record.criterion.evidence_ids) or "—"
        lines.append(
            f"- Decisive criterion: {record.criterion.text} (key `{record.criterion.key}` from "
            f"{record.criterion.source}; evidence {evidence})"
        )
    lines += [f"- Why: {record.rationale}"]
    if record.stub_evidence:
        lines.append("- WARNING: evidence includes stub tool results; not a final decision.")
    lines += ["", "| Option | Label | Eligible | Recommended | Evidence |", "|---|---|---|---|---|"]
    for option in record.options:
        evidence = ", ".join(option.evidence_ids) or "—"
        lines.append(
            f"| {option.id} | {option.label} | {'yes' if option.eligible else 'no'} | "
            f"{'yes' if option.recommended else 'no'} | {evidence} |"
        )
    lines += ["", "Rejected options:"]
    lines += [f"- {item.option_id}: {item.reason}" for item in record.rejected] or ["- none"]
    return "\n".join(lines) + "\n"


def write_records(records: list[DecisionRecord], out_dir: Path) -> list[Path]:
    """Write ``DR-xx.json`` + ``DR-xx.md`` per record and an ``index.md``; return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    index = ["# Decision Records", "", "| DR | Title | Choice | Source | Stub evidence |"]
    index.append("|---|---|---|---|---|")
    for record in sorted(records, key=lambda r: r.dr_id):
        json_path = out_dir / f"{record.dr_id}.json"
        md_path = out_dir / f"{record.dr_id}.md"
        json_path.write_text(json.dumps(record.model_dump(mode="json"), indent=2), "utf-8")
        md_path.write_text(render_markdown(record), "utf-8")
        paths += [json_path, md_path]
        index.append(
            f"| [{record.dr_id}]({md_path.name}) | {record.title} | {record.choice} | "
            f"{record.decision_source} | {'yes' if record.stub_evidence else 'no'} |"
        )
    index_path = out_dir / "index.md"
    index_path.write_text("\n".join(index) + "\n", "utf-8")
    paths.append(index_path)
    return paths
