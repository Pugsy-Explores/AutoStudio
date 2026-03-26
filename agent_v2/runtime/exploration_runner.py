"""
Phase 3 — Exploration Runner.

Bounded, read-only intelligence stage that runs BEFORE planning.
Produces FinalExplorationSchema (planner contract) from structured exploration of
the repository, so the planner receives grounded context rather than hallucinating.

Hard constraints (non-negotiable):
  - NO edit, write, patch, run_tests
  - MAX_STEPS = 5 (hard limit: must not exceed 6 per spec)
  - Isolated state — exploration scratch state MUST NOT pollute main AgentLoop state
  - Dispatcher returns ExecutionResult (Phase 2 contract)
  - Always returns FinalExplorationSchema — never raises on empty
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent_v2.schemas.execution import ExecutionResult
from agent_v2.config import (
    ENABLE_EXPLORATION_ENGINE_V2,
    ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS,
    ENABLE_EXPLORATION_SCOPER,
    EXPLORATION_STEPS,
    get_project_root,
)
from agent.models.model_client import call_reasoning_model, call_reasoning_model_messages
from agent.models.model_config import get_model_for_task, get_prompt_model_name_for_task
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_result_adapter import final_from_legacy_phase3_exploration_result
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.exploration_task_names import (
    EXPLORATION_TASK_ANALYZER,
    EXPLORATION_TASK_QUERY_INTENT,
    EXPLORATION_TASK_SCOPER,
    EXPLORATION_TASK_SELECTOR_BATCH,
    EXPLORATION_TASK_SELECTOR_SINGLE,
    EXPLORATION_TASK_V2,
)
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
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
from agent_v2.schemas.final_exploration import FinalExplorationSchema

# ---------------------------------------------------------------------------
# Action constraints (Step 2)
# ---------------------------------------------------------------------------

ALLOWED_ACTIONS: frozenset[str] = frozenset({"search", "open_file", "shell"})
FORBIDDEN_ACTIONS: frozenset[str] = frozenset({"edit", "write", "patch", "run_tests"})

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
        FinalExplorationSchema
           ↓
        Planner

    Invariants:
      - Only allowed actions: search, open_file, shell (read-only)
      - Max steps: 5 (hard cap ≤ 6)
      - Exploration state is fully isolated from the main agent loop
      - Dispatcher.execute() returns ExecutionResult (Phase 2 contract)
      - Returns a valid FinalExplorationSchema even when no steps succeed
    """

    def __init__(
        self,
        action_generator,
        dispatcher,
        *,
        llm_generate_fn=None,
        enable_v2: bool | None = None,
        model_name: str | None = None,
    ):
        """
        Args:
            action_generator: must expose
                next_action_exploration(instruction: str, items: list) -> dict | None
            dispatcher: Phase-2 Dispatcher whose execute(step, state) -> ExecutionResult
            llm_generate_fn: Optional override (e.g. tests). If None, uses ``call_reasoning_model``
                per exploration stage task (``EXPLORATION_QUERY_INTENT``, ``EXPLORATION_SCOPER``, …)
                so ``task_models`` / ``task_params`` in models_config apply per call.
            model_name: When ``llm_generate_fn`` is set, used as registry ``model_name`` for all
                exploration prompts; if None, uses display-name model routing for
                ``EXPLORATION_V2``. When ``llm_generate_fn`` is None, each stage uses
                display-name routing derived from its task model mapping.
        """
        self.action_generator = action_generator
        self.dispatcher = dispatcher
        if enable_v2 is None:
            self._enable_v2 = ENABLE_EXPLORATION_ENGINE_V2
        else:
            self._enable_v2 = enable_v2
        # Per-stage task_name → task_models + task_params; model_name for prompt YAML overrides.
        # If llm_generate_fn is injected (tests), use one LLM and optional model_name for all prompts.
        if llm_generate_fn is not None:
            _mn = (
                model_name
                if model_name is not None
                else get_prompt_model_name_for_task(EXPLORATION_TASK_V2)
            )
            _llm_q = _llm_s = _llm_sel_single = _llm_sel_batch = _llm_a = llm_generate_fn
            _llm_qm = _llm_sel_single_m = _llm_sel_batch_m = _llm_a_m = None
            _mn_q = _mn_s = _mn_sel_single = _mn_sel_batch = _mn_a = _mn
        else:
            _llm_q = lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_QUERY_INTENT)
            _llm_qm = lambda m: call_reasoning_model_messages(
                m, task_name=EXPLORATION_TASK_QUERY_INTENT
            )
            _llm_s = lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_SCOPER)
            _llm_sel_single = lambda p: call_reasoning_model(
                p, task_name=EXPLORATION_TASK_SELECTOR_SINGLE
            )
            _llm_sel_single_m = lambda m: call_reasoning_model_messages(
                m, task_name=EXPLORATION_TASK_SELECTOR_SINGLE
            )
            _llm_sel_batch = lambda p: call_reasoning_model(
                p, task_name=EXPLORATION_TASK_SELECTOR_BATCH
            )
            _llm_sel_batch_m = lambda m: call_reasoning_model_messages(
                m, task_name=EXPLORATION_TASK_SELECTOR_BATCH
            )
            _llm_a = lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_ANALYZER)
            _llm_a_m = lambda m: call_reasoning_model_messages(
                m, task_name=EXPLORATION_TASK_ANALYZER
            )
            _mn_q = get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT)
            _mn_s = get_prompt_model_name_for_task(EXPLORATION_TASK_SCOPER)
            _mn_sel_single = get_prompt_model_name_for_task(EXPLORATION_TASK_SELECTOR_SINGLE)
            _mn_sel_batch = get_prompt_model_name_for_task(EXPLORATION_TASK_SELECTOR_BATCH)
            _mn_a = get_prompt_model_name_for_task(EXPLORATION_TASK_ANALYZER)
        scoper_v2 = None
        if ENABLE_EXPLORATION_SCOPER:
            scoper_v2 = ExplorationScoper(
                llm_generate=_llm_s,
                max_snippet_chars=ExplorationEngineV2.MAX_SNIPPET_CHARS,
                model_name=_mn_s,
            )
        _llm_result_syn = None
        _mn_syn: str | None = None
        if ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS:
            if llm_generate_fn is not None:
                _llm_result_syn = llm_generate_fn
                _mn_syn = (
                    model_name
                    if model_name is not None
                    else get_prompt_model_name_for_task(EXPLORATION_TASK_V2)
                )
            else:
                _llm_result_syn = lambda p: call_reasoning_model(
                    p, task_name=EXPLORATION_TASK_V2
                )
                _mn_syn = get_prompt_model_name_for_task(EXPLORATION_TASK_V2)
        self._engine_v2 = ExplorationEngineV2(
            dispatcher=dispatcher,
            intent_parser=QueryIntentParser(
                llm_generate=_llm_q,
                llm_generate_messages=_llm_qm,
                model_name=_mn_q,
            ),
            selector=CandidateSelector(
                llm_generate_single=_llm_sel_single,
                llm_generate_batch=_llm_sel_batch,
                llm_generate_messages_single=_llm_sel_single_m,
                llm_generate_messages_batch=_llm_sel_batch_m,
                model_name_single=_mn_sel_single,
                model_name_batch=_mn_sel_batch,
            ),
            inspection_reader=InspectionReader(dispatcher=dispatcher),
            analyzer=UnderstandingAnalyzer(
                llm_generate=_llm_a,
                llm_generate_messages=_llm_a_m,
                model_name=_mn_a,
            ),
            graph_expander=GraphExpander(dispatcher=dispatcher),
            scoper=scoper_v2,
            result_synthesis_llm=_llm_result_syn,
            result_synthesis_model_name=_mn_syn,
        )

    # ------------------------------------------------------------------
    # Public entry point (Step 6)
    # ------------------------------------------------------------------

    def run(
        self,
        instruction: str,
        *,
        obs: Any = None,
        langfuse_trace: Any = None,
    ) -> FinalExplorationSchema:
        """
        Run the bounded exploration loop and return the planner-facing FinalExplorationSchema.

        The loop:
          1. Asks action_generator for the next step (exploration context given).
          2. Validates the action is allowed (read-only gate).
          3. Dispatches via the Phase-2 dispatcher → ExecutionResult.
          4. Appends (step, result) to collected items.
          5. Terminates at finish, None step, or MAX_STEPS.

        Exploration state is isolated and never written back to main agent state.
        """
        state = _ExplorationState(instruction=instruction)
        state.context.setdefault(
            "project_root",
            get_project_root(),
        )
        lf = None
        if obs is not None and getattr(obs, "langfuse_trace", None) is not None:
            lf = obs.langfuse_trace
        elif langfuse_trace is not None:
            lf = langfuse_trace
        if self._enable_v2:
            return self._engine_v2.explore(instruction, state=state, obs=obs, langfuse_trace=lf)
        collected: list[tuple[dict, ExecutionResult]] = []

        for _ in range(MAX_STEPS):
            step = self.action_generator.next_action_exploration(
                instruction, collected, langfuse_trace=lf
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

        legacy = self._build_result(instruction, collected)
        return final_from_legacy_phase3_exploration_result(legacy)

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
