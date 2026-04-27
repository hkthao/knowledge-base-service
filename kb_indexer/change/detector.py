"""Detect file-level changes between two git revisions.

`git diff --name-status -M` parses each line as a single-letter status
(M/A/D/R), giving us a clean wire format that doesn't require shelling
out per-file. R lines also include a similarity score and both paths.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

INDEXED_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".cs")


@dataclass
class ChangeSet:
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    # (old_path, new_path) — old is logically deleted, new is logically added.
    renamed: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.modified or self.added or self.deleted or self.renamed)

    def all_changed_paths(self) -> set[str]:
        out = set(self.modified) | set(self.added) | set(self.deleted)
        for old, new in self.renamed:
            out.add(old)
            out.add(new)
        return out


def detect_code_changes(
    repo_path: str,
    since_commit: str,
    current_commit: str = "HEAD",
    *,
    indexed_exts: tuple[str, ...] = INDEXED_EXTS,
) -> ChangeSet:
    """Run `git diff --name-status -M` and parse the result.

    -M enables rename detection (default 50% similarity). We resolve all
    paths to absolute (joined to `repo_path`) so downstream code never
    has to think about cwd.
    """
    result = subprocess.run(
        [
            "git", "-C", str(repo_path),
            "diff", "--name-status", "-M",
            since_commit, current_commit,
        ],
        capture_output=True, text=True, check=True,
    )
    return _parse_diff(result.stdout, repo_path, indexed_exts)


def detect_doc_changes(
    repo_path: str,
    since_commit: str,
    current_commit: str = "HEAD",
    *,
    doc_exts: tuple[str, ...] = (".md", ".markdown", ".txt"),
) -> ChangeSet:
    """Same as code, but only Markdown/text files."""
    result = subprocess.run(
        [
            "git", "-C", str(repo_path),
            "diff", "--name-status", "-M",
            since_commit, current_commit,
        ],
        capture_output=True, text=True, check=True,
    )
    return _parse_diff(result.stdout, repo_path, doc_exts)


# ── Internal ──────────────────────────────────────────────────────────

def _parse_diff(stdout: str, repo_path: str, accepted_exts: tuple[str, ...]) -> ChangeSet:
    root = Path(repo_path).resolve()
    cs = ChangeSet()

    def _accept(path: str) -> bool:
        return path.endswith(accepted_exts)

    def _abs(rel: str) -> str:
        return str(root / rel)

    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        status = parts[0]

        # R<score> oldpath newpath  /  C<score> oldpath newpath
        if status.startswith("R") or status.startswith("C"):
            if len(parts) < 3:
                continue
            old_rel, new_rel = parts[1], parts[2]
            if _accept(old_rel) or _accept(new_rel):
                cs.renamed.append((_abs(old_rel), _abs(new_rel)))
            continue

        if len(parts) < 2:
            continue
        rel = parts[1]
        if not _accept(rel):
            continue

        if status == "M":
            cs.modified.append(_abs(rel))
        elif status == "A":
            cs.added.append(_abs(rel))
        elif status == "D":
            cs.deleted.append(_abs(rel))
        elif status == "T":
            # Type change (file ↔ symlink) — treat as modification.
            cs.modified.append(_abs(rel))
        # Other statuses (U=unmerged, X=unknown) are skipped on purpose.

    return cs
