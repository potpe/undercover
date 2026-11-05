import re
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DOCUMENT_FILES = {
    "policy_wording": "policy_wording.md",
    "claims_manual": "claims_manual.md",
}
STOP_WORDS = {"a", "an", "and", "for", "in", "of", "on", "the", "to"}


def parse_sections(document: str) -> list[dict[str, str]]:
    text = (DATA_DIR / DOCUMENT_FILES[document]).read_text(encoding="utf-8")
    headings = list(re.finditer(r"^## (\d+)\. (.+)$", text, re.MULTILINE))
    return [
        {
            "document": document,
            "section_id": heading.group(1),
            "title": heading.group(2),
            "content": text[
                heading.start() : headings[index + 1].start()
                if index + 1 < len(headings)
                else len(text)
            ].strip(),
        }
        for index, heading in enumerate(headings)
    ]


def rank_sections(sections: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", query.casefold())
        if term not in STOP_WORDS
    ]
    ranked = []
    for section in sections:
        title = section["title"].casefold()
        content = section["content"].casefold()
        score = sum(3 * (term in title) + (term in content) for term in terms)
        if score:
            ranked.append((score, section))
    ranked.sort(key=lambda item: (-item[0], int(item[1]["section_id"])))
    return [
        {
            "document": section["document"],
            "section_id": section["section_id"],
            "title": section["title"],
            "snippet": " ".join(section["content"].split())[:240],
        }
        for _, section in ranked[:5]
    ]
