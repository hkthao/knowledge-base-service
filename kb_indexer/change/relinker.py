"""Cross-file relink driven by ripgrep.

When a file's exported symbol set changes (added/renamed/removed names),
files that *reference* those names by short identifier need their CALLS
edges rebuilt. That's because cross-file resolution in `index_file` is
heuristic — placeholder Neo4j nodes are created when a referenced symbol
hasn't been indexed yet, and only re-indexing the referencing file with
the now-real target on disk redirects the edge to the right node.

This module finds candidate referencer files via ripgrep; the handler
re-indexes them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# We pass `--type` filters to keep ripgrep fast on large repos. ripgrep's
# built-in `ts` type already covers .tsx/.cts/.mts; `js` covers .jsx/.cjs/.mjs.
_RG_TYPES = ("ts", "js", "cs")


def find_referencers(symbol_names: set[str], repo_path: str) -> set[str]:
    """Return absolute paths of source files that mention any of the
    given short names as a word boundary match. The caller decides what
    to do with them (typically: re-index)."""
    if not symbol_names or not _have_ripgrep():
        return set()

    # Escape user-provided names; build alternation pattern.
    escaped = (re.escape(n) for n in symbol_names if n)
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    if pattern == r"\b(?:)\b":
        return set()

    args = [
        "rg", "--files-with-matches",
        "--no-messages",
        "--regexp", pattern,
    ]
    for ext in _RG_TYPES:
        args.extend(["--type", ext])

    result = subprocess.run(
        args,
        cwd=str(repo_path),
        capture_output=True, text=True,
    )

    root = Path(repo_path).resolve()
    files: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        path = (root / line).resolve()
        if path.is_file():
            files.add(str(path))
    return files


def _have_ripgrep() -> bool:
    return shutil.which("rg") is not None
