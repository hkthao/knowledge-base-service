from kb_indexer.query.filters import build_filter


def test_empty_returns_none():
    assert build_filter(None) is None
    assert build_filter({}) is None


def test_scalar_value_becomes_match_value():
    f = build_filter({"repo": "demo"})
    assert f is not None
    assert len(f.must) == 1
    cond = f.must[0]
    assert cond.key == "repo"
    assert cond.match.value == "demo"


def test_list_value_becomes_match_any():
    f = build_filter({"language": ["typescript", "csharp"]})
    assert f is not None
    cond = f.must[0]
    assert cond.match.any == ["typescript", "csharp"]


def test_none_values_filtered_out():
    f = build_filter({"repo": "demo", "language": None})
    assert len(f.must) == 1
    assert f.must[0].key == "repo"
