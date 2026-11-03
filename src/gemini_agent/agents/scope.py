"""P0 intake checks (AG-06): off-topic refusal and constraint-conflict explanations.

Deterministic keyword rules run before any agent sees the request; the Lead Engineer agent may
additionally refuse in P0, but it can never pull an off-topic request back into scope.

WP-13 (2026-11-01) added two conflict rules for the spec §12 scenarios: a requested supply
temperature that differs from DB-16/DB-17 (S5) and a stated reservoir temperature at a depth (or a
stated gradient) that disagrees with DB-01/03/04 (S9). The design always keeps the design basis.

WP-13 follow-up (2026-11-03, S2): ``missing_request_inputs`` lists which of the challenge inputs
(demand CSVs, cooling demand, reservoir data) a user-written request does not provide, so the
runner can ask once in P1 before anything is loaded or assumed (the default brief is the full
brief with all files and never asks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gemini_agent.schemas.config import DesignBasis, HardLimits
from gemini_agent.tools.design_basis import temperature_at_depth_degc

DEFAULT_BRIEF = (
    "Select, size and validate a deep-geothermal district heating (100 degC) and cooling "
    "(20 degC) system with the GEMINI digital twin; compare alternatives and explain every "
    "decision."
)

_DOMAIN = re.compile(  # specific to the design task: enough on its own
    r"\b(geotherm\w*|heat[- ]?pumps?|hthp|chillers?|doublets?|brine|reservoirs?|aquifers?|ates|"
    r"gemini|digital twin|lcoh|lcoc|boreholes?|permeability|transmissivity|porosity|"
    r"district[- ](?:heating|cooling|energy)|(?:heat|heating|cooling) networks?|"
    r"heat exchangers?)\b",
    re.IGNORECASE,
)
_GENERIC = re.compile(  # everyday words ('summer heat', 'district of Paris'): need company
    r"\b(heat|heating|cooling|district|co2|injectors?|producers?|esp)\b", re.IGNORECASE
)
_GENERIC_ROOT = {"heating": "heat", "injectors": "injector", "producers": "producer"}
_DESIGN_WORDS = re.compile(
    r"\b(design\w*|systems?|plants?|networks?|demand|loads?|supply|return|temperatures?|"
    r"capacity|siz(?:e|ed|ing)|costs?|capex|opex|kpis?|options?|alternatives?|technolog\w*|"
    r"choices?|choose|chose|decisions?|storage|wells?|pumps?|boilers?|emissions?|efficien\w*|"
    r"cop|flows?|mw\w*|kw\w*|degc|power|energy|saves?|savings?|reduc\w*|homes?|houses?|"
    r"buildings?|neighbou?rhoods?|campus|hospitals?)\b|°\s?c\b",
    re.IGNORECASE,
)
_DOUBLETS = re.compile(r"\b(\d+)\s*(?:x\s*)?doublets?\b", re.IGNORECASE)
_FLOW = re.compile(r"\b(\d+(?:\.\d+)?)\s*m(?:3|³)\s*/\s*h\b", re.IGNORECASE)
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
"""A number as written, optionally with thousands separators ('2,500')."""
_DEG_C = r"(?:°\s?C|deg\s?C)"
_NOT_GRADIENT = r"\b(?!\s*(?:/|per\b))"
_TEMPERATURE = re.compile(
    rf"(?<![\d.,])([-−]?)({_NUMBER})\s*{_DEG_C}{_NOT_GRADIENT}", re.IGNORECASE
)
"""A temperature such as '120 °C' or '-10 degC' (not a gradient such as '30 °C/km')."""
_GRADIENT = re.compile(
    rf"(?<![\d.,])({_NUMBER})\s*(?:{_DEG_C}|K)\s*(?:/|per\s+)\s*km\b", re.IGNORECASE
)
_DEPTH = rf"(?:at|@)\s*(?:a\s+depth\s+of\s+)?({_NUMBER})\s*(km|m)\b"
_TEMP_AT_DEPTH = re.compile(rf"(?<![\d.,])({_NUMBER})\s*{_DEG_C}\s*{_DEPTH}", re.IGNORECASE)
"""'150 °C at 2.5 km'."""
_DEPTH_THEN_TEMP = re.compile(
    rf"\b{_DEPTH}[^.;!?°]{{0,40}}?(?<![\d.,])({_NUMBER})\s*{_DEG_C}{_NOT_GRADIENT}",
    re.IGNORECASE,
)
"""'(the reservoir) at 2.5 km is 150 °C' — counted only with a reservoir word in the sentence."""
_RESERVOIR_WORD = re.compile(
    r"\b(reservoirs?|formations?|aquifers?|rock|wells?|boreholes?|bottom[- ]?hole|downhole)\b",
    re.IGNORECASE,
)
_HEAT_WORD = re.compile(r"\bheat\w*", re.IGNORECASE)
_COOL_WORD = re.compile(r"\b(?:cool\w*|chill\w*)", re.IGNORECASE)
_NOT_SUPPLY = re.compile(
    r"\b(return\w*|reservoirs?|formations?|aquifers?|brine|wellheads?|wells?|ambient|outdoors?|"
    r"outside|indoors?|inside|rooms?|air|weather|wet[- ]bulb|dry[- ]bulb|surface|inject\w*|"
    r"produc\w*|bottom\w*|source|sink|depth|driv\w*|approach\w*|lift\w*|exchangers?|"
    r"when\s+it(?:'s|\s+is))\b",
    re.IGNORECASE,
)
"""Words before a temperature that make it something other than a supply temperature."""
_NOT_SUPPLY_AFTER = re.compile(
    r"\W*(?:\w+\W+)?(indoors?|outdoors?|outside|inside|ambient|rooms?|air)\b", re.IGNORECASE
)
"""'21 °C indoors', '10 °C outside air': the next one or two words."""
_SENTENCE_END = re.compile(r"[.;!?](?:\s|$)")

REFUSAL_TEMPLATE = (
    "GeoAgent only designs deep-geothermal district heating (100 degC) and cooling (20 degC) "
    "systems with the GEMINI digital twin. '{excerpt}' is outside that scope; please ask about "
    "the design, its alternatives, KPIs or decisions."
)


@dataclass
class ScopeResult:
    """Outcome of the deterministic scope check."""

    in_scope: bool
    reason: str
    matched_terms: list[str] = field(default_factory=list)


def scope_check(prompt: str) -> ScopeResult:
    """In scope if the request mentions the design domain (an empty prompt uses the brief).

    A specific domain term is enough; an everyday word such as 'heat' or 'district' counts only
    together with a design word ('heating supply', 'cooling options') or a second everyday
    domain word ('heating and cooling', 'injectors and producers').
    """
    text = prompt.strip()
    if not text:
        return ScopeResult(True, "empty request: default challenge brief")
    matched = sorted({match.group(0).lower() for match in _DOMAIN.finditer(text)})
    if matched:
        return ScopeResult(True, "mentions the design domain", matched)
    generic = sorted({match.group(0).lower() for match in _GENERIC.finditer(text)})
    design = sorted({match.group(0).lower() for match in _DESIGN_WORDS.finditer(text)})
    roots = {_GENERIC_ROOT.get(word, word) for word in generic}
    if generic and (design or len(roots) > 1):
        return ScopeResult(True, "mentions the design domain", generic + design)
    return ScopeResult(False, "no geothermal heating/cooling design topic found in the request")


def refusal_message(prompt: str) -> str:
    """Polite refusal that redirects to the design task."""
    excerpt = " ".join(prompt.split())[:80]
    return REFUSAL_TEMPLATE.format(excerpt=excerpt)


def constraint_conflicts(
    prompt: str, limits: HardLimits, basis: DesignBasis | None = None
) -> list[str]:
    """Explain requests that conflict with the hard limits or the design basis.

    The design stays within the limits and on the basis; ``basis`` enables the reservoir
    consistency rule (S9).
    """
    conflicts: list[str] = []
    for match in _DOUBLETS.finditer(prompt):
        count = int(match.group(1))
        if not limits.doublet_count_min <= count <= limits.doublet_count_max:
            conflicts.append(
                f"Requested {count} doublets conflicts with the hard limit "
                f"{limits.doublet_count_min}–{limits.doublet_count_max} doublets (DB-10); "
                "candidates stay within the limit."
            )
    for match in _FLOW.finditer(prompt):
        flow = float(match.group(1))
        if not limits.flow_min_active_m3h <= flow <= limits.flow_max_m3h:
            conflicts.append(
                f"Requested {flow:g} m3/h conflicts with the per-doublet flow limit "
                f"{limits.flow_min_active_m3h:g}–{limits.flow_max_m3h:g} m3/h (DB-11); "
                "flows stay within the limit."
            )
    conflicts += _supply_temperature_conflicts(prompt, limits)
    if basis is not None:
        conflicts += _reservoir_conflicts(prompt, basis)
    return conflicts


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _half_unit(text: str) -> float:
    """Half a unit of the last digit as written: the precision of a stated number."""
    digits = text.replace(",", "")
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    return 0.5 * 10.0**-decimals


def _differs(stated: str, expected: float, tolerance: float = 0.0) -> bool:
    """Return True if ``expected`` is outside the stated number's precision (plus ``tolerance``)."""
    return abs(_number(stated) - expected) > _half_unit(stated) + tolerance


def _supply_temperature_conflicts(prompt: str, limits: HardLimits) -> list[str]:
    """S5: a requested heating/cooling supply temperature that is not DB-16/DB-17.

    A temperature counts as a supply temperature when the words before it (same sentence, after
    the previous temperature) name heating or cooling and nothing else (no return, reservoir,
    ambient, indoor, driving or approach temperature), it is not followed by 'indoors'/'outside',
    and it is not negative; the last service word wins ('heating at 100 degC and cooling at 20').
    """
    claimed = [m.start(1) for m in _TEMP_AT_DEPTH.finditer(prompt)]
    claimed += [m.start(3) for m in _DEPTH_THEN_TEMP.finditer(prompt)]
    conflicts: list[str] = []
    previous_end = 0
    for match in _TEMPERATURE.finditer(prompt):
        window = prompt[previous_end : match.start()]
        previous_end = match.end()
        if match.group(1) or match.start(2) in claimed:
            continue
        ends = list(_SENTENCE_END.finditer(window))
        if ends:
            window = window[ends[-1].end() :]
        if _NOT_SUPPLY.search(window) or _NOT_SUPPLY_AFTER.match(prompt, match.end()):
            continue
        heat = [m.start() for m in _HEAT_WORD.finditer(window)]
        cool = [m.start() for m in _COOL_WORD.finditer(window)]
        if not heat and not cool:
            continue
        stated = match.group(2)
        if max(heat, default=-1) > max(cool, default=-1):
            service, basis_degc, basis_id = "heating", limits.heating_supply_degc, "DB-16"
        else:
            service, basis_degc, basis_id = "cooling", limits.cooling_supply_degc, "DB-17"
        if _differs(stated, basis_degc):
            conflicts.append(
                f"Requested {service} supply {_number(stated):g} degC conflicts with the design "
                f"basis {basis_degc:g} degC ({basis_id}); the design keeps {basis_degc:g} degC "
                "until a change of basis is confirmed."
            )
    return conflicts


def _sentence_at(prompt: str, index: int) -> str:
    starts = [m.end() for m in _SENTENCE_END.finditer(prompt) if m.end() <= index]
    ends = [m.start() for m in _SENTENCE_END.finditer(prompt) if m.start() >= index]
    return prompt[max(starts, default=0) : min(ends, default=len(prompt))]


def _reservoir_conflicts(prompt: str, basis: DesignBasis) -> list[str]:
    """S9: a stated gradient or reservoir temperature at a depth that disagrees with the basis.

    The basis temperature at the stated depth is RC-01 eq 1 via ``temperature_at_depth_degc``
    (DB-04 surface temperature, DB-03 gradient). A stated value is consistent when it agrees
    within the precision it is written with (temperature, and depth × gradient).
    """
    reservoir = basis.reservoir
    gradient = reservoir.geothermal_gradient_k_per_km
    conflicts: list[str] = []
    for match in _GRADIENT.finditer(prompt):
        if _differs(match.group(1), gradient):
            conflicts.append(
                f"Stated gradient {_number(match.group(1)):g} K/km differs from the design basis "
                f"{gradient:g} K/km (DB-03); the design uses DB-03."
            )
    claims = [(m.group(1), m.group(2), m.group(3)) for m in _TEMP_AT_DEPTH.finditer(prompt)]
    used = {m.start(1) for m in _TEMP_AT_DEPTH.finditer(prompt)}
    used |= {m.start(2) for m in _TEMP_AT_DEPTH.finditer(prompt)}
    for match in _DEPTH_THEN_TEMP.finditer(prompt):
        if match.start(1) in used or match.start(3) in used:
            continue
        if _RESERVOIR_WORD.search(_sentence_at(prompt, match.start())):
            claims.append((match.group(3), match.group(1), match.group(2)))
    for temperature, depth, unit in claims:
        scale = 1000.0 if unit.lower() == "km" else 1.0
        depth_m = _number(depth) * scale
        implied = temperature_at_depth_degc(depth_m, reservoir.surface_temperature_degc, gradient)
        depth_tolerance_k = gradient * _half_unit(depth) * scale / 1000.0
        if _differs(temperature, implied, depth_tolerance_k):
            conflicts.append(
                f"Stated {_number(temperature):g} degC at {depth_m:g} m is inconsistent with the "
                f"design basis: {reservoir.surface_temperature_degc:g} degC surface temperature "
                f"(DB-04) and {gradient:g} K/km gradient (DB-03) give {implied:.1f} degC there "
                "(RC-01); the design uses the design basis until the data are confirmed."
            )
    return conflicts


# --------------------------------------------------------------------------- S2: request inputs
REQUEST_INPUTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "demand CSVs (daily heating and cooling demand, data/demand/)",
        re.compile(
            r"\b(csvs?|spreadsheets?|time[- ]?series|(?:hourly|daily|monthly|annual)\s+"
            r"(?:demand|loads?|data|profiles?)|demand\s+(?:data|files?|profiles?|curves?)|"
            r"load\s+(?:data|files?|profiles?|curves?|duration)|attached|attachments?|"
            r"enclosed|data[- ]?files?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cooling demand (cooling supply 20 degC, DB-17/DB-19)",
        re.compile(r"\b(cool\w*|chill\w*|cold)\b", re.IGNORECASE),
    ),
    (
        (
            "reservoir data (depth, temperature, gradient, permeability, thickness; "
            "config/design_basis.yaml)"
        ),
        re.compile(
            r"\b(reservoirs?|aquifers?|formations?|permeabilit\w*|transmissivit\w*|"
            r"porosit\w*|gradients?|millidarcy|md|thickness|bottom[- ]?hole|downhole|"
            r"well\s+(?:data|logs?|tests?)|geolog\w*|subsurface)\b",
            re.IGNORECASE,
        ),
    ),
)
"""The challenge inputs a request can provide, each with the words that count as providing it."""


def missing_request_inputs(prompt: str) -> list[str]:
    """S2: the challenge inputs a user-written request does not provide (spec §12 S2).

    A request provides an input when it names it (a demand file or profile, cooling, the
    reservoir or its properties). An empty request is the default brief, which comes with every
    file; the runner never calls this for it. The list is for one question in P1 ("use the
    repository defaults?"); nothing is assumed before the answer.
    """
    text = prompt.strip()
    if not text:
        return []
    return [label for label, pattern in REQUEST_INPUTS if not pattern.search(text)]
