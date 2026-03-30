from __future__ import annotations

from agent_v2.memory.conversation_memory import InMemoryConversationMemoryStore


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
