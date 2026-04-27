from __future__ import annotations

from pathlib import Path


class CsprojResolver:
    """Map a .cs file to its owning .csproj.

    Discovers all `.csproj` under `repo_root` once at construction. For a
    given file, picks the deepest .csproj on the file's path — that's the
    project that owns the file (matches MSBuild's Compile-glob semantics
    when nested projects are present).

    Caches per-file lookups; safe to reuse across files in the same repo.
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._project_files: list[Path] = sorted(
            p.resolve() for p in self.repo_root.rglob("*.csproj")
        )
        self._cache: dict[str, str] = {}

    def resolve(self, file_path: str) -> str:
        if file_path in self._cache:
            return self._cache[file_path]

        path = Path(file_path).resolve()
        candidates = [
            p for p in self._project_files
            if path.is_relative_to(p.parent)
        ]
        if not candidates:
            raise ValueError(f"No .csproj owns {file_path}")

        # Deepest .csproj wins — tie-break by alphabetical (stable).
        chosen = max(candidates, key=lambda p: (len(p.parent.parts), str(p)))
        resolved = str(chosen)
        self._cache[file_path] = resolved
        return resolved

    def projects(self) -> list[str]:
        return [str(p) for p in self._project_files]
