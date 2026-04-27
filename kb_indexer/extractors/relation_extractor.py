from __future__ import annotations

from ..parsers.ts_parser import Entity, ParseResult, Relation


def resolve_intra_file(parsed: ParseResult) -> list[Relation]:
    """Upgrade `to_qn` for relations whose target lives in the same file.

    Cross-file resolution is the relinker's job (ripgrep). Within a file we can
    resolve safely on name match — so we set `to_qn` and bump confidence.
    """
    by_name: dict[str, Entity] = {}
    for entity in parsed.entities:
        if entity.symbol_type in {"function", "method", "class", "interface"}:
            by_name.setdefault(entity.name, entity)

    resolved: list[Relation] = []
    for rel in parsed.relations:
        target = by_name.get(rel.to_name)
        if target is None:
            resolved.append(rel)
            continue
        resolved.append(Relation(
            from_qn=rel.from_qn,
            to_name=rel.to_name,
            to_qn=target.qualified_name,
            rel_type=rel.rel_type,
            confidence=max(rel.confidence, 0.9),
            resolution_type="same-file",
        ))
    return resolved
