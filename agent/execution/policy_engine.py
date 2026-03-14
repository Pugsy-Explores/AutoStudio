"""Execution policy engine: retry with tool-specific policies and mutation strategies."""

import logging
from typing import Any, Callable

from agent.execution.mutation_strategies import (
    retry_same,
    symbol_retry,
)
from agent.memory.state import AgentState
from agent.retrieval.query_rewriter import SearchAttempt

logger = logging.getLogger(__name__)


def _is_valid_search_result(results: list | None) -> bool:
    """True only if the first result has a non-empty file and a real snippet."""
    if not results:
        return False
    r = results[0]
    if not r.get("file"):
        return False
    if r.get("snippet") in ("", "[]", None):
        return False
    return True


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


def _is_failure(action: str, retry_on: list[str], result: dict[str, Any]) -> bool:
    """Map retry_on to concrete checks. Returns True if result is a failure."""
    if "empty_results" in retry_on:
        results = result.get("results") if isinstance(result.get("results"), list) else None
        return not _is_valid_search_result(results)
    if "symbol_not_found" in retry_on:
        return bool(result.get("error")) or result.get("success") is False
    if "non_zero_exit" in retry_on:
        # INFRA returns { success, output, error }; returncode is in output
        return (result.get("output") or {}).get("returncode", -1) != 0
    return False


def _search_result_summary(raw: dict) -> str:
    """Short summary of search result for rewrite context (e.g. '0 results' or '2 results: a.py, b.py')."""
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


class ExecutionPolicyEngine:
    """
    Retry-capable execution: run tool via injected fns, apply policy, mutate and retry on failure.
    Tool functions are injected so the engine does not depend on concrete adapters.
    """

    def __init__(
        self,
        search_fn: Callable[[str], dict],
        edit_fn: Callable[[dict, AgentState], dict],
        infra_fn: Callable[[dict, AgentState], dict],
        *,
        rewrite_query_fn: Callable[[str, str, list[SearchAttempt]], str] | None = None,
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
                desc = (step.get("description") or "").strip()
                user_req = getattr(state, "instruction", "") or ""
                if self._rewrite_query_fn is not None:
                    q = self._rewrite_query_fn(desc, user_req, [])
                else:
                    q = desc
                print(f"[workflow] SEARCH (single) query={q!r}")
                raw = self._search_fn(q)
                if isinstance(raw, dict) and _is_failure("SEARCH", ["empty_results"], raw):
                    return {"success": False, "output": {"attempt_history": []}, "error": "empty results"}
                return {"success": True, "output": raw}
            if action == "EDIT":
                raw = self._edit_fn(step, state)
                return raw if isinstance(raw, dict) else {"success": False, "output": {}, "error": "edit failed"}
            if action == "INFRA":
                raw = self._infra_fn(step, state)
                return raw if isinstance(raw, dict) else {"success": False, "output": {}, "error": "infra failed"}
        except Exception as e:
            return {"success": False, "output": {}, "error": str(e)}
        return {"success": False, "output": {}, "error": "unknown action"}

    def _execute_search(
        self,
        step: dict,
        state: AgentState,
        max_attempts: int,
        retry_on: list[str],
        attempt_history: list[dict[str, Any]],
    ) -> dict:
        description = (step.get("description") or "").strip()
        user_request = getattr(state, "instruction", "") or ""

        print(f"[workflow] SEARCH step description: {description[:80]}{'...' if len(description) > 80 else ''}")
        print(f"[workflow] SEARCH max_attempts={max_attempts}")

        for attempt_num in range(1, max_attempts + 1):
            # Rewrite with full context: planner step, user request, previous attempts
            if self._rewrite_query_fn is not None:
                attempt_slice: list[SearchAttempt] = [
                    {
                        "query": h.get("query", ""),
                        "result_count": h.get("result_count", 0),
                        "result_summary": h.get("result_summary", ""),
                        "error": h.get("error", ""),
                    }
                    for h in attempt_history
                ]
                query = self._rewrite_query_fn(description, user_request, attempt_slice)
            else:
                query = description or ""
            if not query or not query.strip():
                query = description

            print(f"[workflow] SEARCH attempt {attempt_num}/{max_attempts} query={query!r}")
            logger.info(
                "[policy] SEARCH attempt %s: %s",
                attempt_num,
                (query[:80] + "..." if len(query) > 80 else query),
            )
            try:
                raw = self._search_fn(query)
            except Exception as e:
                attempt_history.append({
                    "query": query,
                    "result_count": 0,
                    "result_summary": "",
                    "error": str(e),
                })
                print(f"[workflow] SEARCH attempt {attempt_num} error: {e}")
                logger.warning("[policy] SEARCH attempt %s failed: %s", attempt_num, e)
                continue

            result_count = len(raw.get("results") or [])
            result_summary = _search_result_summary(raw)
            attempt_history.append({
                "query": query,
                "result_count": result_count,
                "result_summary": result_summary,
            })

            print(f"[workflow] SEARCH attempt {attempt_num} result: {result_summary}")
            if not _is_failure("SEARCH", retry_on, raw):
                if isinstance(raw, dict):
                    raw = dict(raw)
                    raw["attempt_history"] = attempt_history
                state.context["search_query_rewritten"] = query
                state.context["search_results"] = raw
                state.context["files"] = [r.get("file") for r in (raw.get("results") or []) if r.get("file")]
                state.context["snippets"] = [r.get("snippet", "") for r in (raw.get("results") or [])]
                print("[workflow] SEARCH success")
                logger.info("[policy] SEARCH success")
                return {"success": True, "output": raw}
            logger.info("[policy] SEARCH attempt %s: %s", attempt_num, result_summary)

        print(f"[workflow] SEARCH exhausted after {len(attempt_history)} attempts")
        logger.warning("[policy] SEARCH exhausted after %s attempts", len(attempt_history))
        return {
            "success": False,
            "output": {"attempt_history": attempt_history},
            "error": "all search attempts returned empty results",
        }

    def _execute_edit(
        self,
        step: dict,
        state: AgentState,
        max_attempts: int,
        retry_on: list[str],
        mutation: str | None,
        attempt_history: list[dict[str, Any]],
    ) -> dict:
        steps_to_try = symbol_retry(step)[:max_attempts]
        for attempt_num, st in enumerate(steps_to_try, start=1):
            logger.info("[policy] EDIT attempt %s", attempt_num)
            try:
                raw = self._edit_fn(st, state)
            except Exception as e:
                attempt_history.append({"attempt": attempt_num, "error": str(e)})
                continue
            attempt_history.append({"attempt": attempt_num, "success": raw.get("success")})
            if not _is_failure("EDIT", retry_on, raw):
                out = raw.get("output") if isinstance(raw.get("output"), dict) else {}
                out = dict(out)
                out["attempt_history"] = attempt_history
                logger.info("[policy] EDIT success")
                return {"success": True, "output": out, "error": raw.get("error")}
        return {
            "success": False,
            "output": {"attempt_history": attempt_history},
            "error": "edit failed after retries",
        }

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
            rc = raw.get("returncode", -1)
            attempt_history.append({"attempt": attempt_num, "returncode": rc})
            if not _is_failure("INFRA", retry_on, raw):
                out = raw.get("output") if isinstance(raw.get("output"), dict) else {}
                out = dict(out)
                out["attempt_history"] = attempt_history
                logger.info("[policy] INFRA success")
                return {"success": True, "output": out, "error": raw.get("error")}
        return {
            "success": False,
            "output": {"attempt_history": attempt_history},
            "error": "infra command failed after retries",
        }
