from __future__ import annotations

from .llm import LLMClient
from .parsers.ts_parser import Entity
from .settings import settings

_PROMPT_TEMPLATE = """\
Viết 1-2 câu tiếng Việt mô tả nghiệp vụ (business semantics) của hàm/lớp dưới đây.
Tập trung vào: làm gì theo góc nhìn nghiệp vụ, khi nào dùng đến.
Không giải thích kỹ thuật, không nhắc tên biến/tên hàm.
Chỉ trả về 1-2 câu tiếng Việt, không thêm dấu trích dẫn, không markdown.

Tên: {symbol_name}
Loại: {symbol_type}
Signature: {signature}
Docstring: {docstring}
Code:
{content}
"""


def build_prompt(entity: Entity, max_chars: int | None = None) -> str:
    max_chars = max_chars or settings.description_content_chars
    return _PROMPT_TEMPLATE.format(
        symbol_name=entity.name,
        symbol_type=entity.symbol_type,
        signature=entity.signature or "(không có)",
        docstring=entity.docstring or "(không có)",
        content=(entity.content or "").strip()[:max_chars],
    )


def generate_description(entity: Entity, llm: LLMClient) -> str:
    """Returns 1-2 Vietnamese sentences describing the business meaning of an entity.

    Falls through any LLM exception — caller is responsible for marking the
    job failed/retry. We deliberately keep this thin so the worker stays in
    charge of policy.
    """
    raw = llm.complete(build_prompt(entity))
    # Strip any quote wrappers / leading bullets the model may add despite the prompt.
    cleaned = raw.strip().strip("\"'`").lstrip("-•").strip()
    return cleaned
