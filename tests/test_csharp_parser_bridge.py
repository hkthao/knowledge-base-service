"""Tests for the Python side of the Roslyn bridge — the .NET service is mocked
via httpx.MockTransport so these tests don't require the .NET stack."""
import httpx
import pytest

from kb_indexer.parsers.csharp_parser import CSharpParser


@pytest.fixture
def mock_roslyn():
    handlers = {}

    def transport(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        handler = handlers.get(key)
        if handler is None:
            return httpx.Response(404)
        return handler(request)

    parser = CSharpParser(base_url="http://roslyn-mock")
    parser._client = httpx.Client(transport=httpx.MockTransport(transport))
    return parser, handlers


def test_analyze_file_returns_parse_result(mock_roslyn):
    parser, handlers = mock_roslyn
    handlers[("POST", "/analyze/file")] = lambda req: httpx.Response(
        200,
        json={
            "entities": [
                {
                    "qualified_name": "MyApp.Auth::AuthService",
                    "name": "AuthService",
                    "type": "class",
                    "namespace": "MyApp.Auth",
                    "file_path": "src/Auth/AuthService.cs",
                    "line_start": 5,
                    "line_end": 25,
                    "content": "public class AuthService { ... }",
                    "signature": "public class AuthService",
                    "visibility": "public",
                },
                {
                    "qualified_name": "MyApp.Auth::AuthService.Login",
                    "name": "Login",
                    "type": "method",
                    "class_name": "AuthService",
                    "namespace": "MyApp.Auth",
                    "file_path": "src/Auth/AuthService.cs",
                    "line_start": 10,
                    "line_end": 18,
                    "content": "public bool Login() { ... }",
                    "signature": "public bool Login()",
                    "visibility": "public",
                    "is_async": False,
                    "return_type": "bool",
                },
            ],
            "relations": [
                {
                    "from": "MyApp.Auth::AuthService.Login",
                    "to": "MyApp.Auth::Validator.Validate",
                    "type": "CALLS",
                    "confidence": 1.0,
                    "resolution_type": "semantic",
                },
            ],
        },
    )

    parsed = parser.analyze_file("src/Auth/AuthService.cs", "/repos/MyApp/MyApp.csproj")

    qns = {e.qualified_name for e in parsed.entities}
    # Module is synthesized from file_path so DEFINES edges resolve
    assert "src/Auth/AuthService.cs" in qns
    assert "MyApp.Auth::AuthService" in qns
    assert "MyApp.Auth::AuthService.Login" in qns

    method = next(e for e in parsed.entities if e.qualified_name.endswith(".Login"))
    assert method.symbol_type == "method"
    assert method.parent_class == "AuthService"
    assert method.language == "csharp"

    rel = parsed.relations[0]
    assert rel.rel_type == "CALLS"
    assert rel.confidence == 1.0
    assert rel.to_qn == "MyApp.Auth::Validator.Validate"
    assert rel.resolution_type == "semantic"


def test_health_check_truthy(mock_roslyn):
    parser, handlers = mock_roslyn
    handlers[("GET", "/health")] = lambda req: httpx.Response(
        200, json={"status": "ok", "msbuild_loaded": True},
    )
    assert parser.health_check() is True


def test_health_check_falsy_when_msbuild_not_loaded(mock_roslyn):
    parser, handlers = mock_roslyn
    handlers[("GET", "/health")] = lambda req: httpx.Response(
        200, json={"status": "ok", "msbuild_loaded": False},
    )
    assert parser.health_check() is False


def test_analyze_project_groups_by_file(mock_roslyn):
    parser, handlers = mock_roslyn
    handlers[("POST", "/analyze/project")] = lambda req: httpx.Response(
        200,
        json={
            "entities": [
                {"qualified_name": "X::A", "name": "A", "type": "class",
                 "file_path": "src/A.cs", "line_start": 1, "line_end": 5,
                 "content": "class A {}"},
                {"qualified_name": "X::B", "name": "B", "type": "class",
                 "file_path": "src/B.cs", "line_start": 1, "line_end": 5,
                 "content": "class B {}"},
                {"qualified_name": "X::A.Run", "name": "Run", "type": "method",
                 "class_name": "A", "file_path": "src/A.cs",
                 "line_start": 2, "line_end": 4, "content": "void Run() {}"},
            ],
            "relations": [
                {"from": "X::A.Run", "to": "X::B", "type": "CALLS",
                 "confidence": 1.0, "resolution_type": "semantic"},
            ],
        },
    )

    results = parser.analyze_project("/repos/X/X.csproj")
    by_file = {r.file_path: r for r in results}
    assert {"src/A.cs", "src/B.cs"} <= set(by_file.keys())

    a = by_file["src/A.cs"]
    a_qns = {e.qualified_name for e in a.entities}
    assert {"src/A.cs", "X::A", "X::A.Run"} <= a_qns
    assert any(r.rel_type == "CALLS" for r in a.relations)

    b = by_file["src/B.cs"]
    assert not b.relations  # B has no outgoing relations in this fixture
