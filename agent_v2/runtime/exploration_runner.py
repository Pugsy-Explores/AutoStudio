"""
Phase 3 — Exploration Runner.

Bounded, read-only intelligence stage that runs BEFORE planning.
Produces ExplorationResult (SCHEMAS.md Schema 4) from structured exploration of
the repository, so the planner receives grounded context rather than hallucinating.

Hard constraints (non-negotiable):
  - NO edit, write, patch, run_tests
  - MAX_STEPS = 5 (hard limit: must not exceed 6 per spec)
  - Isolated state — exploration scratch state MUST NOT pollute main AgentLoop state
  - Dispatcher returns ExecutionResult (Phase 2 contract)
  - Always returns ExplorationResult — never raises on empty
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent_v2.schemas.execution import ExecutionResult
from agent_v2.config import EXPLORATION_STEPS
from agent_v2.schemas.exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
)

# ---------------------------------------------------------------------------
# Action constraints (Step 2)
# ---------------------------------------------------------------------------

ALLOWED_ACTIONS: frozenset[str] = frozenset({"search", "open_file", "shell"})
FORBIDDEN_ACTIONS: frozenset[str] = frozenset({"edit", "run_tests", "write", "patch"})

_LOG = logging.getLogger(__name__)

MAX_STEPS: int = EXPLORATION_STEPS  # config / env (architecture freeze §3.1)

_ACTION_TO_ITEM_TYPE: dict[str, str] = {
    "open_file": "file",
    "search": "search",
    "shell": "command",
}


# ---------------------------------------------------------------------------
# Isolated exploration scratch state (Step 3)
# ---------------------------------------------------------------------------

@dataclass
class _ExplorationState:
    """
    Lightweight scratch state for the exploration loop.

    MUST NOT be shared with or mutated by the main AgentLoop state.
    Holds the minimum fields the dispatcher + execute_fn need:
      - context: for primitive injection (shell/editor/browser)
      - history: formatted observations for the action generator
      - metadata: counters used by some dispatch paths
    """
    instruction: str
    history: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    step_results: list = field(default_factory=list)
    retry_count: int = 0
    last_error: Optional[str] = None
    debug_last_action: Optional[str] = None


# ---------------------------------------------------------------------------
# ExplorationRunner
# ---------------------------------------------------------------------------

class ExplorationRunner:
    """
    Bounded, read-only exploration stage that precedes planning.

    Architecture position:
        User Instruction
           ↓
        ExplorationRunner   ← this class
           ↓
        ExplorationResult
           ↓
        Planner

    Invariants:
      - Only allowed actions: search, open_file, shell (read-only)
      - Max steps: 5 (hard cap ≤ 6)
      - Exploration state is fully isolated from the main agent loop
      - Dispatcher.execute() returns ExecutionResult (Phase 2 contract)
      - Returns a valid ExplorationResult even when no steps succeed
    """

    def __init__(self, action_generator, dispatcher):
        """
        Args:
            action_generator: must expose
                next_action_exploration(instruction: str, items: list) -> dict | None
            dispatcher: Phase-2 Dispatcher whose execute(step, state) -> ExecutionResult
        """
        self.action_generator = action_generator
        self.dispatcher = dispatcher

    # ------------------------------------------------------------------
    # Public entry point (Step 6)
    # ------------------------------------------------------------------

    def run(self, instruction: str, *, langfuse_trace: Any = None) -> ExplorationResult:
        """
        Run the bounded exploration loop and return a structured ExplorationResult.

        The loop:
          1. Asks action_generator for the next step (exploration context given).
          2. Validates the action is allowed (read-only gate).
          3. Dispatches via the Phase-2 dispatcher → ExecutionResult.
          4. Appends (step, result) to collected items.
          5. Terminates at finish, None step, or MAX_STEPS.

        Exploration state is isolated and never written back to main agent state.
        """
        state = _ExplorationState(instruction=instruction)
        collected: list[tuple[dict, ExecutionResult]] = []

        for _ in range(MAX_STEPS):
            step = self.action_generator.next_action_exploration(
                instruction, collected, langfuse_trace=langfuse_trace
            )

            if not step:
                break

            action = (step.get("action") or "").lower()

            if action == "finish":
                break

            if not self._is_valid_action(action):
                _LOG.warning(
                    "ExplorationRunner skipped disallowed action %r (read-only phase)",
                    action,
                )
                continue

            result = self.dispatcher.execute(step, state)
            collected.append((step, result))

        return self._build_result(instruction, collected)

    # ------------------------------------------------------------------
    # Action constraint gate (Step 2)
    # ------------------------------------------------------------------

    def _is_valid_action(self, action: str) -> bool:
        """Return True only for read-only actions allowed during exploration."""
        return action in ALLOWED_ACTIONS

    # ------------------------------------------------------------------
    # Reference extractor (Step 4 helper)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_ref(step: dict) -> str:
        return (
            step.get("path")
            or step.get("query")
            or step.get("command")
            or step.get("description")
            or "unknown"
        )

    # ------------------------------------------------------------------
    # ExplorationItem builder (Step 4)
    # ------------------------------------------------------------------

    def _build_item(
        self,
        step: dict,
        result: ExecutionResult,
        idx: int,
    ) -> ExplorationItem:
        """Map one (step, ExecutionResult) pair to an ExplorationItem."""
        action = (step.get("_react_action_raw") or step.get("action") or "").lower()
        item_type = _ACTION_TO_ITEM_TYPE.get(action, "other")
        ref = self._extract_ref(step)

        # Content — always from ExecutionResult.output (Phase 2 schema)
        summary = result.output.summary if result.output else f"{action} executed"
        data = result.output.data if result.output else {}

        key_points = _extract_key_points(data, action)
        entities = _extract_entities(data, action)

        # Relevance — success yields higher score; failure lower
        score = 0.8 if result.success else 0.3
        reason = (
            f"{'Successful' if result.success else 'Failed'} {action} on {ref!r}"
        )

        return ExplorationItem(
            item_id=f"item_{idx}",
            type=item_type,
            source=ExplorationSource(ref=ref, location=None),
            content=ExplorationContent(
                summary=summary,
                key_points=key_points,
                entities=entities,
            ),
            relevance=ExplorationRelevance(score=score, reason=reason),
            metadata=ExplorationItemMetadata(
                timestamp=result.metadata.timestamp,
                tool_name=result.metadata.tool_name,
            ),
        )

    # ------------------------------------------------------------------
    # ExplorationResult builder (Step 5)
    # ------------------------------------------------------------------

    def _build_result(
        self,
        instruction: str,
        items: list[tuple[dict, ExecutionResult]],
    ) -> ExplorationResult:
        """Build the final ExplorationResult from collected (step, result) pairs."""
        exploration_items = [
            self._build_item(step, result, idx)
            for idx, (step, result) in enumerate(items)
        ]

        key_findings = [
            item.content.summary
            for item in exploration_items
            if item.content.summary
        ]

        overall = (
            f"Exploration completed for: {instruction!r}. "
            f"Gathered {len(exploration_items)} source(s)."
            if exploration_items
            else f"Exploration produced no results for: {instruction!r}."
        )

        # Schema 4 Rule 5: knowledge_gaps / knowledge_gaps_empty_reason are mutually exclusive.
        # Phase 3 uses a static gap noting that LLM-driven gap analysis is deferred to Phase 4.
        if exploration_items:
            knowledge_gaps: list[str] = [
                "Deeper semantic analysis pending (LLM summarizer wired in Phase 4)"
            ]
            knowledge_gaps_empty_reason: str | None = None
        else:
            knowledge_gaps = []
            knowledge_gaps_empty_reason = (
                "No items were gathered; gaps cannot be assessed from empty exploration."
            )

        return ExplorationResult(
            exploration_id=f"exp_{uuid.uuid4().hex[:8]}",
            instruction=instruction,
            items=exploration_items,
            summary=ExplorationSummary(
                overall=overall,
                key_findings=key_findings,
                knowledge_gaps=knowledge_gaps,
                knowledge_gaps_empty_reason=knowledge_gaps_empty_reason,
            ),
            metadata=ExplorationResultMetadata(
                total_items=len(exploration_items),
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
        )


# ---------------------------------------------------------------------------
# Module-level helpers — structured data extraction from ExecutionResult.output.data
# ---------------------------------------------------------------------------

def _extract_key_points(data: dict, action: str) -> list[str]:
    """Extract human-readable key points from tool output data dict."""
    if not data:
        return [f"{action} output captured"]

    points: list[str] = []

    if action == "search":
        results = data.get("results") or data.get("candidates") or []
        if isinstance(results, list):
            for r in results[:5]:
                if isinstance(r, dict):
                    file_ref = r.get("file", "")
                    snippet = (r.get("snippet") or r.get("content") or "")[:120].strip()
                    if file_ref:
                        points.append(f"{file_ref}: {snippet}" if snippet else file_ref)

    elif action == "open_file":
        path = data.get("file_path") or data.get("path", "")
        if path:
            points.append(f"Read file: {path}")
        content = data.get("file_content") or data.get("output") or data.get("content")
        if content and isinstance(content, str):
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()][:3]
            points.extend(lines)

    elif action == "shell":
        stdout = data.get("stdout") or data.get("output") or ""
        if stdout and isinstance(stdout, str):
            lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()][:3]
            points.extend(lines)

    return points or [f"{action} output captured"]


def _extract_entities(data: dict, action: str) -> list[str]:
    """Extract named entities (file paths, symbols) from tool output data dict."""
    entities: list[str] = []

    if action == "search":
        results = data.get("results") or data.get("candidates") or []
        if isinstance(results, list):
            for r in results[:8]:
                if isinstance(r, dict):
                    file_ref = r.get("file")
                    if file_ref:
                        entities.append(str(file_ref))
                    sym = r.get("symbol")
                    if sym:
                        entities.append(str(sym))

    elif action == "open_file":
        path = data.get("file_path") or data.get("path")
        if path:
            entities.append(str(path))

    elif action == "shell":
        # Best-effort: any file-like tokens in stdout
        stdout = data.get("stdout") or data.get("output") or ""
        if stdout and isinstance(stdout, str):
            for token in stdout.split():
                if "/" in token and len(token) < 120:
                    entities.append(token)
                    if len(entities) >= 5:
                        break

    # Deduplicate while preserving insertion order
    return list(dict.fromkeys(entities))
