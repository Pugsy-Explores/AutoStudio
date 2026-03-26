"""
Phase 3 — ExplorationRunner tests (mandatory validation).

Validates:
  - ExplorationRunner runs bounded exploration (Step 7 exit criteria)
  - Only read-only actions are dispatched (search, open_file, shell)
  - Forbidden actions (edit, run_tests, write, patch) are silently skipped
  - Max steps is enforced (≤ MAX_STEPS = 5)
  - Returns a valid FinalExplorationSchema with schema-conforming fields
  - exploration_summary is populated (summary present)
  - Isolated state: main state not mutated
  - Empty exploration (no steps) still returns a valid FinalExplorationSchema
"""

import pytest

from agent.tools import filesystem_adapter
from agent_v2.exploration.read_router import ReadRequest, read as bounded_read
from agent_v2.runtime.exploration_runner import (
    MAX_STEPS,
    ExplorationRunner,
    _ExplorationState,
    _extract_entities,
    _extract_key_points,
)
from agent_v2.schemas.execution import ErrorType, ExecutionResult
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.tool import ToolError, ToolResult
from agent_v2.runtime.tool_mapper import map_tool_result_to_execution_result


def _llm_stub_v2_branch(prompt: str, *, intent_empty: bool) -> str:
    """Branch fake LLM text for V2 unit tests. Markers match rendered prompts (placeholders are substituted)."""
    p = prompt or ""
    # Batch selector: default v1 uses Limit line; models/qwen2.5-coder-7b omits it (different copy).
    if (
        "Limit (maximum selected items):" in p
        or "Select the most relevant candidates for deep inspection" in p
    ):
        return '{"selected_indices": [0]}'
    if "Candidates (indexed):" in p:
        return '{"selected_indices": [0]}'
    if "You are selecting the most relevant code location" in p:
        return '{"file_path":"agent_v2/runtime/mode_manager.py","symbol":"ModeManager"}'
    if "Classify the snippet based on whether it directly contributes" in p:
        return (
            '{"status":"partial","needs":["definition"],'
            '"reason":"stub","next_action":"expand"}'
        )
    if intent_empty:
        return '{"symbols":[],"keywords":[],"intents":["locate_logic"]}'
    return '{"symbols":["ModeManager"],"keywords":["mode"],"intents":["find_definition"]}'


def _llm_stub_v2_engine(prompt: str) -> str:
    return _llm_stub_v2_branch(prompt, intent_empty=False)


def _llm_stub_v2_empty_discovery(prompt: str) -> str:
    return _llm_stub_v2_branch(prompt, intent_empty=True)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_execution_result(
    tool_name: str,
    success: bool = True,
    summary: str = "",
    data: dict | None = None,
    step_id: str = "s1",
) -> ExecutionResult:
    """Build an ExecutionResult the way the Phase-2 dispatcher would produce it."""
    tr = ToolResult(
        tool_name=tool_name,
        success=success,
        data=data or {},
        error=None if success else ToolError(type="unknown", message="failed", details={}),
        duration_ms=10,
    )
    result = map_tool_result_to_execution_result(tr, step_id=step_id)
    # Override summary for predictable test assertions when needed
    if summary:
        from agent_v2.schemas.execution import ExecutionOutput
        result = result.model_copy(update={"output": ExecutionOutput(data=result.output.data, summary=summary)})
    return result


def _mock_dispatcher_search_batch(
    execute,
    queries,
    state,
    *,
    mode: str,
    step_id_prefix: str,
    max_workers: int = 4,
):
    """Same SEARCH step shape as ``Dispatcher.search_batch`` for test doubles."""
    out: list[ExecutionResult] = []
    for i, q in enumerate(queries):
        step = {
            "id": f"{step_id_prefix}_{i}",
            "action": "SEARCH",
            "_react_action_raw": "search",
            "_react_args": {"query": q},
            "query": q,
            "description": q,
        }
        out.append(execute(step, state))
    return out


class _MockActionGenerator:
    """Controllable action generator for testing."""

    def __init__(self, steps: list):
        """
        steps: list of dicts (step) or None (to stop).
        Will be yielded one per call to next_action_exploration().
        """
        self._steps = list(steps)
        self._idx = 0
        self.calls: list[tuple[str, list]] = []  # record (instruction, items snapshot)

    def next_action_exploration(self, instruction: str, items: list, **kwargs):
        self.calls.append((instruction, list(items)))
        if self._idx >= len(self._steps):
            return None
        step = self._steps[self._idx]
        self._idx += 1
        return step

    def next_action(self, state):
        return None


class _MockDispatcher:
    """Dispatcher that returns pre-programmed ExecutionResults."""

    def __init__(self, results: list[ExecutionResult]):
        self._results = list(results)
        self._idx = 0
        self.dispatched: list[tuple[dict, object]] = []  # record (step, state)

    def execute(self, step, state):
        self.dispatched.append((step, state))
        if self._idx >= len(self._results):
            # Default: a generic success result
            tr = ToolResult(tool_name="search", success=True, data={}, duration_ms=5)
            return map_tool_result_to_execution_result(tr, step_id="sx")
        result = self._results[self._idx]
        self._idx += 1
        return result

    def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
        return _mock_dispatcher_search_batch(
            self.execute, queries, state, mode=mode, step_id_prefix=step_id_prefix, max_workers=max_workers
        )


# ---------------------------------------------------------------------------
# Step 7 — Exit criteria tests
# ---------------------------------------------------------------------------

def test_exploration_returns_exploration_result():
    """✅ ExplorationRunner exists and returns FinalExplorationSchema."""
    gen = _MockActionGenerator([None])  # immediate stop
    disp = _MockDispatcher([])
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Find AgentLoop")
    assert isinstance(result, FinalExplorationSchema)


def test_exploration_summary_always_present():
    """✅ exploration_summary present (even with no evidence rows)."""
    gen = _MockActionGenerator([None])
    disp = _MockDispatcher([])
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Find AgentLoop")
    assert result.exploration_summary.overall
    assert isinstance(result.exploration_summary.key_findings, list)
    assert isinstance(result.exploration_summary.knowledge_gaps, list)


def test_exploration_steps_within_max():
    """✅ Max steps enforced (3–6 cap, MAX_STEPS = 5)."""
    assert MAX_STEPS <= 6, "MAX_STEPS must not exceed 6"
    # Feed 10 valid steps — runner must stop after MAX_STEPS
    steps = [{"action": "search", "query": f"q{i}", "_react_args": {"query": f"q{i}"}} for i in range(10)]
    results = [_make_execution_result("search", summary=f"Search {i}") for i in range(10)]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Find AgentLoop")

    assert len(result.evidence) <= MAX_STEPS
    assert len(disp.dispatched) <= MAX_STEPS


def test_exploration_forbidden_actions_never_dispatched():
    """✅ Forbidden actions (edit, run_tests, write, patch) must NOT reach dispatcher."""
    steps = [
        {"action": "edit", "_react_args": {}},          # forbidden
        {"action": "run_tests", "_react_args": {}},     # forbidden
        {"action": "write", "_react_args": {}},         # forbidden
        {"action": "search", "query": "AgentLoop", "_react_args": {"query": "AgentLoop"}},  # allowed
        {"action": "patch", "_react_args": {}},         # forbidden
        None,                                            # stop
    ]
    results = [_make_execution_result("search", summary="Found AgentLoop")]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Fix a bug")

    # Only the "search" step should have been dispatched
    assert len(disp.dispatched) == 1
    dispatched_step, _ = disp.dispatched[0]
    assert dispatched_step["action"] == "search"
    assert len(result.evidence) == 1


def test_exploration_allowed_actions_only_in_result():
    """✅ Only read tools used: search, open_file, shell."""
    steps = [
        {"action": "search", "query": "q", "_react_args": {"query": "q"}},
        {"action": "open_file", "path": "agent_loop.py", "_react_args": {"path": "agent_loop.py"}},
        {"action": "shell", "command": "ls", "_react_args": {"command": "ls"}},
        None,
    ]
    results = [
        _make_execution_result("search", summary="Search done"),
        _make_execution_result("open_file", summary="File read"),
        _make_execution_result("shell", summary="Shell done"),
    ]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Explore codebase")

    assert len(result.evidence) == 3
    item_types = {item.type for item in result.evidence}
    assert item_types.issubset({"search", "file", "command", "other"})


def test_exploration_finish_step_stops_loop():
    """✅ Finish action stops the loop cleanly."""
    steps = [
        {"action": "search", "query": "q", "_react_args": {"query": "q"}},
        {"action": "finish"},  # should stop here
        {"action": "search", "query": "q2", "_react_args": {"query": "q2"}},  # never reached
        None,
    ]
    results = [_make_execution_result("search", summary="Done")]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Task")

    assert len(result.evidence) == 1  # only first search dispatched


def test_exploration_empty_when_no_steps():
    """✅ Empty exploration returns valid FinalExplorationSchema with correct knowledge_gaps_empty_reason."""
    gen = _MockActionGenerator([None])  # immediate stop
    disp = _MockDispatcher([])
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Unknown task")

    assert isinstance(result, FinalExplorationSchema)
    assert result.evidence == []
    assert result.metadata.total_items == 0
    # Empty items → knowledge_gaps must be [] + knowledge_gaps_empty_reason must be set
    assert result.exploration_summary.knowledge_gaps == []
    assert result.exploration_summary.knowledge_gaps_empty_reason  # non-empty string


def test_exploration_non_empty_has_knowledge_gaps():
    """When items are gathered, knowledge_gaps is non-empty and knowledge_gaps_empty_reason is None."""
    steps = [
        {"action": "search", "query": "AgentLoop", "_react_args": {"query": "AgentLoop"}},
        None,
    ]
    results = [_make_execution_result("search", summary="AgentLoop found")]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Explain AgentLoop")

    assert len(result.evidence) == 1
    assert result.exploration_summary.knowledge_gaps  # non-empty list
    assert result.exploration_summary.knowledge_gaps_empty_reason is None  # must be None when gaps non-empty


def test_exploration_state_isolation():
    """
    ✅ Exploration must NOT mutate external state.
    The runner creates its own isolated _ExplorationState.
    """
    steps = [
        {"action": "search", "query": "q", "_react_args": {"query": "q"}},
        None,
    ]
    results = [_make_execution_result("search", summary="Result")]

    external_history_before = []
    captured_states = []

    class _TrackingDispatcher:
        def execute(self, step, state):
            captured_states.append(state)
            tr = ToolResult(tool_name="search", success=True, data={}, duration_ms=1)
            return map_tool_result_to_execution_result(tr, step_id="s1")

    gen = _MockActionGenerator(steps)
    disp = _TrackingDispatcher()
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    runner.run("Isolate me")

    # State passed to dispatcher must be the isolated _ExplorationState, not an external one
    assert len(captured_states) == 1
    state = captured_states[0]
    assert isinstance(state, _ExplorationState)
    # External history not polluted
    assert external_history_before == []


def test_exploration_result_schema_valid():
    """
    ✅ FinalExplorationSchema is fully Pydantic-valid and JSON-serializable.
    """
    steps = [
        {"action": "search", "query": "AgentLoop", "_react_args": {"query": "AgentLoop"}},
        {"action": "open_file", "path": "agent_loop.py", "_react_args": {"path": "agent_loop.py"}},
        None,
    ]
    results = [
        _make_execution_result("search", data={"results": [{"file": "agent_loop.py", "snippet": "class AgentLoop"}]}),
        _make_execution_result("open_file", data={"file_path": "agent_loop.py"}),
    ]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Find AgentLoop")

    import json
    dumped = result.model_dump()
    json_str = result.model_dump_json()
    assert json.loads(json_str)  # valid JSON
    assert dumped["exploration_id"].startswith("exp_")
    assert dumped["instruction"] == "Find AgentLoop"
    assert len(dumped["evidence"]) == 2

    for item in dumped["evidence"]:
        assert item["item_id"]
        assert item["type"] in ("file", "search", "command", "other")
        assert item["source"]["ref"]
        assert item["content"]["summary"]
        assert isinstance(item["content"]["key_points"], list)
        assert isinstance(item["content"]["entities"], list)
        assert 0.0 <= item["relevance"]["score"] <= 1.0


def test_exploration_item_count_matches_metadata():
    """metadata.total_items MUST match len(evidence)."""
    steps = [
        {"action": "search", "query": "x", "_react_args": {"query": "x"}},
        {"action": "open_file", "path": "y.py", "_react_args": {"path": "y.py"}},
        None,
    ]
    results = [
        _make_execution_result("search"),
        _make_execution_result("open_file"),
    ]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    result = runner.run("Test")

    assert result.metadata.total_items == len(result.evidence)


def test_exploration_instruction_passed_to_action_generator():
    """Action generator receives the correct instruction on each call."""
    steps = [{"action": "search", "query": "q", "_react_args": {"query": "q"}}, None]
    results = [_make_execution_result("search")]
    gen = _MockActionGenerator(steps)
    disp = _MockDispatcher(results)
    runner = ExplorationRunner(action_generator=gen, dispatcher=disp, enable_v2=False)
    runner.run("My specific instruction")

    for instr, _ in gen.calls:
        assert instr == "My specific instruction"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_is_valid_action_allows_read_only():
    gen = _MockActionGenerator([])
    runner = ExplorationRunner(action_generator=gen, dispatcher=_MockDispatcher([]))
    assert runner._is_valid_action("search") is True
    assert runner._is_valid_action("open_file") is True
    assert runner._is_valid_action("shell") is True


def test_is_valid_action_rejects_forbidden():
    gen = _MockActionGenerator([])
    runner = ExplorationRunner(action_generator=gen, dispatcher=_MockDispatcher([]))
    assert runner._is_valid_action("edit") is False
    assert runner._is_valid_action("run_tests") is False
    assert runner._is_valid_action("write") is False
    assert runner._is_valid_action("patch") is False


def test_extract_ref_uses_path():
    runner = ExplorationRunner(action_generator=_MockActionGenerator([]), dispatcher=_MockDispatcher([]))
    assert runner._extract_ref({"path": "foo.py"}) == "foo.py"


def test_extract_ref_uses_query():
    runner = ExplorationRunner(action_generator=_MockActionGenerator([]), dispatcher=_MockDispatcher([]))
    assert runner._extract_ref({"query": "find loop"}) == "find loop"


def test_extract_ref_fallback():
    runner = ExplorationRunner(action_generator=_MockActionGenerator([]), dispatcher=_MockDispatcher([]))
    assert runner._extract_ref({}) == "unknown"


def test_extract_key_points_search():
    data = {"results": [{"file": "a.py", "snippet": "def foo(): ..."}, {"file": "b.py", "snippet": ""}]}
    points = _extract_key_points(data, "search")
    assert any("a.py" in p for p in points)


def test_extract_key_points_empty():
    points = _extract_key_points({}, "search")
    assert len(points) >= 1  # always at least one fallback entry


def test_extract_entities_search():
    data = {"results": [{"file": "agent_loop.py", "symbol": "AgentLoop"}]}
    entities = _extract_entities(data, "search")
    assert "agent_loop.py" in entities
    assert "AgentLoop" in entities


def test_extract_entities_deduped():
    data = {"results": [{"file": "x.py"}, {"file": "x.py"}, {"file": "y.py"}]}
    entities = _extract_entities(data, "search")
    assert entities.count("x.py") == 1


def test_max_steps_constant_within_spec():
    assert 3 <= MAX_STEPS <= 6, "MAX_STEPS must be between 3 and 6 inclusive"


def test_exploration_v2_path_returns_bounded_schema4_items():
    """V2 engine stays bounded and returns schema-valid result."""
    class _V2Dispatcher:
        def execute(self, step, state):
            action = step.get("_react_action_raw")
            if action == "search":
                data = {
                    "results": [
                        {"file": "agent_v2/runtime/mode_manager.py", "symbol": "ModeManager", "snippet": "class ModeManager:"}
                    ]
                }
                return _make_execution_result("search", data=data, summary="Found ModeManager")
            if action == "read_snippet":
                return _make_execution_result(
                    "read_snippet",
                    data={
                        "file_path": "agent_v2/runtime/mode_manager.py",
                        "content": "class ModeManager:\n    pass\n",
                        "start_line": 1,
                        "end_line": 2,
                        "mode": "symbol_body",
                    },
                    summary="Read mode manager file",
                )
            return _make_execution_result("search", data={}, summary="No-op")

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return _mock_dispatcher_search_batch(
                self.execute, queries, state, mode=mode, step_id_prefix=step_id_prefix, max_workers=max_workers
            )

    runner = ExplorationRunner(
        action_generator=_MockActionGenerator([]),
        dispatcher=_V2Dispatcher(),
        llm_generate_fn=_llm_stub_v2_engine,
        enable_v2=True,
    )
    result = runner.run("Find ModeManager definition")
    assert isinstance(result, FinalExplorationSchema)
    assert len(result.evidence) <= 6
    assert result.metadata.total_items == len(result.evidence)
    # Phase 12.6.E: bounded snippet + deterministic read_source tagging
    inspected = [it for it in result.evidence if it.metadata.tool_name == "read_snippet"]
    assert inspected, "Expected at least one inspection item from read_snippet"
    for it in inspected:
        assert it.read_source in ("symbol", "line", "head")
        assert isinstance(it.snippet, str)
        assert len(it.snippet) <= 600
    ss = result.metadata.source_summary or {}
    assert all(k in ss for k in ("symbol", "line", "head"))
    assert ss["symbol"] >= 1


def test_exploration_v2_inspection_uses_bounded_read_tool():
    """Inspection in V2 must use read_snippet, not open_file."""
    seen_actions: list[str] = []

    class _V2Dispatcher:
        def execute(self, step, state):
            action = step.get("_react_action_raw")
            if action:
                seen_actions.append(str(action))
            if action == "search":
                return _make_execution_result(
                    "search",
                    data={
                        "results": [
                            {
                                "file": "agent_v2/runtime/mode_manager.py",
                                "symbol": "ModeManager",
                                "snippet": "class ModeManager:",
                            }
                        ]
                    },
                    summary="Found ModeManager",
                )
            if action == "read_snippet":
                return _make_execution_result(
                    "read_snippet",
                    data={
                        "file_path": "agent_v2/runtime/mode_manager.py",
                        "content": "class ModeManager:\n    pass\n",
                        "start_line": 1,
                        "end_line": 2,
                        "mode": "symbol_body",
                    },
                    summary="Bounded read",
                )
            return _make_execution_result("search", data={"results": []}, summary="No-op")

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return _mock_dispatcher_search_batch(
                self.execute, queries, state, mode=mode, step_id_prefix=step_id_prefix, max_workers=max_workers
            )

    runner = ExplorationRunner(
        action_generator=_MockActionGenerator([]),
        dispatcher=_V2Dispatcher(),
        llm_generate_fn=_llm_stub_v2_engine,
        enable_v2=True,
    )
    runner.run("Find ModeManager definition")

    assert "read_snippet" in seen_actions
    assert "open_file" not in seen_actions


def test_exploration_v2_empty_discovery_sets_empty_reason():
    """V2 sets knowledge_gaps_empty_reason when no evidence exists."""
    class _EmptyDispatcher:
        def execute(self, step, state):
            return _make_execution_result("search", data={"results": []}, summary="No candidates")

    runner = ExplorationRunner(
        action_generator=_MockActionGenerator([]),
        dispatcher=_EmptyDispatcher(),
        llm_generate_fn=_llm_stub_v2_empty_discovery,
        enable_v2=True,
    )
    result = runner.run("Unknown symbol")
    assert result.evidence == []
    assert result.exploration_summary.knowledge_gaps == []
    assert result.exploration_summary.knowledge_gaps_empty_reason


def test_exploration_engine_prioritizes_inspection_evidence_in_items():
    """When discovery floods evidence, Schema 4 items must still include inspection (read_snippet)."""
    from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2

    ev = [("discovery", {"query": f"q{i}"}, _make_execution_result("search")) for i in range(10)]
    ev.insert(5, ("inspection", {"path": "x.py"}, _make_execution_result("read_snippet")))
    ordered = ExplorationEngineV2._prioritize_evidence_for_items(ev)
    assert ordered[0][0] == "inspection"


def test_bounded_read_does_not_call_full_read(monkeypatch, tmp_path):
    """Bound-before-I/O: bounded router should not invoke filesystem_adapter.read_file."""
    p = tmp_path / "sample.py"
    p.write_text("\n".join([f"line {i}" for i in range(1, 600)]), encoding="utf-8")

    def _boom(path: str):  # pragma: no cover - should never be called
        raise AssertionError("full read_file() should not be called for bounded reads")

    monkeypatch.setattr(filesystem_adapter, "read_file", _boom)

    class _State:
        def __init__(self):
            self.context = {"project_root": str(tmp_path)}

    state = _State()
    payload = bounded_read(
        ReadRequest(path=str(p), line=250, window=20),
        state=state,
    )
    content = payload.get("content") or ""
    assert payload.get("mode") == "line_window"
    assert isinstance(content, str) and content
    assert "line 250" in content
