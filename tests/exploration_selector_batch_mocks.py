"""Aligned SelectorBatchResult mocks for symbol-aware exploration tests."""

from __future__ import annotations

from agent_v2.schemas.exploration import SelectorBatchResult


def mock_selector_batch_first_n(scoped_or_candidates, limit: int, **kwargs) -> SelectorBatchResult:
    """
    Return a batch aligned with production: ``selected_top_indices[i]`` maps the i-th
    selected row to index ``i`` in the scoped slice, with ``selected_symbols[str(i)]``
    carrying the candidate's discovery symbol when present.
    """
    sel = list(scoped_or_candidates[:limit])
    top_idx = list(range(len(sel)))
    sym: dict[str, list[str]] = {}
    for j, c in zip(top_idx, sel):
        name = getattr(c, "symbol", None)
        if name:
            sym[str(j)] = [name]
    return SelectorBatchResult(
        selected_candidates=sel,
        selected_top_indices=top_idx,
        selected_symbols=sym,
        **kwargs,
    )
