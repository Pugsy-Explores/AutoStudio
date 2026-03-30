"""Execution policy engine: retry with tool-specific policies and mutation strategies."""

import logging
from enum import Enum
from typing import Any, Callable

from planner.planner_utils import ALLOWED_ACTIONS

try:
    from config.agent_runtime import (
        ENABLE_MINIMAL_EDIT_PIPELINE,
        ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE,
        REACT_MODE,
    )
except ImportError:
    ENABLE_MINIMAL_EDIT_PIPELINE = False
    ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE = False
    REACT_MODE = False

from agent.execution.mutation_strategies import (
    get_initial_search_variants,
    retry_same,
    symbol_retry,
)
from agent.memory.state import AgentState
from agent.retrieval.query_rewriter import SearchAttempt
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
from agent.retrieval.retrieval_expander import normalize_file_path

logger = logging.getLogger(__name__)

_ALLOWED_ACTIONS_SET = set(ALLOWED_ACTIONS)

# Stage 44: max LLM rewrite queries per SEARCH attempt (attempts 2+); bounds hybrid fanout per attempt.
_MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT = 5


def _normalize_rewrite_query_list(raw: list[Any], max_total: int) -> list[str]:
    """
    Strip, drop empty, exact dedupe (first occurrence wins), hard cap. Non-str entries skipped.
    Used for rewriter output only; attempt-1 deterministic variants do not use this.
    """
    if max_total < 1:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_total:
            break
    return out


class InvalidStepError(Exception):
    """Raised when a step fails pre-dispatch schema validation."""


def validate_step_input(step: dict) -> None:
    """
    Validate step schema before dispatch. Raises InvalidStepError if invalid.
    Checks: step is dict, action in allowed set, required fields per action type.
    """
    if not isinstance(step, dict):
        raise InvalidStepError("step must be a dict")
    action = (step.get("action") or "EXPLAIN").upper()
    if action not in _ALLOWED_ACTIONS_SET:
        raise InvalidStepError(f"action must be one of {ALLOWED_ACTIONS}, got {action!r}")
    # SEARCH, EDIT, EXPLAIN need description (query/instruction); INFRA can have empty description
    if action in ("SEARCH", "EDIT", "EXPLAIN"):
        desc = step.get("description") or step.get("query") or ""
        if not isinstance(desc, str):
            raise InvalidStepError(f"{action} step requires description (str), got {type(desc).__name__}")
    # Optional: reject obviously malformed steps (e.g. description too long for safety)
    desc = step.get("description") or step.get("query") or ""
    if isinstance(desc, str) and len(desc) > 50_000:
        raise InvalidStepError("description/query exceeds max length (50000 chars)")


def _is_valid_search_result(results: list | None, raw: dict[str, Any] | None = None) -> bool:
    """True if the first result has a file path; snippet may be empty for graph/BM25 hits."""
    # Stage 43 / 45: directory-listing fallbacks are not semantic code retrieval; policy must retry/rewrite.
    if raw and isinstance(raw, dict) and raw.get("retrieval_fallback") in ("file_search", "list_dir"):
        return False
    if not results:
        return False
    r = results[0]
    if not r.get("file"):
        return False
    snip = r.get("snippet")
    if snip in ("", "[]", None):
        f = (r.get("file") or "").lower()
        if f.endswith((".py", ".pyi")):
            return True
        return False
    return True


def search_result_quality(raw: dict[str, Any] | None) -> str:
    """
    Soft signal for SEARCH success: does not change validity of _is_valid_search_result.
    - strong: typed symbol-body signals or non-trivial snippet text on at least one hit
    - weak: policy-valid but file-centric / empty-snippet style hits (e.g. .py path only)
    """
    if not raw or not isinstance(raw, dict):
        return "weak"
    results = raw.get("results") or []
    if not results:
        return "weak"
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("implementation_body_present") is True:
            return "strong"
        if r.get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY:
            return "strong"
        snip = (r.get("snippet") or "").strip()
        if snip and snip not in ("(no snippet)", "[]"):
            return "strong"
    return "weak"


POLICIES = {
    "SEARCH": {
        "max_attempts": 5,
        "mutation": "query_variants",
        "retry_on": ["empty_results"],
    },
    "EDIT": {
        "max_attempts": 2,
        "mutation": "symbol_retry",
        "retry_on": ["symbol_not_found"],
    },
    "INFRA": {
        "max_attempts": 2,
        "mutation": "retry_same",
        "retry_on": ["non_zero_exit"],
    },
    "EXPLAIN": {"max_attempts": 1},
}


class ResultClassification(str, Enum):
    """Classification of step result for recovery policy dispatch."""

    SUCCESS = "SUCCESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    FATAL_FAILURE = "FATAL_FAILURE"


# Explicit dispatch: failure type -> recovery action (used by agent_loop for retry/replan decisions)
FAILURE_RECOVERY_DISPATCH = {
    "empty_results": "rewrite_query_retry",  # retrieval empty -> rewrite query -> retry
    "invalid_step": "replanner",  # planner hallucination -> trigger replanner
    "patch_rejected": "retry_edit",  # patch validator rejects -> retry edit
    "tool_error": "fallback_tool",  # tool error -> fallback tool
}


# Indicators that a failure is context-related (missing/weak retrieval) rather than
# a hard unrecoverable error.  When these appear in an "exhausted retries" error the
# failure should be RETRYABLE so the agent_loop can replan with better context rather
# than giving up.
_CONTEXT_RELATED_INDICATORS = (
    "weak grounding",
    "missing context",
    "symbol not found",
    "symbol_not_found",
    "empty",
    "low-content",
    "insufficient",
    "no context",
    "patch_anchor_not_found",
    "weakly_grounded",
    "no_changes_planned",
    "empty_patch",
)

# Failure reason codes that indicate the failure is context-related
_CONTEXT_RELATED_REASON_CODES = frozenset({
    "patch_anchor_not_found",
    "weakly_grounded_patch",
    "empty_patch",
    "no_changes",
})


def _is_context_related_failure(error: str, output: Any) -> bool:
    """Return True when the failure appears to be caused by missing/weak context."""
    for indicator in _CONTEXT_RELATED_INDICATORS:
        if indicator in error:
            return True
    if isinstance(output, dict):
        frc = str(output.get("failure_reason_code") or "").lower()
        if frc in _CONTEXT_RELATED_REASON_CODES:
            return True
    return False


def classify_result(action: str, result: dict[str, Any] | None) -> ResultClassification:
    """
    Classify step result for recovery policy. Every step result must be classified.
    RETRYABLE_FAILURE: agent_loop may replan/retry. FATAL_FAILURE: stop without replan.

    Context-aware: failures caused by missing/weak context are RETRYABLE even after
    retries are exhausted, so the agent can replan with better retrieval.
    """
    if result is None or not isinstance(result, dict):
        return ResultClassification.FATAL_FAILURE
    if result.get("success") is True:
        return ResultClassification.SUCCESS
    # Minimal / ultra-minimal pipeline: never FATAL for EDIT (isolation mode)
    if (ENABLE_MINIMAL_EDIT_PIPELINE or ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE) and action.upper() == "EDIT":
        return ResultClassification.RETRYABLE_FAILURE

    error = (result.get("error") or "").lower()
    output = result.get("output") or {}

    # Exhausted retries — context-aware: if failure is due to missing context,
    # classify as RETRYABLE so agent_loop can replan, not give up.
    if "exhausted" in error or "after retries" in error:
        if _is_context_related_failure(error, output):
            return ResultClassification.RETRYABLE_FAILURE
        return ResultClassification.FATAL_FAILURE
    if isinstance(output, dict) and output.get("attempt_history"):
        # Policy engine returned attempt_history; check if we exhausted
        policy = POLICIES.get(action.upper(), {})
        max_attempts = policy.get("max_attempts", 1)
        history = output.get("attempt_history", [])
        if len(history) >= max_attempts:
            if _is_context_related_failure(error, output):
                return ResultClassification.RETRYABLE_FAILURE
            return ResultClassification.FATAL_FAILURE

    # Empty results (retrieval) -> RETRYABLE (query rewrite in policy engine)
    if "empty" in error or "empty results" in error:
        return ResultClassification.RETRYABLE_FAILURE

    # Patch/edit failures -> RETRYABLE (symbol_retry in policy engine)
    if "patch" in error or "edit" in error or "symbol_not_found" in error:
        return ResultClassification.RETRYABLE_FAILURE

    # Infra/tool non-zero exit -> RETRYABLE (retry_same in policy engine)
    if "infra" in error or "returncode" in error:
        return ResultClassification.RETRYABLE_FAILURE

    # Validation failures (invalid step) -> RETRYABLE (replanner in agent_loop)
    if "validation" in error or "invalid" in error:
        return ResultClassification.RETRYABLE_FAILURE

    # Task 8: Tool failure, timeout -> RETRYABLE (retry + fallback, do not terminate)
    if "timeout" in error or "tool" in error or "fallback" in error:
        return ResultClassification.RETRYABLE_FAILURE

    # Context-related failure codes in output -> RETRYABLE
    if _is_context_related_failure(error, output):
        return ResultClassification.RETRYABLE_FAILURE

    # Unknown/unhandled -> FATAL to avoid infinite retry loops
    return ResultClassification.FATAL_FAILURE


def _with_classification(result: dict[str, Any], action: str) -> dict[str, Any]:
    """Inject classification into result dict for recovery policy dispatch."""
    out = dict(result)
    out["classification"] = classify_result(action, result).value
    return out


def _is_failure(action: str, retry_on: list[str], result: dict[str, Any] | None) -> bool:
    """Map retry_on to concrete checks. Returns True if result is a failure."""
    if result is None or not isinstance(result, dict):
        return True
    if "empty_results" in retry_on:
        results = result.get("results") if isinstance(result.get("results"), list) else None
        return not _is_valid_search_result(results, result)
    if "symbol_not_found" in retry_on:
        return bool(result.get("error")) or result.get("success") is False
    if "non_zero_exit" in retry_on:
        # INFRA returns { success, output, error }; returncode is in output
        return (result.get("output") or {}).get("returncode", -1) != 0
    return False


# Max snippet length stored in search_memory for EXPLAIN context (avoid huge prompts)
_SEARCH_MEMORY_SNIPPET_MAX = 500


def _search_result_summary(raw: dict | None) -> str:
    """Short summary of search result for rewrite context (e.g. '0 results' or '2 results: a.py, b.py')."""
    if raw is None or not isinstance(raw, dict):
        return "0 results"
    results = raw.get("results")
    if not results or not isinstance(results, list):
        return "0 results"
    n = len(results)
    if n == 0:
        return "0 results"
    files = [r.get("file") or r.get("path") or "" for r in results[:3] if r]
    files = [f for f in files if f]
    if not files:
        return f"{n} result(s)"
    return f"{n} result(s): " + ", ".join(files)


def _build_search_memory(query: str, raw: dict) -> dict:
    """Structured search context for EXPLAIN: query + results (file, snippet truncated, optional typed fields)."""
    results = raw.get("results") or []
    rows: list[dict] = []
    for r in results:
        if not r:
            continue
        row: dict = {
            "file": normalize_file_path(r.get("file") or r.get("path") or ""),
            "snippet": (r.get("snippet") or "")[: _SEARCH_MEMORY_SNIPPET_MAX],
        }
        if r.get("candidate_kind"):
            row["candidate_kind"] = str(r["candidate_kind"])
        if r.get("retrieval_result_type"):
            row["retrieval_result_type"] = r["retrieval_result_type"]
        if "implementation_body_present" in r:
            row["implementation_body_present"] = r["implementation_body_present"]
        if r.get("line") is not None:
            try:
                row["line"] = int(r["line"])
            except (TypeError, ValueError):
                row["line"] = r["line"]
        if "line_range" in r and r["line_range"] is not None:
            row["line_range"] = r["line_range"]
        rows.append(row)
    return {"query": query, "results": rows}


def _append_tool_memory(state: AgentState, entry: dict) -> None:
    """Append one tool call to context.tool_memories (list of tool call records)."""
    state.context.setdefault("tool_memories", []).append(entry)


class ExecutionPolicyEngine:
    """
    Retry-capable execution: run tool via injected fns, apply policy, mutate and retry on failure.
    Tool functions are injected so the engine does not depend on concrete adapters.
    """

    def __init__(
        self,
        search_fn: Callable[[str, AgentState], dict],
        edit_fn: Callable[[dict, AgentState], dict],
        infra_fn: Callable[[dict, AgentState], dict],
        *,
        rewrite_query_fn: Callable[[str, str, list[SearchAttempt], "AgentState | None"], str] | None = None,
        max_total_attempts: int = 10,
    ):
        self._search_fn = search_fn
        self._edit_fn = edit_fn
        self._infra_fn = infra_fn
        self._rewrite_query_fn = rewrite_query_fn
        self._max_total_attempts = max_total_attempts

    def execute_with_policy(self, step: dict, state: AgentState) -> dict:
        """
        Execute step with policy: retry with mutation until success or exhausted.
        Returns dict with success, output, error (same contract as dispatch).
        On exhausted retries: success=False, output={ attempt_history } only (no results/returncode).
        """
        action = (step.get("action") or "EXPLAIN").upper()
        print(f"[workflow] policy {action}")
        policy = POLICIES.get(action, {"max_attempts": 1})
        max_attempts = min(
            policy.get("max_attempts", 1),
            self._max_total_attempts,
        )
        retry_on = policy.get("retry_on") or []
        mutation = policy.get("mutation")

        # Actions that skip policy (single attempt, no retry)
        if action == "EXPLAIN" or max_attempts < 1:
            return self._run_once(step, state, action)
        # Minimal / ultra-minimal pipeline: single attempt, no retry for EDIT
        if (ENABLE_MINIMAL_EDIT_PIPELINE or ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE) and action == "EDIT":
            return self._run_once(step, state, action)

        attempt_history: list[dict[str, Any]] = []

        if action == "SEARCH":
            return self._execute_search(step, state, max_attempts, retry_on, attempt_history)
        if action == "EDIT":
            return self._execute_edit(step, state, max_attempts, retry_on, mutation, attempt_history)
        if action == "INFRA":
            return self._execute_infra(step, state, max_attempts, retry_on, mutation, attempt_history)

        return self._run_once(step, state, action)

    def _run_once(self, step: dict, state: AgentState, action: str) -> dict:
        """Single attempt; used for EXPLAIN or unknown. Caller should route EXPLAIN outside engine."""
        try:
            if action == "SEARCH":
                retrieval_input = (step.get("query") or step.get("description") or "").strip()
                user_req = getattr(state, "instruction", "") or ""
                if self._rewrite_query_fn is not None:
                    q_ret = self._rewrite_query_fn(retrieval_input, user_req, [], state)
                    q_list = [q_ret] if isinstance(q_ret, str) else (q_ret or [])
                else:
                    q_list = [retrieval_input]
                q_list = [x for x in q_list if isinstance(x, str) and (x or "").strip()]
                if not q_list:
                    q_list = [retrieval_input or ""]
                raw = None
                for q in q_list:
                    q = (q or "").strip() or retrieval_input
                    if not q:
                        continue
                    print(f"[workflow] SEARCH (single) query={q!r}")
                    raw = self._search_fn(q, state)
                    if isinstance(raw, dict) and not _is_failure("SEARCH", ["empty_results"], raw):
                        raw = dict(raw)
                        sq = search_result_quality(raw)
                        raw["search_quality"] = sq
                        state.context["search_quality"] = sq
                        return _with_classification({"success": True, "output": raw}, "SEARCH")
                return _with_classification(
                    {"success": False, "output": raw if isinstance(raw, dict) else {"attempt_history": []}, "error": "empty results"},
                    "SEARCH",
                )
            if action == "EDIT":
                raw = self._edit_fn(step, state)
                r = raw if isinstance(raw, dict) else {"success": False, "output": {}, "error": "edit failed"}
                return _with_classification(r, "EDIT")
            if action == "INFRA":
                raw = self._infra_fn(step, state)
                r = raw if isinstance(raw, dict) else {"success": False, "output": {}, "error": "infra failed"}
                return _with_classification(r, "INFRA")
        except Exception as e:
            return _with_classification({"success": False, "output": {}, "error": str(e)}, action)
        return _with_classification({"success": False, "output": {}, "error": "unknown action"}, action)

    def _execute_search(
        self,
        step: dict,
        state: AgentState,
        max_attempts: int,
        retry_on: list[str],
        attempt_history: list[dict[str, Any]],
    ) -> dict:
        retrieval_input = (step.get("query") or step.get("description") or "").strip()
        user_request = getattr(state, "instruction", "") or ""

        print(f"[workflow] SEARCH step retrieval_input: {retrieval_input[:80]}{'...' if len(retrieval_input) > 80 else ''}")
        print(f"[workflow] SEARCH max_attempts={max_attempts}")

        for attempt_num in range(1, max_attempts + 1):
            # Stage 42: attempt 1 only — bounded deterministic variants, no LLM rewriter
            # REACT_MODE: disable query_variants; use model output as-is (execution decides)
            if attempt_num == 1 and not REACT_MODE:
                initial = get_initial_search_variants(retrieval_input, max_total=3)
                initial = [q for q in (initial or []) if isinstance(q, str) and (q or "").strip()]
                use_initial = bool(initial)
            else:
                use_initial = False

            if use_initial:
                queries_to_try = initial
            elif REACT_MODE:
                # REACT_MODE: no query rewriting; use model output as-is
                queries_to_try = [retrieval_input or ""]
            elif self._rewrite_query_fn is not None:
                attempt_slice: list[SearchAttempt] = [
                    {
                        "tool": h.get("tool", ""),
                        "query": h.get("query", ""),
                        "result_count": h.get("result_count", 0),
                        "result_summary": h.get("result_summary", ""),
                        "error": h.get("error", ""),
                    }
                    for h in attempt_history
                ]
                try:
                    rewrite_ret = self._rewrite_query_fn(retrieval_input, user_request, attempt_slice, state)
                    queries_to_try = [rewrite_ret] if isinstance(rewrite_ret, str) else (rewrite_ret or [])
                except Exception as e:
                    logger.warning("[policy] Rewriter failed, using retrieval_input: %s", e)
                    queries_to_try = [retrieval_input or ""]
            else:
                queries_to_try = [retrieval_input or ""]
            if use_initial:
                queries_to_try = [q for q in queries_to_try if isinstance(q, str) and (q or "").strip()]
            else:
                queries_to_try = _normalize_rewrite_query_list(queries_to_try, _MAX_REWRITE_QUERIES_PER_SEARCH_ATTEMPT)
            if not queries_to_try:
                queries_to_try = [retrieval_input or ""]

            success_in_attempt = False
            for q_idx, query in enumerate(queries_to_try):
                query = (query or "").strip() or retrieval_input
                if not query:
                    continue
                print(f"[workflow] SEARCH attempt {attempt_num}/{max_attempts} query={query!r}" + (f" (variant {q_idx + 1}/{len(queries_to_try)})" if len(queries_to_try) > 1 else ""))
                logger.info(
                    "[policy] SEARCH attempt %s: %s",
                    attempt_num,
                    (query[:80] + "..." if len(query) > 80 else query),
                )
                try:
                    raw = self._search_fn(query, state)
                except Exception as e:
                    attempt_history.append({
                        "tool": state.context.get("chosen_tool", ""),
                        "query": query,
                        "result_count": 0,
                        "result_summary": "",
                        "error": str(e),
                    })
                    print(f"[workflow] SEARCH attempt {attempt_num} error: {e}")
                    logger.warning("[policy] SEARCH attempt %s failed: %s", attempt_num, e)
                    continue

                if raw is None or not isinstance(raw, dict):
                    raw = {"results": [], "query": query}

                result_count = len(raw.get("results") or [])
                result_summary = _search_result_summary(raw)
                attempt_history.append({
                    "tool": state.context.get("chosen_tool", ""),
                    "query": query,
                    "result_count": result_count,
                    "result_summary": result_summary,
                })

                print(f"[workflow] SEARCH attempt {attempt_num} result: {result_summary}")
                if not _is_failure("SEARCH", retry_on, raw):
                    if isinstance(raw, dict):
                        raw = dict(raw)
                        raw["attempt_history"] = attempt_history
                    sq = search_result_quality(raw)
                    raw["search_quality"] = sq
                    state.context["search_quality"] = sq
                    state.context["search_query_rewritten"] = query
                    state.context["search_results"] = raw
                    state.context["files"] = [
                        normalize_file_path(r.get("file") or "")
                        for r in (raw.get("results") or [])
                        if r and (r.get("file") or r.get("path"))
                    ]
                    state.context["snippets"] = [r.get("snippet", "") for r in (raw.get("results") or [])]
                    # Structured memory for EXPLAIN and tool_memories
                    state.context["search_memory"] = _build_search_memory(query, raw)
                    _append_tool_memory(
                        state,
                        {
                            "tool": "search_code",
                            "query": query,
                            "result_count": result_count,
                            "files": state.context["files"],
                            "snippets_preview": [((r.get("snippet") or "")[:200]) for r in (raw.get("results") or [])],
                        },
                    )
                    print("[workflow] SEARCH success")
                    logger.info("[policy] SEARCH success")
                    return _with_classification({"success": True, "output": raw}, "SEARCH")
                logger.info("[policy] SEARCH attempt %s: %s", attempt_num, result_summary)

        print(f"[workflow] SEARCH exhausted after {len(attempt_history)} attempts")
        logger.warning("[policy] SEARCH exhausted after %s attempts", len(attempt_history))
        return _with_classification(
            {
                "success": False,
                "output": {"attempt_history": attempt_history},
                "error": "all search attempts returned empty results",
            },
            "SEARCH",
        )

    def _execute_edit(
        self,
        step: dict,
        state: AgentState,
        max_attempts: int,
        retry_on: list[str],
        mutation: str | None,
        attempt_history: list[dict[str, Any]],
    ) -> dict:
        # ReAct: EDIT always executes. No conditional routing, no symbol_retry, no critic before execution.
        # (Main dispatch bypasses policy_engine for EDIT; this path exists for any alternate callers.)
        try:
            raw = self._edit_fn(step, state)
        except Exception as e:
            attempt_history.append({"attempt": 1, "error": str(e)})
            return _with_classification(
                {"success": False, "output": {"attempt_history": attempt_history}, "error": str(e)},
                "EDIT",
            )
        r = raw if isinstance(raw, dict) else {"success": False, "output": {}, "error": "edit failed"}
        attempt_history.append({"attempt": 1, "success": r.get("success")})
        out = r.get("output") if isinstance(r.get("output"), dict) else {}
        out = dict(out)
        out["attempt_history"] = attempt_history
        return _with_classification(
            {"success": r.get("success", False), "output": out, "error": r.get("error"), "executed": r.get("executed", True)},
            "EDIT",
        )

    def _execute_infra(
        self,
        step: dict,
        state: AgentState,
        max_attempts: int,
        retry_on: list[str],
        mutation: str | None,
        attempt_history: list[dict[str, Any]],
    ) -> dict:
        steps_to_try = retry_same(step)[:max_attempts]
        for attempt_num, st in enumerate(steps_to_try, start=1):
            logger.info("[policy] INFRA attempt %s", attempt_num)
            try:
                raw = self._infra_fn(st, state)
            except Exception as e:
                attempt_history.append({"attempt": attempt_num, "returncode": -1, "error": str(e)})
                continue
            rc = (raw.get("output") or {}).get("returncode", -1)
            attempt_history.append({"attempt": attempt_num, "returncode": rc})
            if not _is_failure("INFRA", retry_on, raw):
                out = raw.get("output") if isinstance(raw.get("output"), dict) else {}
                out = dict(out)
                out["attempt_history"] = attempt_history
                _append_tool_memory(
                    state,
                    {"tool": "infra", "returncode": out.get("returncode", -1), "success": True},
                )
                logger.info("[policy] INFRA success")
                return _with_classification({"success": True, "output": out, "error": raw.get("error")}, "INFRA")
        return _with_classification(
            {
                "success": False,
                "output": {"attempt_history": attempt_history},
                "error": "infra command failed after retries",
            },
            "INFRA",
        )
