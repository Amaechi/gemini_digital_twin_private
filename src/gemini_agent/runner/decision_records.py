"""Decision Records DR-01…DR-10 (AG-07): options offered by the workflow, written as JSON + MD.

Options come from tool results and the configuration, never from an LLM. ``recommended`` is set
only by a deterministic rule: the option holding the top candidate of
``ranking.rank_candidates`` (the optimiser's rule, so DRs agree with the selected design), a
register default (DR-06, DR-08) or a spec rule (DR-09). Evidence ids are
tool-result ids (``TR-…``) or config references (``config:…``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from gemini_agent.runner import ranking
from gemini_agent.runner.state import RunState
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.decision import (
    DR_TITLES,
    DecisionOption,
    DecisionQuestion,
    DecisionRecord,
    DecisionSource,
    OptionChoice,
    Phase,
    RejectedOption,
    Role,
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


def _group_options(
    state: RunState, key_fn: Callable[[Candidate], tuple[str, str] | None]
) -> tuple[list[DecisionOption], str | None]:
    """Group geothermal candidates into options; return the options and the top-ranked key."""
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
    ranked = ranking.rank_candidates(state.candidates, state.kpis)
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
        options.append(
            DecisionOption(
                id=option_id,
                label=label,
                evidence_ids=evidence,
                recommended=option_id == top_key,
                notes=f"candidates: {', '.join(members)}"
                + (f"; top-ranked ({basis})" if option_id == top_key else ""),
            )
        )
    return options, top_key


def _tool_evidence(state: RunState, *tools: str) -> list[str]:
    return [r.id for r in state.evidence_for(tools=set(tools))]


def build_options(dr_id: str, state: RunState, config_dir: Path | None) -> list[DecisionOption]:
    """Options for one Decision Record from the current run state."""
    by_id = {c.id: c for c in state.candidates}
    if dr_id == "DR-01":
        options, _ = _group_options(
            state, lambda c: (f"{c.doublets}-doublets", f"{c.doublets} doublet(s)")
        )
        return sorted(options, key=lambda o: o.id)
    if dr_id == "DR-02":
        basis = load_config("design_basis", config_dir)
        options, _ = _group_options(
            state, lambda c: (f"depth-{c.depth_m:g}m", f"Reservoir at {c.depth_m:g} m TVD")
        )
        default = f"depth-{basis.reservoir.mid_depth_m:g}m"
        for option in options:
            if option.id == default:
                option.evidence_ids.append("config:design_basis.yaml#DB-01")
        return options
    if dr_id == "DR-03":
        return _group_options(state, lambda c: _HEAT_TOPUP.get(_base_concept(c, by_id)))[0]
    if dr_id == "DR-04":
        return _group_options(state, lambda c: _COOLING.get(_base_concept(c, by_id)))[0]
    if dr_id == "DR-05":
        return _group_options(
            state,
            lambda c: (
                ("seasonal-ht-ates", "Seasonal HT-ATES storage (C6)")
                if c.concept == "C6"
                else ("no-storage", "No seasonal storage")
            ),
        )[0]
    if dr_id == "DR-07":
        return _group_options(
            state,
            lambda c: (
                (c.gas_handling.value.replace("_", "-"), f"Gas handling: {c.gas_handling.value}")
                if c.gas_handling
                else None
            ),
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
            selected = candidate_id == state.selected_id
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
                        if selected
                        else "not the verified design (only the verified design can be selected)"
                    ),
                )
            )
        return options
    raise KeyError(dr_id)


def build_question(dr_id: str, state: RunState, phase: Phase, config_dir: Path | None):
    """``DecisionQuestion`` for a DR, or None if the run has no options for it yet."""
    options = build_options(dr_id, state, config_dir)
    if not options or not any(option.eligible for option in options):
        return None
    question, role = DR_QUESTIONS[dr_id]
    return DecisionQuestion(
        dr_id=dr_id,  # type: ignore[arg-type]
        title=DR_TITLES[dr_id],
        question=question,
        phase=phase,
        role=role,
        options=options,
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
        f"- Why: {record.rationale}",
    ]
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
