"""Tokenize context search queries."""

import re

STOP_WORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to"})


def query_terms(query: str) -> set[str]:
    """Return query words or refuse a query without searchable words."""
    terms = set(re.findall(r"\w+", query.casefold()))
    if not terms:
        raise ValueError("query must contain at least one word")
    return terms


def meaningful_terms(query: str) -> set[str]:
    """Return query words after filtering the legacy search stopwords."""
    terms = query_terms(query) - STOP_WORDS
    if not terms:
        raise ValueError("query must contain at least one meaningful term")
    return terms
