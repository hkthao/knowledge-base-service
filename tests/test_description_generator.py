from kb_indexer.description_generator import build_prompt, generate_description
from kb_indexer.parsers.ts_parser import Entity


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


def _entity() -> Entity:
    return Entity(
        qualified_name="src/credit/limits.ts::checkCreditLimit",
        name="checkCreditLimit",
        symbol_type="function",
        file_path="src/credit/limits.ts",
        line_start=10, line_end=25,
        content="export function checkCreditLimit(customer: Customer): boolean { ... }",
        signature="checkCreditLimit(customer: Customer): boolean",
        docstring="/** Validates credit ceiling */",
        language="typescript",
    )


def test_build_prompt_includes_metadata():
    prompt = build_prompt(_entity())
    assert "checkCreditLimit" in prompt
    assert "checkCreditLimit(customer: Customer): boolean" in prompt
    assert "Validates credit ceiling" in prompt
    assert "tiếng Việt" in prompt


def test_build_prompt_truncates_content():
    entity = _entity()
    entity.content = "x" * 10_000
    prompt = build_prompt(entity, max_chars=200)
    code_section = prompt.split("Code:\n", 1)[1]
    assert len(code_section.strip()) <= 200


def test_generate_description_strips_quote_wrappers():
    llm = _FakeLLM('"Kiểm tra hạn mức tín dụng của khách hàng trước khi cho vay."')
    out = generate_description(_entity(), llm)
    assert out.startswith("Kiểm tra")
    assert not out.startswith('"')
    assert not out.endswith('"')


def test_generate_description_strips_bullet_prefix():
    llm = _FakeLLM("- Kiểm tra hạn mức tín dụng của khách hàng.")
    out = generate_description(_entity(), llm)
    assert out.startswith("Kiểm tra")
