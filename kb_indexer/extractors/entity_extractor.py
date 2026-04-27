from __future__ import annotations

from ..parsers import ts_parser
from ..parsers.csharp_parser import CSharpParser
from ..parsers.ts_parser import ParseResult


class UnsupportedFileType(ValueError):
    pass


def extract_from_file(
    file_path: str,
    *,
    project_path: str | None = None,
    csharp_parser: CSharpParser | None = None,
) -> ParseResult:
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")):
        return ts_parser.parse_file(file_path)
    if file_path.endswith(".cs"):
        if project_path is None:
            raise ValueError(f"C# parsing requires project_path (.csproj) for {file_path}")
        parser = csharp_parser or CSharpParser()
        return parser.analyze_file(file_path, project_path)
    raise UnsupportedFileType(f"Unsupported file type: {file_path}")
