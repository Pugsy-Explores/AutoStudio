from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_v2.config import EXPLORATION_EXPAND_MAX_DEPTH, EXPLORATION_EXPAND_MAX_NODES
from agent_v2.schemas.execution import ExecutionMetadata, ExecutionOutput
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import ExplorationTarget

_LOG = logging.getLogger(__name__)


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
        max_nodes: int = EXPLORATION_EXPAND_MAX_NODES,
        max_depth: int = EXPLORATION_EXPAND_MAX_DEPTH,
        direction_hint: str | None = None,
        skip_files: set[str] | None = None,
        skip_symbols: set[str] | None = None,
    ) -> tuple[list[ExplorationTarget], ExecutionResult]:
        _LOG.debug("[GraphExpander.expand]")
        if not symbol.strip():
            return [], self._make_result("empty symbol", {}, success=False)

        # Expansion is graph-first by contract; fallback to search if graph has no rows.
        from agent.retrieval.adapters.graph import fetch_graph  # noqa: PLC0415
        from config.retrieval_config import ENABLE_GRAPH_LOOKUP  # noqa: PLC0415

        project_root = ""
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            project_root = str(ctx.get("project_root") or "")
        graph_rows: list = []
        warnings: list = []
        if ENABLE_GRAPH_LOOKUP:
            graph_rows, warnings = fetch_graph(symbol, project_root=project_root, top_k=max_nodes * 2)
        skip_files = skip_files or set()
        skip_symbols = skip_symbols or set()

        def _norm_file(fp: str) -> str:
            raw = str(fp or "").strip()
            if not raw:
                return ""
            p = Path(raw)
            if not p.is_absolute() and project_root:
                p = Path(project_root) / raw
            try:
                return str(p.resolve())
            except Exception:
                return str(p)

        if graph_rows:
            filtered_rows: list[dict] = []
            for row in graph_rows:
                if not isinstance(row, dict):
                    continue
                fp = _norm_file(str(row.get("file") or ""))
                sym = str(row.get("symbol") or "").strip() if row.get("symbol") else ""
                if fp and fp in skip_files:
                    continue
                if sym and sym in skip_symbols:
                    continue
                filtered_rows.append(row)

            callers: list[ExplorationTarget] = []
            callees: list[ExplorationTarget] = []
            related: list[ExplorationTarget] = []
            for row in filtered_rows[: max_nodes * 2]:
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

            hint = (direction_hint or "").strip().lower() or None
            if hint == "callers":
                combined = list(callers)
                if not combined:
                    combined = list(related)
            elif hint == "callees":
                combined = list(callees)
                if not combined:
                    combined = list(related)
            elif hint == "both":
                combined = (callers + callees)[:max_nodes]
                if not combined:
                    combined = list(related)
            else:
                combined = (callers + callees + related)[:max_nodes]

            combined = combined[:max_nodes]
            result_data = {
                "results": [t.model_dump(mode="json") for t in combined],
                "callers": [t.model_dump(mode="json") for t in callers[:max_nodes]],
                "callees": [t.model_dump(mode="json") for t in callees[:max_nodes]],
                "related": [t.model_dump(mode="json") for t in related[:max_nodes]],
                "warnings": warnings,
                "max_depth": max_depth,
                "anchor_file": file_path,
                "direction_hint": hint,
            }
            return combined, self._make_result(
                f"Graph expansion returned {len(combined)} target(s) for {symbol}",
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
        raw_targets = self._extract_targets(data)
        out: list[ExplorationTarget] = []
        for t in raw_targets[:max_nodes]:
            fp = _norm_file(t.file_path)
            sym = (t.symbol or "").strip()
            if fp and fp in skip_files:
                continue
            if sym and sym in skip_symbols:
                continue
            out.append(
                ExplorationTarget(
                    file_path=fp or t.file_path,
                    symbol=t.symbol,
                    line=t.line,
                    source="expansion",
                )
            )
        return out[:max_nodes], result

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
