"""Stage 13: patch_generator heuristics (planner placeholder vs code; synthetic repairs)."""

from __future__ import annotations

from pathlib import Path

from editing.patch_generator import _looks_like_code, _synthetic_repair, to_structured_patches


def test_looks_like_code_rejects_planner_placeholder_and_equals_spec():
    assert not _looks_like_code("Apply changes from: multiply(2, 3) == 6")
    assert not _looks_like_code("Review for impact: fix tokenize")
    assert _looks_like_code("def foo():\n    return 1\n")


def test_synthetic_multiply_and_text_sub(tmp_path: Path):
    ops = tmp_path / "ops.py"
    ops.write_text(
        "def multiply(a: int, b: int) -> int:\n    return a * b + 1\n",
        encoding="utf-8",
    )
    s = _synthetic_repair("Repair multiply(2,3)==6", str(ops), "multiply", str(tmp_path))
    assert s and s.get("target_node") == "function_body"

    pa = tmp_path / "part_a.py"
    pa.write_text('SUFFIX = "legacy"\n', encoding="utf-8")
    t = _synthetic_repair(
        "Rename legacy to unified in benchmark_local/part_a.py",
        str(pa),
        "",
        str(tmp_path),
    )
    assert t and t.get("action") == "text_sub"


def test_to_structured_skips_non_hinted_files_when_instruction_names_paths(tmp_path: Path):
    root = tmp_path
    a = root / "a.py"
    b = root / "b.py"
    a.write_text("x=1\n", encoding="utf-8")
    b.write_text("y=1\n", encoding="utf-8")
    plan = {
        "changes": [
            {"file": str(a), "symbol": "", "action": "modify", "patch": "Apply changes from: x", "reason": ""},
            {"file": str(b), "symbol": "", "action": "modify", "patch": "Apply changes from: y", "reason": ""},
        ]
    }
    ctx = {"project_root": str(root), "ranked_context": [], "retrieved_symbols": []}
    out = to_structured_patches(plan, "Only edit b.py please", ctx)
    files = {Path(c["file"]).resolve() for c in out["changes"]}
    assert (root / "b.py").resolve() in files
    assert (root / "a.py").resolve() not in files
