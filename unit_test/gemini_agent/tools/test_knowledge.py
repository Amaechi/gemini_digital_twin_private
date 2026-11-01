"""Unit tests for gemini_agent.tools.knowledge (TL-19)."""

from gemini_agent.tools.knowledge import (
    SNIPPET_MAX_CHARS,
    KnowledgeSearch,
    knowledge_search,
    query_terms,
)


def test_reference_cards_and_gemini_docs_are_indexed():
    """Cards (Markdown) and GEMINI docs (.rst) are both searchable with cited snippets."""
    result = knowledge_search("heat pump COP Carnot lift")
    assert result.data.snippets, result.summary
    top = result.data.snippets[0]
    assert top.source.endswith("RC-04_heat_pump.md")
    assert top.citation.startswith("RC-04_heat_pump.md § ")
    assert all(len(s.text) <= SNIPPET_MAX_CHARS for s in result.data.snippets)
    assert len(result.summary) <= 300 and "RC-04_heat_pump.md" in result.sources

    esp = knowledge_search("electrical submersible pump ESP")
    assert any(s.source.endswith(".rst") for s in esp.data.snippets)


def test_no_match_and_query_terms():
    """Off-topic queries return no snippet; short words and stop words are ignored."""
    assert knowledge_search("pizza margherita").data.snippets == []
    assert query_terms("The COP of a HP and the lift") == ["cop", "lift"]


def test_sections_ranked_and_compacted(tmp_path):
    """Markdown and rst files are split at headings; title hits outrank body hits."""
    (tmp_path / "a.md").write_text(
        "# Card A\nintro\n## Brine chemistry\nscaling scaling\n## Other\n" + "x\n" * 2000
    )
    (tmp_path / "b.rst").write_text("Scaling model\n=============\n\nbody text about scaling\n")
    index = KnowledgeSearch([tmp_path])
    assert index.section_count == 4
    hits = index.search("scaling", limit=5)
    assert [h.section for h in hits] == ["Scaling model", "Brine chemistry"]
    other = index.search("other", limit=1)[0]
    assert len(other.text) <= SNIPPET_MAX_CHARS and other.text.endswith("…")
