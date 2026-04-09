"""
Phase 2 dispatcher — enforces the ToolResult → ExecutionResult normalization boundary.

Invariant: Dispatcher.execute() returns ExecutionResult, or list[ExecutionResult] for
           internal ``search_multi`` (Option A — one logical multi-search, N normalized results).

Legacy bridge path (until all tool handlers return the schema ToolResult natively):
  _execute_fn(step, state) → raw (dict | old ToolResult dataclass)
  → coerce_to_tool_result(raw, tool_name=...) → schema ToolResult
  → assert isinstance(tool_result, ToolResult)
  → map_tool_result_to_execution_result(tool_result, step_id) → ExecutionResult
"""
# DO NOT import from agent.* here
import copy
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, cast

# agent_v2.primitives is NOT imported at module level — doing so creates a circular import
# through agent.tools.filesystem_adapter → agent → agent.execution.step_dispatcher → agent_v2.primitives.
# Primitives are imported lazily inside __init__ only when a Dispatcher is instantiated.
from agent_v2.runtime.fault_hooks import maybe_inject_open_file_fault_raw
from agent_v2.runtime.tool_mapper import coerce_to_tool_result, map_tool_result_to_execution_result
from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.tool import ToolResult


# Step → tool_name resolution for the legacy ReAct dispatch path
_REACT_ACTION_TO_TOOL: dict[str, str] = {
    "SEARCH": "search",
    "READ": "open_file",
    "EDIT": "edit",
    "RUN_TEST": "run_tests",
    "FINISH": "finish",
}


# Input validation error for tool inputs
class ToolInputValidationError(Exception):
    """Raised when tool input is invalid."""


def _validate_tool_inputs(tool_name: str, args: dict) -> None:
    """Minimal validation for critical tool inputs."""
    errors = []
    
    if tool_name in ("search", "search_multi"):
        query = str(args.get("query") or "").strip()
        queries = args.get("queries")
        if tool_name == "search" and not query:
            errors.append("search requires non-empty 'query' argument")
        if tool_name == "search_multi":
            if not isinstance(queries, list) or not queries:
                errors.append("search_multi requires non-empty 'queries' list")
    
    elif tool_name in ("open_file", "write", "edit"):
        path = str(args.get("path") or "").strip()
        if not path:
            errors.append(f"{tool_name} requires non-empty 'path' argument")
    
    elif tool_name == "edit":
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            errors.append("edit requires non-empty 'instruction' argument")
    
    elif tool_name == "shell":
        command = str(args.get("command") or "").strip()
        if not command:
            errors.append("shell requires non-empty 'command' argument")
        # Basic safety: forbid chaining tokens
        if any(tok in command for tok in ("&&", ";", "|", "`")):
            errors.append("shell command contains forbidden chaining tokens (&&, ;, |, `)")
    
    if errors:
        raise ToolInputValidationError("; ".join(errors))


def _resolve_tool_name(step: dict) -> str:
    """Derive a normalized tool name from the step dict."""
    # Prefer the raw ReAct action name (already lowercased, matches ToolDefinition names)
    action_raw = step.get("_react_action_raw", "")
    if action_raw:
        return action_raw
    action = (step.get("action") or "").upper()
    return _REACT_ACTION_TO_TOOL.get(action, action.lower() or "unknown")


def _resolve_step_id(step: dict) -> str:
    """Derive a string step_id from the step dict."""
    return str(step.get("step_id") or step.get("id") or "unknown")


class Dispatcher:
    """
    Thin execution wrapper that enforces the normalization boundary.

    Responsibilities:
      1. Invoke the tool handler (_execute_fn).
      2. Coerce the raw output to schema ToolResult (legacy bridge).
      3. Assert the ToolResult contract (type safety).
      4. Map ToolResult → ExecutionResult.
      5. Return ExecutionResult — nothing else.
    """

    def __init__(
        self,
        execute_fn=None,
        shell=None,
        editor=None,
        browser=None,
    ):
        self._execute_fn = execute_fn or self._execute_step

        # Lazy import only when defaults need to be created.
        # Importing agent_v2.primitives at module level causes a circular import:
        #   agent_v2.primitives.editor → agent.tools.filesystem_adapter →
        #   agent → agent.execution.step_dispatcher → agent_v2.primitives
        # Passing shell/editor/browser explicitly avoids the import entirely.
        if shell is None or editor is None or browser is None:
            from agent_v2.primitives import Browser, Editor, Shell  # noqa: PLC0415
            self._shell = shell if shell is not None else Shell()
            self._editor = editor if editor is not None else Editor()
            self._browser = browser if browser is not None else Browser()
        else:
            self._shell = shell
            self._editor = editor
            self._browser = browser

    def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
        """
        Execute a step and return a normalized ExecutionResult, or list[ExecutionResult]
        for ``search_multi`` (batched vector retrieval path).
        """
        # Deep copy step to prevent mutation during execution
        safe_step = copy.deepcopy(step)
        
        if getattr(state, "context", None) is not None:
            state.context.setdefault("shell", self._shell)
            state.context.setdefault("editor", self._editor)
            state.context.setdefault("browser", self._browser)

        tool_name = _resolve_tool_name(safe_step) if isinstance(safe_step, dict) else "unknown"
        step_id = _resolve_step_id(safe_step) if isinstance(safe_step, dict) else "unknown"

        # Validate inputs before execution
        try:
            args = safe_step.get("_react_args") if isinstance(safe_step, dict) else {}
            _validate_tool_inputs(tool_name, args)
        except ToolInputValidationError as e:
            return ExecutionResult(
                step_id=step_id,
                success=False,
                status="failure",
                output=ExecutionOutput(
                    data={},
                    summary=f"Input validation failed: {e}",
                    full_output=None,
                ),
                error=ExecutionError(
                    type=ErrorType.validation_error,
                    message=str(e),
                    details={},
                ),
                metadata=ExecutionMetadata(
                    tool_name=tool_name,
                    duration_ms=0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
            )

        # Step 2: run handler (optional test fault injection before real tools)
        fault_raw = (
            maybe_inject_open_file_fault_raw(tool_name, safe_step, state)
            if isinstance(safe_step, dict)
            else None
        )
        if fault_raw is not None:
            raw = fault_raw
        else:
            raw = self._execute_fn(safe_step, state)

        # search_multi: handler returns list[dict] → list[ExecutionResult] (Option A).
        if (
            tool_name == "search_multi"
            and isinstance(raw, list)
            and raw
            and all(isinstance(x, dict) for x in raw)
        ):
            out_list: list[ExecutionResult] = []
            for i, r in enumerate(raw):
                tr = coerce_to_tool_result(r, tool_name="search")
                assert isinstance(tr, ToolResult), (
                    f"coerce_to_tool_result must return ToolResult; got {type(tr).__name__}"
                )
                er = map_tool_result_to_execution_result(tr, step_id=f"{step_id}_{i}")
                if er.output is None or not str(er.output.summary or "").strip():
                    raise ValueError("ExecutionResult.output.summary must be present and non-empty")
                out_list.append(er)
            return out_list

        # Short-circuit: if raw is already ExecutionResult, align step_id and enforce invariants
        if isinstance(raw, ExecutionResult):
            result = raw
            # Align step_id if needed
            if result.step_id != step_id:
                result = result.model_copy(update={"step_id": step_id})
            # Enforce invariants
            if result.output is None or not str(result.output.summary or "").strip():
                raise ValueError("ExecutionResult.output.summary must be present and non-empty")
            if result.success and result.error is not None:
                raise ValueError("ExecutionResult.error must be None when success=True")
            if not result.success and result.error is None:
                raise ValueError("ExecutionResult.error must be present when success=False")
            return result

        # Step 3 (legacy bridge): coerce whatever came back into schema ToolResult
        tool_result = coerce_to_tool_result(raw, tool_name=tool_name)

        # Step 4 (type assertion): every path through this function must yield a ToolResult
        assert isinstance(tool_result, ToolResult), (
            f"coerce_to_tool_result must return ToolResult; got {type(tool_result).__name__}"
        )

        # Step 5: normalize to ExecutionResult — the only type returned from here
        result = map_tool_result_to_execution_result(tool_result, step_id=step_id)
        if not isinstance(result, ExecutionResult):
            raise TypeError(
                f"Dispatcher contract violation: expected ExecutionResult, got {type(result).__name__}"
            )
        if result.output is None or not str(result.output.summary or "").strip():
            raise ValueError("ExecutionResult.output.summary must be present and non-empty")
        if isinstance(raw, dict):
            logging.getLogger(__name__).debug(
                "Dispatcher coerced dict tool output to ExecutionResult (tool=%s)",
                tool_name,
            )
        return result

    def search_batch(
        self,
        queries: list[str],
        state: Any,
        *,
        mode: str,
        step_id_prefix: str,
        max_workers: int = 4,
    ) -> list[ExecutionResult]:
        """
        Run discovery searches for ``queries`` in order.

        When ``RETRIEVAL_V2_MULTI_SEARCH`` is enabled and len(queries) > 1, uses a single
        ``search_multi`` step so ``retrieve`` issues one ``vector_retriever.search_batch``
        (daemon ``POST /retrieve/vector/batch`` when remote-first). Otherwise falls back to
        N parallel ``execute(SEARCH)`` (one vector call per query).
        """
        if not queries:
            return []

        n = len(queries)
        multi_on = os.getenv("RETRIEVAL_V2_MULTI_SEARCH", "1").lower() in ("1", "true", "yes")
        if multi_on and n > 1:
            step = {
                "id": f"{step_id_prefix}_multi",
                "action": "SEARCH",
                "_react_action_raw": "search_multi",
                "_react_args": {"queries": list(queries)},
                "query": "",
                "description": f"search_multi:{mode}:{n}",
            }
            merged = self.execute(step, state)
            if isinstance(merged, list) and len(merged) == n:
                return merged

        out: list[ExecutionResult | None] = [None] * n

        def _run_index(i: int, q: str) -> tuple[int, ExecutionResult]:
            step = {
                "id": f"{step_id_prefix}_{i}",
                "action": "SEARCH",
                "_react_action_raw": "search",
                "_react_args": {"query": q},
                "query": q,
                "description": q,
            }
            task_state = copy.copy(state)
            base_ctx = getattr(state, "context", None)
            task_state.context = copy.deepcopy(base_ctx) if isinstance(base_ctx, dict) else {}
            res = self.execute(step, task_state)
            if isinstance(res, list):
                raise RuntimeError("unexpected list from single SEARCH execute")
            return (i, res)

        workers = min(max_workers, max(1, n))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_run_index, i, queries[i]) for i in range(n)]
            for fut in as_completed(futures):
                i, res = fut.result()
                out[i] = res

        return cast(list[ExecutionResult], out)

    @staticmethod
    def _execute_step(step, state):
        raise RuntimeError(
            "Dispatcher requires an execute_fn. "
            "Legacy dispatch wiring must be injected from agent_v2.runtime.bootstrap."
        )
