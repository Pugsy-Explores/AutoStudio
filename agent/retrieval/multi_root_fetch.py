"""Merge graph / BM25 / vector retrieval across multiple indexed project roots.

Serena adapter stays single-root (primary ``project_root`` only) to avoid MCP ambiguity.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

RowMerger = Callable[[str, str, int], tuple[list[dict], list[str]]]


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    """First-seen wins; key = resolved file path + symbol."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        fp = (r.get("file") or "").strip()
        if not fp:
            continue
        try:
            key_path = str(Path(fp).resolve())
        except OSError:
            key_path = fp
        sym = (r.get("symbol") or "").strip()
        dedup = f"{key_path}|{sym}"
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append(r)
    return out


def fetch_merged(
    fetch_fn: RowMerger,
    query: str,
    roots: tuple[str, ...],
    top_k: int,
    *,
    max_rows: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Call ``fetch_fn(query, root, top_k)`` for each root; dedupe; cap length."""
    if len(roots) <= 1:
        r0 = roots[0] if roots else ""
        return fetch_fn(query, r0, top_k)

    merged: list[dict] = []
    warnings: list[str] = []

    with ThreadPoolExecutor(max_workers=min(len(roots), 2)) as ex:
        future_to_root = {
            ex.submit(fetch_fn, query, root, top_k): root
            for root in roots
        }
        try:
            for fut in as_completed(future_to_root, timeout=len(roots) * 5):
                root = future_to_root[fut]
                try:
                    rows, warns = fut.result(timeout=5)
                    merged.extend(rows)
                    warnings.extend(warns)
                except TimeoutError:
                    logger.warning(
                        "[multi_root_fetch] root=%s timed out after 5s — skipping",
                        root,
                    )
                except Exception as e:
                    logger.warning("[multi_root_fetch] root=%s failed: %s", root, e)
        except TimeoutError:
            logger.warning(
                "[multi_root_fetch] as_completed timed out (roots=%d) — partial merge",
                len(roots),
            )

    deduped = _dedupe_rows(merged)
    cap = max_rows if max_rows is not None else top_k * max(1, len(roots))
    cap = max(cap, top_k)
    out = deduped[:cap]
    logger.debug(
        "[multi_root_fetch] roots=%d merged_in=%d deduped=%d out=%d",
        len(roots),
        len(merged),
        len(deduped),
        len(out),
    )
    return out, warnings
