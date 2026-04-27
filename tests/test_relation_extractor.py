from kb_indexer.extractors.relation_extractor import resolve_intra_file
from kb_indexer.parsers import ts_parser


def test_intra_file_calls_get_resolved_to_qn():
    src = b"""
function helper(): number { return 1; }
function caller(): number { return helper(); }
""".strip()

    parsed = ts_parser.parse_source("src/u.ts", src)
    parsed.relations = resolve_intra_file(parsed)

    call = next(r for r in parsed.relations if r.rel_type == "CALLS" and r.from_qn.endswith("::caller"))
    assert call.to_qn == "src/u.ts::helper"
    assert call.confidence >= 0.9
    assert call.resolution_type == "same-file"


def test_unresolved_calls_keep_to_qn_none():
    src = b"function caller(): number { return externalLib(); }"
    parsed = ts_parser.parse_source("src/u.ts", src)
    parsed.relations = resolve_intra_file(parsed)
    call = next(r for r in parsed.relations if r.rel_type == "CALLS")
    assert call.to_qn is None
    assert call.to_name == "externalLib"


def test_already_resolved_relations_are_preserved():
    """Roslyn returns relations with `to_qn` already filled in (semantic).
    The same-file resolver must not overwrite that — even if a same-file
    entity happens to share the short name."""
    from kb_indexer.parsers.ts_parser import Entity, ParseResult, Relation

    parsed = ParseResult(file_path="src/X.cs", language="csharp")
    parsed.entities = [
        Entity(
            qualified_name="MyApp::Local.Validate",
            name="Validate",
            symbol_type="method",
            file_path="src/X.cs",
            line_start=1, line_end=5,
            content="", parent_class="Local", language="csharp",
        ),
    ]
    parsed.relations = [
        Relation(
            from_qn="MyApp::Local.Run",
            to_name="Validate",
            to_qn="OtherApp::RemoteValidator.Validate",  # Roslyn-semantic resolution
            rel_type="CALLS",
            confidence=1.0,
            resolution_type="semantic",
        ),
    ]

    resolved = resolve_intra_file(parsed)
    assert resolved[0].to_qn == "OtherApp::RemoteValidator.Validate"
    assert resolved[0].resolution_type == "semantic"
