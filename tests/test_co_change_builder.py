import shutil
import subprocess
from pathlib import Path

import pytest

from kb_indexer.extractors import co_change_builder


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


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def test_pairs_appear_after_min_count(repo):
    # 3 commits both touching a.ts + b.ts → counts to 3
    for i in range(3):
        _commit(repo, {"a.ts": f"// {i}", "b.ts": f"// {i}"}, f"c{i}")

    pairs = co_change_builder.build_pairs(str(repo), min_count=3)
    assert any(
        {p.file_a, p.file_b} == {"a.ts", "b.ts"} and p.count >= 3
        for p in pairs
    )


def test_below_min_count_filtered_out(repo):
    _commit(repo, {"a.ts": "//", "b.ts": "//"}, "init")
    pairs = co_change_builder.build_pairs(str(repo), min_count=3)
    assert pairs == []


def test_skips_mass_format_commits(repo):
    files = {f"src/x{i}.ts": f"// {i}" for i in range(40)}
    _commit(repo, files, "huge format")
    # Make a couple of small commits that share the same files
    _commit(repo, {"src/x0.ts": "// edit"}, "edit a")
    _commit(repo, {"src/x0.ts": "// edit2"}, "edit again")

    pairs = co_change_builder.build_pairs(
        str(repo), min_count=1, max_files_per_commit=10,
    )
    # The 40-file commit is dropped, so x0/x1 don't get a co-change edge.
    assert not any(
        {p.file_a, p.file_b} == {"src/x0.ts", "src/x1.ts"}
        for p in pairs
    )


def test_includes_csharp_files(repo):
    for i in range(3):
        _commit(repo, {"A.cs": f"// {i}", "B.cs": f"// {i}"}, f"c{i}")
    pairs = co_change_builder.build_pairs(str(repo), min_count=3)
    assert any({p.file_a, p.file_b} == {"A.cs", "B.cs"} for p in pairs)
