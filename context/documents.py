"""Prepared Markdown section access for the governance documents."""

import re
from typing import Any

from context.data import SOURCES, provenance
from context.text import meaningful_terms

SECTION_PATTERN = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
DOCUMENTS = ("claims_manual.md", "policy_wording.md")


def _prepare_document(filename: str) -> list[dict[str, str]]:
    content = SOURCES[filename]["content"]
    matches = list(SECTION_PATTERN.finditer(content))
    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section_content = content[match.start() : end].strip()
        sections.append(
            {
                "document": filename.removesuffix(".md"),
                "section_id": match.group(1),
                "title": match.group(2),
                "content": section_content,
            }
        )
    return sections


PREPARED_DOCUMENTS = {filename: _prepare_document(filename) for filename in DOCUMENTS}


def search_documents(query: str) -> dict[str, Any]:
    """Rank both governance documents by meaningful query-term matches."""
    words = meaningful_terms(query)
    ranked_results: list[tuple[int, dict[str, Any]]] = []
    for filename, sections in PREPARED_DOCUMENTS.items():
        for section in sections:
            content = section["content"]
            title = section["title"].casefold()
            folded_content = content.casefold()
            rank = sum(
                (3 if word in title else 0) + (1 if word in folded_content else 0)
                for word in words
            )
            if rank:
                source = provenance(filename)
                snippet = " ".join(content.split())[:240]
                ranked_results.append(
                    (
                        rank,
                        {
                            "document": section["document"],
                            "section_id": section["section_id"],
                            "title": section["title"],
                            "snippet": snippet,
                            "source": source,
                        },
                    )
                )
    ranked_results.sort(
        key=lambda item: (
            -item[0],
            str(item[1]["document"]),
            int(str(item[1]["section_id"])),
        )
    )
    results = [result for _, result in ranked_results[:5]]
    sources = [
        provenance(filename)
        for filename in DOCUMENTS
        if any(result["source"]["file"] == filename for result in results)
    ]
    return {"answer": results, "sources": sources}


def read_section(document: str, number: int) -> dict[str, Any]:
    """Return one numbered section from a governance document."""
    filename = _document_filename(document)
    section = next(
        (
            item
            for item in PREPARED_DOCUMENTS[filename]
            if item["section_id"] == str(number)
        ),
        None,
    )
    if section is None:
        raise ValueError(f"section {number} does not exist in {filename}")
    source = provenance(filename)
    return {
        "answer": {**section, "source": source},
        "sources": [source],
    }


def _document_filename(document: str) -> str:
    normalized = document.strip().casefold()
    aliases = {
        "claims manual": "claims_manual.md",
        "claims_manual": "claims_manual.md",
        "claims_manual.md": "claims_manual.md",
        "policy wording": "policy_wording.md",
        "policy_wording": "policy_wording.md",
        "policy_wording.md": "policy_wording.md",
    }
    if normalized not in aliases:
        raise ValueError(f"unknown document: {document}")
    return aliases[normalized]
