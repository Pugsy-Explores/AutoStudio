from __future__ import annotations

import pytest

from agent_v2.memory.conversation_memory import (
    FileConversationMemoryStore,
    InMemoryConversationMemoryStore,
    get_or_create_conversation_store,
    get_session_id_from_state,
)


def test_in_memory_store_fifo():
    st = InMemoryConversationMemoryStore()
    sid = "s1"
    for i in range(60):
        st.append_turn(sid, "user", f"m{i}")
    assert len(st.load(sid).turns) <= 50


def test_set_last_final_answer_summary_cap():
    st = InMemoryConversationMemoryStore()
    st.set_last_final_answer_summary("s1", "x" * 5000, max_chars=100)
    assert len(st.load("s1").last_final_answer_summary) == 100


def test_file_store_persistence_round_trip(tmp_path):
    d = tmp_path / "sessions"
    a = FileConversationMemoryStore(sessions_dir=d)
    a.append_turn("sess-a", "user", "hello")
    a.append_turn("sess-a", "assistant", "world")
    b = FileConversationMemoryStore(sessions_dir=d)
    st = b.load("sess-a")
    assert [t.role for t in st.turns] == ["user", "assistant"]
    assert st.turns[0].text_summary == "hello"
    assert st.turns[1].text_summary == "world"


def test_safe_session_stem_path_characters_contained(tmp_path):
    d = tmp_path / "sessions"
    s = FileConversationMemoryStore(sessions_dir=d)
    s.append_turn("../../../passwd", "user", "leak")
    files = list(d.glob("*.json"))
    assert len(files) == 1
    assert files[0].resolve().parent == d.resolve()
    assert ".." not in files[0].name


def test_safe_session_stem_all_special_maps_to_default_file(tmp_path):
    d = tmp_path / "sessions"
    s = FileConversationMemoryStore(sessions_dir=d)
    s.append_turn("@@@", "user", "x")
    assert (d / "default.json").is_file()


def test_file_store_multiple_sessions_isolated(tmp_path):
    d = tmp_path / "sessions"
    s = FileConversationMemoryStore(sessions_dir=d)
    s.append_turn("one", "user", "a")
    s.append_turn("two", "user", "b")
    assert (d / "one.json").is_file()
    assert (d / "two.json").is_file()
    assert FileConversationMemoryStore(sessions_dir=d).load("one").turns[0].text_summary == "a"
    assert FileConversationMemoryStore(sessions_dir=d).load("two").turns[0].text_summary == "b"


def test_get_session_id_default_without_metadata():
    class _State:
        metadata = {}
        context = {}

    assert get_session_id_from_state(_State()) == "default"


def test_get_or_create_conversation_store_uses_default_session_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_V2_USE_FILE_CONVERSATION_MEMORY", "1")
    monkeypatch.setenv("AGENT_V2_CONVERSATION_SESSIONS_DIR", str(tmp_path / "sessions"))

    class _State:
        def __init__(self) -> None:
            self.context: dict = {}
            self.metadata: dict = {}

    state = _State()
    sid = get_session_id_from_state(state)
    assert sid == "default"

    store = get_or_create_conversation_store(state)
    store.append_turn(sid, "user", "ping")

    store2 = get_or_create_conversation_store(state)
    assert store2 is store
    assert store2.load("default").turns[0].text_summary == "ping"

    # New process / new AgentState: new store instance, same files
    state2 = _State()
    fresh = get_or_create_conversation_store(state2)
    assert fresh is not store
    assert fresh.load("default").turns[0].text_summary == "ping"


@pytest.fixture(autouse=True)
def _restore_file_conversation_env(monkeypatch):
    """Tests in this module default to in-memory unless they set env (avoid writing CWD)."""
    monkeypatch.setenv("AGENT_V2_USE_FILE_CONVERSATION_MEMORY", "0")
    yield
