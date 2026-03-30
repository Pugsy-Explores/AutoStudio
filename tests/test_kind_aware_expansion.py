"""Kind-aware expansion (Phase 4)."""

from __future__ import annotations

from unittest.mock import patch

from agent.retrieval.retrieval_expander import expansion_action_for_result, expand_search_results


def test_expansion_action_legacy_symbol_present():
    r = {"file": "a.py", "symbol": "Foo", "snippet": "x"}
    assert expansion_action_for_result(r, kind_aware=False) == "read_symbol_body"
    assert expansion_action_for_result(r, kind_aware=True) == "read_symbol_body"


def test_expansion_action_legacy_no_symbol():
    r = {"file": "a.py", "symbol": "", "snippet": "x"}
    assert expansion_action_for_result(r, kind_aware=False) == "read_file"
    assert expansion_action_for_result(r, kind_aware=True) == "read_file"


def test_kind_aware_file_overrides_symbol():
    r = {"file": "a.py", "symbol": "Foo", "candidate_kind": "file"}
    assert expansion_action_for_result(r, kind_aware=True) == "read_file_header"


def test_kind_aware_symbol():
    r = {"file": "a.py", "symbol": "Foo", "candidate_kind": "symbol"}
    assert expansion_action_for_result(r, kind_aware=True) == "read_symbol_body"


def test_kind_aware_region():
    r = {"file": "a.py", "candidate_kind": "region", "line_range": [10, 20]}
    assert expansion_action_for_result(r, kind_aware=True) == "read_region_bounded"


def test_kind_aware_localization_with_symbol():
    r = {"file": "a.py", "symbol": "X", "candidate_kind": "localization"}
    assert expansion_action_for_result(r, kind_aware=True) == "read_symbol_body"


def test_kind_aware_localization_no_symbol():
    r = {"file": "a.py", "symbol": "", "candidate_kind": "localization"}
    assert expansion_action_for_result(r, kind_aware=True) == "read_file"


def test_expand_search_results_preserves_line_range_and_kind():
    with patch("agent.retrieval.retrieval_expander.ENABLE_KIND_AWARE_EXPANSION", True):
        out = expand_search_results(
            [
                {
                    "file": "a.py",
                    "path": "",
                    "symbol": "",
                    "snippet": "x",
                    "candidate_kind": "region",
                    "line_range": [5, 9],
                    "line": 5,
                }
            ]
        )
    assert len(out) == 1
    assert out[0]["action"] == "read_region_bounded"
    assert out[0]["line_range"] == [5, 9]
    assert out[0]["candidate_kind"] == "region"


def test_expand_search_results_kind_aware_off_matches_legacy():
    with patch("agent.retrieval.retrieval_expander.ENABLE_KIND_AWARE_EXPANSION", False):
        out = expand_search_results(
            [{"file": "a.py", "symbol": "Z", "candidate_kind": "file", "snippet": "x"}]
        )
    assert out[0]["action"] == "read_symbol_body"
