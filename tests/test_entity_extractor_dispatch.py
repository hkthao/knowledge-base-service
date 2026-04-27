import pytest

from kb_indexer.extractors.entity_extractor import UnsupportedFileType, extract_from_file


def test_dispatches_typescript_to_tree_sitter(tmp_path):
    f = tmp_path / "x.ts"
    f.write_text("export function foo() { return 1; }\n")
    parsed = extract_from_file(str(f))
    assert parsed.language == "typescript"
    assert any(e.symbol_type == "function" and e.name == "foo" for e in parsed.entities)


def test_csharp_requires_project_path(tmp_path):
    f = tmp_path / "X.cs"
    f.write_text("class X {}\n")
    with pytest.raises(ValueError, match="project_path"):
        extract_from_file(str(f))


def test_unsupported_extension_raises(tmp_path):
    f = tmp_path / "thing.rs"
    f.write_text("fn main() {}\n")
    with pytest.raises(UnsupportedFileType):
        extract_from_file(str(f))
