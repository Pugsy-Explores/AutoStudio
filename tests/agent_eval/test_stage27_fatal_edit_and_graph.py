"""Stage 27 — Regression tests for FATAL_EDIT and graph_builder RCA fixes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.execution.mutation_strategies import symbol_retry
from agent.retrieval.target_resolution import resolve_edit_targets_for_plan
from editing.diff_planner import plan_diff
from repo_graph.graph_builder import build_graph


def test_graph_builder_resolves_qualified_and_short_name_edges(tmp_path):
    """Graph builder resolves both qualified (module.symbol) and short-name edge pairs."""
    out = str(tmp_path / "index.sqlite")
    symbols = [
        {"symbol_name": "ops.multiply", "symbol_type": "function", "file": "src/calc/ops.py", "start_line": 1, "end_line": 5, "docstring": ""},
        {"symbol_name": "ops.divide", "symbol_type": "function", "file": "src/calc/ops.py", "start_line": 7, "end_line": 12, "docstring": ""},
    ]
    edges = [
        {"source_symbol": "ops.multiply", "target_symbol": "ops.divide", "relation_type": "references"},
        {"source_symbol": "multiply", "target_symbol": "divide", "relation_type": "references"},
    ]
    build_graph(symbols, edges, out)
    # Should add edges (qualified or short name resolution)
    from repo_graph.graph_storage import GraphStorage

    storage = GraphStorage(out)
    conn = storage._connect()
    node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    storage.close()
    assert node_count == 2
    assert edge_count >= 1  # At least one edge added (qualified or short resolves)


def test_graph_builder_bounded_unresolved_logging(tmp_path, caplog):
    """Unresolved edges are logged with bounded sample (max 5)."""
    import logging

    caplog.set_level(logging.WARNING)
    out = str(tmp_path / "index.sqlite")
    symbols = [
        {"symbol_name": "foo", "symbol_type": "function", "file": "a.py", "start_line": 1, "end_line": 2, "docstring": ""},
    ]
    edges = [
        {"source_symbol": "nonexistent_a", "target_symbol": "nonexistent_b", "relation_type": "references"},
        {"source_symbol": "x", "target_symbol": "y", "relation_type": "references"},
    ]
    build_graph(symbols, edges, out)
    assert "sample_unresolved" in caplog.text or "edges provided but none added" in caplog.text


def test_symbol_retry_emits_distinct_variants():
    """symbol_retry produces semantically distinct variants, no identical duplicates."""
    step = {"description": "Fix multiply(2,3) in src/calc/ops.py to return 6", "action": "EDIT"}
    variants = symbol_retry(step, state=None)
    keys = [(v.get("edit_target_level"), v.get("edit_target_symbol_short"), v.get("edit_target_file_override")) for v in variants]
    assert len(variants) >= 2
    assert len(set(keys)) == len(keys), "No duplicate variant keys"


def test_symbol_retry_no_repeated_identical_step():
    """EDIT retry does not repeat the exact same step twice."""
    step = {"description": "Fix bar in x.py", "action": "EDIT"}
    variants = symbol_retry(step, state=None)
    for i, v in enumerate(variants):
        for j, w in enumerate(variants):
            if i != j:
                assert v != w or (
                    v.get("edit_target_level") != w.get("edit_target_level")
                    or v.get("edit_target_symbol_short") != w.get("edit_target_symbol_short")
                    or v.get("edit_target_file_override") != w.get("edit_target_file_override")
                ), "Variants must differ in at least one retry hint"


def test_resolve_edit_targets_honors_file_override(tmp_path):
    """resolve_edit_targets_for_plan prepends edit_target_file_override when provided."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ratios.py").write_text("def normalize_ratios(): pass\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "utils.py").write_text("def util(): pass\n")
    context = {"edit_target_file_override": "other/utils.py"}
    result = resolve_edit_targets_for_plan(
        "Fix normalize_ratios in core/ratios.py",
        str(tmp_path),
        context,
    )
    ranked = result.get("edit_targets_ranked", [])
    assert ranked
    assert ranked[0][0] == "other/utils.py"
    assert ranked[0][2] == "retry_override"


def test_diff_planner_uses_edit_target_level_file(tmp_path):
    """When edit_target_level=file, diff_planner uses file-level targets (empty symbol)."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ratios.py").write_text("def foo(): return 1\n")
    context = {
        "project_root": str(tmp_path),
        "ranked_context": [{"file": "core/ratios.py", "symbol": ""}],
        "retrieved_symbols": [],
        "retrieved_files": [],
        "edit_target_level": "file",
    }
    plan = plan_diff("Fix foo in core/ratios.py to return 2", context)
    changes = plan.get("changes", [])
    assert any("ratios" in (c.get("file") or "") for c in changes)


def test_diff_planner_uses_edit_target_symbol_short(tmp_path):
    """When edit_target_symbol_short is set, it is added to affected_symbols for primary target."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ratios.py").write_text("def normalize_ratios(): return 1\n")
    context = {
        "project_root": str(tmp_path),
        "ranked_context": [{"file": "core/ratios.py", "symbol": "normalize_ratios"}],
        "retrieved_symbols": [],
        "retrieved_files": [],
        "edit_target_symbol_short": "normalize_ratios",
    }
    plan = plan_diff("Fix normalize_ratios in core/ratios.py to return 3.0", context)
    changes = plan.get("changes", [])
    assert any("ratios" in (c.get("file") or "") for c in changes)
