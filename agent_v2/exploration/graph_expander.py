from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_v2.schemas.execution import ExecutionMetadata, ExecutionOutput
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import ExplorationTarget


class GraphExpander:
    """
    Controlled expansion adapter.

    Uses search through dispatcher as a safe read-only source while preserving
    the expansion semantics (callers/callees/related symbol probes).
    """

    def __init__(self, dispatcher):
        self._dispatcher = dispatcher

    def expand(
        self,
        symbol: str,
        file_path: str,
        state: Any,
        *,
        max_nodes: int = 10,
        max_depth: int = 1,
    ) -> tuple[list[ExplorationTarget], ExecutionResult]:
        if not symbol.strip():
            return [], self._make_result("empty symbol", {}, success=False)

        # Expansion is graph-first by contract; fallback to search if graph has no rows.
        from agent.retrieval.adapters.graph import fetch_graph  # noqa: PLC0415

        project_root = ""
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            project_root = str(ctx.get("project_root") or "")
        graph_rows, warnings = fetch_graph(symbol, project_root=project_root, top_k=max_nodes)
        if graph_rows:
            callers: list[ExplorationTarget] = []
            callees: list[ExplorationTarget] = []
            related: list[ExplorationTarget] = []
            for row in graph_rows[:max_nodes]:
                target = ExplorationTarget(
                    file_path=str(row.get("file") or ""),
                    symbol=(str(row.get("symbol")).strip() if row.get("symbol") else None),
                    line=(int(row["line"]) if isinstance(row.get("line"), int) else None),
                    source="expansion",
                )
                snippet = str(row.get("snippet") or "").lower()
                if "caller" in snippet:
                    callers.append(target)
                elif "callee" in snippet:
                    callees.append(target)
                else:
                    related.append(target)
            result_data = {
                "results": [t.model_dump(mode="json") for t in (callers + callees + related)[:max_nodes]],
                "callers": [t.model_dump(mode="json") for t in callers[:max_nodes]],
                "callees": [t.model_dump(mode="json") for t in callees[:max_nodes]],
                "related": [t.model_dump(mode="json") for t in related[:max_nodes]],
                "warnings": warnings,
                "max_depth": max_depth,
                "anchor_file": file_path,
            }
            return (callers + callees + related)[:max_nodes], self._make_result(
                f"Graph expansion returned {len(result_data['results'])} target(s) for {symbol}",
                result_data,
                success=True,
            )

        step_count = int(getattr(state, "steps_taken", 0))
        query = f"{symbol} callers callees definition"
        step = {
            "id": f"expand_{step_count + 1}",
            "action": "SEARCH",
            "_react_action_raw": "search",
            "_react_args": {"query": query},
            "query": query,
            "description": query,
        }
        result = self._dispatcher.execute(step, state)
        data = result.output.data if result.output else {}
        return self._extract_targets(data)[:max_nodes], result

    @staticmethod
    def _extract_targets(data: dict) -> list[ExplorationTarget]:
        if not isinstance(data, dict):
            return []
        results = data.get("results") or data.get("candidates") or []
        out: list[ExplorationTarget] = []
        for row in results if isinstance(results, list) else []:
            if not isinstance(row, dict):
                continue
            file_path = str(row.get("file") or row.get("file_path") or "").strip()
            if not file_path:
                continue
            out.append(
                ExplorationTarget(
                    file_path=file_path,
                    symbol=(str(row.get("symbol")).strip() if row.get("symbol") else None),
                    line=(int(row["line"]) if isinstance(row.get("line"), int) else None),
                    source="expansion",
                )
            )
        return out

    @staticmethod
    def _make_result(summary: str, data: dict, *, success: bool) -> ExecutionResult:
        return ExecutionResult(
            step_id="graph_expand",
            success=success,
            status="success" if success else "failure",
            output=ExecutionOutput(summary=summary, data=data),
            error=None,
            metadata=ExecutionMetadata(
                tool_name="graph_lookup",
                duration_ms=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        )
