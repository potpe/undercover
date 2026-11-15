"""Tests for governance context queries and provenance."""

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from context import data, documents, service


def test_every_source_is_loaded_once_and_sections_are_prepared_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []
    original_read_text = Path.read_text

    def count_reads(
        path: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        if path.parent == data.DATA_DIRECTORY:
            reads.append(path.name)
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", count_reads)
    loaded = importlib.reload(data)
    prepared = importlib.reload(documents).PREPARED_DOCUMENTS

    assert reads == list(data.SOURCE_FILES)
    assert set(loaded.SOURCES) == set(data.SOURCE_FILES)
    assert all(prepared[name] for name in documents.DOCUMENTS)
    assert all(
        section["content"] and not section["content"].startswith("source:")
        for sections in prepared.values()
        for section in sections
    )

    server = importlib.import_module("context.server")
    server.build_server()
    service.lookup_term("carrier")
    documents.search_documents("deductible")
    assert reads == list(data.SOURCE_FILES)
    assert documents.PREPARED_DOCUMENTS is prepared


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        (
            "cArRiEr",
            "An airline, railway, ferry, cruise or coach company that "
            "you have a ticket with.",
        )
    ],
)
def test_lookup_term_case_insensitive_and_provenance(term: str, expected: str) -> None:
    result = service.lookup_term(term)
    assert result["answer"]["found"] is True
    assert result["answer"]["definition"] == expected
    assert result["sources"] == [
        {
            "file": "glossary.yaml",
            "system": "The policy wording, version 2026.1",
            "owner": "Legal",
            "reviewed": "2026-09-01",
        }
    ]


def test_unknown_term_returns_known_term_names() -> None:
    result = service.lookup_term("not a term")
    assert result["answer"]["found"] is False
    assert "Carrier" in result["answer"]["known_terms"]


@pytest.mark.parametrize("record", ["claim", "policy", "previous_claim"])
def test_describe_each_record(record: str) -> None:
    result = service.describe_fields(record.upper())
    assert result["answer"]["record"] == record
    assert result["answer"]["fields"]
    assert result["sources"][0]["file"] == "data_dictionary.yaml"


@pytest.mark.parametrize("plan", ["economy", "premium", "first"])
def test_plan_has_deductible_and_add_ons(plan: str) -> None:
    result = service.get_plan(plan.upper())
    assert result["answer"]["plan"] == plan
    assert "deductible" in result["answer"]
    assert result["answer"]["deductible_applies_to"] == [
        "baggage",
        "emergency_medical",
    ]
    assert len(result["answer"]["add_ons"]) == 3
    assert result["sources"][0]["file"] == "plans.yaml"


def test_claim_rules_and_reference_search() -> None:
    rules = service.get_claim_rules()
    by_name = {rule["rule"]: rule for rule in rules["answer"]}
    assert set(by_name) == {
        "filing_window",
        "pre_existing_look_back",
        "repeat_claims",
        "meals_without_receipts",
    }
    assert by_name["filing_window"]["days"] == 90
    assert by_name["pre_existing_look_back"]["days"] == 60
    assert by_name["repeat_claims"]["claims"] == 3
    assert by_name["repeat_claims"]["within_months"] == 12
    assert by_name["meals_without_receipts"]["amount"] == 35
    assert rules["sources"][0]["file"] == "claim_rules.yaml"

    result = service.search_reference_data("Tropical Storm Marisol")
    assert result["answer"][0]["topic"] == "events"
    assert len(result["answer"][0]["facts"]) == 1
    assert result["sources"][0]["file"] == "reference_data.yaml"


def test_reference_search_requires_all_query_words_and_topic() -> None:
    result = service.search_reference_data("Marisol Atlantis")
    assert result["answer"] == []
    assert service.search_reference_data("marisol")["answer"][0]["topic"] == "events"
    assert service.search_reference_data("Marisol for")["answer"] == []


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (service.describe_fields, ("unsupported",)),
        (service.get_plan, ("unsupported",)),
        (service.search_reference_data, ("  ",)),
        (documents.search_documents, ("  ",)),
    ],
)
def test_invalid_inputs_refuse(
    operation: Callable[[str], Any], arguments: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="."):
        operation(*arguments)


def test_document_search_and_section_read_carry_source_per_result() -> None:
    results = documents.search_documents("deductible the")
    assert results["answer"]
    assert {entry["source"]["file"] for entry in results["answer"]} == {
        "claims_manual.md",
        "policy_wording.md",
    }
    assert all(
        entry["source"]["file"] == f"{entry['document']}.md"
        for entry in results["answer"]
    )
    assert len(results["answer"]) <= 5
    assert all(len(entry["snippet"]) <= 240 for entry in results["answer"])
    assert all("  " not in entry["snippet"] for entry in results["answer"])
    assert len(results["sources"]) == 2

    section = documents.read_section("POLICY_WORDING.md", 1)
    assert section["answer"]["title"] == "Your plan"
    assert section["answer"]["document"] == "policy_wording"
    assert section["answer"]["content"] == (
        "## 1. Your plan\n\n"
        "| Benefit | Economy | Premium | First |\n"
        "|---|---|---|---|\n"
        "| Trip Cancellation and Interruption, maximum | $1,500 | $5,000 | $10,000 |\n"
        "| Trip Delay, waiting period | 12 hours | 6 hours | 3 hours |\n"
        "| Trip Delay, maximum | $300 | $800 | $1,500 |\n"
        "| Baggage Delay, waiting period | 24 hours | 12 hours | 6 hours |\n"
        "| Baggage Delay, maximum | $100 | $300 | $500 |\n"
        "| Baggage and Personal Effects, maximum | $750 | $1,500 | $3,000 |\n"
        "| Per-item limit | $150 | $300 | $500 |\n"
        "| Passport and Travel Documents, maximum | $150 | $250 | $400 |\n"
        "| Emergency Medical, maximum | $25,000 | $100,000 | $250,000 |\n"
        "| Deductible | $100 | $50 | $0 |\n\n"
        "The deductible applies once per claim to Baggage and Personal Effects and "
        "to Emergency Medical. It does not apply to the other benefits."
    )
    assert "Version 2026.1" not in section["answer"]["content"]
    assert section["answer"]["source"] == section["sources"][0]


def test_document_search_uses_any_meaningful_term_and_title_weighting() -> None:
    results = documents.search_documents("payment completely-unmatched")
    assert results["answer"]
    assert results["answer"][0]["title"] == "Payment authority"


@pytest.mark.parametrize("query", ["", "!!!"])
def test_searches_refuse_queries_without_meaningful_terms(query: str) -> None:
    with pytest.raises(ValueError, match="at least one"):
        documents.search_documents(query)
    with pytest.raises(ValueError, match="at least one"):
        service.search_reference_data(query)


def test_document_search_rejects_stopwords_but_reference_keeps_them() -> None:
    with pytest.raises(ValueError, match="meaningful"):
        documents.search_documents("the and of")
    assert service.search_reference_data("the and of")["answer"]


def test_blank_term_refuses_but_unknown_term_lists_known_terms() -> None:
    with pytest.raises(ValueError, match="blank"):
        service.lookup_term("   ")
    unknown = service.lookup_term("not a term")
    assert unknown["answer"]["found"] is False
    assert unknown["answer"]["known_terms"]


@pytest.mark.parametrize(
    ("document", "number"),
    [("unknown", 1), ("claims_manual", 999)],
)
def test_invalid_document_and_section_refuse(document: str, number: int) -> None:
    with pytest.raises(ValueError, match="."):
        documents.read_section(document, number)
