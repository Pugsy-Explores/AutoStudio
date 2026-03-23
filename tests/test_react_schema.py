"""Tests for ReAct schema validation and parsing."""

import json

import pytest

from agent.execution.react_schema import ALLOWED_ACTIONS, validate_action
from agent.orchestrator.execution_loop import _react_parse_response


class TestValidateAction:
    def test_search_valid(self):
        valid, err = validate_action("search", {"query": "find foo"})
        assert valid is True
        assert err is None

    def test_search_missing_query(self):
        valid, err = validate_action("search", {})
        assert valid is False
        assert "query" in (err or "")

    def test_search_empty_query(self):
        valid, err = validate_action("search", {"query": ""})
        assert valid is False

    def test_search_extra_field(self):
        valid, err = validate_action("search", {"query": "x", "path": "y"})
        assert valid is False
        assert "Unexpected" in (err or "")

    def test_edit_valid(self):
        valid, err = validate_action("edit", {"instruction": "add null check", "path": "src/foo.py"})
        assert valid is True

    def test_edit_missing_instruction(self):
        valid, err = validate_action("edit", {"path": "x.py"})
        assert valid is False
        assert "instruction" in (err or "")

    def test_edit_missing_path(self):
        valid, err = validate_action("edit", {"instruction": "add null check"})
        assert valid is False
        assert "path" in (err or "")

    def test_run_tests_empty_args(self):
        valid, err = validate_action("run_tests", {})
        assert valid is True

    def test_run_tests_extra_field(self):
        valid, err = validate_action("run_tests", {"timeout": 60})
        assert valid is False

    def test_invalid_action(self):
        valid, err = validate_action("unknown", {})
        assert valid is False
        assert "Invalid action" in (err or "")


class TestReactParseResponse:
    def test_valid_json(self):
        data = {"thought": "need to search", "action": "search", "args": {"query": "foo"}}
        thought, action, args = _react_parse_response(json.dumps(data))
        assert thought == "need to search"
        assert action == "search"
        assert args == {"query": "foo"}

    def test_json_in_markdown(self):
        data = {"thought": "x", "action": "finish", "args": {}}
        raw = f"```json\n{json.dumps(data)}\n```"
        thought, action, args = _react_parse_response(raw)
        assert action == "finish"
        assert args == {}

    def test_invalid_json_returns_none(self):
        thought, action, args = _react_parse_response("not json")
        assert thought is None
        assert action is None
        assert args is None

    def test_missing_action_returns_none(self):
        data = {"thought": "x", "args": {}}
        thought, action, args = _react_parse_response(json.dumps(data))
        assert thought is None
        assert action is None
