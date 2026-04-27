"""Build module-level CO_CHANGED edges from git history.

Per plan §10.7 (revised): edges live between Module nodes (file-level),
not Function nodes — git log doesn't expose symbol-level diffs cheaply.
Mass-format / mass-rename commits would otherwise spam co-occurrence
counts; we filter commits that touch more than `max_files_per_commit`
source files.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from neo4j import Driver

from ..log import get_logger

log = get_logger(__name__)

INDEXED_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".cs")
DEFAULT_MAX_FILES_PER_COMMIT = 30
DEFAULT_MIN_COUNT = 3
DEFAULT_LOOKBACK = 500


@dataclass
class CoChangePair:
    file_a: str
    file_b: str
    count: int
    last_seen: str


def build_pairs(
    repo_path: str,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    min_count: int = DEFAULT_MIN_COUNT,
    max_files_per_commit: int = DEFAULT_MAX_FILES_PER_COMMIT,
) -> list[CoChangePair]:
    result = subprocess.run(
        [
            "git", "-C", str(repo_path),
            "log", "--name-only", "--format=%H%x1f%aI",
            f"-{lookback}",
        ],
        capture_output=True, text=True, check=True,
    )
    commits = _parse_log(result.stdout)

    co_count: dict[tuple[str, str], int] = defaultdict(int)
    last_seen: dict[tuple[str, str], str] = {}

    for commit_hash, date, files in commits:
        source_files = [f for f in files if f.endswith(INDEXED_EXTS)]
        if len(source_files) > max_files_per_commit:
            # Refactor / format commits dilute the signal.
            continue
        for a, b in combinations(sorted(source_files), 2):
            key = (a, b)
            co_count[key] += 1
            last_seen[key] = date

    return [
        CoChangePair(file_a=a, file_b=b, count=count, last_seen=last_seen[(a, b)])
        for (a, b), count in co_count.items()
        if count >= min_count
    ]


def write_to_neo4j(
    drv: Driver,
    pairs: list[CoChangePair],
    *,
    repo_path: str | None = None,
) -> int:
    """Insert/refresh CO_CHANGED edges between Module nodes."""
    written = 0
    root = repo_path.rstrip("/") if repo_path else None
    with drv.session() as session:
        for pair in pairs:
            qn_a = f"{root}/{pair.file_a}" if root else pair.file_a
            qn_b = f"{root}/{pair.file_b}" if root else pair.file_b
            res = session.run(
                """
                MATCH (a:Module {qualified_name: $qn_a})
                MATCH (b:Module {qualified_name: $qn_b})
                MERGE (a)-[r:CO_CHANGED]-(b)
                SET r.count = $count, r.last_seen = $last_seen
                RETURN r
                """,
                qn_a=qn_a, qn_b=qn_b,
                count=pair.count, last_seen=pair.last_seen,
            )
            if res.peek() is not None:
                written += 1
    log.info("co_changed_written", count=written, total_pairs=len(pairs))
    return written


# ── Internal ──────────────────────────────────────────────────────────

def _parse_log(stdout: str) -> list[tuple[str, str, list[str]]]:
    """Parse `git log --name-only --format=...` output into
    (commit_hash, date, [files])."""
    commits: list[tuple[str, str, list[str]]] = []
    current_hash: str | None = None
    current_date: str | None = None
    current_files: list[str] = []

    for raw_line in stdout.splitlines():
        if "\x1f" in raw_line:
            if current_hash is not None:
                commits.append((current_hash, current_date or "", current_files))
            parts = raw_line.split("\x1f", 1)
            current_hash = parts[0]
            current_date = parts[1] if len(parts) > 1 else ""
            current_files = []
            continue
        if current_hash is None:
            continue
        path = raw_line.strip()
        if path:
            current_files.append(path)

    if current_hash is not None:
        commits.append((current_hash, current_date or "", current_files))
    return commits
