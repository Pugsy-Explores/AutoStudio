from pathlib import Path

from agent_v2.exploration import file_symbol_outline as mod


def test_load_python_file_outline_includes_full_code(monkeypatch, tmp_path: Path):
    p = tmp_path / "a.py"
    p.write_text("def foo():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        "repo_index.parser.parse_file",
        lambda _fp: object(),
    )
    monkeypatch.setattr(
        "repo_index.symbol_extractor.extract_symbols",
        lambda _tree, _fp, _src: [
            {
                "symbol_name": "foo",
                "symbol_type": "function",
                "start_line": 1,
                "end_line": 2,
            }
        ],
    )

    out = mod.load_python_file_outline(str(p))
    assert out
    row = out[0]
    assert row["name"] == "foo"
    assert row["start_line"] == "1"
    assert row["end_line"] == "2"
    assert "def foo():" in row["code"]
    assert "return 1" in row["code"]
