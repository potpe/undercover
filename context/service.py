"""Read-only context operations over the packaged governance sources."""

import re
from typing import Any

from context.data import SOURCES, provenance
from context.text import query_terms


def envelope(answer: Any, filename: str) -> dict[str, Any]:
    return {"answer": answer, "sources": [provenance(filename)]}


def lookup_term(term: str) -> dict[str, Any]:
    """Look up a policy term without regard to capitalization."""
    if not term.strip():
        raise ValueError("term must not be blank")
    glossary = SOURCES["glossary.yaml"]
    requested = term.strip().casefold()
    terms = glossary["terms"]
    found = next((item for item in terms if item["term"].casefold() == requested), None)
    if found is None:
        answer = {
            "found": False,
            "known_terms": [item["term"] for item in terms],
        }
    else:
        answer = {"found": True, **found}
    return envelope(answer, "glossary.yaml")


def describe_fields(record: str) -> dict[str, Any]:
    """Describe the fields in a claim, policy or previous-claim record."""
    dictionary = SOURCES["data_dictionary.yaml"]
    requested = record.strip().casefold()
    records = dictionary["records"]
    found = next(
        (item for item in records if item["record"].casefold() == requested), None
    )
    if found is None:
        raise ValueError(f"unknown record: {record}")
    return envelope(found, "data_dictionary.yaml")


def get_plan(plan: str) -> dict[str, Any]:
    """Return a plan's limits, deductible rules and available add-ons."""
    plans = SOURCES["plans.yaml"]
    requested = plan.strip().casefold()
    found = next(
        (item for item in plans["plans"] if item["plan"].casefold() == requested),
        None,
    )
    if found is None:
        raise ValueError(f"unknown plan: {plan}")
    answer = {
        **found,
        "deductible_applies_to": plans["deductible_applies_to"],
        "add_ons": plans["add_ons"],
    }
    return envelope(answer, "plans.yaml")


def get_claim_rules() -> dict[str, Any]:
    """Return the claims operations rules used across claims."""
    return envelope(SOURCES["claim_rules.yaml"]["rules"], "claim_rules.yaml")


def search_reference_data(query: str) -> dict[str, Any]:
    """Search outside reference facts where every query word appears."""
    words = query_terms(query)
    reference = SOURCES["reference_data.yaml"]
    matches = []
    for topic in reference["topics"]:
        facts = [
            fact
            for fact in topic["facts"]
            if words.issubset(set(re.findall(r"\w+", fact.casefold())))
        ]
        if facts:
            matches.append(
                {
                    "topic": topic["topic"],
                    "description": topic["description"],
                    "facts": facts,
                }
            )
    return envelope(matches, "reference_data.yaml")
