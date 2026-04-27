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
