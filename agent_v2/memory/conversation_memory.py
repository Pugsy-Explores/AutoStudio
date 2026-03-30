"""
Conversation memory — protocol + in-memory store (no large code blobs).

Load/save at runtime boundaries only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
import hashlib

# No raw code / snippet keys in stored records
FORBIDDEN_CONTENT_KEYS: frozenset[str] = frozenset({"raw_code", "code_dump", "full_file", "snippet_body"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConversationTurn:
    role: str
    text_summary: str
    ts: str = field(default_factory=_now_iso)


@dataclass
class ConversationState:
    session_id: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    rolling_summary: str = ""
    last_final_answer_summary: str = ""


class ConversationMemoryStore(Protocol):
    def load(self, session_id: str) -> ConversationState: ...

    def append_turn(self, session_id: str, role: str, text_summary: str) -> None: ...

    def set_last_final_answer_summary(self, session_id: str, summary: str, *, max_chars: int = 4000) -> None: ...

    def compact(self, session_id: str, *, max_turns: int = 50) -> None: ...


class InMemoryConversationMemoryStore:
    """Process-local store for tests and single-session CLI."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationState] = {}

    def load(self, session_id: str) -> ConversationState:
        sid = (session_id or "default").strip() or "default"
        if sid not in self._sessions:
            self._sessions[sid] = ConversationState(session_id=sid)
        return self._sessions[sid]

    def append_turn(self, session_id: str, role: str, text_summary: str) -> None:
        st = self.load(session_id)
        t = (text_summary or "").strip()[:8000]
        st.turns.append(ConversationTurn(role=role[:32], text_summary=t))
        self.compact(session_id, max_turns=50)

    def set_last_final_answer_summary(self, session_id: str, summary: str, *, max_chars: int = 4000) -> None:
        st = self.load(session_id)
        s = (summary or "").strip()[:max_chars]
        st.last_final_answer_summary = s

    def compact(self, session_id: str, *, max_turns: int = 50) -> None:
        st = self.load(session_id)
        if len(st.turns) > max_turns:
            st.turns = st.turns[-max_turns:]
        rs = st.rolling_summary
        if len(rs) > 12000:
            h = hashlib.sha256(rs.encode("utf-8")).hexdigest()[:16]
            st.rolling_summary = f"(compact#{h}) " + rs[-8000:]


CONVERSATION_MEMORY_STORE_KEY = "conversation_memory_store"
SESSION_ID_METADATA_KEY = "chat_session_id"


def get_session_id_from_state(state: Any) -> str:
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        sid = md.get(SESSION_ID_METADATA_KEY)
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    return "default"


def get_or_create_in_memory_store(state: Any) -> InMemoryConversationMemoryStore:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        raise TypeError("state.context must be a dict")
    existing = ctx.get(CONVERSATION_MEMORY_STORE_KEY)
    if isinstance(existing, InMemoryConversationMemoryStore):
        return existing
    store = InMemoryConversationMemoryStore()
    ctx[CONVERSATION_MEMORY_STORE_KEY] = store
    return store
