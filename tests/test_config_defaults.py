"""
Config defaults regression tests.

Ensures config defaults match previous hardcoded values after consolidation.
Run with: pytest tests/test_config_defaults.py -v
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_relevant_env():
    """Clear env vars that affect config defaults so we test true defaults."""
    to_restore = {}
    keys = [
        "EDIT_PROPOSAL_MAX_CONTENT",
        "EDIT_PROPOSAL_EVIDENCE_MAX",
        "EDIT_PROPOSAL_SYMBOL_BLOCK_MAX",
        "SEMANTIC_FEEDBACK_MAX_SUMMARY",
        "MAX_SEMANTIC_RETRIES",
        "REASON_TRUNCATE_LEN",
        "TRAJECTORY_REASON_MAX",
        "RETRY_QUERY_MAX_LEN",
        "RETRY_SUGGESTION_MAX_LEN",
    ]
    for k in keys:
        if k in os.environ:
            to_restore[k] = os.environ.pop(k)
    yield
    for k, v in to_restore.items():
        os.environ[k] = v


def test_config_defaults_match_previous_values():
    """
    All config defaults equal the values that were previously hardcoded in logic.
    No behavior change after config consolidation.
    """
    # Re-import to pick up env-cleared defaults
    import importlib
    import config.agent_runtime as agent_runtime
    import config.editing_config as editing_config
    importlib.reload(agent_runtime)
    importlib.reload(editing_config)

    # Previous hardcoded values (from edit_proposal_generator, retry_planner, execution_loop, semantic_feedback)
    expected = {
        "EDIT_PROPOSAL_MAX_CONTENT": 8000,
        "EDIT_PROPOSAL_EVIDENCE_MAX": 1500,
        "EDIT_PROPOSAL_SYMBOL_BLOCK_MAX": 500,
        "SEMANTIC_FEEDBACK_MAX_SUMMARY": 500,
        "MAX_SEMANTIC_RETRIES": 2,
        "REASON_TRUNCATE_LEN": 500,
        "TRAJECTORY_REASON_MAX": 300,
        "RETRY_QUERY_MAX_LEN": 500,
        "RETRY_SUGGESTION_MAX_LEN": 200,
    }

    assert editing_config.EDIT_PROPOSAL_MAX_CONTENT == expected["EDIT_PROPOSAL_MAX_CONTENT"]
    assert editing_config.EDIT_PROPOSAL_EVIDENCE_MAX == expected["EDIT_PROPOSAL_EVIDENCE_MAX"]
    assert editing_config.EDIT_PROPOSAL_SYMBOL_BLOCK_MAX == expected["EDIT_PROPOSAL_SYMBOL_BLOCK_MAX"]
    assert editing_config.SEMANTIC_FEEDBACK_MAX_SUMMARY == expected["SEMANTIC_FEEDBACK_MAX_SUMMARY"]

    assert agent_runtime.MAX_SEMANTIC_RETRIES == expected["MAX_SEMANTIC_RETRIES"]
    assert agent_runtime.REASON_TRUNCATE_LEN == expected["REASON_TRUNCATE_LEN"]
    assert agent_runtime.TRAJECTORY_REASON_MAX == expected["TRAJECTORY_REASON_MAX"]
    assert agent_runtime.RETRY_QUERY_MAX_LEN == expected["RETRY_QUERY_MAX_LEN"]
    assert agent_runtime.RETRY_SUGGESTION_MAX_LEN == expected["RETRY_SUGGESTION_MAX_LEN"]
