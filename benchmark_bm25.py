#!/usr/bin/env python3
"""
Production benchmark: rank_bm25 (BM25Okapi) vs bm25s.

Requires: pip install rank-bm25 bm25s psutil numpy
Run from AutoStudio repo root: python benchmark_bm25.py
"""

from __future__ import annotations

import gc
import os

# bm25s reads this at import time to disable tqdm noise in benchmark output
os.environ.setdefault("DISABLE_TQDM", "1")

import re
import sys
import time
from pathlib import Path

import numpy as np

try:
    import psutil
except ImportError as e:
    raise SystemExit(
        "psutil is required for RSS measurements. Install with: pip install psutil"
    ) from e

from rank_bm25 import BM25Okapi

try:
    import bm25s  # noqa: E402
except ImportError as e:
    raise SystemExit(
        "bm25s is required. Install with: pip install bm25s"
    ) from e

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent

IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "env",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".eggs",
    }
)

CODE_EXTENSIONS = (".py", ".ts", ".js", ".go", ".rs", ".java", ".cpp", ".md")

# 25 realistic developer-style queries (categories A–E + cross-cutting; spec: 22–25)
QUERIES: list[str] = [
    # A — Code structure
    "class retrieval pipeline",
    "function search_batch",
    "async handler implementation",
    # B — System concepts
    "vector search implementation",
    "embedding model usage",
    "reranking logic",
    # C — Project-specific (AutoStudio)
    "retrieve_v2",
    "dispatcher search",
    "exploration engine",
    # D — Debugging / ops style
    "error handling logic",
    "timeout retry mechanism",
    "logging configuration",
    # E — Infra
    "database connection setup",
    "api endpoint definition",
    "configuration loading",
    # Cross-cutting (realistic search phrasing)
    "where is the retrieval pipeline configured",
    "how does reranking work with candidates",
    "chroma collection embedding",
    "dispatcher policy check",
    "planner structured steps",
    "trace logger execution",
    "edit pipeline diff apply",
    "repository indexer chunk tokenizer",
    "sentence transformer embedding model",
    "fastapi route handler",
]

assert 22 <= len(QUERIES) <= 25, f"expected 22–25 queries, got {len(QUERIES)}"

# Queries used for top-k correctness spot-check (must exist in QUERIES)
CORRECTNESS_QUERIES = (
    "retrieve_v2",
    "dispatcher search",
    "vector search implementation",
)


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return True
    return b"\x00" in chunk


def should_skip_dir(name: str) -> bool:
    if name in IGNORE_DIR_NAMES:
        return True
    return name.endswith(".egg-info")


def load_corpus(repo: Path) -> tuple[list[str], list[str]]:
    """Walk entire repo; return parallel lists of text and relative paths."""
    texts: list[str] = []
    paths: list[str] = []
    repo = repo.resolve()

    for root, dirs, files in os.walk(repo, topdown=True):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for fname in files:
            if not fname.endswith(CODE_EXTENSIONS):
                continue
            path = Path(root) / fname
            if is_probably_binary(path):
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Empty files: keep (stability); tokenization yields []
            rel = str(path.relative_to(repo))
            texts.append(raw)
            paths.append(rel)

    return texts, paths


def snippet_for_doc(text: str, max_len: int = 160) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def top_k_indices(scores, k: int = 3) -> np.ndarray:
    s = np.asarray(scores, dtype=np.float64)
    if s.size == 0:
        return np.array([], dtype=int)
    k = min(k, s.size)
    # argpartition then sort top k
    idx = np.argpartition(-s, k - 1)[:k]
    return idx[np.argsort(-s[idx])]


def print_correctness_block(
    label: str,
    query: str,
    indices: np.ndarray,
    paths: list[str],
    docs: list[str],
) -> None:
    print(f"  [{label}] query: {query!r}")
    for rank, doc_i in enumerate(indices, start=1):
        doc_i = int(doc_i)
        p = paths[doc_i] if doc_i < len(paths) else "?"
        snip = snippet_for_doc(docs[doc_i]) if doc_i < len(docs) else ""
        print(f"    #{rank}  idx={doc_i}  {p}")
        print(f"        {snip}")


def run_queries_timed(
    score_fn,
    queries: list[str],
) -> tuple[list[float], float, float, float]:
    """score_fn(q_tokens) -> scores array. Returns times and avg/min/max ms."""
    times: list[float] = []
    for q in queries:
        q_tok = tokenize(q)
        t0 = time.perf_counter()
        _ = score_fn(q_tok)
        ms = (time.perf_counter() - t0) * 1000.0
        times.append(ms)
    avg = sum(times) / len(times) if times else 0.0
    return times, avg, min(times) if times else 0.0, max(times) if times else 0.0


def main() -> None:
    repo = REPO_ROOT
    print(f"Repository root: {repo}")
    print("Loading corpus (full tree; excluding .git, node_modules, venv, ...)...")

    docs, paths = load_corpus(repo)
    tokenized_docs = [tokenize(t) for t in docs]

    print(f"Documents indexed: {len(docs)}")
    print(f"Query count: {len(QUERIES)}")
    if not docs:
        print("No documents found. Exiting.")
        sys.exit(1)

    gc.collect()
    rss_after_load = rss_mb()

    # --- rank_bm25 ---
    gc.collect()
    rss_before_bm25 = rss_mb()
    t0 = time.perf_counter()
    bm25 = BM25Okapi(tokenized_docs)
    index_time_bm25 = time.perf_counter() - t0
    gc.collect()
    rss_after_bm25 = rss_mb()
    mem_bm25 = rss_after_bm25 - rss_before_bm25

    def score_bm25(q_tokens: list[str]):
        return bm25.get_scores(q_tokens)

    bm25_times, avg_b, min_b, max_b = run_queries_timed(score_bm25, QUERIES)

    # Drop BM25 before building BM25S so RSS delta reflects BM25S index alone
    del bm25
    gc.collect()

    # --- bm25s ---
    rss_before_bm25s = rss_mb()
    t0 = time.perf_counter()
    bm25s_model = bm25s.BM25()
    bm25s_model.index(tokenized_docs)
    index_time_bm25s = time.perf_counter() - t0
    gc.collect()
    rss_after_bm25s = rss_mb()
    mem_bm25s = rss_after_bm25s - rss_before_bm25s

    def score_bm25s(q_tokens: list[str]):
        return bm25s_model.get_scores(q_tokens)

    bm25s_times, avg_s, min_s, max_s = run_queries_timed(score_bm25s, QUERIES)

    # --- Correctness: top-3 overlap / side-by-side for 3 queries ---
    print("\n" + "=" * 60)
    print("CORRECTNESS SPOT-CHECK (top-3 document indices + snippets)")
    print("=" * 60)
    for cq in CORRECTNESS_QUERIES:
        if cq not in QUERIES:
            continue
        qt = tokenize(cq)
        bm25_cmp = BM25Okapi(tokenized_docs)
        sc_b = np.asarray(bm25_cmp.get_scores(qt))
        del bm25_cmp
        gc.collect()
        sc_s = np.asarray(bm25s_model.get_scores(qt))
        top_b = top_k_indices(sc_b, 3)
        top_s = top_k_indices(sc_s, 3)
        overlap = len(set(top_b.tolist()) & set(top_s.tolist()))
        print(f"\nQuery: {cq!r}")
        print(f"  Top-3 index overlap (BM25 vs BM25S): {overlap} / 3")
        print_correctness_block("BM25", cq, top_b, paths, docs)
        print_correctness_block("BM25S", cq, top_s, paths, docs)

    # --- Per-query table (abbreviated) ---
    print("\n" + "=" * 60)
    print("PER-QUERY LATENCY (ms)")
    print("=" * 60)
    print(f"{'Query':<48} {'BM25':>10} {'BM25S':>10}")
    print("-" * 70)
    for q, tb, ts in zip(QUERIES, bm25_times, bm25s_times):
        qshort = q if len(q) <= 46 else q[:43] + "..."
        print(f"{qshort:<48} {tb:10.3f} {ts:10.3f}")

    # --- Summary ---
    speedup = (avg_b / avg_s) if avg_s > 0 else float("nan")
    # Positive % = BM25S index step retained less RSS than BM25; negative = BM25S higher
    mem_reduction_pct = (
        ((mem_bm25 - mem_bm25s) / mem_bm25 * 100.0) if mem_bm25 > 1e-6 else float("nan")
    )

    print("\n================ BM25 =================")
    print(f"Index time: {index_time_bm25:.3f} sec")
    print(f"Memory: +{mem_bm25:.2f} MB")
    print(f"Avg query: {avg_b:.3f} ms")
    print(f"Min/Max: {min_b:.3f} / {max_b:.3f} ms")

    print("\n================ BM25S ================")
    print(f"Index time: {index_time_bm25s:.3f} sec")
    print(f"Memory: +{mem_bm25s:.2f} MB")
    print(f"Avg query: {avg_s:.3f} ms")
    print(f"Min/Max: {min_s:.3f} / {max_s:.3f} ms")

    print("\n=============== COMPARISON =============")
    if mem_bm25 >= 5.0:
        print(f"Memory reduction: {mem_reduction_pct:.1f}%")
        print("(positive => BM25S index RSS delta smaller than BM25)")
    else:
        print("Memory reduction: (n/a — BM25 RSS delta < 5 MB; OS noise dominates)")
        print(f"  Compare absolute MB above: BM25 +{mem_bm25:.2f} MB vs BM25S +{mem_bm25s:.2f} MB")
    if mem_bm25 > 1e-6:
        print(f"RSS index delta ratio (BM25S / BM25): {mem_bm25s / mem_bm25:.2f}x")
    print(f"Speedup: {speedup:.2f}x")

    print("\n--- Reference RSS (approx.) ---")
    print(f"RSS after corpus load: {rss_after_load:.1f} MB")
    print(f"RSS after BM25 index (before delete): {rss_after_bm25:.1f} MB")
    print(f"RSS after BM25S index: {rss_after_bm25s:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
