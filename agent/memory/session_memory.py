"""Session memory: conversation history, recent files, recent symbols per session."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_RECENT_FILES = 20
MAX_RECENT_SYMBOLS = 30
MAX_CONVERSATION_TURNS = 50


@dataclass
class SessionState:
    """In-memory session state. Attached to AgentState during session."""

    conversation_history: list[dict] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    recent_symbols: list[str] = field(default_factory=list)

    def add_turn(
        self,
        instruction: str,
        summary: str = "",
        task_id: str = "",
        files_modified: list[str] | None = None,
        symbols_retrieved: list[str] | None = None,
    ) -> None:
        """Record a conversation turn and update recent files/symbols."""
        self.conversation_history.append({
            "turn": len(self.conversation_history) + 1,
            "instruction": instruction,
            "summary": summary,
            "task_id": task_id,
        })
        if len(self.conversation_history) > MAX_CONVERSATION_TURNS:
            self.conversation_history = self.conversation_history[-MAX_CONVERSATION_TURNS:]

        if files_modified:
            for f in files_modified:
                if f and f not in self.recent_files:
                    self.recent_files.insert(0, f)
            self.recent_files = self.recent_files[:MAX_RECENT_FILES]

        if symbols_retrieved:
            for s in symbols_retrieved:
                if s and s not in self.recent_symbols:
                    self.recent_symbols.insert(0, s)
            self.recent_symbols = self.recent_symbols[:MAX_RECENT_SYMBOLS]

    def to_context_dict(self) -> dict[str, Any]:
        """Return dict suitable for injection into AgentState.context."""
        return {
            "session_conversation_turns": len(self.conversation_history),
            "session_recent_files": self.recent_files[:10],
            "session_recent_symbols": self.recent_symbols[:10],
        }


def extract_symbols_from_context(context: dict) -> list[str]:
    """Extract symbol names from agent context (retrieved_symbols, ranked_context, etc.)."""
    symbols: list[str] = []
    for key in ("retrieved_symbols", "retrieved_references"):
        val = context.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    symbols.append(item)
                elif isinstance(item, dict) and "symbol" in item:
                    symbols.append(str(item["symbol"]))
    ranked = context.get("ranked_context") or []
    for item in ranked:
        if isinstance(item, dict) and "symbol" in item:
            symbols.append(str(item["symbol"]))
    return list(dict.fromkeys(symbols))  # dedupe
