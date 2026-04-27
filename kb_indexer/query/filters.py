"""Build Qdrant filters from a flat user-supplied dict.

Keeps the API contract simple: callers send `{"repo": "x", "source_reliability": "high"}`
and we translate into Qdrant `Filter` shape. Lists become `MatchAny`,
scalars become `MatchValue`. Unknown keys pass through as keyword
matches — payload indexes already cover the common ones.
"""

from __future__ import annotations

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue


def build_filter(filters: dict | None) -> Filter | None:
    if not filters:
        return None
    must: list[FieldCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            must.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=must) if must else None
