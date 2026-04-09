"""Phase 5.4 — state.memory namespace with legacy context mirroring."""
from __future__ import annotations

import pytest

from agent_v2.memory.conversation_memory import (
    CONVERSATION_MEMORY_STORE_KEY,
    FileConversationMemoryStore,
    InMemoryConversationMemoryStore,
    get_or_create_conversation_store,
)
from agent_v2.memory.task_working_memory import (
    TASK_WORKING_MEMORY_CONTEXT_KEY,
    TaskWorkingMemory,
    reset_task_working_memory,
    task_working_memory_from_state,
)
from agent_v2.runtime.session_memory import (
    PLANNER_SESSION_MEMORY_CONTEXT_KEY,
    SessionMemory,
    planner_session_memory_from_state,
)
from agent_v2.state.agent_state import MEMORY_SESSION, MEMORY_WORKING, AgentState


def test_task_working_migrates_from_context_to_memory() -> None:
    st = AgentState(instruction="x")
    tm = TaskWorkingMemory(current_goal="legacy")
    st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] = tm
    out = task_working_memory_from_state(st)
    assert out is tm
    assert st.memory[MEMORY_WORKING] is tm
    assert st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] is tm


def test_task_working_prefers_memory_and_syncs_context() -> None:
    st = AgentState(instruction="x")
    tm_mem = TaskWorkingMemory(current_goal="mem")
    tm_ctx = TaskWorkingMemory(current_goal="ctx")
    st.memory[MEMORY_WORKING] = tm_mem
    st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] = tm_ctx
    out = task_working_memory_from_state(st)
    assert out is tm_mem
    assert st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] is tm_mem


def test_reset_task_working_updates_memory_and_context() -> None:
    st = AgentState(instruction="x")
    st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] = TaskWorkingMemory(current_goal="old")
    task_working_memory_from_state(st)
    reset_task_working_memory(st)
    assert st.memory[MEMORY_WORKING].current_goal == ""
    assert st.context[TASK_WORKING_MEMORY_CONTEXT_KEY] is st.memory[MEMORY_WORKING]


def test_planner_session_migrates_from_context_to_memory() -> None:
    st = AgentState(instruction="x")
    sm = SessionMemory(current_task="legacy")
    st.context[PLANNER_SESSION_MEMORY_CONTEXT_KEY] = sm
    out = planner_session_memory_from_state(st)
    assert out is sm
    assert st.memory[MEMORY_SESSION] is sm


def test_planner_session_prefers_memory_and_syncs_context() -> None:
    st = AgentState(instruction="x")
    sm_mem = SessionMemory(current_task="mem")
    sm_ctx = SessionMemory(current_task="ctx")
    st.memory[MEMORY_SESSION] = sm_mem
    st.context[PLANNER_SESSION_MEMORY_CONTEXT_KEY] = sm_ctx
    out = planner_session_memory_from_state(st)
    assert out is sm_mem
    assert st.context[PLANNER_SESSION_MEMORY_CONTEXT_KEY] is sm_mem


def test_fresh_state_populates_both_slots() -> None:
    st = AgentState(instruction="x")
    tw = task_working_memory_from_state(st)
    ps = planner_session_memory_from_state(st)
    assert isinstance(tw, TaskWorkingMemory)
    assert isinstance(ps, SessionMemory)
    assert st.memory[MEMORY_WORKING] is tw
    assert st.memory[MEMORY_SESSION] is ps


def test_planner_session_dict_in_context_migrates_to_model_and_memory() -> None:
    st = AgentState(instruction="x")
    st.context[PLANNER_SESSION_MEMORY_CONTEXT_KEY] = {"current_task": "from_dict"}
    out = planner_session_memory_from_state(st)
    assert isinstance(out, SessionMemory)
    assert out.current_task == "from_dict"
    assert st.memory[MEMORY_SESSION] is out
    assert st.context[PLANNER_SESSION_MEMORY_CONTEXT_KEY] is out


def test_conversation_store_migrates_from_context_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_V2_USE_FILE_CONVERSATION_MEMORY", "0")
    st = AgentState(instruction="x")
    legacy = InMemoryConversationMemoryStore()
    st.context[CONVERSATION_MEMORY_STORE_KEY] = legacy
    got = get_or_create_conversation_store(st)
    assert got is legacy
    assert st.memory[CONVERSATION_MEMORY_STORE_KEY] is legacy


def test_conversation_store_prefers_memory_and_syncs_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_V2_USE_FILE_CONVERSATION_MEMORY", "1")
    monkeypatch.setenv("AGENT_V2_CONVERSATION_SESSIONS_DIR", str(tmp_path / "sessions"))
    st = AgentState(instruction="x")
    mem_store = FileConversationMemoryStore(sessions_dir=tmp_path / "a")
    ctx_store = FileConversationMemoryStore(sessions_dir=tmp_path / "b")
    st.memory[CONVERSATION_MEMORY_STORE_KEY] = mem_store
    st.context[CONVERSATION_MEMORY_STORE_KEY] = ctx_store
    got = get_or_create_conversation_store(st)
    assert got is mem_store
    assert st.context[CONVERSATION_MEMORY_STORE_KEY] is mem_store
