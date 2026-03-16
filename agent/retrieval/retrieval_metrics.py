"""Retrieval stage latency metrics. Wrap each pipeline stage with start/end for timing."""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

STAGES = (
    "bm25",
    "vector",
    "grep",
    "repo_map",
    "rrf_merge",
    "graph_expand",
    "symbol_expand",
    "rerank",
    "context_prune",
)


class RetrievalMetrics:
    """Track per-stage latency. Use start(stage)/end(stage) around each pipeline stage."""

    def __init__(self, trace_id: str | None = None, query_id: str | None = None, step_id: int | None = None):
        self.trace_id = trace_id or ""
        self.query_id = query_id or ""
        self.step_id = step_id
        self._started: dict[str, float] = {}
        self._durations: dict[str, float] = defaultdict(float)

    def start(self, stage: str) -> None:
        self._started[stage] = time.monotonic()

    def end(self, stage: str) -> None:
        if stage in self._started:
            self._durations[stage] += time.monotonic() - self._started[stage]
            del self._started[stage]

    def get_durations(self) -> dict[str, float]:
        return dict(self._durations)

    def log(self) -> None:
        if not self._durations:
            return
        parts = [f"{k}={v:.2f}s" for k, v in sorted(self._durations.items())]
        meta = []
        if self.trace_id:
            meta.append(f"trace_id={self.trace_id}")
        if self.query_id:
            meta.append(f"query_id={self.query_id}")
        if self.step_id is not None:
            meta.append(f"step_id={self.step_id}")
        prefix = " ".join(meta) + " " if meta else ""
        logger.info("[retrieval_metrics] %s%s", prefix, " ".join(parts))
