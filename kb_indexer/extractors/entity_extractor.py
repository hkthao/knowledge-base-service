from __future__ import annotations

from ..parsers import ts_parser
from ..parsers.ts_parser import ParseResult


class UnsupportedFileType(ValueError):
    pass


def extract_from_file(file_path: str) -> ParseResult:
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")):
        return ts_parser.parse_file(file_path)
    if file_path.endswith(".cs"):
        # Roslyn bridge lands in week 3 (§7).
        raise UnsupportedFileType("C# parsing requires roslyn-service (not in week 1–2 scope)")
    raise UnsupportedFileType(f"Unsupported file type: {file_path}")
