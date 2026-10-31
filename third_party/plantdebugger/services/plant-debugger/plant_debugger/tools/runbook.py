from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunbookSnippet:
    source: str
    title: str
    text: str
    score: float

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "title": self.title,
            "text": self.text,
        }


class RunbookSearch:
    def __init__(self, runbooks_dir: Path) -> None:
        self.runbooks_dir = runbooks_dir
        self._documents = self._load_documents()

    def _load_documents(self) -> list[tuple[str, str, str]]:
        docs: list[tuple[str, str, str]] = []
        if not self.runbooks_dir.exists():
            return docs
        for path in sorted(self.runbooks_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.stem.replace("_", " ")
            docs.append((path.name, title, text))
        return docs

    def search(self, query: str, limit: int = 3) -> list[RunbookSnippet]:
        terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_\-]+", query) if len(term) > 2}
        scored: list[RunbookSnippet] = []
        for source, title, text in self._documents:
            lowered = f"{title}\n{text}".lower()
            score = sum(lowered.count(term) for term in terms)
            # Always retain the three core runbooks for an ambiguous incident, while still ranking
            # the strongest keyword match first.
            if score or any(key in source for key in ("cavitation", "valve", "bearing")):
                body = self._compact(text)
                scored.append(
                    RunbookSnippet(source=source, title=title, text=body, score=float(score))
                )
        scored.sort(key=lambda item: (-item.score, item.source))
        return scored[:limit]

    @staticmethod
    def _compact(text: str, max_chars: int = 1200) -> str:
        lines = [line.rstrip() for line in text.splitlines()]
        body = "\n".join(line for line in lines if line.strip())
        return body[:max_chars]
