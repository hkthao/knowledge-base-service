import shutil

import pytest

from kb_indexer.change.relinker import find_referencers


@pytest.fixture(autouse=True)
def _require_rg():
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not available")


def test_finds_files_referencing_short_names(tmp_path):
    (tmp_path / "a.ts").write_text("export function validateUser() {}\n")
    (tmp_path / "b.ts").write_text("import { validateUser } from './a';\nvalidateUser();\n")
    (tmp_path / "c.ts").write_text("function unrelated() {}\n")

    found = find_referencers({"validateUser"}, str(tmp_path))
    rels = {p.split(str(tmp_path) + "/", 1)[-1] for p in found}
    # Both files mention validateUser; c.ts must NOT be in the result.
    assert "a.ts" in rels
    assert "b.ts" in rels
    assert "c.ts" not in rels


def test_word_boundary_avoids_partial_matches(tmp_path):
    (tmp_path / "x.ts").write_text("function checkCreditLimit() {}\n")
    (tmp_path / "y.ts").write_text("function checkCreditLimitExtended() {}\n")
    found = find_referencers({"checkCreditLimit"}, str(tmp_path))
    rels = {p.split(str(tmp_path) + "/", 1)[-1] for p in found}
    assert "x.ts" in rels
    # y.ts has the longer name as a substring — \b shouldn't match.
    assert "y.ts" not in rels


def test_empty_set_returns_empty(tmp_path):
    (tmp_path / "x.ts").write_text("function foo() {}\n")
    assert find_referencers(set(), str(tmp_path)) == set()
