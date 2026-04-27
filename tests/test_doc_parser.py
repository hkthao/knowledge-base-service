from kb_indexer.parsers.doc_parser import _chunk_markdown, parse_file


def test_markdown_split_by_headings(tmp_path):
    md = """# Intro

Mở đầu tài liệu.

## Cài đặt

Cài bằng pip install foo.

## Sử dụng

Gọi foo.run() và chờ kết quả.
"""
    chunks = _chunk_markdown(md)
    titles = [c.title for c in chunks if c.content.strip()]
    assert "Cài đặt" in titles
    assert "Sử dụng" in titles


def test_markdown_chunks_within_long_section():
    long_text = "## H\n" + ("Đoạn văn rất dài. " * 200)
    chunks = _chunk_markdown(long_text)
    assert len(chunks) > 1
    assert all(c.title == "H" for c in chunks)


def test_parse_file_dispatches(tmp_path):
    f = tmp_path / "guide.md"
    f.write_text("# Hello\n\nWorld\n", encoding="utf-8")
    chunks = parse_file(str(f))
    assert chunks
    assert chunks[0].title == "Hello"
