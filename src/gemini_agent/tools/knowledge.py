# Adapted from marcosanyo/PlantDebugger (Apache-2.0), Copyright 2026 Plant Debugger contributors,
# file services/plant-debugger/plant_debugger/tools/runbook.py @ d69507a. Modified by GeoAgent team,
# 2026-11-01: searches reference cards (Markdown) and GEMINI docs (reStructuredText) split into
# sections; stop-word filter and title weighting; cited snippets; returns a ToolResult (TL-19).
"""TL-19 ``knowledge_search``: keyword search over the reference library (spec §6, §10.2).

Sources: ``data/knowledge/**/*.md`` (reference cards RC-01…RC-12 and notes) and the GEMINI
documentation ``src/gemini_documentation/source/*.rst``. Each document is split at its headings;
a hit returns a cited snippet of at most 1,200 characters (``source § section``). Keyword search
is the MUST of TL-19; embeddings are a MAY and not implemented.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field

from gemini_agent.schemas.common import StrictModel, ToolResult
from gemini_agent.schemas.config import REPO_ROOT

SNIPPET_MAX_CHARS = 1200
"""TL-19: ≤ 1,200-character cited snippets."""

DEFAULT_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "data" / "knowledge",
    REPO_ROOT / "src" / "gemini_documentation" / "source",
)

_TITLE_WEIGHT = 3
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-]*")
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "what", "which", "are", "was",
        "were", "how", "does", "into", "its", "can", "not", "use", "per", "via", "our", "you",
        "your", "have", "has", "had", "any", "all", "may", "must", "should", "will",
    }
)  # fmt: skip


class KnowledgeSnippet(StrictModel):
    """One cited search hit."""

    source: str = Field(description="Path relative to the repository root.")
    section: str
    citation: str = Field(description="'<file> § <section>' — quote this when citing.")
    text: str = Field(max_length=SNIPPET_MAX_CHARS)
    score: float = Field(ge=0)


class KnowledgeSearchResult(StrictModel):
    """Result of ``knowledge_search``."""

    query: str
    terms: list[str]
    snippets: list[KnowledgeSnippet]


class _Section(StrictModel):
    source: str
    doc_title: str
    section: str
    text: str


def query_terms(query: str) -> list[str]:
    """Lower-case search terms (≥ 3 characters, stop words removed), de-duplicated in order."""
    seen: dict[str, None] = {}
    for term in _TOKEN.findall(query.lower()):
        if len(term) >= 3 and term not in _STOPWORDS:
            seen.setdefault(term, None)
    return list(seen)


def _split_sections(path: Path, root: Path) -> list[_Section]:
    """Split a Markdown or reStructuredText file at its headings."""
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path.relative_to(root.parent))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    is_rst = path.suffix == ".rst"
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if is_rst:
            if (
                idx + 1 < len(lines)
                and line.strip()
                and not _RST_UNDERLINE.match(line)
                and _RST_UNDERLINE.match(lines[idx + 1])
                and len(lines[idx + 1].strip()) >= len(line.strip()) - 2
            ):
                headings.append((idx, line.strip()))
        else:
            match = _MD_HEADING.match(line)
            if match:
                headings.append((idx, match.group(2)))
    doc_title = headings[0][1] if headings else path.stem.replace("_", " ")
    if not headings:
        return [_Section(source=rel, doc_title=doc_title, section=doc_title, text="\n".join(lines))]
    sections: list[_Section] = []
    preamble = lines[: headings[0][0]]
    if any(line.strip() for line in preamble):
        sections.append(
            _Section(source=rel, doc_title=doc_title, section=doc_title, text="\n".join(preamble))
        )
    for pos, (start, title) in enumerate(headings):
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        body = [ln for ln in lines[start:end] if not (is_rst and _RST_UNDERLINE.match(ln))]
        sections.append(
            _Section(source=rel, doc_title=doc_title, section=title, text="\n".join(body))
        )
    return sections


def _compact(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """Drop blank lines and trailing spaces; cut at ``max_chars`` (Plant Debugger ``_compact``)."""
    body = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 1].rstrip() + "…"


class KnowledgeSearch:
    """Keyword index over Markdown and reStructuredText files."""

    def __init__(self, roots: tuple[Path, ...] | list[Path] = DEFAULT_ROOTS) -> None:
        """Index every Markdown and reStructuredText file under ``roots``."""
        self.roots = tuple(Path(root) for root in roots)
        self._sections = self._load()

    def _load(self) -> list[_Section]:
        sections: list[_Section] = []
        for root in self.roots:
            if not root.exists():
                continue
            files = sorted(path for pattern in ("*.md", "*.rst") for path in root.rglob(pattern))
            for path in files:
                sections.extend(_split_sections(path, root))
        return sections

    @property
    def section_count(self) -> int:
        """Number of indexed sections."""
        return len(self._sections)

    def search(self, query: str, limit: int = 3) -> list[KnowledgeSnippet]:
        """Return up to ``limit`` cited snippets ranked by keyword score (ties: source order)."""
        terms = query_terms(query)
        if not terms or limit <= 0:
            return []
        scored: list[tuple[float, int, KnowledgeSnippet]] = []
        for order, section in enumerate(self._sections):
            title = f"{section.doc_title} {section.section}".lower()
            body = section.text.lower()
            score = sum(body.count(term) + _TITLE_WEIGHT * title.count(term) for term in terms)
            if score <= 0:
                continue
            file_name = Path(section.source).name
            scored.append(
                (
                    float(score),
                    order,
                    KnowledgeSnippet(
                        source=section.source,
                        section=section.section,
                        citation=f"{file_name} § {section.section}",
                        text=_compact(section.text),
                        score=float(score),
                    ),
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [snippet for _, _, snippet in scored[:limit]]


@lru_cache(maxsize=4)
def _default_index(roots: tuple[Path, ...]) -> KnowledgeSearch:
    return KnowledgeSearch(roots)


def knowledge_search(
    query: str, limit: int = 3, roots: tuple[Path, ...] = DEFAULT_ROOTS
) -> ToolResult[KnowledgeSearchResult]:
    """TL-19 tool: cited keyword search over reference cards and GEMINI docs.

    Source: spec §6 TL-19 (Plant Debugger RunbookSearch pattern).
    """
    limit = max(1, min(int(limit), 5))
    index = _default_index(tuple(roots))
    snippets = index.search(query, limit=limit)
    terms = query_terms(query)
    if snippets:
        cited = "; ".join(snippet.citation for snippet in snippets)
        summary = f"{len(snippets)} snippet(s) for '{query[:60]}': {cited}"
    else:
        summary = f"No reference snippet matches '{query[:80]}'."
    return ToolResult[KnowledgeSearchResult](
        data=KnowledgeSearchResult(query=query, terms=terms, snippets=snippets),
        summary=summary[:300],
        sources=sorted({Path(snippet.source).name for snippet in snippets}),
    )
