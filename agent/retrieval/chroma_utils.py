"""ChromaDB 1.x persistent client helper (shared by vector search and repo indexer).

RCA — typical warnings from ``vector_retriever`` client init
----------------------------------------------------------

1. **"Could not connect to tenant default_tenant"**
   The on-disk store under ``.../.symbol_graph/embeddings`` was created with an older Chroma
   (pre--1.x / DuckDB layout) or is partially written. Chroma 1.x expects ``chroma.sqlite3``
   + segment metadata; a mismatched file triggers SysDB/tenant resolution failures.

   **Fix:** Remove that ``embeddings`` directory and re-run ``repo_index.indexer.index_repo``
   (or ``chroma-migrate`` if you must keep data). Do not mix Chroma 0.4 and 1.x stores.

2. **"'RustBindingsAPI' object has no attribute 'bindings'"**
   Chroma 1.x defaults to ``chromadb.api.rust.RustBindingsAPI``; ``bindings`` is assigned in
   ``start()``. This error usually means the Rust shim failed to initialize (broken/partial
   ``chromadb`` / ``chromadb_rust_bindings`` install, or a rare lifecycle bug).

   **Fix:** ``pip install -U chromadb`` (reinstall so wheels match your OS/arch). Avoid two
   processes migrating the same DB concurrently.

3. **Path shown as ``.../.symbol_graph/embeddings``**
   That is the intended persist directory (not the repo root). Failures are about that store,
   not the project root string itself.

Telemetry is disabled in ``Settings(anonymized_telemetry=False)`` for local dev stores.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def try_persistent_chroma_client(embeddings_dir: str | Path) -> Any | None:
    """Return a Chroma persistent client for ``embeddings_dir``, or None on failure (logged)."""
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as e:
        logger.debug("[chroma] import failed: %s", e)
        return None

    path = Path(embeddings_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    key = str(path)

    try:
        # PersistentClient sets persist_directory + is_persistent; we only tune telemetry.
        settings = Settings(anonymized_telemetry=False)
        return chromadb.PersistentClient(path=key, settings=settings)
    except BaseException as e:  # noqa: BLE001 - match vector_retriever: rust may not subclass Exception
        _log_chroma_init_failure(e, key)
        return None


def _log_chroma_init_failure(exc: BaseException, persist_path: str) -> None:
    msg_l = str(exc).lower()
    base = (
        f"pip install -U 'chromadb>=1.5.0,<2' sentence-transformers; "
        f"if errors persist, delete the store and re-index: rm -rf {persist_path!r}"
    )
    if "tenant" in msg_l or "default_tenant" in msg_l:
        reason = "tenant/sysdb (often legacy or incompatible Chroma data on disk)"
        extra = " or run: pip install chroma-migrate && chroma-migrate"
    elif "bindings" in msg_l or "rustbindingsapi" in msg_l:
        reason = "Rust bindings failed to attach (skewed/broken chromadb install)"
        extra = "; reinstall chromadb so chromadb_rust_bindings matches your platform"
    else:
        reason = type(exc).__name__
        extra = ""
    logger.warning(
        "[chroma] PersistentClient failed (%s): %s",
        reason,
        exc,
    )
    logger.warning("[chroma] Remediation: %s%s", base, extra)
