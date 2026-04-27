from pathlib import Path

import pytest

from kb_indexer.parsers.csproj_resolver import CsprojResolver


def _make_repo(tmp_path: Path) -> Path:
    """Layout:
       repo/
         MyApp/MyApp.csproj
         MyApp/src/Auth/Validator.cs
         MyApp/src/Util.cs
         MyApp/Sub/Sub.csproj
         MyApp/Sub/Nested.cs
    """
    (tmp_path / "MyApp").mkdir()
    (tmp_path / "MyApp" / "MyApp.csproj").write_text("<Project/>")
    (tmp_path / "MyApp" / "src" / "Auth").mkdir(parents=True)
    (tmp_path / "MyApp" / "src" / "Auth" / "Validator.cs").write_text("// validator")
    (tmp_path / "MyApp" / "src" / "Util.cs").write_text("// util")
    (tmp_path / "MyApp" / "Sub").mkdir()
    (tmp_path / "MyApp" / "Sub" / "Sub.csproj").write_text("<Project/>")
    (tmp_path / "MyApp" / "Sub" / "Nested.cs").write_text("// nested")
    return tmp_path


def test_resolves_to_owning_csproj(tmp_path):
    repo = _make_repo(tmp_path)
    r = CsprojResolver(str(repo))
    assert r.resolve(str(repo / "MyApp" / "src" / "Auth" / "Validator.cs")).endswith("MyApp.csproj")
    assert r.resolve(str(repo / "MyApp" / "src" / "Util.cs")).endswith("MyApp.csproj")


def test_picks_deepest_csproj_for_nested_projects(tmp_path):
    repo = _make_repo(tmp_path)
    r = CsprojResolver(str(repo))
    chosen = r.resolve(str(repo / "MyApp" / "Sub" / "Nested.cs"))
    assert chosen.endswith("Sub.csproj"), f"expected nested Sub.csproj, got {chosen}"


def test_unowned_file_raises(tmp_path):
    (tmp_path / "stray.cs").write_text("// stray")
    r = CsprojResolver(str(tmp_path))
    with pytest.raises(ValueError):
        r.resolve(str(tmp_path / "stray.cs"))


def test_caches_lookups(tmp_path):
    repo = _make_repo(tmp_path)
    r = CsprojResolver(str(repo))
    target = str(repo / "MyApp" / "src" / "Util.cs")
    first = r.resolve(target)
    # If we delete the .csproj after the first lookup, the cached answer must hold.
    (repo / "MyApp" / "MyApp.csproj").unlink()
    assert r.resolve(target) == first
