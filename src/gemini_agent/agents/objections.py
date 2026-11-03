"""Deterministic objection → lever mapping of the review loop (``--engine policy``, AG-12).

The review loop (2026-11-03, FINAL_SPRINT §E) asks the user for an objection to the recommended
design after the P9 verdict and maps the free text to exactly one bounded what-if lever
(``tools.whatif.LEVERS``). With the LLM engine the Lead Engineer agent does the mapping
(``prompts.system.review_prompt``, ``LeverChoice`` output); this module is the policy engine's
version and the fallback: a few keyword patterns for the levers a reviewer names most —
backup/boiler size, return temperature, number of doublets, depth (→ a screened candidate of
another depth), flow, concept, DB-30 availability. Anything else is "no lever matches" and the
runner asks again.

Numbers in the result are copied from the user's text or from the offered candidate briefs
(golden rule 1: nothing is computed here; ``more`` / ``fewer`` doublets step the current count by
one and the DesignGuard bounds it in the what-if).
"""

from __future__ import annotations

import re

from gemini_agent.schemas.decision import CandidateBrief, LeverChoice, ReviewQuestion

_NUMBER = re.compile(r"(?<![\w-])(\d+(?:[.,]\d+)?)(?:\s*(km|m)\b)?", re.IGNORECASE)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "single": 1, "double": 2, "triple": 3}
_BACKUP_WORDS = ("boiler", "backup", "back-up", "peak unit", "reserve", "chiller capacity")
_SMALLER = (
    "too big",
    "too large",
    "oversiz",
    "big",
    "large",
    "smaller",
    "remove",
    "less",
    "reduce",
    "excessive",
    "too much",
    "over",
    "without",
    "drop",
)
_LARGER = (
    "too small",
    "undersiz",
    "bigger",
    "larger",
    "more",
    "robust",
    "increase",
    "not enough",
    "insufficient",
    "add",
    "install",
)
_MORE = ("more", "another", "add", "extra", "additional", "increase")
_FEWER = ("fewer", "less", "remove", "drop", "reduce", "one less")
_CONCEPT = re.compile(r"\bc([1-8])\b", re.IGNORECASE)
_CONCEPT_WORDS = {"absorption": ("C3", "C4"), "storage": ("C6",), "ates": ("C6",)}


def _first_number(text: str) -> tuple[float, str] | None:
    """First number in the text with its unit suffix (``km`` / ``m`` / '')."""
    match = _NUMBER.search(text)
    if match is None:
        for word, value in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", text):
                return float(value), ""
        return None
    value = float(match.group(1).replace(",", "."))
    return value, (match.group(2) or "").lower()


def _choice(lever: str, value: str, rationale: str) -> LeverChoice:
    return LeverChoice(lever=lever, value=value, rationale=rationale)


def _no_match(rationale: str) -> LeverChoice:
    return LeverChoice(lever=None, value=None, rationale=rationale)


def _offered(question: ReviewQuestion, lever_id: str) -> bool:
    return question.lever(lever_id) is not None


def _nearest(candidates: list[CandidateBrief], depth_m: float) -> CandidateBrief | None:
    with_depth = [c for c in candidates if c.depth_m is not None]
    if not with_depth:
        return None
    return min(with_depth, key=lambda c: abs((c.depth_m or 0.0) - depth_m))


def _depth_candidate(question: ReviewQuestion, text: str) -> LeverChoice:
    """``deeper`` / ``shallower`` / a depth → the screened candidate nearest that depth."""
    others = [c for c in question.candidates if c.id != question.selected.id]
    current = question.selected.depth_m
    number = _first_number(text)
    picked: CandidateBrief | None = None
    if number is not None:
        value, unit = number
        depth = value * 1000.0 if unit == "km" or value < 20 else value
        picked = _nearest(others, depth)
    elif "deep" in text and current is not None:
        deeper = [c for c in others if c.depth_m is not None and c.depth_m > current]
        picked = min(deeper, key=lambda c: c.depth_m or 0.0) if deeper else None
    elif "shallow" in text and current is not None:
        shallower = [c for c in others if c.depth_m is not None and c.depth_m < current]
        picked = max(shallower, key=lambda c: c.depth_m or 0.0) if shallower else None
    if picked is None or not _offered(question, "concept"):
        return _no_match(
            "The objection is about the well depth, but no screened candidate of the run at "
            "another depth is available to re-pick (depth is not a direct lever)."
        )
    return _choice(
        "concept",
        picked.id,
        "The objection is about the well depth; the concept lever re-picks the screened "
        "candidate of the run nearest the requested depth.",
    )


def map_objection_by_keywords(question: ReviewQuestion) -> LeverChoice:
    """Keyword mapping of ``question.objection`` to one offered lever (see module doc)."""
    text = " ".join(question.objection.lower().split())
    number = _first_number(text)

    if any(word in text for word in _BACKUP_WORDS) and _offered(question, "OPT-ROBUST-BACKUP"):
        if any(word in text for word in _SMALLER):
            return _choice(
                "OPT-ROBUST-BACKUP",
                "off",
                "The objection names the backup (boiler / chiller) size as too large; the only "
                "backup lever removes the TL-15 robust backups (DR-11).",
            )
        if any(word in text for word in _LARGER):
            return _choice(
                "OPT-ROBUST-BACKUP",
                "on",
                "The objection asks for more backup capacity; the backup lever installs the "
                "TL-15 robust backups (DR-11).",
            )
        return _no_match(
            "The objection names the backup units but not a direction (too large or too small); "
            "say which, or name a lever."
        )
    if "return" in text or "db-31" in text or "db-32" in text:
        cooling = "cool" in text or "db-32" in text
        lever = "DB-32" if cooling else "DB-31"
        if number is None:
            return _no_match(
                f"The objection is about the {'cooling' if cooling else 'heating'} return "
                f"temperature ({lever}) but states no value; give the value in degC."
            )
        if not _offered(question, lever):
            return _no_match(f"The return-temperature lever {lever} is not offered.")
        return _choice(
            lever,
            f"{number[0]:g}",
            f"The objection states a {'cooling' if cooling else 'heating'} return temperature; "
            f"it is copied as the {lever} value.",
        )
    # Flow before doublets: a flow is stated "per doublet".
    if "flow" in text and _offered(question, "design_flow_m3h"):
        if number is None:
            return _no_match("The objection is about the flow but states no value in m3/h.")
        return _choice(
            "design_flow_m3h",
            f"{number[0]:g}",
            "The objection states a design flow per doublet; it is copied as the flow lever "
            "(DesignGuard DB-11 checks it).",
        )
    if "doublet" in text and _offered(question, "doublets"):
        current = question.selected.doublets
        digits = re.search(r"\d", text) is not None
        if not digits and any(word in text for word in _MORE):
            count = current + 1
        elif not digits and any(word in text for word in _FEWER):
            count = current - 1
        elif number is not None and number[1] == "":
            count = int(number[0])
        else:
            return _no_match(
                "The objection is about the doublets but states no count or direction; say how "
                "many doublets (the DesignGuard bounds the count)."
            )
        return _choice(
            "doublets",
            str(count),
            "The objection asks for another number of doublets; the count is copied as the "
            "doublets lever (DesignGuard DB-10 checks it).",
        )
    if "depth" in text or "deep" in text or "shallow" in text:
        return _depth_candidate(question, text)
    if ("db-30" in text or "availab" in text or "interpretation" in text) and _offered(
        question, "DB-30"
    ):
        option = "B" if re.search(r"\binterpretation b\b|\bb\b", text) else "A"
        return _choice(
            "DB-30",
            option,
            "The objection is about the doublet availability interpretation (DB-30); the "
            "named interpretation is copied as the lever value.",
        )
    match = _CONCEPT.search(text)
    concept = f"C{match.group(1)}" if match else None
    if concept is None:
        for word, options in _CONCEPT_WORDS.items():
            if word in text:
                concept = next(
                    (c for c in options if any(b.concept == c for b in question.candidates)),
                    None,
                )
                break
    if concept is not None and _offered(question, "concept"):
        matches = [c for c in question.candidates if c.concept == concept]
        if matches:
            return _choice(
                "concept",
                matches[0].id if len(matches) == 1 else concept,
                "The objection names another concept of the run; the concept lever re-picks "
                "its screened candidate.",
            )
        return _no_match(
            "The objection names a concept that is not among the usable screened candidates of "
            "the run."
        )
    return _no_match(
        "No offered lever matches the objection: name the backup size, a return temperature, "
        "the number of doublets, the depth, the flow, DB-30 or a concept of the run."
    )


__all__ = ["map_objection_by_keywords"]
