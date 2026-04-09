"""
Session-scoped planner memory: intent anchor, streaks, compressed step history.

No persistence, no I/O. Explore-cap override logic lives in PlannerV2 only — not here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class IntentAnchor(BaseModel):
    goal: str = ""
    target: str = ""
    entity: str = ""

    @field_validator("goal", "target", "entity", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        return s[:500]


class CompressedStep(BaseModel):
    t: str = ""
    tool: str = ""
    summary: str = ""

    @field_validator("t", "tool", mode="before")
    @classmethod
    def _strip_short(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()[:32]

    @field_validator("summary", mode="before")
    @classmethod
    def _strip_summary(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()[:120]


_VAGUE_USER_RE = re.compile(
    r"^(do\s+it|fix\s+it|go\s+ahead|yes|ok|please|thanks|continue|proceed)\.?$",
    re.IGNORECASE,
)

# Hard cap: FIFO trim when exceeded (deterministic prompt size).
RECENT_STEPS_MAX: int = 5


def is_vague_user_text(text: str) -> bool:
    """True for follow-ups like \"do it\" or empty/too-short strings."""
    t = (text or "").strip()
    if not t:
        return True
    if _VAGUE_USER_RE.match(t):
        return True
    if len(t) <= 2:
        return True
    return False


def derive_intent_anchor_from_user_text(text: str) -> IntentAnchor:
    """
    Deterministic heuristic from user text only (no LLM, not exploration).
    """
    raw = (text or "").strip()
    if not raw:
        return IntentAnchor()
    lower = raw.lower()

    goal = "task"
    if any(w in lower for w in ("fix", "bug", "error", "broken")):
        goal = "fix bug"
    elif any(lower.startswith(w) for w in ("explain", "what", "how", "why", "where", "which")):
        goal = "explain"
    elif any(w in lower for w in ("add ", "create ", "implement ", "build ")):
        goal = "implement"
    elif any(w in lower for w in ("refactor", "rename", "cleanup", "clean up")):
        goal = "refactor"
    elif any(w in lower for w in ("test", "tests", "pytest")):
        goal = "test"

    target = raw
    if len(raw) > 200:
        target = raw[:200] + "…"

    entity = ""
    m = re.search(
        r"\b([A-Za-z_][A-Za-z0-9_]*Error|KeyError|TypeError|ValueError|AttributeError)\b",
        raw,
    )
    if m:
        entity = m.group(1)
    else:
        m2 = re.search(r"\b[A-Z]{2,}[A-Z0-9_]+\b", raw)
        if m2 and len(m2.group(0)) <= 40:
            entity = m2.group(0)

    return IntentAnchor(goal=goal[:200], target=target[:500], entity=entity[:200])


class SessionMemory(BaseModel):
    session_id: str = ""
    current_task: str = ""
    intent_anchor: IntentAnchor = Field(default_factory=IntentAnchor)
    last_user_instruction: str = ""
    last_decision: str = ""
    last_tool: str = ""
    active_file: Optional[str] = None
    active_symbols: list[str] = Field(default_factory=list)
    recent_steps: list[CompressedStep] = Field(default_factory=list)
    explore_streak: int = 0
    # Cumulative planner EXPLORE decisions this task (reset on substantive new user turn).
    explore_decisions_total: int = 0
    # Inner-loop steps from the most recent exploration engine run (for cost visibility).
    last_exploration_engine_steps: int = 0
    # Last PlanValidationError message from planner JSON (cleared on success or new user turn).
    last_planner_validation_error: str = ""
    updated_at: str = ""

    @field_validator(
        "current_task",
        "last_user_instruction",
        "last_decision",
        "last_tool",
        "last_planner_validation_error",
        mode="before",
    )
    @classmethod
    def _cap_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()[:2000]

    @field_validator("active_file", mode="before")
    @classmethod
    def _active_file(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s[:500] if s else None

    @field_validator("active_symbols", mode="before")
    @classmethod
    def _cap_symbols(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v[:5]:
            s = str(x).strip()[:80]
            if s:
                out.append(s)
        return out

    def record_user_turn(self, instruction: str) -> None:
        text = (instruction or "").strip()
        self.last_user_instruction = text[:2000]
        if not text:
            self._touch()
            return
        if is_vague_user_text(text) or (len(text) < 4 and not self.intent_anchor.goal):
            self._touch()
            return
        anchor = derive_intent_anchor_from_user_text(text)
        self.intent_anchor = anchor
        self.current_task = (anchor.target or text)[:200]
        self.explore_decisions_total = 0
        self.last_exploration_engine_steps = 0
        self.last_planner_validation_error = ""
        self._touch()

    def record_planner_output(self, *, decision: str, tool: str) -> None:
        d = (decision or "").strip().lower()
        self.last_decision = d
        self.last_tool = (tool or "").strip()[:64]
        if d == "explore":
            self.explore_streak = int(self.explore_streak) + 1
            self.explore_decisions_total = int(self.explore_decisions_total) + 1
        else:
            self.explore_streak = 0
        self._touch()

    def record_last_exploration_engine_steps(self, steps: int) -> None:
        """Set inner-loop step count from the latest FinalExplorationSchema.metadata.engine_loop_steps."""
        try:
            n = int(steps)
        except (TypeError, ValueError):
            n = 0
        self.last_exploration_engine_steps = max(0, n)
        self._touch()

    def record_executor_event(
        self,
        *,
        decision_kind: str,
        tool: str,
        summary: str,
        active_file: Optional[str] = None,
        active_symbols: Optional[list[str]] = None,
    ) -> None:
        step = CompressedStep(
            t=(decision_kind or "")[:32],
            tool=(tool or "")[:32],
            summary=(summary or "")[:120],
        )
        self.recent_steps.append(step)
        self.recent_steps = self.recent_steps[-RECENT_STEPS_MAX:]
        if active_file is not None:
            af = str(active_file).strip()[:500]
            self.active_file = af if af else None
        if active_symbols is not None:
            self.active_symbols = [str(x).strip()[:80] for x in active_symbols[:5] if str(x).strip()]
        self._touch()

    def to_prompt_block(self) -> str:
        """Pruned JSON for planner prompt (~2.5k char cap)."""
        payload: dict[str, Any] = {
            "current_task": _truncate(self.current_task, 400),
            "intent_anchor": self.intent_anchor.model_dump(),
            "last_user_instruction": _truncate(self.last_user_instruction, 400),
            "last_decision": self.last_decision,
            "last_tool": self.last_tool,
            "active_file": self.active_file,
            "active_symbols": self.active_symbols[:5],
            "recent_steps": [s.model_dump() for s in self.recent_steps[-RECENT_STEPS_MAX:]],
            "explore_streak": self.explore_streak,
            "explore_decisions_total": self.explore_decisions_total,
            "last_exploration_engine_steps": self.last_exploration_engine_steps,
        }
        if self.session_id:
            payload["session_id"] = _truncate(self.session_id, 80)
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw) > 2500:
            payload["last_user_instruction"] = _truncate(str(payload.get("last_user_instruction", "")), 120)
            payload["current_task"] = _truncate(str(payload.get("current_task", "")), 120)
            raw = json.dumps(payload, ensure_ascii=False)
        return raw

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


PLANNER_SESSION_MEMORY_CONTEXT_KEY = "planner_session_memory"


def _sync_planner_session_to_context(state: Any, sm: SessionMemory) -> None:
    ctx = getattr(state, "context", None)
    if isinstance(ctx, dict):
        ctx[PLANNER_SESSION_MEMORY_CONTEXT_KEY] = sm


def planner_session_memory_from_state(state: Any) -> SessionMemory:
    """
    Return the run's SessionMemory, preferring ``state.memory[MEMORY_SESSION]`` and
    mirroring to ``context['planner_session_memory']`` for backward compatibility.
    """
    from agent_v2.state.agent_state import MEMORY_SESSION, ensure_agent_memory_dict

    memory = ensure_agent_memory_dict(state)
    slot = memory.get(MEMORY_SESSION)
    if isinstance(slot, SessionMemory):
        _sync_planner_session_to_context(state, slot)
        return slot
    if isinstance(slot, dict):
        sm = SessionMemory.model_validate(slot)
        memory[MEMORY_SESSION] = sm
        _sync_planner_session_to_context(state, sm)
        return sm

    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        try:
            state.context = {}
            ctx = state.context
        except Exception:
            sm = SessionMemory()
            memory[MEMORY_SESSION] = sm
            return sm

    key = PLANNER_SESSION_MEMORY_CONTEXT_KEY
    existing = ctx.get(key)
    if isinstance(existing, SessionMemory):
        memory[MEMORY_SESSION] = existing
        return existing
    if isinstance(existing, dict):
        sm = SessionMemory.model_validate(existing)
        ctx[key] = sm
        memory[MEMORY_SESSION] = sm
        return sm

    sm = SessionMemory()
    ctx[key] = sm
    memory[MEMORY_SESSION] = sm
    return sm
