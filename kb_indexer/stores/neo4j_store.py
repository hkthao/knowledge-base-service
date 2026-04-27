from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from ..parsers.ts_parser import Entity, Relation
from ..settings import settings

UNIQUE_LABELS = ("Function", "Method", "Class", "Interface", "Module", "Document", "Issue")
NODE_LABEL_FOR_SYMBOL = {
    "function": "Function",
    "method": "Method",
    "class": "Class",
    "interface": "Interface",
    "module": "Module",
}


def driver() -> Driver:
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


# ── Schema setup ──────────────────────────────────────────────────────

def ensure_constraints(drv: Driver) -> None:
    statements = [
        f"CREATE CONSTRAINT {label.lower()}_qn IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.qualified_name IS UNIQUE"
        for label in UNIQUE_LABELS if label not in {"Document", "Issue"}
    ]
    statements += [
        "CREATE CONSTRAINT document_chunk IF NOT EXISTS FOR (n:Document) REQUIRE n.chunk_id IS UNIQUE",
        "CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (n:Issue) REQUIRE n.issue_id IS UNIQUE",
        "CREATE CONSTRAINT commit_hash IF NOT EXISTS FOR (n:Commit) REQUIRE n.commit_hash IS UNIQUE",
    ]
    statements += [
        f"CREATE INDEX {label.lower()}_chunk IF NOT EXISTS FOR (n:{label}) ON (n.chunk_id)"
        for label in ("Function", "Method", "Class", "Interface", "Module")
    ]
    statements.append(
        "CREATE INDEX file_path_idx IF NOT EXISTS FOR (n:Function) ON (n.file_path)"
    )

    with drv.session() as session:
        for stmt in statements:
            session.run(stmt)


# ── Writes ────────────────────────────────────────────────────────────

def upsert_entity(tx, entity: Entity, chunk_id: str, repo: str) -> None:
    label = NODE_LABEL_FOR_SYMBOL[entity.symbol_type]
    tx.run(
        f"""
        MERGE (n:{label} {{qualified_name: $qn}})
        SET n.chunk_id = $chunk_id,
            n.name = $name,
            n.file_path = $file_path,
            n.line_start = $line_start,
            n.line_end = $line_end,
            n.signature = $signature,
            n.docstring = $docstring,
            n.parent_class = $parent_class,
            n.language = $language,
            n.repo = $repo
        """,
        qn=entity.qualified_name,
        chunk_id=chunk_id,
        name=entity.name,
        file_path=entity.file_path,
        line_start=entity.line_start,
        line_end=entity.line_end,
        signature=entity.signature,
        docstring=entity.docstring,
        parent_class=entity.parent_class,
        language=entity.language,
        repo=repo,
    )

    # DEFINES edge from Module → entity
    if entity.symbol_type != "module":
        tx.run(
            """
            MATCH (m:Module {qualified_name: $module_qn})
            MATCH (n {qualified_name: $qn})
            MERGE (m)-[:DEFINES]->(n)
            """,
            module_qn=entity.file_path,
            qn=entity.qualified_name,
        )


def upsert_relation(tx, relation: Relation) -> None:
    if relation.rel_type == "IMPORTS":
        tx.run(
            """
            MATCH (m:Module {qualified_name: $from_qn})
            MERGE (target:Module {qualified_name: $to_name})
            ON CREATE SET target.placeholder = true
            MERGE (m)-[r:IMPORTS]->(target)
            SET r.confidence = $confidence,
                r.resolution_type = $resolution_type
            """,
            from_qn=relation.from_qn,
            to_name=relation.to_name,
            confidence=relation.confidence,
            resolution_type=relation.resolution_type,
        )
        return

    target_qn = relation.to_qn or relation.to_name
    tx.run(
        f"""
        MATCH (a {{qualified_name: $from_qn}})
        MERGE (b {{qualified_name: $to_qn}})
        ON CREATE SET b.placeholder = true, b.name = $to_name
        MERGE (a)-[r:{relation.rel_type}]->(b)
        SET r.confidence = $confidence,
            r.resolution_type = $resolution_type
        """,
        from_qn=relation.from_qn,
        to_qn=target_qn,
        to_name=relation.to_name,
        confidence=relation.confidence,
        resolution_type=relation.resolution_type,
    )


def insert_parse_result(
    drv: Driver,
    *,
    entities: list[Entity],
    relations: list[Relation],
    chunk_id_by_qn: dict[str, str],
    repo: str,
) -> list[str]:
    """Insert entities + relations idempotently. Returns the list of qualified_names touched."""
    qns = []
    with drv.session() as session:
        for entity in entities:
            chunk_id = chunk_id_by_qn.get(entity.qualified_name, "")
            session.execute_write(upsert_entity, entity, chunk_id, repo)
            qns.append(entity.qualified_name)
        for relation in relations:
            session.execute_write(upsert_relation, relation)
    return qns


def delete_by_file(drv: Driver, file_path: str) -> None:
    with drv.session() as session:
        session.run(
            """
            MATCH (n) WHERE n.file_path = $file_path
            DETACH DELETE n
            """,
            file_path=file_path,
        )


# ── Read helpers ──────────────────────────────────────────────────────

def callers(drv: Driver, chunk_id: str, max_hops: int = 2) -> list[dict[str, Any]]:
    cypher = (
        f"MATCH (caller)-[:CALLS*1..{max_hops}]->(fn) "
        "WHERE fn.chunk_id = $chunk_id "
        "RETURN caller.qualified_name AS qualified_name, "
        "       caller.file_path AS file_path, "
        "       caller.line_start AS line_start"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, chunk_id=chunk_id)]


def callees(drv: Driver, chunk_id: str, max_hops: int = 2) -> list[dict[str, Any]]:
    cypher = (
        f"MATCH (fn)-[:CALLS*1..{max_hops}]->(callee) "
        "WHERE fn.chunk_id = $chunk_id "
        "RETURN callee.qualified_name AS qualified_name, "
        "       callee.file_path AS file_path"
    )
    with drv.session() as session:
        return [dict(r) for r in session.run(cypher, chunk_id=chunk_id)]
