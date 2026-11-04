"""Decision Records DR-01…DR-10 (AG-07): options offered by the workflow, written as JSON + MD.

Options come from tool results and the configuration, never from an LLM. ``recommended`` is set
only by a deterministic rule: the option holding the top candidate of
``ranking.rank_candidates`` (the optimiser's rule, so DRs agree with the selected design), a
register default (DR-06, DR-08, DR-11) or a spec rule (DR-09). Evidence ids are
tool-result ids (``TR-…``) or config references (``config:…``).

DR-11 (pre-L-2) is the one record whose choice changes the design: the P8 workflow step
``robust_backup`` prices the TL-15 robust backups as a variant; the option chosen here is the
design the runner selects for P9 (``installed_design``). OPT-ROBUST-BACKUP sets the
recommendation; the probability of meeting demand and the LCOH + LCOC change are offered as
criteria, so either engine can decide either way on the evidence.

Criteria (L-1 follow-up): each question also offers decisive criteria evaluated here on the tool
results — the recommendation rule first, then KPI criteria that discriminate between the
eligible options — and the evidence keys a rationale may cite (field names of the evidence tool
results, the tools, assumption ids). A choice names one criterion and must be one of its best
options, so its rationale is grounded and cannot contradict itself.

Review loop (2026-11-03): DR-12 "User review" is written by ``runner/review.py``; an adopted
``OPT-ROBUST-BACKUP=off`` makes the DR-11 robust option ineligible in the rerun pass.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from gemini_agent.runner import ranking
from gemini_agent.runner.state import RobustBackup, RunState
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
    "DR-11": (
        (
            "Which peak-backup capacity is installed: the base-year nameplates or the TL-15 "
            "robust nameplates?"
        ),
        Role.ECONOMICS,
    ),
    "DR-12": ("Any objection to the recommended design?", Role.LEAD_ENGINEER),
}
"""DR-12 (review loop, 2026-11-03) is built by ``runner/review.py`` from the user's objection and
the what-if result, not by :func:`build_question`."""

ROBUST_BACKUP_ID = "OPT-ROBUST-BACKUP"
"""Register entry of the DR-11 recommendation rule (pre-L-2)."""
INSTALLED_BACKUP_OPTION = "installed-backup"


@dataclass(frozen=True)
class RobustBackupRule:
    """OPT-ROBUST-BACKUP: which DR-11 option the rule recommends, at which TL-15 percentile."""

    recommend: Literal["robust", "installed"]
    percentile: float

    @property
    def option_id(self) -> str:
        """DR-11 option id of the robust backups, e.g. ``p90-robust-backup``."""
        return f"p{self.percentile:g}-robust-backup"


def robust_backup_rule(config_dir: Path | None = None) -> RobustBackupRule:
    """Load OPT-ROBUST-BACKUP; its percentile must be the one TL-15 used (UQ-MC)."""
    register = load_config("assumptions", config_dir)
    value: Any = register.get(ROBUST_BACKUP_ID).value
    recommend = value.get("recommend")
    if recommend not in ("robust", "installed"):
        raise ValueError(f"{ROBUST_BACKUP_ID}.recommend must be robust or installed: {recommend}")
    percentile = float(value["robust_backup_percentile"])
    uq: Any = register.get("UQ-MC").value
    if abs(percentile - float(uq["robust_backup_percentile"])) > 1e-9:
        raise ValueError(
            f"{ROBUST_BACKUP_ID}.robust_backup_percentile {percentile:g} differs from UQ-MC "
            f"robust_backup_percentile {uq['robust_backup_percentile']} (TL-15 computes "
            "robust_backup_mw at the UQ-MC value)"
        )
    return RobustBackupRule(recommend, percentile)


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


def _resource_note(state: RunState, config_dir: Path | None) -> str:
    """Productivity basis of DR-01: reservoir permeability (design basis) + the TL-04 summary."""
    record = next(
        (r for r in reversed(state.tool_results) if r.tool == "productivity_index" and r.ok), None
    )
    if record is None or record.stub:
        return ""
    basis = load_config("design_basis", config_dir)
    return (
        f"resource: permeability {basis.reservoir.permeability_md:g} mD (design basis); "
        f"{record.id} {record.summary}"
    )


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
        # WP-14: the doublet count rests on the well productivity; quote TL-04's own summary.
        resource = _resource_note(state, config_dir)
        for option in options:
            option.notes = f"{option.notes}; {resource}"[:500] if resource else option.notes
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
    if dr_id == "DR-11":
        return _backup_options(state, config_dir, members)
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


def _mw(sizes: Mapping[str, float]) -> str:
    return ", ".join(f"{key} {value:.2f}" for key, value in sorted(sizes.items())) or "none"


def _backup_evidence(state: RunState, backup: RobustBackup) -> list[str]:
    """Return the robust_backup result, the TL-15 result it rests on and the register entries."""
    uncertainty = [
        r.id
        for r in state.evidence_for(candidate_ids={backup.candidate_id}, tools={"uncertainty"})
        if r.tool == "uncertainty"
    ]
    return (
        [backup.tool_result_id]
        + uncertainty[-1:]
        + [f"config:assumptions.yaml#{ROBUST_BACKUP_ID}", "config:assumptions.yaml#UQ-MC"]
    )


def _backup_options(
    state: RunState, config_dir: Path | None, members: Members
) -> list[DecisionOption]:
    """DR-11 options: keep the installed backups or install the TL-15 robust ones."""
    backup = state.robust_backup
    if backup is None or backup.candidate_id != state.selected_id:
        return []  # no robust_backup result for the selected design yet
    rule = robust_backup_rule(config_dir)
    excluded = state.excluded_ids()
    evidence = _backup_evidence(state, backup)
    robust_id = backup.robust_candidate_id
    installed_ok = backup.candidate_id not in excluded
    robust_ok = robust_id is not None and backup.robust_meets_demand and robust_id not in excluded
    # Review loop (DR-12): a user who adopted OPT-ROBUST-BACKUP=off removed the robust backups;
    # the option stays visible but is not eligible in this pass (the user's decision stands).
    user_off = state.review_levers.get(ROBUST_BACKUP_ID) is False
    robust_ok = robust_ok and not user_off
    p0, p1 = backup.probability_demand_met, backup.probability_demand_met_robust
    raises = robust_ok and p0 is not None and p1 is not None and p1 > p0
    install = raises and (rule.recommend == "robust" or not installed_ok)
    members[INSTALLED_BACKUP_OPTION] = [backup.candidate_id] if installed_ok else []
    members[rule.option_id] = [robust_id] if robust_ok and robust_id else []

    def prob(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    def signed(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.2f}"

    installed_notes = (
        f"{backup.candidate_id}: TL-15 P(demand met) {prob(p0)} with the installed backups"
        + ("; recommended (OPT-ROBUST-BACKUP)" if not install else "")
    )
    if robust_ok:
        robust_notes = (
            f"{robust_id}: TL-15 P(demand met) {prob(p1)}; vs {backup.candidate_id} "
            f"ΔLCOH {signed(backup.delta_lcoh_per_mwh)}, ΔLCOC {signed(backup.delta_lcoc_per_mwh)}"
            f" USD/MWh, ΔCAPEX {signed(backup.delta_capex_million)} MUSD ({backup.year_mode} "
            "year)" + ("; recommended (OPT-ROBUST-BACKUP)" if install else "")
        )
    elif user_off:
        robust_notes = "removed by the user review (DR-12: OPT-ROBUST-BACKUP off)"
    elif robust_id is not None and robust_id in excluded:
        robust_notes = f"{robust_id} excluded by DesignGuard/verifier"
    elif robust_id is not None:
        robust_notes = f"{robust_id} does not meet demand on every base-year day"
    else:
        robust_notes = backup.reason or "no robust variant evaluated"
    return [
        DecisionOption(
            id=INSTALLED_BACKUP_OPTION,
            label=f"Keep the base-year backup nameplates of {backup.candidate_id} "
            f"({_mw(backup.installed_backup_mw)} MW)",
            evidence_ids=evidence,
            recommended=installed_ok and not install,
            eligible=installed_ok,
            notes=installed_notes[:500],
        ),
        DecisionOption(
            id=rule.option_id,
            label=f"Install the TL-15 P{rule.percentile:g} robust backups "
            f"({_mw(backup.robust_backup_mw)} MW)" + (f" as {robust_id}" if robust_id else ""),
            evidence_ids=evidence,
            recommended=install,
            eligible=bool(robust_ok),
            notes=robust_notes[:500],
        ),
    ]


def installed_design(record: DecisionRecord, state: RunState) -> str | None:
    """Design a DR-11 choice installs (None for other records or without its evidence)."""
    backup = state.robust_backup
    if record.dr_id != "DR-11" or backup is None:
        return None
    if record.choice == INSTALLED_BACKUP_OPTION:
        return backup.candidate_id
    return backup.robust_candidate_id


def _backup_criteria(
    state: RunState, options: list[DecisionOption], keys: list[str]
) -> list[DecisionCriterion]:
    """DR-11 evidence criteria: probability of meeting demand vs the LCOH + LCOC change."""
    backup = state.robust_backup
    eligible = [o.id for o in options if o.eligible]
    if backup is None or len(eligible) < 2:
        return []
    robust = next(o for o in eligible if o != INSTALLED_BACKUP_OPTION)
    criteria = []
    p0, p1 = backup.probability_demand_met, backup.probability_demand_met_robust
    if p0 is not None and p1 is not None and p0 != p1 and "probability_demand_met" in keys:
        criteria.append(
            DecisionCriterion(
                id="highest-probability_demand_met",
                key="probability_demand_met",
                source="robust_backup",
                text="highest probability_demand_met over the TL-15 samples in robust_backup",
                best_option_ids=[robust if p1 > p0 else INSTALLED_BACKUP_OPTION],
                evidence_ids=_evidence_with(state, options, "probability_demand_met"),
            )
        )
    change = backup.delta_lcoh_plus_lcoc_per_mwh
    key = "delta_lcoh_plus_lcoc_per_mwh"
    if change is not None and abs(change) > 1e-9 and key in keys:
        criteria.append(
            DecisionCriterion(
                id=f"lowest-{key}",
                key=key,
                source="robust_backup",
                text=f"lowest {key}: the LCOH plus LCOC change in robust_backup",
                best_option_ids=[INSTALLED_BACKUP_OPTION if change > 0 else robust],
                evidence_ids=_evidence_with(state, options, key),
            )
        )
    return criteria


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
        # Evidence of the options the criterion selects (pre-L-2: DR-10 after DR-11 must not
        # cite the verification of the design DR-11 replaced).
        winners = [o for o in options if o.id in best]
        evidence = _evidence_with(state, winners, key) or sorted(
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
    if dr_id == "DR-11" and recommended and ROBUST_BACKUP_ID in keys:
        if best[0] == INSTALLED_BACKUP_OPTION:
            text = f"register rule {ROBUST_BACKUP_ID} keeps installed_backup_mw for this design"
        else:
            text = (
                f"register rule {ROBUST_BACKUP_ID}: install robust_backup_mw when it raises "
                "probability_demand_met"
            )
        return make("opt-robust-backup", ROBUST_BACKUP_ID, "assumptions register", text)
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
            text = (  # WP-14: the ranking key is the TL-13 objective LCOH + LCOC (ranking.py)
                f"lowest lcoh_per_mwh + lcoc_per_mwh among guard-passed candidates without "
                f"unmet demand in {source}"
            )
            return make("ranking-lowest-lcoh_per_mwh", "lcoh_per_mwh", source, text)
    if dr_id in _CANDIDATE_DRS and "passed" in keys:
        if not recommended:
            text = "first eligible option in the offered order; its candidates passed the guard"
            return make("first-eligible-passed", "passed", "design_guard", text)
        if ranking.has_kpi_evidence(state.kpis):
            text = "option holding the top-ranked candidate that passed the guard"
            return make("top-ranked-passed", "passed", "design_guard", text)
        text = (
            "option holding the first candidate in generation order that passed the guard; "
            "costs not computed yet"
        )
        return make("generation-order-passed", "passed", "design_guard", text)
    key = next(iter(keys), None)
    if key is None:
        return None
    if recommended:
        return make(
            "workflow-recommended", key, key, f"option the workflow rule recommends ({key})"
        )
    return make("offered-order", key, key, f"first eligible option in the offered order ({key})")


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
        if len(values) < 2:
            continue  # an option without data cannot lose: nothing to compare
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
    extra = _backup_criteria(state, options, keys) if dr_id == "DR-11" else []
    for criterion in extra + _kpi_criteria(state, options, members, keys):
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
    recommended = [o.id for o in record.options if o.recommended]
    if recommended and record.choice not in recommended:
        if record.dr_id == "DR-11":
            follows = "the chosen option is installed and verified in P9"
        elif record.dr_id == "DR-12":
            follows = "the user's review decision stands; the P9 DesignVerifier verifies the design"
        else:
            follows = "the selected design follows the workflow ranking"
        lines.append(
            f"- Note: differs from the workflow-recommended option {', '.join(recommended)}; "
            f"{follows}."
        )
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
