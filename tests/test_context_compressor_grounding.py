"""compress_context must not strip EXPLAIN grounding metadata."""

from unittest.mock import patch

from agent.repo_intelligence.context_compressor import compress_context


def test_compress_summary_path_preserves_typed_fields():
    long_snip = "x" * 2000
    row = {
        "file": "a.py",
        "symbol": "foo",
        "snippet": long_snip,
        "implementation_body_present": True,
        "retrieval_result_type": "symbol_body",
        "candidate_kind": "symbol",
        "line": 10,
        "line_range": [10, 20],
    }
    with patch(
        "agent.repo_intelligence.context_compressor.call_small_model",
        return_value="one line summary",
    ):
        out, _ratio = compress_context([row], repo_summary={}, task_goal="", max_tokens=8)
    assert len(out) >= 1
    r0 = out[0]
    assert r0.get("implementation_body_present") is True
    assert r0.get("retrieval_result_type") == "symbol_body"
    assert r0.get("candidate_kind") == "symbol"
    assert r0.get("line") == 10
    assert r0.get("line_range") == [10, 20]
    assert r0.get("compressed") is True


def test_compress_under_budget_returns_copy_with_metadata():
    row = {
        "file": "b.py",
        "snippet": "short",
        "implementation_body_present": True,
        "candidate_kind": "file",
    }
    out, ratio = compress_context([row], max_tokens=10000)
    assert ratio == 1.0
    assert out[0].get("implementation_body_present") is True
    assert out[0].get("candidate_kind") == "file"
