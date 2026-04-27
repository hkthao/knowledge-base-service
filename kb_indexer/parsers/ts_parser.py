from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

SymbolType = Literal["function", "method", "class", "interface", "module"]

_TS_LANG = Language(tsts.language_typescript())
_TSX_LANG = Language(tsts.language_tsx())
_JS_LANG = Language(tsjs.language())


def _language_for(path: str) -> Language:
    if path.endswith(".tsx"):
        return _TSX_LANG
    if path.endswith((".ts", ".mts", ".cts")):
        return _TS_LANG
    return _JS_LANG


@dataclass
class Entity:
    qualified_name: str
    name: str
    symbol_type: SymbolType
    file_path: str
    line_start: int
    line_end: int
    content: str
    signature: str | None = None
    docstring: str | None = None
    parent_class: str | None = None
    language: str = "typescript"


@dataclass
class Relation:
    from_qn: str
    to_name: str  # cross-file resolution happens in relation_extractor / relinker
    rel_type: str  # CALLS | IMPORTS | EXTENDS | IMPLEMENTS | DEFINES | USES_TYPE
    confidence: float = 0.5
    resolution_type: str = "heuristic"
    to_qn: str | None = None  # set when resolved within the same file


@dataclass
class ParseResult:
    file_path: str
    language: str
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


def parse_file(file_path: str) -> ParseResult:
    text = Path(file_path).read_bytes()
    return parse_source(file_path, text)


def parse_source(file_path: str, source: bytes) -> ParseResult:
    lang = _language_for(file_path)
    parser = Parser(lang)
    tree = parser.parse(source)
    language_label = "typescript" if file_path.endswith((".ts", ".tsx", ".mts", ".cts")) else "javascript"

    module_qn = file_path
    result = ParseResult(file_path=file_path, language=language_label)
    result.entities.append(Entity(
        qualified_name=module_qn,
        name=Path(file_path).name,
        symbol_type="module",
        file_path=file_path,
        line_start=1,
        line_end=tree.root_node.end_point[0] + 1,
        content="",  # module content not embedded as one chunk
        language=language_label,
    ))

    _walk(tree.root_node, source, file_path, language_label, module_qn, parent_class=None, result=result)
    _extract_imports(tree.root_node, source, module_qn, result)
    return result


# ── Walker ────────────────────────────────────────────────────────────

def _walk(
    node: Node,
    source: bytes,
    file_path: str,
    language_label: str,
    module_qn: str,
    parent_class: str | None,
    result: ParseResult,
) -> None:
    kind = node.type

    if kind in {"class_declaration", "interface_declaration"}:
        name = _name_of(node, source)
        if name is None:
            for child in node.children:
                _walk(child, source, file_path, language_label, module_qn, parent_class, result)
            return
        qn = f"{module_qn}::{name}"
        symbol_type: SymbolType = "interface" if kind == "interface_declaration" else "class"
        result.entities.append(Entity(
            qualified_name=qn,
            name=name,
            symbol_type=symbol_type,
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            content=_text(node, source),
            signature=_first_line(_text(node, source)),
            language=language_label,
        ))
        # heritage
        _extract_heritage(node, source, qn, result)
        for child in node.children:
            _walk(child, source, file_path, language_label, module_qn, parent_class=name, result=result)
        return

    if kind in {"function_declaration", "function_signature"}:
        name = _name_of(node, source)
        if name is not None:
            qn = f"{module_qn}::{name}"
            entity = Entity(
                qualified_name=qn,
                name=name,
                symbol_type="function",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                content=_text(node, source),
                signature=_signature_of(node, source),
                docstring=_docstring_before(node, source),
                language=language_label,
            )
            result.entities.append(entity)
            _extract_calls(node, source, qn, result)
        return

    if kind in {"method_definition", "method_signature"}:
        name = _name_of(node, source)
        if name is not None and parent_class is not None:
            qn = f"{module_qn}::{parent_class}.{name}"
            result.entities.append(Entity(
                qualified_name=qn,
                name=name,
                symbol_type="method",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                content=_text(node, source),
                signature=_signature_of(node, source),
                docstring=_docstring_before(node, source),
                parent_class=parent_class,
                language=language_label,
            ))
            _extract_calls(node, source, qn, result)
        return

    # Arrow functions assigned to const/let/var at top level
    if kind in {"lexical_declaration", "variable_declaration"}:
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            if value_node.type in {"arrow_function", "function_expression", "function"}:
                name = _text(name_node, source)
                qn = f"{module_qn}::{name}"
                result.entities.append(Entity(
                    qualified_name=qn,
                    name=name,
                    symbol_type="function",
                    file_path=file_path,
                    line_start=declarator.start_point[0] + 1,
                    line_end=declarator.end_point[0] + 1,
                    content=_text(declarator, source),
                    signature=_signature_of(value_node, source),
                    docstring=_docstring_before(node, source),
                    language=language_label,
                ))
                _extract_calls(value_node, source, qn, result)
        return

    for child in node.children:
        _walk(child, source, file_path, language_label, module_qn, parent_class, result)


# ── Helpers ───────────────────────────────────────────────────────────

def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_line(s: str) -> str:
    line = s.splitlines()[0] if s else ""
    return line.strip()


def _name_of(node: Node, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node, source)


def _signature_of(node: Node, source: bytes) -> str | None:
    params = node.child_by_field_name("parameters")
    return_type = node.child_by_field_name("return_type")
    name_node = node.child_by_field_name("name")
    parts: list[str] = []
    if name_node is not None:
        parts.append(_text(name_node, source))
    if params is not None:
        parts.append(_text(params, source))
    if return_type is not None:
        parts.append(_text(return_type, source))
    return " ".join(parts) if parts else None


def _docstring_before(node: Node, source: bytes) -> str | None:
    sibling = node.prev_sibling
    while sibling is not None and sibling.type == "comment":
        text = _text(sibling, source).strip()
        if text.startswith("/**"):
            return text
        sibling = sibling.prev_sibling
    return None


def _extract_heritage(class_node: Node, source: bytes, qn: str, result: ParseResult) -> None:
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "extends_clause":
                    for name_node in sub.children:
                        if name_node.type in {"identifier", "type_identifier", "member_expression"}:
                            result.relations.append(Relation(
                                from_qn=qn,
                                to_name=_text(name_node, source),
                                rel_type="EXTENDS",
                                confidence=0.7,
                                resolution_type="heuristic",
                            ))
                elif sub.type == "implements_clause":
                    for type_node in sub.children:
                        if type_node.type in {"type_identifier", "generic_type", "identifier"}:
                            result.relations.append(Relation(
                                from_qn=qn,
                                to_name=_text(type_node, source),
                                rel_type="IMPLEMENTS",
                                confidence=0.7,
                                resolution_type="heuristic",
                            ))


def _extract_calls(fn_node: Node, source: bytes, caller_qn: str, result: ParseResult) -> None:
    stack = [fn_node]
    while stack:
        node = stack.pop()
        if node is not fn_node and node.type in {
            "function_declaration", "method_definition", "function_expression", "arrow_function",
        }:
            continue  # don't descend into nested functions; they get their own walk
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                callee_name = _callee_name(fn, source)
                if callee_name and callee_name != "this":
                    confidence = 0.9 if fn.type == "identifier" else 0.5
                    result.relations.append(Relation(
                        from_qn=caller_qn,
                        to_name=callee_name,
                        rel_type="CALLS",
                        confidence=confidence,
                        resolution_type="heuristic",
                    ))
        for child in node.children:
            stack.append(child)


def _callee_name(node: Node, source: bytes) -> str | None:
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "member_expression":
        property_node = node.child_by_field_name("property")
        if property_node is not None:
            return _text(property_node, source)
    return None


def _extract_imports(root: Node, source: bytes, module_qn: str, result: ParseResult) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in {"import_statement", "import_declaration"}:
            source_node = node.child_by_field_name("source")
            if source_node is not None:
                target = _text(source_node, source).strip("\"'`")
                result.relations.append(Relation(
                    from_qn=module_qn,
                    to_name=target,
                    rel_type="IMPORTS",
                    confidence=0.8,
                    resolution_type="heuristic",
                ))
            continue
        for child in node.children:
            stack.append(child)
