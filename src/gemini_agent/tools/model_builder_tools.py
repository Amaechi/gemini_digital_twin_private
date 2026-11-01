"""TL-17 ``model_gap_analysis`` and TL-18 sandboxed model generation (spec §5.1, §6, §7.1, §12 S7).

TL-17 compares the GEMINI component types the candidates need with ``COMPONENT_REGISTRY``. The
types a candidate needs come from the WP-06 concept layouts (``config_generator.CONCEPT_LAYOUTS``,
CFG-LAYOUT), the single source of truth for which components a generated plant uses. It also
lists the known model limitations recorded while WP-01…WP-06 were built (PROGRESS, open
follow-ups WP-12); they become report limitations (WP-14).

TL-18 lets the Model Builder agent write ONE new GEMINI model from a reference card (REF-01…03):

- ``read_reference_card``: the card text (``data/knowledge/reference_cards/RC-xx_*.md``).
- ``write_model_file``: writes only the five files of a NEW model folder and its test folder
  inside a sandbox that mirrors the repository layout (``src/gemini_model/<name>/{__init__.py,
  <name>.py, REFERENCE.md}``, ``unit_test/gemini_model/<name>/{__init__.py, test_<name>.py}``).
  Absolute paths, ``..``, backslashes, symlinks, other file names, existing GEMINI model names and
  a sandbox root inside the repository (except under the git-ignored ``runs/``) are refused.
  Generated Python is parsed and screened (import allow-list, no file/exec builtins); this is
  defence in depth, not a security boundary.
- ``run_pytest``: runs the model's tests in a subprocess (timeout, no cache, no bytecode,
  minimal environment) and stores the report with a SHA-256 digest of the files it tested.
- ``register_model``: only after a passing run on unchanged files, imports the class from the
  sandbox and adds it to the in-memory registry (runtime only; ``registry.py`` is never edited).
  Promoting a model into ``src/`` is a reviewed human step (copy both folders, add the registry
  entry and the ``.rst`` page, open a PR).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import keyword
import os
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Literal, cast

from pydantic import Field

from gemini_agent.runner.toolbox import CardArgs, NoArgs, ToolContext, ToolSpec
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import SUMMARY_MAX_CHARS, StrictModel, ToolResult
from gemini_agent.schemas.config import REPO_ROOT

TOOL_GAP = "TL-17"
TOOL_BUILD = "TL-18"
CARDS_DIR = REPO_ROOT / "data" / "knowledge" / "reference_cards"
SANDBOX_DIR_NAME = "model_builder"
"""Runner sandbox: ``<run_dir>/model_builder`` (``runs/`` is git-ignored)."""
STATE_DIR_NAME = ".model_builder"
"""Inside a sandbox: pytest reports, junit XML and registration records."""
MAX_FILE_BYTES = 64_000
DEFAULT_PYTEST_TIMEOUT_S = 120.0
MAX_PYTEST_TIMEOUT_S = 600.0
OUTPUT_TAIL_CHARS = 4000
CARD_ID_PATTERN = r"^RC-\d{2}$"
NAME_PATTERN = r"^[a-z][a-z0-9_]{2,47}$"
"""New model folder names and component type keys (snake_case, 3–48 characters)."""

ALL_CONCEPTS: tuple[str, ...] = ("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
_DEFAULT_DOUBLETS = {"C0": 0, "C1": 3, "C8": 3}
"""Doublets per concept before candidates exist (spec §3.4; C5 "2–3" taken as 2); others 2.
Only ``adder`` depends on the count (two or more degassers)."""
_DERIVED_PARENT = "C2"
"""Parent plant of C6–C8 before screening (WP-06 ``concept_layout`` default)."""

ALLOWED_MODEL_IMPORTS = frozenset(
    {"__future__", "math", "typing", "dataclasses", "enum", "functools", "numpy", "scipy"}
    | {"gemini_model"}
)
ALLOWED_TEST_IMPORTS = ALLOWED_MODEL_IMPORTS | {"pytest"}
BANNED_MODULES = ("numpy.ctypeslib", "numpy.f2py", "numpy.distutils", "numpy.lib", "scipy.io")
"""Allowed packages' submodules that reach files, C or compilers."""
BANNED_NAMES = frozenset(
    {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals"}
    | {"vars", "memoryview", "exit", "quit", "__builtins__", "getattr", "setattr", "delattr"}
)
BANNED_ATTRIBUTES = frozenset(
    {"sys", "os", "modules", "io", "lib", "ctypeslib", "f2py", "npyio", "importorskip", "exit"}
    | {"save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "genfromtxt"}
    | {"fromfile", "tofile", "memmap", "open_memmap", "fromregex", "DataSource", "dump", "dumps"}
    | {"f_globals", "f_locals", "f_back", "gi_frame", "cr_frame", "tb_frame"}
    | {"MonkeyPatch", "chdir", "syspath_prepend"}
)
"""Attribute names refused in generated code; any dunder except ``ALLOWED_DUNDERS`` too."""
ALLOWED_DUNDERS = frozenset({"__init__", "__name__", "__doc__"})
REQUIRED_OUTPUTS = ("power_th", "power_el", "emission")
"""Every GEMINI energy model sets these outputs (spec §7.2 header)."""


class ModelBuilderError(ValueError):
    """A TL-18 request was refused (reason in the message; sent back to the agent)."""


def _clip(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =========================================================================== TL-17 gap analysis
class ComponentInfo(StrictModel):
    """Where a component type comes from."""

    gm_id: str | None = Field(default=None, description="Spec §7.2 change id, e.g. 'GM-07'.")
    reference_card: str | None = Field(default=None, description="Card id, e.g. 'RC-07'.")
    purpose: str


COMPONENT_CATALOGUE: dict[str, ComponentInfo] = {
    "producer_well": ComponentInfo(gm_id="GM-01", reference_card="RC-01", purpose="producer + ESP"),
    "injector_well": ComponentInfo(gm_id="GM-01", reference_card="RC-01", purpose="injector"),
    "filter": ComponentInfo(purpose="brine filter (GEMINI)"),
    "separator": ComponentInfo(gm_id="GM-12", reference_card="RC-08", purpose="degasser"),
    "heat_exchanger": ComponentInfo(reference_card="RC-03", purpose="plate HEX (ε-NTU rating)"),
    "heatpump": ComponentInfo(gm_id="GM-03", reference_card="RC-04", purpose="HTHP on brine"),
    "chp": ComponentInfo(reference_card="RC-08", purpose="CHP on associated gas"),
    "boiler": ComponentInfo(reference_card="RC-08", purpose="gas peak boiler"),
    "absorption_chiller": ComponentInfo(gm_id="GM-04", reference_card="RC-05", purpose="LiBr"),
    "electric_chiller": ComponentInfo(gm_id="GM-05", reference_card="RC-06", purpose="chiller"),
    "heat_rejection": ComponentInfo(gm_id="GM-06", reference_card="RC-07", purpose="tower"),
    "free_cooling": ComponentInfo(gm_id="GM-07", reference_card="RC-07", purpose="economiser"),
    "electric_boiler": ComponentInfo(gm_id="GM-08", purpose="electric boiler"),
    "thermal_storage": ComponentInfo(gm_id="GM-09", reference_card="RC-09", purpose="HT-ATES"),
    "splitter": ComponentInfo(purpose="flow splitter (GEMINI)"),
    "mixer": ComponentInfo(purpose="flow mixer (GEMINI)"),
    "adder": ComponentInfo(purpose="gas adder (GEMINI)"),
}
"""Component types the plant concepts can use, with their spec item and reference card."""


class KnownLimitation(StrictModel):
    """A documented model limitation (becomes a report limitation, WP-14)."""

    id: str
    area: Literal["subsurface", "plant", "gemini", "tooling"]
    component_types: list[str] = Field(description="Registry types it concerns.")
    title: str
    detail: str
    impact: str
    mitigation: str
    severity: Literal["minor", "moderate", "major"]
    source: str = Field(description="Where it was found (WP / tool / card).")
    only_concepts: list[str] = Field(
        default_factory=list, description="Restrict to these concepts (empty: all users)."
    )


def _lim(
    id_: str,
    area: Literal["subsurface", "plant", "gemini", "tooling"],
    types: Sequence[str],
    title: str,
    detail: str,
    impact: str,
    mitigation: str,
    severity: Literal["minor", "moderate", "major"],
    source: str,
    only_concepts: Sequence[str] = (),
) -> KnownLimitation:
    return KnownLimitation(
        id=id_,
        area=area,
        component_types=list(types),
        title=title,
        detail=detail,
        impact=impact,
        mitigation=mitigation,
        severity=severity,
        source=source,
        only_concepts=list(only_concepts),
    )


_WELLS = ("producer_well", "injector_well")
KNOWN_LIMITATIONS: tuple[KnownLimitation, ...] = (
    _lim(
        "LIM-SUB-01",
        "subsurface",
        ("producer_well",),
        "ESP has no flow affinity",
        "GEMINI's ESP scales head by (f/60)² and power by (f/60)³ at the same flow but has no "
        "flow affinity, so its curve power is optimistic at reduced frequency (SUB-ESP).",
        "ESP curve power is approximate at part load.",
        "Plant power uses the DB-13/DB-40 rule QΔp/0.70; the curve power is a comparison only.",
        "minor",
        "WP-05 TL-05, SUB-ESP",
    ),
    _lim(
        "LIM-SUB-02",
        "subsurface",
        _WELLS,
        "Constant brine PVT",
        "ρ, μ, c_p constant (DB-08/DB-33); no μ(T), so cold-injection injectivity is not lowered "
        "(II = PI, SUB-II); cold injection adds about +6 bar on the 26.5 bar(g) injection WHP.",
        "Injection pressure and pump power underestimated at the injector.",
        "WP-11 SUB-II sensitivity; report injection WHP with the cold-injection note.",
        "moderate",
        "WP-01 GM-01, WP-05",
    ),
    _lim(
        "LIM-SUB-03",
        "subsurface",
        ("producer_well",),
        "Steady wellbore heat loss",
        "Steady U = 10 W/m²K: wellhead 77.1 °C at 50 m³/h versus about 81–83 °C transient.",
        "Low-flow wellhead temperature is conservative (heat underestimated).",
        "State as conservative; transient wellbore model out of scope.",
        "minor",
        "WP-05 TL-05/TL-06",
    ),
    _lim(
        "LIM-SUB-04",
        "subsurface",
        ("producer_well",),
        "ESP motor heating not modelled",
        "Motor/pump losses heat the brine by about 0.4 K; not added to the wellhead temperature.",
        "Wellhead temperature slightly underestimated.",
        "None needed (conservative).",
        "minor",
        "WP-05",
    ),
    _lim(
        "LIM-SUB-05",
        "subsurface",
        ("injector_well",),
        "No fracture-pressure limit",
        "Injection wellhead pressure is not checked against a formation fracture pressure.",
        "A high-rate injection point could exceed a fracture limit unnoticed.",
        "Ask for the fracture gradient (request_missing_input) before final sizing.",
        "moderate",
        "WP-05 TL-05",
    ),
    _lim(
        "LIM-SUB-06",
        "subsurface",
        ("producer_well",),
        "No flashing check above 100 °C",
        "Brine above 100 °C (C4: 108 °C reservoir at 3.2 km, DB-29) is not checked for "
        "flashing in the surface letdown; CFG-SURFACE only flags brine above 100 °C.",
        "Deep-concept surface pressures may be optimistic.",
        "Keep the surface loop pressurised above saturation; flag C4 in the report.",
        "moderate",
        "WP-05, WP-06 CFG-SURFACE",
        ("C4",),
    ),
    _lim(
        "LIM-SUB-07",
        "subsurface",
        ("producer_well",),
        "ESP far from BEP at minimum flow",
        "At 50 m³/h the ESP runs at Q/(Q_BEP·f/60) 0.33 (TL-05 esp_flow_to_bep_ratio), outside "
        "the preferred operating range.",
        "Minimum-flow efficiency and pump life uncertain.",
        "Prefer turndown by switching doublets; note in DR-01.",
        "minor",
        "WP-05 SUB-ESP",
    ),
    _lim(
        "LIM-SUB-08",
        "subsurface",
        ("esp",),
        "Standalone esp type is water-only",
        "The registry type 'esp' is GEMINI's water ESP: head and power are not scaled by the "
        "brine specific gravity (the producer ESP is, via well_flow._FluidESP; brine ESP Δp "
        "and power × 1.03).",
        "A project using 'esp' directly underestimates brine pump Δp and power.",
        "Use producer_well (the generator does); scale 'esp' by the pvt SG if it is ever used.",
        "minor",
        "WP-01 review",
    ),
    _lim(
        "LIM-PLT-01",
        "plant",
        ("thermal_storage",),
        "No storage component in the plant generator",
        "C6 runs as its parent plant; the HT-ATES outlet temperature is not modelled and "
        "recovered heat below 100 °C would need a booster heat pump.",
        "C6 benefit rests on dispatch bookkeeping (STO-REC-HT), not a simulated storage loop.",
        "Report C6 as indicative; booster HP not sized.",
        "major",
        "WP-06 concept_layout, WP-03 GM-09",
    ),
    _lim(
        "LIM-PLT-02",
        "plant",
        ("heatpump",),
        "Heat-pump fouling not modelled",
        "The closed-form Lorenz heat pump has no fouling factor on evaporator or condenser.",
        "COP slightly optimistic over the plant life.",
        "Covered by the VER-HP-ETA cap and WP-11 sensitivity.",
        "minor",
        "WP-03 GM-03",
    ),
    _lim(
        "LIM-PLT-03",
        "plant",
        ("heat_exchanger",),
        "HEX imprecise for balanced flows",
        "The GEMINI ε-NTU rating model is imprecise as C_r → 1.",
        "Balanced-flow HEX duty has a small error.",
        "CFG-HEX sizes UA by inverting RC-03 eq 1 at the design point.",
        "minor",
        "WP-06 CFG-HEX",
    ),
    _lim(
        "LIM-PLT-04",
        "plant",
        ("heat_exchanger",),
        "HEX temperature difference uses abs()",
        "The GEMINI HEX uses abs(T_s,in − T_p,in): a colder primary gives heat in the wrong "
        "direction instead of zero duty.",
        "A mis-wired or off-design HEX can report positive duty.",
        "Generator wires brine hot side only; verifier first-law check (PH-22).",
        "moderate",
        "WP-06, WP-09 PH-22",
    ),
    _lim(
        "LIM-PLT-05",
        "plant",
        ("absorption_chiller",),
        "No absorption isolation loop",
        "Brine drives the absorption generator directly (titanium tubes, RC-05); the isolation "
        "HEX of spec §3.4 is not modelled.",
        "Drive temperature optimistic by one HEX approach.",
        "Note in DR-04; WP-11 sensitivity on drive temperature.",
        "moderate",
        "WP-02 GM-04, WP-06",
    ),
    _lim(
        "LIM-GEM-01",
        "gemini",
        ("compressor",),
        "GEMINI Compressor omits k and drive losses",
        "Compressor power omits the isentropic exponent k (about 1.3, power low) and the motor "
        "and drive efficiencies 0.93 × 0.97.",
        "Unused by the plant; would underestimate power if used.",
        "If used: fix behind a parameter and add REFERENCE.md.",
        "minor",
        "WP-01 GM-02",
    ),
    _lim(
        "LIM-TOOL-01",
        "tooling",
        _WELLS,
        "Private well_flow helpers used as API",
        "tools/doublet.py calls gemini_simulator.well_flow._build_dpdt and _build_esp.",
        "Renaming them breaks TL-05/TL-06.",
        "Treat both as API; this analysis checks they exist.",
        "minor",
        "WP-05 tools/doublet.py",
    ),
)
"""Known limitations (PROGRESS open follow-ups, WP-12)."""


class RequiredComponent(StrictModel):
    """One component type the candidates need."""

    component_type: str
    needed_by: list[str] = Field(description="Candidate ids (or concept ids if none exist).")
    registered: bool
    model_class: str | None = Field(default=None, description="'module.Class' if registered.")
    gm_id: str | None = None
    reference_card: str | None = None
    in_generated_plant: bool = Field(
        default=True,
        description="False: the concept needs it but the WP-06 generator does not add it yet.",
    )


class MissingComponent(StrictModel):
    """A required component type that is not in the registry."""

    component_type: str
    needed_by: list[str]
    gm_id: str | None = None
    reference_card: str | None = None
    buildable: bool = Field(description="A reference card exists: the Model Builder can try.")
    suggested_model_name: str = Field(description="New model folder (never an existing one).")
    action: str


class LimitationEntry(KnownLimitation):
    """A known limitation and the candidates it affects."""

    affects: list[str] = Field(default_factory=list)


class InternalApiCheck(StrictModel):
    """Live check of private helpers other tools rely on (LIM-TOOL-01)."""

    module: str
    names: list[str]
    present: bool


class ModelGapReport(StrictModel):
    """``model_gap_analysis`` result (TL-17)."""

    registered_types: list[str]
    required: list[RequiredComponent]
    missing_component_types: list[str]
    missing: list[MissingComponent]
    unused_registered_types: list[str]
    blocked_candidates: list[str] = Field(description="Candidates needing a missing type.")
    limitations: list[LimitationEntry]
    internal_api: list[InternalApiCheck]
    note: str


CONCEPTUAL_TYPES = frozenset({"thermal_storage"})
"""Needed by a concept (C6 storage) but not added by the WP-06 generator (LIM-PLT-01)."""


def _layout_types(layout: Any, doublets: int, *, conceptual: bool = True) -> list[str]:
    """Component types the WP-06 generator adds for a layout (CFG-LAYOUT, config_generator).

    ``adder``: gas adder with two or more degassers, or heat-rejection adder when more than one
    cooling unit rejects heat (the electric chiller always does). ``conceptual`` adds
    :data:`CONCEPTUAL_TYPES` the concept needs but the generator does not build.
    """
    types = ["boiler", "electric_chiller", "heat_rejection"]
    absorption = layout.absorption and doublets > 0  # no active doublet: no absorption drive
    if doublets > 0:
        types += ["producer_well", "injector_well", "filter", "splitter", "mixer"]
        if layout.degas:
            types += ["separator", "chp"]
        if layout.direct_hex:
            types.append("heat_exchanger")
        if layout.heat_pump:
            types.append("heatpump")
        if absorption:
            types.append("absorption_chiller")
    if layout.free_cooling:
        types.append("free_cooling")
    if (layout.degas and doublets > 1) or layout.free_cooling or absorption:
        types.append("adder")
    if conceptual and layout.storage:
        types.append("thermal_storage")
    return types


def candidate_component_types(candidate: Candidate, *, conceptual: bool = True) -> list[str]:
    """Registry types a candidate's generated plant uses (WP-06 ``concept_layout``)."""
    from gemini_agent.tools.config_generator import concept_layout

    _, layout, _ = concept_layout(candidate)
    return _layout_types(layout, candidate.doublets, conceptual=conceptual)


def concept_component_types(concept: str) -> list[str]:
    """Registry types of a concept family before candidates exist (C6–C8 on the C2 plant)."""
    from gemini_agent.tools.config_generator import concept_layout

    if concept not in ALL_CONCEPTS:
        raise ValueError(f"unknown concept '{concept}' (known: {', '.join(ALL_CONCEPTS)})")
    stand_in = SimpleNamespace(concept=concept, parent_id=_DERIVED_PARENT)
    _, layout, _ = concept_layout(cast(Candidate, stand_in), _DERIVED_PARENT)
    return _layout_types(layout, _DEFAULT_DOUBLETS.get(concept, 2))


def suggested_model_name(component_type: str, reference_card: str | None) -> str:
    """Return a new folder name for a type, e.g. 'free_cooling' + RC-07 → 'free_cooling_rc07'."""
    suffix = (reference_card or "gen").lower().replace("-", "")
    base = f"{component_type}_{suffix}"[:44]
    name, k = base, 2
    while (REPO_ROOT / "src" / "gemini_model" / name).exists():
        name, k = f"{base}_v{k}", k + 1
    return name


def _class_path(cls: Any) -> str:
    return f"{getattr(cls, '__module__', '?')}.{getattr(cls, '__qualname__', repr(cls))}"


def _internal_api_checks() -> list[InternalApiCheck]:
    from gemini_simulator import well_flow

    names = ["_build_dpdt", "_build_esp"]
    present = all(callable(getattr(well_flow, n, None)) for n in names)
    return [InternalApiCheck(module="gemini_simulator.well_flow", names=names, present=present)]


def model_gap_analysis(
    candidates: Sequence[Candidate] | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
) -> ToolResult[ModelGapReport]:
    """TL-17: required component types vs ``COMPONENT_REGISTRY`` plus known limitations.

    Without candidates every concept C0–C8 is analysed (ids = concept ids). ``registry`` defaults
    to the live ``gemini_simulator.registry.COMPONENT_REGISTRY``.
    """
    if registry is None:
        from gemini_simulator import registry as registry_module

        registry = registry_module.COMPONENT_REGISTRY
    needs: dict[str, list[str]] = {}
    concept_of: dict[str, str] = {}
    if candidates:
        for candidate in candidates:
            concept_of[candidate.id] = candidate.concept
            for ctype in candidate_component_types(candidate):
                needs.setdefault(ctype, []).append(candidate.id)
    else:
        for concept in ALL_CONCEPTS:
            concept_of[concept] = concept
            for ctype in concept_component_types(concept):
                needs.setdefault(ctype, []).append(concept)
    ids = list(concept_of)

    required: list[RequiredComponent] = []
    missing: list[MissingComponent] = []
    for ctype in sorted(needs):
        info = COMPONENT_CATALOGUE.get(ctype, ComponentInfo(purpose="uncatalogued"))
        cls = registry.get(ctype)
        required.append(
            RequiredComponent(
                component_type=ctype,
                needed_by=needs[ctype],
                registered=cls is not None,
                model_class=_class_path(cls) if cls is not None else None,
                gm_id=info.gm_id,
                reference_card=info.reference_card,
                in_generated_plant=ctype not in CONCEPTUAL_TYPES,
            )
        )
        if cls is None:
            buildable = info.reference_card is not None
            missing.append(
                MissingComponent(
                    component_type=ctype,
                    needed_by=needs[ctype],
                    gm_id=info.gm_id,
                    reference_card=info.reference_card,
                    buildable=buildable,
                    suggested_model_name=suggested_model_name(ctype, info.reference_card),
                    action=(
                        f"Model Builder: build from {info.reference_card} in the sandbox, test, "
                        "register at runtime"
                        if buildable
                        else "no reference card: escalate (cannot build without a source)"
                    ),
                )
            )
    missing_types = [m.component_type for m in missing]
    blocked = sorted({cid for m in missing for cid in m.needed_by})
    limitations = [
        LimitationEntry(
            **lim.model_dump(),
            affects=sorted(
                cid
                for cid in {c for t in lim.component_types for c in needs.get(t, [])}
                if not lim.only_concepts or concept_of[cid] in lim.only_concepts
            ),
        )
        for lim in KNOWN_LIMITATIONS
    ]
    api = _internal_api_checks()
    unused = sorted(set(registry) - set(needs))
    report = ModelGapReport(
        registered_types=sorted(registry),
        required=required,
        missing_component_types=missing_types,
        missing=missing,
        unused_registered_types=unused,
        blocked_candidates=blocked,
        limitations=limitations,
        internal_api=api,
        note=(
            "required types from the WP-06 concept layouts (CFG-LAYOUT); limitations from the "
            "WP-01…WP-06 build (PROGRESS)"
        ),
    )
    affecting = sum(1 for lim in limitations if lim.affects)
    gap_text = ", ".join(missing_types) if missing_types else "none"
    warnings = [
        f"missing component type '{t}' (needed by {', '.join(needs[t])})" for t in missing_types
    ]
    warnings += [
        f"internal API {c.module}.{'/'.join(c.names)} missing (LIM-TOOL-01)"
        for c in api
        if not c.present
    ]
    return ToolResult[ModelGapReport](
        data=report,
        summary=_clip(
            f"model_gap_analysis: {len(required)} required types for {len(ids)} "
            f"candidates; missing: {gap_text}; {len(limitations)} known limitations "
            f"({affecting} affect candidates)."
        ),
        warnings=warnings,
        sources=["spec §7.2", "CFG-LAYOUT", "gemini_simulator.registry"],
    )


# =========================================================================== TL-18 cards
class ReferenceCard(StrictModel):
    """One reference card (REF-01: the Model Builder writes code from the card)."""

    card_id: str
    title: str
    path: str = Field(description="Path relative to the repository root.")
    feeds: str
    sections: list[str]
    sha256: str
    text: str


def read_reference_card(card_id: str, *, cards_dir: Path = CARDS_DIR) -> ToolResult[ReferenceCard]:
    """TL-18 ``read_reference_card``: the full card text (cards are a few kB)."""
    if not re.fullmatch(CARD_ID_PATTERN, card_id):
        raise ModelBuilderError(f"card_id must look like 'RC-07', got {card_id!r}")
    matches = sorted(cards_dir.glob(f"{card_id}_*.md"))
    if len(matches) != 1:
        raise ModelBuilderError(f"no unique reference card for {card_id} in {cards_dir.name}/")
    path = matches[0]
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), card_id)
    feeds = next((ln.split(":**", 1)[1].strip() for ln in lines if ln.startswith("**Feeds")), "")
    sections = [ln.lstrip("# ").strip() for ln in lines if ln.startswith("## ")]
    try:
        shown = str(path.relative_to(REPO_ROOT))
    except ValueError:
        shown = path.name
    return ToolResult[ReferenceCard](
        data=ReferenceCard(
            card_id=card_id,
            title=title,
            path=shown,
            feeds=feeds,
            sections=sections,
            sha256=_sha256(raw),
            text=text,
        ),
        summary=_clip(f"read_reference_card {title}: {len(sections)} sections; feeds {feeds}"),
        sources=[card_id],
    )


# =========================================================================== TL-18 sandbox
FileKind = Literal["model_init", "model", "reference", "test_init", "test"]
_PATH_RE = re.compile(r"^(src|unit_test)/gemini_model/([^/]+)/([^/]+)$")


def model_file_paths(name: str) -> dict[FileKind, str]:
    """Return the only five files a model may have (paths relative to the sandbox root)."""
    return {
        "model_init": f"src/gemini_model/{name}/__init__.py",
        "model": f"src/gemini_model/{name}/{name}.py",
        "reference": f"src/gemini_model/{name}/REFERENCE.md",
        "test_init": f"unit_test/gemini_model/{name}/__init__.py",
        "test": f"unit_test/gemini_model/{name}/test_{name}.py",
    }


def check_sandbox_root(root: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Return the resolved root; refuse the repository or a folder in it outside ``runs/``.

    ``runs/final/`` is tracked (``.gitignore`` un-ignores it), so it is refused as well.
    """
    resolved = Path(root).resolve()
    repo = Path(repo_root).resolve()
    if resolved.is_relative_to(repo) and (
        not resolved.is_relative_to(repo / "runs")
        or resolved == repo / "runs"
        or resolved.is_relative_to(repo / "runs" / "final")
    ):
        raise ModelBuilderError(
            "sandbox root must be outside the repository or in a git-ignored runs/<id>/ folder "
            "(not runs/final/)"
        )
    return resolved


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and name not in ALLOWED_DUNDERS


def _check_module(module: str, allowed: frozenset[str], line: int) -> None:
    if module.split(".")[0] not in allowed:
        raise ModelBuilderError(
            f"line {line}: import of '{module}' not allowed (allowed: {', '.join(sorted(allowed))})"
        )
    if any(module == banned or module.startswith(banned + ".") for banned in BANNED_MODULES):
        raise ModelBuilderError(f"line {line}: import of '{module}' not allowed")


def screen_python(source: str, kind: Literal["model", "test", "init"]) -> None:
    """Parse generated Python and refuse disallowed imports/builtins (defence in depth).

    Refused: imports outside the allow-list or in ``BANNED_MODULES``, star imports, names in
    ``BANNED_NAMES``, attributes in ``BANNED_ATTRIBUTES``, dunder names/attributes other than
    ``__init__``/``__name__``/``__doc__``, and string constants containing ``__`` (format-string
    attribute access) except docstrings and other bare string statements.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ModelBuilderError(f"syntax error line {exc.lineno}: {exc.msg}") from exc
    allowed = ALLOWED_TEST_IMPORTS if kind == "test" else ALLOWED_MODEL_IMPORTS
    bare_strings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name, allowed, line)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if kind == "init" and node.level == 1:
                    continue
                raise ModelBuilderError(f"line {line}: relative import not allowed")
            module = node.module or ""
            _check_module(module, allowed, line)
            for alias in node.names:
                if alias.name == "*":
                    raise ModelBuilderError(f"line {line}: star import not allowed")
                if alias.name in BANNED_ATTRIBUTES or alias.name in BANNED_NAMES:
                    raise ModelBuilderError(f"line {line}: import of '{alias.name}' not allowed")
                _check_module(f"{module}.{alias.name}", allowed, line)
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or _is_dunder(node.id)):
            raise ModelBuilderError(f"line {line}: '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and (
            node.attr in BANNED_ATTRIBUTES or _is_dunder(node.attr)
        ):
            raise ModelBuilderError(f"line {line}: attribute '{node.attr}' is not allowed")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "__" in node.value
            and id(node) not in bare_strings
        ):
            raise ModelBuilderError(f"line {line}: string constants must not contain '__'")


class WrittenFile(StrictModel):
    """``write_model_file`` result."""

    model_name: str
    kind: str
    path: str = Field(description="Relative to the sandbox root.")
    bytes: int
    sha256: str


class PytestReport(StrictModel):
    """``run_pytest`` result for one model."""

    model_name: str
    passed: bool
    returncode: int | None
    timed_out: bool
    timeout_s: float
    duration_s: float
    tests: int
    failures: int
    errors: int
    skipped: int
    failed_tests: list[str] = Field(default_factory=list)
    files_sha256: str = Field(description="Digest of the model's files when the tests ran.")
    output_tail: str = ""
    error: str | None = None


class RegistrationRecord(StrictModel):
    """``register_model`` result (runtime registration only)."""

    model_name: str
    component_type: str
    class_name: str
    module: str = Field(description="Private module name the class was loaded under.")
    source: str = Field(description="Model file relative to the sandbox root.")
    files_sha256: str
    tests_passed: int
    runtime_only: bool = True
    promotion: str


class ModelSandbox:
    """A folder that mirrors the repository layout; the only place generated models are written."""

    def __init__(self, root: Path | str, *, repo_root: Path = REPO_ROOT) -> None:
        """Create (or reuse) the sandbox at ``root``; refuses roots inside the repository."""
        self.repo_root = Path(repo_root).resolve()
        self.root = check_sandbox_root(Path(root), self.repo_root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ names and paths
    def check_model_name(self, name: str) -> str:
        """Return ``name`` if valid and NEW (not an existing GEMINI model or test folder)."""
        if not isinstance(name, str) or not re.fullmatch(NAME_PATTERN, name):
            raise ModelBuilderError(f"model name must match {NAME_PATTERN}, got {name!r}")
        if keyword.iskeyword(name) or name.startswith("test"):
            raise ModelBuilderError(f"model name {name!r} is reserved")
        for area in ("src", "unit_test"):
            base = self.repo_root / area / "gemini_model"
            if (base / name).exists() or (base / f"{name}.py").exists():
                raise ModelBuilderError(
                    f"'{name}' already exists in {area}/gemini_model: write a NEW model folder"
                )
        return name

    def resolve(self, relative_path: str) -> tuple[str, FileKind, Path]:
        """Validate a sandbox-relative path; return (model name, file kind, absolute path)."""
        if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 200:
            raise ModelBuilderError("path must be a non-empty relative path (≤ 200 characters)")
        if "\\" in relative_path or "\x00" in relative_path or ":" in relative_path:
            raise ModelBuilderError(f"path {relative_path!r}: use plain POSIX relative paths")
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.split("/")):
            raise ModelBuilderError(f"path {relative_path!r} escapes or is not normalised")
        match = _PATH_RE.match(relative_path)
        if match is None:
            raise ModelBuilderError(
                f"path {relative_path!r} is outside src/gemini_model/<new>/ and "
                "unit_test/gemini_model/<new>/"
            )
        name = self.check_model_name(match.group(2))
        kinds = {path: kind for kind, path in model_file_paths(name).items()}
        kind = kinds.get(relative_path)
        if kind is None:
            allowed = ", ".join(sorted(model_file_paths(name).values()))
            raise ModelBuilderError(f"path {relative_path!r} not allowed; allowed: {allowed}")
        target = self.root.joinpath(*pure.parts)
        probe = self.root
        for part in pure.parts:
            probe = probe / part
            if probe.is_symlink():
                raise ModelBuilderError(f"path {relative_path!r} passes through a symlink")
        if not target.resolve().is_relative_to(self.root):
            raise ModelBuilderError(f"path {relative_path!r} resolves outside the sandbox")
        return name, kind, target

    @property
    def state_dir(self) -> Path:
        """Folder for reports and registration records (never a symlink)."""
        path = self.root / STATE_DIR_NAME
        if path.is_symlink():
            raise ModelBuilderError(f"{STATE_DIR_NAME} in the sandbox must not be a symlink")
        return path

    # ------------------------------------------------------------------ files
    def write(self, relative_path: str, content: str) -> WrittenFile:
        """Write one allowed file (Python is screened first)."""
        name, kind, target = self.resolve(relative_path)
        if not isinstance(content, str):
            raise ModelBuilderError("content must be text")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ModelBuilderError(f"file too large ({len(data)} > {MAX_FILE_BYTES} bytes)")
        if kind != "reference":
            screen_python(
                content,
                "test" if kind == "test" else "init" if kind.endswith("init") else "model",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        return WrittenFile(
            model_name=name, kind=kind, path=relative_path, bytes=len(data), sha256=_sha256(data)
        )

    def path(self, name: str, kind: FileKind) -> Path:
        """Absolute path of one of a model's files."""
        return self.root / model_file_paths(name)[kind]

    def files_digest(self, name: str) -> str:
        """SHA-256 over the model's existing files (path + content)."""
        digest = hashlib.sha256()
        for rel in sorted(model_file_paths(name).values()):
            target = self.root / rel
            if target.is_file():
                digest.update(rel.encode() + b"\0" + target.read_bytes() + b"\0")
        return digest.hexdigest()

    # ------------------------------------------------------------------ reports
    def _record_path(self, name: str, what: str) -> Path:
        return self.state_dir / f"{name}.{what}.json"

    def save_report(self, report: PytestReport) -> None:
        """Persist the last pytest report of a model."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._record_path(report.model_name, "pytest").write_text(
            report.model_dump_json(indent=1), encoding="utf-8"
        )

    def load_report(self, name: str) -> PytestReport | None:
        """Last pytest report of a model (None if never run)."""
        path = self._record_path(name, "pytest")
        if not path.is_file():
            return None
        return PytestReport.model_validate_json(path.read_text(encoding="utf-8"))

    def save_registration(self, record: RegistrationRecord) -> None:
        """Persist a registration record (audit)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._record_path(record.model_name, "registration").write_text(
            record.model_dump_json(indent=1), encoding="utf-8"
        )


def write_model_file(sandbox: ModelSandbox, path: str, content: str) -> ToolResult[WrittenFile]:
    """TL-18 ``write_model_file``: one of the five allowed files of a NEW model."""
    written = sandbox.write(path, content)
    return ToolResult[WrittenFile](
        data=written,
        summary=_clip(f"write_model_file: {written.path} ({written.bytes} bytes) in the sandbox."),
        sources=["spec §7.1"],
    )


# =========================================================================== TL-18 pytest
_BOOTSTRAP = (
    "import sys\n"
    "import gemini_model\n"
    "gemini_model.__path__.append(sys.argv[1])\n"
    "import pytest\n"
    "sys.exit(pytest.main(sys.argv[2:]))\n"
)
"""Child process: sandbox models become ``gemini_model.<name>`` without shadowing GEMINI."""


_POSIX = hasattr(os, "killpg")


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the child's process group (POSIX) or the child (elsewhere)."""
    try:
        if _POSIX:
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _child_env(sandbox: ModelSandbox) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(sandbox.repo_root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HOME": str(sandbox.root),
        "LANG": "C.UTF-8",
        "NO_PROXY": "*",
    }


def _parse_junit(path: Path) -> tuple[int, int, int, int, list[str]]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return 0, 0, 0, 0, []
    failed = [
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in suite.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    return (
        int(suite.get("tests", 0)),
        int(suite.get("failures", 0)),
        int(suite.get("errors", 0)),
        int(suite.get("skipped", 0)),
        failed,
    )


def run_model_tests(
    sandbox: ModelSandbox,
    model_name: str,
    *,
    timeout_s: float = DEFAULT_PYTEST_TIMEOUT_S,
    python: str = sys.executable,
) -> PytestReport:
    """Run ``unit_test/gemini_model/<name>/`` in a subprocess with a timeout; store the report."""
    name = sandbox.check_model_name(model_name)
    timeout_s = float(min(max(timeout_s, 1.0), MAX_PYTEST_TIMEOUT_S))
    digest = sandbox.files_digest(name)
    base: dict[str, Any] = {
        "model_name": name,
        "timeout_s": timeout_s,
        "files_sha256": digest,
        "returncode": None,
        "timed_out": False,
        "duration_s": 0.0,
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    absent = [k for k in ("model", "reference", "test") if not sandbox.path(name, k).is_file()]
    if absent:
        report = PytestReport(passed=False, error=f"missing files: {', '.join(absent)}", **base)
        sandbox.save_report(report)
        return report
    sandbox.state_dir.mkdir(parents=True, exist_ok=True)
    ini = sandbox.state_dir / "pytest.ini"
    ini.write_text("[pytest]\njunit_family = xunit2\n", encoding="utf-8")
    junit = sandbox.state_dir / f"{name}.junit.xml"
    junit.unlink(missing_ok=True)
    command = [
        python,
        "-P",  # no script/cwd directory on sys.path
        "-s",  # no user site-packages
        "-c",
        _BOOTSTRAP,
        str(sandbox.root / "src" / "gemini_model"),
        "-q",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "-c",
        str(ini),
        "--rootdir",
        str(sandbox.root),
        f"--junitxml={junit}",
        str(sandbox.root / "unit_test" / "gemini_model" / name),
    ]
    start = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=sandbox.root,
        env=_child_env(sandbox),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=_POSIX,
    )
    try:
        output, _ = process.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(process)
        output, _ = process.communicate()
        timed_out = True
    _kill_group(process)  # nothing the tests started may outlive the run
    base["duration_s"] = round(time.perf_counter() - start, 3)
    base["returncode"] = None if timed_out else process.returncode
    base["timed_out"] = timed_out
    text = output.decode("utf-8", errors="replace")
    failed: list[str] = []
    if junit.is_file():
        tests, failures, errors, skipped, failed = _parse_junit(junit)
        base.update(tests=tests, failures=failures, errors=errors, skipped=skipped)
    executed = base["tests"] - base["skipped"]
    passed = (
        not timed_out
        and process.returncode == 0
        and executed >= 1
        and base["failures"] == 0
        and base["errors"] == 0
    )
    error = None
    if timed_out:
        error = f"pytest timed out after {timeout_s:.0f} s"
    elif process.returncode == 5 or executed < 1:
        error = "no test ran (REF-02 needs a reference-example test)"
    if sandbox.files_digest(name) != digest:
        passed, error = False, "model files changed during the test run"
    report = PytestReport(
        passed=passed,
        failed_tests=failed,
        output_tail=text[-OUTPUT_TAIL_CHARS:],
        error=error,
        **base,
    )
    sandbox.save_report(report)
    return report


def run_pytest(
    sandbox: ModelSandbox, model_name: str, *, timeout_s: float = DEFAULT_PYTEST_TIMEOUT_S
) -> ToolResult[PytestReport]:
    """TL-18 ``run_pytest``: the model's tests in a subprocess (timeout)."""
    report = run_model_tests(sandbox, model_name, timeout_s=timeout_s)
    state = "passed" if report.passed else "FAILED"
    detail = report.error or f"{report.failures} failures, {report.errors} errors"
    return ToolResult[PytestReport](
        data=report,
        summary=_clip(
            f"run_pytest {report.model_name}: {state}; {report.tests} tests, {detail}; "
            f"{report.duration_s:.1f} s."
        ),
        warnings=[] if report.passed else [f"tests of {report.model_name} did not pass"],
        sources=["spec §7.1 REF-02"],
    )


# =========================================================================== TL-18 register
def _load_model_class(sandbox: ModelSandbox, name: str, digest: str, class_name: str | None) -> Any:
    module_name = f"_geoagent_sandbox_{name}_{digest[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, sandbox.path(name, "model"))
    if spec is None or spec.loader is None:
        raise ModelBuilderError(f"cannot load {model_file_paths(name)['model']}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    keep_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ in the sandbox
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ModelBuilderError(f"importing the model failed: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.dont_write_bytecode = keep_bytecode
    try:
        return _checked_class(module, module_name, class_name)
    except ModelBuilderError:
        sys.modules.pop(module_name, None)
        raise


def _checked_class(module: Any, module_name: str, class_name: str | None) -> Any:
    from gemini_model.model_abstract import DynamicModel, Model, StaticModel

    classes = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Model) and obj.__module__ == module_name
    ]
    if class_name is not None:
        classes = [cls for cls in classes if cls.__name__ == class_name]
    if len(classes) != 1:
        found = ", ".join(sorted(cls.__name__ for cls in classes)) or "none"
        raise ModelBuilderError(
            f"expected exactly one GEMINI model class{f' named {class_name}' if class_name else ''}"
            f" in the model file, found: {found}"
        )
    cls = classes[0]
    if not issubclass(cls, StaticModel | DynamicModel):
        raise ModelBuilderError(f"{cls.__name__} must subclass StaticModel or DynamicModel")
    try:
        instance = cls()
    except Exception as exc:
        raise ModelBuilderError(f"{cls.__name__}() failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(getattr(instance, "parameters", None), dict):
        raise ModelBuilderError(f"{cls.__name__}().parameters must be a dict")
    for method in ("update_parameters", "calculate_output", "get_output"):
        if not callable(getattr(instance, method, None)):
            raise ModelBuilderError(f"{cls.__name__} lacks {method}()")
    return cls, module_name


def register_model(
    sandbox: ModelSandbox,
    model_name: str,
    *,
    component_type: str | None = None,
    class_name: str | None = None,
    registry: MutableMapping[str, Any] | None = None,
) -> RegistrationRecord:
    """Register a tested sandbox model in the in-memory registry (never edits ``registry.py``).

    Refused unless the last ``run_pytest`` of this model passed on exactly the current files,
    REFERENCE.md cites a reference card (REF-01) and the type is not registered yet.
    """
    name = sandbox.check_model_name(model_name)
    ctype = component_type or name
    if not re.fullmatch(NAME_PATTERN, ctype):
        raise ModelBuilderError(f"component type must match {NAME_PATTERN}, got {ctype!r}")
    if registry is None:
        from gemini_simulator import registry as registry_module

        registry = registry_module.COMPONENT_REGISTRY
    if ctype in registry:
        raise ModelBuilderError(f"component type '{ctype}' is already registered; not replaced")
    report = sandbox.load_report(name)
    if report is None:
        raise ModelBuilderError(f"run_pytest has not run for '{name}'")
    if not report.passed:
        raise ModelBuilderError(f"the last run_pytest of '{name}' did not pass")
    digest = sandbox.files_digest(name)
    if digest != report.files_sha256:
        raise ModelBuilderError(f"files of '{name}' changed after the last run_pytest; rerun it")
    reference = sandbox.path(name, "reference")
    if not re.search(r"RC-\d{2}", reference.read_text(encoding="utf-8")):
        raise ModelBuilderError("REFERENCE.md must cite its reference card (REF-01)")
    model_text = sandbox.path(name, "model").read_text(encoding="utf-8")
    absent = [
        key
        for key in REQUIRED_OUTPUTS
        if f'"{key}"' not in model_text and f"'{key}'" not in model_text
    ]
    if absent:
        raise ModelBuilderError(f"model must set outputs {', '.join(absent)} (spec §7.2)")
    cls, module_name = _load_model_class(sandbox, name, digest, class_name)
    registry[ctype] = cls
    record = RegistrationRecord(
        model_name=name,
        component_type=ctype,
        class_name=cls.__name__,
        module=module_name,
        source=model_file_paths(name)["model"],
        files_sha256=digest,
        tests_passed=report.tests - report.skipped,
        promotion=(
            f"runtime only: to keep it, copy src/gemini_model/{name}/ and "
            f"unit_test/gemini_model/{name}/ into the repository, add the registry entry and the "
            ".rst page in a reviewed PR"
        ),
    )
    sandbox.save_registration(record)
    return record


def register_model_tool(
    sandbox: ModelSandbox,
    model_name: str,
    *,
    component_type: str | None = None,
    class_name: str | None = None,
    registry: MutableMapping[str, Any] | None = None,
) -> ToolResult[RegistrationRecord]:
    """TL-18 ``register_model`` as a ``ToolResult``."""
    record = register_model(
        sandbox,
        model_name,
        component_type=component_type,
        class_name=class_name,
        registry=registry,
    )
    return ToolResult[RegistrationRecord](
        data=record,
        summary=_clip(
            f"register_model: {record.class_name} registered as '{record.component_type}' at "
            f"runtime after {record.tests_passed} passing tests (sandbox; not in registry.py)."
        ),
        warnings=["runtime registration only; promote through a reviewed PR"],
        sources=["spec §7.1", "TL-18"],
    )


# =========================================================================== runner bindings
class WriteModelFileArgs(StrictModel):
    """Arguments of ``write_model_file``."""

    path: str = Field(
        max_length=200,
        description="Sandbox-relative, e.g. 'src/gemini_model/<new>/<new>.py' (5 files allowed).",
    )
    content: str = Field(max_length=MAX_FILE_BYTES, description="Full file text.")


class RunPytestArgs(StrictModel):
    """Arguments of ``run_pytest``."""

    model_name: str = Field(pattern=NAME_PATTERN, description="New model folder name.")
    timeout_s: float = Field(default=DEFAULT_PYTEST_TIMEOUT_S, ge=5, le=MAX_PYTEST_TIMEOUT_S)


class RegisterModelArgs(StrictModel):
    """Arguments of ``register_model``."""

    model_name: str = Field(pattern=NAME_PATTERN)
    component_type: str | None = Field(
        default=None, pattern=NAME_PATTERN, description="Registry key; None: the model name."
    )
    class_name: str | None = Field(default=None, max_length=64)


def runner_sandbox(context: ToolContext) -> ModelSandbox:
    """Return the run's sandbox ``<run_dir>/model_builder``."""
    return ModelSandbox(context.run_dir / SANDBOX_DIR_NAME)


def _gap_tool(args: NoArgs, context: ToolContext) -> ToolResult[ModelGapReport]:
    return model_gap_analysis(context.candidates or None)


def _card_tool(args: CardArgs, context: ToolContext) -> ToolResult[ReferenceCard]:
    return read_reference_card(args.card_id)


def _write_tool(args: WriteModelFileArgs, context: ToolContext) -> ToolResult[WrittenFile]:
    return write_model_file(runner_sandbox(context), args.path, args.content)


def _pytest_tool(args: RunPytestArgs, context: ToolContext) -> ToolResult[PytestReport]:
    return run_pytest(runner_sandbox(context), args.model_name, timeout_s=args.timeout_s)


def _register_tool(args: RegisterModelArgs, context: ToolContext) -> ToolResult[RegistrationRecord]:
    return register_model_tool(
        runner_sandbox(context),
        args.model_name,
        component_type=args.component_type,
        class_name=args.class_name,
    )


def model_builder_specs() -> list[ToolSpec]:
    """Real TL-17/TL-18 bindings (same names as the WP-10 stubs; ``stub=False``)."""
    return [
        ToolSpec(
            "model_gap_analysis",
            "Compare required component types with the GEMINI registry; list known limitations.",
            NoArgs,
            _gap_tool,
            owner="WP-12",
            stub=False,
        ),
        ToolSpec(
            "read_reference_card",
            "Read one reference card (the only source for a new model).",
            CardArgs,
            _card_tool,
            owner="WP-12",
            stub=False,
        ),
        ToolSpec(
            "write_model_file",
            "Write one file of a NEW model or its test folder in the run sandbox.",
            WriteModelFileArgs,
            _write_tool,
            owner="WP-12",
            stub=False,
        ),
        ToolSpec(
            "run_pytest",
            "Run the new model's tests in a subprocess with a timeout.",
            RunPytestArgs,
            _pytest_tool,
            owner="WP-12",
            stub=False,
        ),
        ToolSpec(
            "register_model",
            "Register a model whose tests passed, at runtime only.",
            RegisterModelArgs,
            _register_tool,
            owner="WP-12",
            stub=False,
        ),
    ]


__all__ = [
    "COMPONENT_CATALOGUE",
    "KNOWN_LIMITATIONS",
    "TOOL_BUILD",
    "TOOL_GAP",
    "ModelBuilderError",
    "ModelGapReport",
    "ModelSandbox",
    "PytestReport",
    "ReferenceCard",
    "RegistrationRecord",
    "candidate_component_types",
    "check_sandbox_root",
    "concept_component_types",
    "model_builder_specs",
    "model_file_paths",
    "model_gap_analysis",
    "read_reference_card",
    "register_model",
    "register_model_tool",
    "run_model_tests",
    "run_pytest",
    "screen_python",
    "suggested_model_name",
    "write_model_file",
]
