"""Extract recent commits from git and write them as Commit nodes with
TOUCHED_BY edges to source-file Module nodes.

Per plan §5.1 the schema includes:
- Commit { commit_hash*, message, author, date }
- (Commit)-[:TOUCHED_BY]->(Function|Method|Class|Module)

We attach TOUCHED_BY at file (Module) granularity since git diff doesn't
identify which symbol inside a file was actually edited cheaply.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from neo4j import Driver

from ..log import get_logger

log = get_logger(__name__)

# Use ASCII unit separators to make multi-line commit messages parseable.
_GIT_FORMAT = "%H%x1f%an%x1f%aI%x1f%s"  # hash | author | iso-date | subject

INDEXED_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".cs")


@dataclass
class Commit:
    hash: str
    author: str
    date: str
    message: str
    files: list[str]


def list_commits(repo_path: str, limit: int = 500) -> list[Commit]:
    """Return up to `limit` recent commits with their touched source files."""
    result = subprocess.run(
        [
            "git", "-C", str(repo_path),
            "log", f"--format={_GIT_FORMAT}",
            "--name-only", f"-{limit}",
        ],
        capture_output=True, text=True, check=True,
    )
    return _parse_log(result.stdout)


def write_to_neo4j(
    drv: Driver,
    commits: list[Commit],
    *,
    repo_path: str | None = None,
) -> int:
    """Upsert Commit nodes + TOUCHED_BY edges. Returns commits written."""
    written = 0
    with drv.session() as session:
        for commit in commits:
            session.run(
                """
                MERGE (c:Commit {commit_hash: $hash})
                SET c.author = $author,
                    c.date = $date,
                    c.message = $message
                """,
                hash=commit.hash,
                author=commit.author,
                date=commit.date,
                message=commit.message,
            )
            for file_path in commit.files:
                if not file_path.endswith(INDEXED_EXTS):
                    continue
                # Module node uses the absolute path written by the indexer
                # (see _index_one in indexing). Build it the same way.
                qualified_name = (
                    f"{repo_path.rstrip('/')}/{file_path}"
                    if repo_path else file_path
                )
                session.run(
                    """
                    MATCH (c:Commit {commit_hash: $hash})
                    OPTIONAL MATCH (m:Module {qualified_name: $qn})
                    WITH c, m WHERE m IS NOT NULL
                    MERGE (c)-[:TOUCHED_BY]->(m)
                    """,
                    hash=commit.hash, qn=qualified_name,
                )
            written += 1
    log.info("commits_written", count=written)
    return written


def _parse_log(stdout: str) -> list[Commit]:
    out: list[Commit] = []
    current: Commit | None = None
    for raw_line in stdout.splitlines():
        if "\x1f" in raw_line:
            if current is not None:
                out.append(current)
            parts = raw_line.split("\x1f", 3)
            if len(parts) < 4:
                continue
            current = Commit(
                hash=parts[0],
                author=parts[1],
                date=parts[2],
                message=parts[3],
                files=[],
            )
            continue

        if current is None:
            continue

        path = raw_line.strip()
        if path:
            current.files.append(path)

    if current is not None:
        out.append(current)
    return out
