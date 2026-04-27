from kb_indexer.parsers import ts_parser


def test_parses_function_declaration_with_qualified_name():
    src = b"""
export function validateUser(user: User): boolean {
  if (!user.id) {
    return false;
  }
  return true;
}
""".strip()

    result = ts_parser.parse_source("src/auth/validator.ts", src)

    fn_entities = [e for e in result.entities if e.symbol_type == "function"]
    assert len(fn_entities) == 1
    fn = fn_entities[0]
    assert fn.qualified_name == "src/auth/validator.ts::validateUser"
    assert fn.name == "validateUser"
    assert "boolean" in (fn.signature or "")
    assert fn.line_start == 1


def test_parses_class_methods():
    src = b"""
export class AuthService {
  login(username: string, password: string): boolean {
    return this.validate(username);
  }

  private validate(name: string): boolean {
    return name.length > 0;
  }
}
""".strip()

    result = ts_parser.parse_source("src/auth/AuthService.ts", src)

    classes = [e for e in result.entities if e.symbol_type == "class"]
    methods = [e for e in result.entities if e.symbol_type == "method"]
    assert [c.qualified_name for c in classes] == ["src/auth/AuthService.ts::AuthService"]
    assert sorted(m.qualified_name for m in methods) == [
        "src/auth/AuthService.ts::AuthService.login",
        "src/auth/AuthService.ts::AuthService.validate",
    ]
    for m in methods:
        assert m.parent_class == "AuthService"


def test_extracts_calls_with_confidence():
    src = b"""
function helper(x: number): number {
  return x * 2;
}

function caller(): number {
  return helper(10);
}
""".strip()

    result = ts_parser.parse_source("src/util.ts", src)
    calls = [r for r in result.relations if r.rel_type == "CALLS"]
    assert any(r.from_qn == "src/util.ts::caller" and r.to_name == "helper" for r in calls)
    for c in calls:
        assert 0.0 < c.confidence <= 1.0


def test_extracts_imports():
    src = b'import { validateUser } from "./validator";\n'
    result = ts_parser.parse_source("src/auth/index.ts", src)
    imports = [r for r in result.relations if r.rel_type == "IMPORTS"]
    assert len(imports) == 1
    assert imports[0].to_name == "./validator"
    assert imports[0].confidence >= 0.7


def test_module_entity_emitted_first():
    result = ts_parser.parse_source("src/x.ts", b"export const x = 1;\n")
    assert result.entities[0].symbol_type == "module"
    assert result.entities[0].qualified_name == "src/x.ts"


def test_extends_clause_yields_relation():
    src = b"""
class Base {}
class Child extends Base {}
""".strip()
    result = ts_parser.parse_source("src/c.ts", src)
    extends = [r for r in result.relations if r.rel_type == "EXTENDS"]
    assert len(extends) == 1
    assert extends[0].from_qn == "src/c.ts::Child"
    assert extends[0].to_name == "Base"
