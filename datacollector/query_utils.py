"""Small deterministic normalizers shared by Planner and Searcher."""

from __future__ import annotations

import re


QUERY_TOKEN = re.compile(r'"[^"]+"|\S+')


def normalize_search_query(value: str) -> str:
    """Collapse whitespace and repeated adjacent search terms or phrases."""

    tokens = QUERY_TOKEN.findall(" ".join(value.split()))
    normalized: list[str] = []
    previous_key: str | None = None
    for token in tokens:
        key = token.strip('"\'“”„.,;:()[]{}').casefold()
        if key and key == previous_key:
            continue
        normalized.append(token)
        previous_key = key
    return " ".join(normalized)


def normalize_search_queries(values: list[str]) -> tuple[list[str], int]:
    """Normalize, discard blanks, and deduplicate queries by folded text."""

    results: list[str] = []
    seen: set[str] = set()
    changed = 0
    for raw_value in values:
        normalized = normalize_search_query(raw_value)
        if normalized != raw_value.strip():
            changed += 1
        key = normalized.casefold()
        if not normalized or key in seen:
            if normalized:
                changed += 1
            continue
        seen.add(key)
        results.append(normalized)
    return results, changed
