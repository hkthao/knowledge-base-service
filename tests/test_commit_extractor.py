import shutil
import subprocess
from pathlib import Path

import pytest

from kb_indexer.extractors.commit_extractor import list_commits


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


def test_lists_commits_with_files(repo):
    (repo / "a.ts").write_text("// a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")

    (repo / "a.ts").write_text("// edit")
    (repo / "b.ts").write_text("// b")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")

    commits = list_commits(str(repo), limit=10)
    assert len(commits) == 2
    second, first = commits  # newest first
    assert second.message == "second"
    assert sorted(second.files) == ["a.ts", "b.ts"]
    assert first.message == "first"
    assert first.files == ["a.ts"]
    assert second.author == "Test"


def test_handles_multiline_subjects_safely(repo):
    (repo / "x.ts").write_text("//")
    _git(repo, "add", "-A")
    # Subject is single-line by git convention; this just sanity-checks that
    # weird characters in the subject don't break the unit-separator parser.
    _git(repo, "commit", "-q", "-m", "fix: handle 'quotes' \"and\" escapes")
    commits = list_commits(str(repo), limit=10)
    assert commits[0].message.startswith("fix: handle")
