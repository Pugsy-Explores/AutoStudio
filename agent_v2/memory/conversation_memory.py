"""
Conversation memory — protocol + in-memory store (no large code blobs).

Load/save at runtime boundaries only. Phase 5.2: optional JSON file persistence per session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import hashlib
import logging
import re

from pydantic import BaseModel, Field

# No raw code / snippet keys in stored records
FORBIDDEN_CONTENT_KEYS: frozenset[str] = frozenset({"raw_code", "code_dump", "full_file", "snippet_body"})

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationTurn(BaseModel):
    role: str
    text_summary: str
    ts: str = Field(default_factory=_now_iso)


class ConversationState(BaseModel):
    session_id: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)
    rolling_summary: str = ""
    last_final_answer_summary: str = ""


def _normalize_session_id(session_id: str) -> str:
    sid = (session_id or "default").strip() or "default"
    return sid


def _safe_session_file_stem(session_id: str) -> str:
    """
    Filesystem-safe stem: only [a-zA-Z0-9_-]; everything else → '_' (path injection / invalid names).
    Empty-after-strip → 'default'.
    """
    sid = _normalize_session_id(session_id)
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", sid)
    stem = stem.strip("_")
    return stem if stem else "default"


def _apply_compact(st: ConversationState, *, max_turns: int = 50) -> None:
    if len(st.turns) > max_turns:
        st.turns = st.turns[-max_turns:]
    rs = st.rolling_summary
    if len(rs) > 12000:
        h = hashlib.sha256(rs.encode("utf-8")).hexdigest()[:16]
        st.rolling_summary = f"(compact#{h}) " + rs[-8000:]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class ConversationMemoryStore(Protocol):
    def load(self, session_id: str) -> ConversationState: ...

    def get_state(self, session_id: str) -> ConversationState: ...

    def append_turn(self, session_id: str, role: str, text_summary: str) -> None: ...

    def set_last_final_answer_summary(self, session_id: str, summary: str, *, max_chars: int = 4000) -> None: ...

    def compact(self, session_id: str, *, max_turns: int = 50) -> None: ...


class InMemoryConversationMemoryStore:
    """Process-local store for tests and single-session CLI."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationState] = {}

    def load(self, session_id: str) -> ConversationState:
        sid = _normalize_session_id(session_id)
        if sid not in self._sessions:
            self._sessions[sid] = ConversationState(session_id=sid)
        return self._sessions[sid]

    def get_state(self, session_id: str) -> ConversationState:
        return self.load(session_id)

    def append_turn(self, session_id: str, role: str, text_summary: str) -> None:
        st = self.load(session_id)
        t = (text_summary or "").strip()[:8000]
        st.turns.append(ConversationTurn(role=role[:32], text_summary=t))
        _apply_compact(st, max_turns=50)

    def set_last_final_answer_summary(self, session_id: str, summary: str, *, max_chars: int = 4000) -> None:
        st = self.load(session_id)
        s = (summary or "").strip()[:max_chars]
        st.last_final_answer_summary = s

    def compact(self, session_id: str, *, max_turns: int = 50) -> None:
        st = self.load(session_id)
        _apply_compact(st, max_turns=max_turns)


class FileConversationMemoryStore:
    """
    One JSON file per session under ``sessions_dir`` (default ``.agent_memory/sessions``).

    Read-modify-write on each mutating call; safe for a new store instance to see prior writes.
    """

    def __init__(self, sessions_dir: str | Path | None = None) -> None:
        if sessions_dir is None:
            from agent_v2.config import get_conversation_sessions_dir

            sessions_dir = get_conversation_sessions_dir()
        self._sessions_dir = Path(sessions_dir).expanduser().resolve()

    def _path(self, session_id: str) -> Path:
        stem = _safe_session_file_stem(session_id)
        return self._sessions_dir / f"{stem}.json"

    def load(self, session_id: str) -> ConversationState:
        path = self._path(session_id)
        sid = _normalize_session_id(session_id)
        if path.is_file():
            try:
                st = ConversationState.model_validate_json(path.read_text(encoding="utf-8"))
                st.session_id = sid
                return st
            except Exception:
                return ConversationState(session_id=sid)
        return ConversationState(session_id=sid)

    def get_state(self, session_id: str) -> ConversationState:
        return self.load(session_id)

    def _persist(self, session_id: str, state: ConversationState) -> None:
        path = self._path(session_id)
        state.session_id = _normalize_session_id(session_id)
        _atomic_write_text(path, state.model_dump_json())

    def append_turn(self, session_id: str, role: str, text_summary: str) -> None:
        st = self.load(session_id)
        t = (text_summary or "").strip()[:8000]
        st.turns.append(ConversationTurn(role=role[:32], text_summary=t))
        _apply_compact(st, max_turns=50)
        self._persist(session_id, st)

    def set_last_final_answer_summary(self, session_id: str, summary: str, *, max_chars: int = 4000) -> None:
        st = self.load(session_id)
        st.last_final_answer_summary = (summary or "").strip()[:max_chars]
        self._persist(session_id, st)

    def compact(self, session_id: str, *, max_turns: int = 50) -> None:
        st = self.load(session_id)
        _apply_compact(st, max_turns=max_turns)
        self._persist(session_id, st)


CONVERSATION_MEMORY_STORE_KEY = "conversation_memory_store"
SESSION_ID_METADATA_KEY = "chat_session_id"


def get_session_id_from_state(state: Any) -> str:
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        sid = md.get(SESSION_ID_METADATA_KEY)
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    return "default"


def get_or_create_conversation_store(state: Any) -> ConversationMemoryStore:
    from agent_v2.config import get_conversation_sessions_dir, use_file_conversation_memory
    from agent_v2.state.agent_state import ensure_agent_memory_dict

    memory = ensure_agent_memory_dict(state)
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        raise TypeError("state.context must be a dict")

    want_file = use_file_conversation_memory()

    def _compatible(store: Any) -> bool:
        if want_file:
            return isinstance(store, FileConversationMemoryStore)
        return isinstance(store, InMemoryConversationMemoryStore)

    mem_slot = memory.get(CONVERSATION_MEMORY_STORE_KEY)
    if _compatible(mem_slot):
        ctx[CONVERSATION_MEMORY_STORE_KEY] = mem_slot
        return mem_slot

    ctx_existing = ctx.get(CONVERSATION_MEMORY_STORE_KEY)
    if _compatible(ctx_existing):
        memory[CONVERSATION_MEMORY_STORE_KEY] = ctx_existing
        return ctx_existing

    if (mem_slot is not None and not _compatible(mem_slot)) or (
        ctx_existing is not None and not _compatible(ctx_existing)
    ):
        logger.warning(
            "Replacing incompatible conversation store in state.memory/context"
        )

    if want_file:
        store: ConversationMemoryStore = FileConversationMemoryStore(
            sessions_dir=get_conversation_sessions_dir()
        )
    else:
        store = InMemoryConversationMemoryStore()
    memory[CONVERSATION_MEMORY_STORE_KEY] = store
    ctx[CONVERSATION_MEMORY_STORE_KEY] = store
    return store


def get_or_create_in_memory_store(state: Any) -> ConversationMemoryStore:
    """Backward-compatible name; delegates to :func:`get_or_create_conversation_store`."""
    return get_or_create_conversation_store(state)
