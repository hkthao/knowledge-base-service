import shutil
import subprocess
from pathlib import Path

import pytest

from kb_indexer.change.detector import detect_code_changes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    r = tmp_path / "demo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    return r


def test_detects_modified_added_deleted(repo):
    (repo / "a.ts").write_text("export function a() {}\n")
    (repo / "b.ts").write_text("export function b() {}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Modify a, add c, delete b
    (repo / "a.ts").write_text("export function a() { return 1; }\n")
    (repo / "c.ts").write_text("export function c() {}\n")
    (repo / "b.ts").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")

    cs = detect_code_changes(str(repo), base)
    rel = lambda p: p.split(str(repo) + "/", 1)[-1]
    assert sorted(rel(p) for p in cs.modified) == ["a.ts"]
    assert sorted(rel(p) for p in cs.added) == ["c.ts"]
    assert sorted(rel(p) for p in cs.deleted) == ["b.ts"]
    assert cs.renamed == []


def test_detects_renames(repo):
    (repo / "old.ts").write_text("export function foo() {}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    _git(repo, "mv", "old.ts", "new.ts")
    _git(repo, "commit", "-q", "-m", "rename")

    cs = detect_code_changes(str(repo), base)
    assert len(cs.renamed) == 1
    old, new = cs.renamed[0]
    assert old.endswith("/old.ts")
    assert new.endswith("/new.ts")


def test_filters_non_indexed_extensions(repo):
    (repo / "a.ts").write_text("// ts\n")
    (repo / "README.md").write_text("# md\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    (repo / "a.ts").write_text("// ts edit\n")
    (repo / "README.md").write_text("# md edit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "edit")

    cs = detect_code_changes(str(repo), base)
    assert any(p.endswith("a.ts") for p in cs.modified)
    assert not any(p.endswith(".md") for p in cs.modified)


def test_empty_when_no_changes(repo):
    (repo / "a.ts").write_text("// noop\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    cs = detect_code_changes(str(repo), base)
    assert cs.is_empty()
