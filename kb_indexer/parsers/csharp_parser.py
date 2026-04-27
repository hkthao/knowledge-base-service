from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import httpx

from ..settings import settings
from .ts_parser import Entity, ParseResult, Relation


class CSharpParser:
    """HTTP bridge to the .NET 8 roslyn-service.

    Returns the same `ParseResult` shape as `ts_parser` so the rest of the
    indexing pipeline doesn't need to know about the language difference.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 300.0):
        self.base_url = (base_url or settings.roslyn_url).rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def health_check(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/health", timeout=5.0)
            return resp.is_success and resp.json().get("msbuild_loaded", False)
        except Exception:
            return False

    def invalidate(self, project_path: str) -> None:
        self._client.post(
            f"{self.base_url}/cache/invalidate",
            json={"project_path": project_path},
        ).raise_for_status()

    def analyze_file(self, file_path: str, project_path: str) -> ParseResult:
        resp = self._client.post(
            f"{self.base_url}/analyze/file",
            json={"file_path": file_path, "project_path": project_path},
        )
        resp.raise_for_status()
        return _to_parse_result(resp.json(), default_file_path=file_path)

    def analyze_project(self, project_path: str) -> list[ParseResult]:
        resp = self._client.post(
            f"{self.base_url}/analyze/project",
            json={"project_path": project_path},
        )
        resp.raise_for_status()
        return _to_parse_results_per_file(resp.json())


# ── JSON → ParseResult conversion ─────────────────────────────────────

_SYMBOL_TYPE_MAP = {
    "method": "method",
    "class": "class",
    "interface": "interface",
}


def _to_parse_result(payload: dict, default_file_path: str) -> ParseResult:
    entities_raw = payload.get("entities", [])
    relations_raw = payload.get("relations", [])

    entities, files = _build_entities(entities_raw, default_file_path)
    # Ensure a Module entity exists for every file referenced by entities.
    # The neo4j_store layer expects Module(qualified_name=file_path) so DEFINES
    # edges resolve.
    for file_path in files:
        entities.insert(0, Entity(
            qualified_name=file_path,
            name=Path(file_path).name,
            symbol_type="module",
            file_path=file_path,
            line_start=1,
            line_end=1,
            content="",
            language="csharp",
        ))

    relations = [
        Relation(
            from_qn=r["from"],
            to_name=r["to"].rsplit(".", 1)[-1] if "." in r["to"] else r["to"],
            to_qn=r["to"],
            rel_type=r["type"],
            confidence=float(r.get("confidence", 1.0)),
            resolution_type=r.get("resolution_type", "semantic"),
        )
        for r in relations_raw
    ]

    language = "csharp"
    return ParseResult(
        file_path=default_file_path,
        language=language,
        entities=entities,
        relations=relations,
    )


def _to_parse_results_per_file(payload: dict) -> list[ParseResult]:
    """Group entities by file_path so the indexing pipeline can write per-file."""
    entities_raw = payload.get("entities", [])
    relations_raw = payload.get("relations", [])

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entity in entities_raw:
        grouped[entity["file_path"]].append(entity)

    # Relations are attached to the file of the `from` entity.
    by_qn_to_file = {e["qualified_name"]: e["file_path"] for e in entities_raw}
    rel_grouped: dict[str, list[dict]] = defaultdict(list)
    for relation in relations_raw:
        file_path = by_qn_to_file.get(relation["from"])
        if file_path is not None:
            rel_grouped[file_path].append(relation)

    results: list[ParseResult] = []
    for file_path, file_entities in grouped.items():
        results.append(_to_parse_result(
            {"entities": file_entities, "relations": rel_grouped.get(file_path, [])},
            default_file_path=file_path,
        ))
    return results


def _build_entities(entities_raw: list[dict], default_file_path: str) -> tuple[list[Entity], set[str]]:
    out: list[Entity] = []
    files: set[str] = set()
    for raw in entities_raw:
        symbol_type = _SYMBOL_TYPE_MAP.get(raw.get("type", ""))
        if symbol_type is None:
            continue
        file_path = raw.get("file_path") or default_file_path
        files.add(file_path)
        out.append(Entity(
            qualified_name=raw["qualified_name"],
            name=raw["name"],
            symbol_type=symbol_type,
            file_path=file_path,
            line_start=int(raw.get("line_start", 1)),
            line_end=int(raw.get("line_end", 1)),
            content=raw.get("content", ""),
            signature=raw.get("signature"),
            docstring=raw.get("docstring"),
            parent_class=raw.get("class_name"),
            language="csharp",
        ))
    return out, files
