"""Unit tests for Phase 3 stabilization: react context windowing and task mode."""

from agent_v2.runtime.react_context import (
    MAX_OBS_CHARS,
    MAX_STEPS_IN_CONTEXT,
    classify_react_task_mode,
    format_react_history_for_prompt,
    json_action_list_for_mode,
    normalize_path_for_dedup,
    truncate_observation,
)


def test_classify_read_only():
    assert classify_react_task_mode("Explain how AgentLoop handles retries") == "read_only"
    assert classify_react_task_mode("How does Dispatcher connect to tools?") == "read_only"
    assert classify_react_task_mode("Find where X is defined") == "read_only"


def test_classify_code_change():
    assert classify_react_task_mode("Fix the bug in foo.py") == "code_change"
    assert classify_react_task_mode("Add a test for the parser") == "code_change"


def test_history_window_and_truncation():
    long_obs = "x" * 5000
    hist = [
        {"thought": "a", "action": "search", "args": {"query": "q"}, "observation": "old"},
        {"thought": "b", "action": "open_file", "args": {"path": "/p"}, "observation": long_obs},
        {"thought": "c", "action": "finish", "args": {}, "observation": "done"},
    ]
    out = format_react_history_for_prompt(hist)
    assert "old" not in out
    assert "Thought: c" in out
    assert "Thought: b" in out
    assert len(out) < len(long_obs)
    assert len(out.split("Observation:")[-1]) <= MAX_OBS_CHARS + 50


def test_max_obs_chars_constant():
    assert MAX_OBS_CHARS == 1200
    assert MAX_STEPS_IN_CONTEXT == 2
    assert len(truncate_observation("a" * 2000)) == MAX_OBS_CHARS


def test_json_action_list_modes():
    assert "finish" in json_action_list_for_mode("read_only")
    assert "edit" not in json_action_list_for_mode("read_only")
    assert "edit" in json_action_list_for_mode("code_change")


def test_normalize_path_for_dedup_stable():
    a = normalize_path_for_dedup("/tmp/foo/bar")
    b = normalize_path_for_dedup("/tmp/foo/bar")
    assert a == b
