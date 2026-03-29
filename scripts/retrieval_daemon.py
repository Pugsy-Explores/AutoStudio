#!/usr/bin/env python3
"""Retrieval daemon: embedding + retrieval API (BM25, vector, repo_map, batch rerank).

Cross-encoder reranking: ``POST /rerank/batch`` runs ``MiniLMReranker.rerank_batch`` (batched ONNX) in-process.
Agents may use in-process MiniLM instead (``RETRIEVAL_REMOTE_RERANK_FIRST=0``).

Requires: pip install fastapi uvicorn

Usage:
    python scripts/retrieval_daemon.py [--project-root DIR]
    python scripts/retrieval_daemon.py --daemon
    python scripts/retrieval_daemon.py --stop

Env (memory):
    RETRIEVAL_DAEMON_LAZY_EMBEDDING=1  Defer SentenceTransformer until first /embed or /retrieve/vector (default ON).
    RETRIEVAL_DAEMON_LOCK_FILE=path  Override path for single-instance flock (default: logs/retrieval_daemon.lock).
    RETRIEVAL_SINGLE_PROJECT=1  Only one project_root per process (default ON); set 0 for multi-repo.
    RETRIEVAL_EAGER_LOAD=1  Eager subsystem init at startup (default ON).

Endpoints:
    POST /embed   Body: {"texts": [...]}
    POST /retrieve/vector   {"query", "project_root", "top_k"}
    POST /retrieve/vector/batch  {"queries", "project_root", "top_k"}
    POST /retrieve/bm25      {"query", "project_root", "top_k"}
    POST /retrieve/repo_map {"query", "project_root"}
    POST /rerank/batch  {"requests": [{"query", "docs"}, ...]}
    GET  /health  embedding_loaded, reranker_loaded, warmup{...}
"""

from __future__ import annotations

# macOS: avoid "single-threaded process forked" crash when daemon loads PyTorch (SentenceTransformer)
# after os.fork(). Must be set before any imports that pull in PyTorch/ObjC.
import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
# Hugging Face tokenizers: avoid deadlock / warnings when the process later forks (e.g. --daemon).
# Must be set before any import that loads tokenizers.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# This process must not call back into itself via RETRIEVAL_REMOTE_FIRST HTTP.
os.environ.setdefault("RETRIEVAL_SKIP_REMOTE", "1")

import argparse
import atexit
import json
import logging
import sys
import threading
import warnings
from pathlib import Path
from uuid import uuid4

# Project root
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.logging_config import configure_logging
from config.retrieval_config import (
    RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS,
    RETRIEVAL_DAEMON_VECTOR_BATCH_MAX,
)

try:
    from fastapi import FastAPI
    from pydantic import BaseModel, BeforeValidator, Field
    from typing import Annotated
    from uvicorn import run as uvicorn_run

    _FASTAPI_AVAILABLE = True

    def _coerce_query(v: object) -> str:
        if v is None:
            return ""
        return str(v) if not isinstance(v, str) else v

    def _coerce_texts(v: object) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [_coerce_query(t) for t in v]

    class EmbedRequest(BaseModel):
        """Embed request. {"texts": [...]}"""
        texts: Annotated[list[str], BeforeValidator(_coerce_texts)] = []

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

    class ProjectQueryRequest(BaseModel):
        """Vector/BM25: query + workspace root."""
        query: Annotated[str, BeforeValidator(_coerce_query)] = ""
        project_root: Annotated[str, BeforeValidator(_coerce_query)] = ""
        top_k: int = 10

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

    class RepoMapRequest(BaseModel):
        """Repo map lookup."""
        query: Annotated[str, BeforeValidator(_coerce_query)] = ""
        project_root: Annotated[str, BeforeValidator(_coerce_query)] = ""

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

    class VectorBatchRequest(BaseModel):
        """Batch vector search: multiple queries in one call."""
        queries: Annotated[list[str], BeforeValidator(_coerce_texts)] = []
        project_root: Annotated[str, BeforeValidator(_coerce_query)] = ""
        top_k: int = 10

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

    class RerankSlot(BaseModel):
        """One query + candidate doc strings for cross-encoder scoring."""

        query: str = ""
        docs: list[str] = Field(default_factory=list)

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

    class RerankBatchRequest(BaseModel):
        """Batch rerank: multiple (query, docs) groups — one ``rerank_batch`` ONNX pass."""

        requests: list[RerankSlot] = Field(default_factory=list)

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

except ImportError:
    _FASTAPI_AVAILABLE = False
    ProjectQueryRequest = None  # type: ignore[misc, assignment]
    RepoMapRequest = None  # type: ignore[misc, assignment]
    VectorBatchRequest = None  # type: ignore[misc, assignment]
    RerankBatchRequest = None  # type: ignore[misc, assignment]

configure_logging(
    level=logging.INFO,
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PID_FILE = _ROOT / "logs" / "retrieval_daemon.pid"

_registered_roots: set[str] = set()


def _daemon_lock_path() -> Path:
    return Path(os.getenv("RETRIEVAL_DAEMON_LOCK_FILE", str(_ROOT / "logs" / "retrieval_daemon.lock")))


def _log_rss(stage: str) -> None:
    rss = -1
    try:
        import psutil

        rss = psutil.Process().memory_info().rss // (1024 * 1024)
    except ImportError:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) // 1024
                        break
        except Exception:
            pass
    logger.info("[MEMORY] stage=%s rss_mb=%s pid=%s", stage, rss, os.getpid())


def initialize_subsystems(project_root: str | None, embedding_model) -> dict:
    """Structured eager initialization (replaces ad-hoc warmup)."""
    out: dict[str, bool | str] = {}
    if not project_root:
        out["skipped"] = True
        out["reason"] = "no project_root"
        return out

    pr = str(Path(project_root).resolve())

    _SINGLE_PROJECT = os.getenv("RETRIEVAL_SINGLE_PROJECT", "1").lower() in ("1", "true", "yes")
    if _SINGLE_PROJECT and _registered_roots and pr not in _registered_roots:
        raise RuntimeError(
            f"[DAEMON] RETRIEVAL_SINGLE_PROJECT=1: only one project_root allowed. "
            f"Already loaded: {list(_registered_roots)}. Attempted: {pr}. "
            f"Set RETRIEVAL_SINGLE_PROJECT=0 to allow multiple roots."
        )
    _registered_roots.add(pr)
    os.environ["SERENA_PROJECT_DIR"] = pr

    _log_rss("init_start")

    try:
        logger.info("[INIT] loading bm25 project_root=%s", pr)
        from agent.retrieval.bm25_retriever import build_bm25_index

        index = build_bm25_index(pr)
        out["bm25"] = bool(index)
        corpus_size = getattr(index, "corpus_size", "?") if index else 0
        logger.info("[INIT] bm25 loaded corpus_size=%s", corpus_size)
    except Exception as e:
        logger.warning("[INIT] bm25 failed: %s", e)
        out["bm25"] = False
    _log_rss("after_bm25")

    try:
        logger.info("[INIT] loading repo_map project_root=%s", pr)
        from agent.retrieval.repo_map_lookup import load_repo_map

        rm = load_repo_map(pr)
        out["repo_map"] = rm is not None
        logger.info("[INIT] repo_map loaded ok=%s", rm is not None)
    except Exception as e:
        logger.warning("[INIT] repo_map failed: %s", e)
        out["repo_map"] = False
    _log_rss("after_repo_map")

    from agent.retrieval.vector_retriever import COLLECTION_NAME, EMBEDDINGS_DIR, EMBEDDINGS_SUBDIR

    chroma_path = Path(pr) / EMBEDDINGS_DIR / EMBEDDINGS_SUBDIR
    if chroma_path.exists() and embedding_model is not None:
        try:
            logger.info("[INIT] loading chroma project_root=%s", pr)
            from agent.retrieval.vector_retriever import _get_client, vector_search_with_embedder

            def _emb(q: str):
                return embedding_model.encode(q).tolist()

            vector_search_with_embedder("warmup", pr, 1, _emb)
            client = _get_client(pr)
            if client:
                try:
                    coll = client.get_collection(COLLECTION_NAME)
                    logger.info("[INIT] chroma loaded vectors=%s", coll.count())
                except Exception:
                    pass
            out["vector_chroma"] = True
        except Exception as e:
            logger.warning("[INIT] chroma failed: %s", e)
            out["vector_chroma"] = False
    else:
        out["vector_chroma"] = False
        logger.info("[INIT] chroma skipped (no embeddings dir or no embedding model)")
    _log_rss("after_chroma")

    try:
        from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
        from repo_graph.graph_storage import GraphStorage

        db = Path(pr) / SYMBOL_GRAPH_DIR / INDEX_SQLITE
        if db.is_file():
            logger.info("[INIT] loading graph db=%s", db)
            st = GraphStorage(str(db))
            try:
                nodes = st.get_all_nodes()
                logger.info("[INIT] graph loaded nodes=%s", len(nodes))
            finally:
                st.close()
            out["graph_index"] = True
        else:
            out["graph_index"] = False
            logger.info("[INIT] graph skipped (db not found)")
    except Exception as e:
        logger.warning("[INIT] graph failed: %s", e)
        out["graph_index"] = False
    _log_rss("after_graph")

    logger.info("[INIT COMPLETE] pid=%s result=%s", os.getpid(), out)
    return out


def _preflight_checks() -> None:
    """Log Python platform and optional deps before the server binds."""
    import platform

    logger.info(
        "[retrieval_daemon] preflight: Python %s — %s / %s",
        platform.python_version(),
        platform.system(),
        platform.machine(),
    )
    for label, mod in (
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("chromadb", "chromadb"),
        ("sentence_transformers", "sentence_transformers"),
        ("rank_bm25", "rank_bm25"),
    ):
        try:
            __import__(mod)
            logger.info("[retrieval_daemon] preflight: %s import OK", label)
        except ImportError as e:
            logger.warning("[retrieval_daemon] preflight: %s not available (%s)", label, e)


def _daemonize() -> None:
    """Fork and detach from terminal.

    On macOS, fork+PyTorch/ObjC can crash with 'single-threaded process forked'.
    Set OBJC_DISABLE_INITIALIZE_FORK_SAFETY so ObjC can initialize in the child.
    """
    os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    os.chdir(str(_ROOT))
    with open(os.devnull, "r") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    with open(os.devnull, "a") as devnull:
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _remove_pid() -> None:
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass


def _is_daemon_running(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if retrieval daemon is already running (flock probe, then HTTP)."""
    try:
        import fcntl as _fcntl

        lp = _daemon_lock_path()
        if lp.exists():
            with open(lp, "r+") as f:
                try:
                    _fcntl.flock(f, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    _fcntl.flock(f, _fcntl.LOCK_UN)
                    return False
                except BlockingIOError:
                    return True
    except ImportError:
        pass
    except Exception:
        pass

    try:
        import urllib.request

        req = urllib.request.Request(f"http://{host}:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False


def _stop_daemon() -> int:
    """Stop the daemon by sending SIGTERM to PID recorded in the lock file."""
    lp = _daemon_lock_path()
    if not lp.exists():
        logger.error("No lock file at %s — daemon not running?", lp)
        return 1
    try:
        pid = int(lp.read_text().strip())
        os.kill(pid, 15)
        logger.info("Sent SIGTERM to PID %s", pid)
        return 0
    except (ProcessLookupError, ValueError) as e:
        logger.info("Process already gone: %s", e)
        return 0
    except OSError as e:
        logger.error("Failed to stop: %s", e)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval daemon (embedding + remote retrieval)")
    from config.retrieval_config import RETRIEVAL_DAEMON_PORT

    parser.add_argument(
        "--daemon",
        action="store_true",
        help="[DEPRECATED] use nohup or scripts/start_retrieval_daemon.sh",
    )
    parser.add_argument("--stop", action="store_true", help="Stop the daemon")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port (default from RETRIEVAL_DAEMON_PORT={RETRIEVAL_DAEMON_PORT})",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Workspace to warm BM25 / repo_map / Chroma / graph (default: SERENA_PROJECT_DIR or cwd)",
    )
    args = parser.parse_args()

    if args.stop:
        return _stop_daemon()

    port = args.port if args.port is not None else RETRIEVAL_DAEMON_PORT
    host = args.host

    _have_flock = False
    try:
        import fcntl as _fcntl

        lp = _daemon_lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        _lock_fd = open(lp, "w")
        _fcntl.flock(_lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        _have_flock = True
        logger.info("[DAEMON] flock acquired lock_file=%s pid=%s", lp, os.getpid())
    except BlockingIOError:
        logger.info("[DAEMON] already running (flock held) — exiting cleanly")
        return 0
    except ImportError:
        logger.warning("[DAEMON] fcntl not available — relying on HTTP health check only")

    logger.info(
        "[DAEMON] starting pid=%s ppid=%s cwd=%s",
        os.getpid(),
        os.getppid(),
        os.getcwd(),
    )

    daemon_project_root = str(
        Path(args.project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    )
    logger.info("[retrieval_daemon] project root for warmup: %s", daemon_project_root)

    if not _have_flock and _is_daemon_running(port, host):
        logger.info("Retrieval daemon already running on %s:%s — not starting", host, port)
        return 0

    if args.daemon:
        warnings.warn(
            "--daemon is deprecated. Use: nohup python scripts/retrieval_daemon.py > logs/daemon.log 2>&1 &",
            DeprecationWarning,
            stacklevel=1,
        )
        logger.warning(
            "[DAEMON] --daemon is deprecated; run with nohup or scripts/start_retrieval_daemon.sh. "
            "Continuing in foreground."
        )

    _preflight_checks()

    if not _FASTAPI_AVAILABLE:
        logger.error("fastapi/uvicorn required: pip install fastapi uvicorn")
        return 1

    app = FastAPI(title="AutoStudio Retrieval Daemon")

    _embedding_model = None
    warmup_state: dict = {}
    _embed_load_lock = threading.Lock()
    # Defer SentenceTransformer load until first /embed or /retrieve/vector (saves ~0.5–2+ GiB at startup).
    _lazy_embedding_enabled = os.getenv("RETRIEVAL_DAEMON_LAZY_EMBEDDING", "1").lower() in (
        "1",
        "true",
        "yes",
    )
    # Performance audits: RETRIEVAL_DAEMON_SKIP_WARMUP=1 skips BM25/repo_map/Chroma/graph warmup
    _skip_warmup = os.getenv("RETRIEVAL_DAEMON_SKIP_WARMUP", "").lower() in ("1", "true", "yes")

    def _ensure_embedding_model() -> bool:
        """Load MiniLM once on first use when lazy embedding is enabled."""
        nonlocal _embedding_model
        if _embedding_model is not None:
            return True
        if not _lazy_embedding_enabled:
            return False
        with _embed_load_lock:
            if _embedding_model is not None:
                return True
            try:
                from sentence_transformers import SentenceTransformer

                _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
                _ = _embedding_model.encode(["warmup text"])
                logger.info("[retrieval_daemon] lazy embedding model loaded")
                return True
            except ImportError:
                logger.warning("[retrieval_daemon] sentence_transformers not installed; /embed disabled")
            except Exception as e:
                logger.warning("[retrieval_daemon] lazy embedding load failed: %s", e)
            return False

    def _vector_batch_chroma_search(
        queries: list[str],
        project_root: str | None,
        top_k: int,
    ) -> tuple[list[dict] | None, str | None]:
        """Batched ST encode + one Chroma ``query`` call. ``queries`` must be non-empty stripped strings."""
        from agent.retrieval.retrieval_expander import normalize_file_path
        from agent.retrieval.vector_retriever import COLLECTION_NAME, _get_client

        root_str = project_root or daemon_project_root
        client = _get_client(root_str)
        if not client:
            return None, "chroma client unavailable"
        coll = client.get_collection(COLLECTION_NAME)
        try:
            embeddings = _embedding_model.encode(queries)
        except Exception as enc_err:
            logger.exception("[DAEMON] batch embed failed queries=%s", len(queries))
            return None, f"embedding failed: {enc_err}"
        raw = coll.query(
            query_embeddings=embeddings.tolist(),
            n_results=top_k,
        )
        per_query_results: list[dict] = []
        docs_list = raw.get("documents", [])
        metas_list = raw.get("metadatas", [])
        for i, query in enumerate(queries):
            docs = docs_list[i] if i < len(docs_list) else []
            metas = metas_list[i] if i < len(metas_list) else []
            hits = []
            for doc, meta in zip(docs, metas or []):
                meta = meta or {}
                path = normalize_file_path(meta.get("path", ""))
                if not path:
                    continue
                hits.append({
                    "file": path,
                    "symbol": meta.get("symbol", ""),
                    "line": meta.get("line", 0),
                    "snippet": (doc or "")[:500] if isinstance(doc, str) else str(doc)[:500],
                })
            per_query_results.append({"query": query, "results": hits})
        return per_query_results, None

    _reranker_model = None
    _reranker_lock = threading.Lock()

    def _ensure_reranker() -> bool:
        """Lazy-load MiniLM ONNX reranker for ``POST /rerank/batch``."""
        nonlocal _reranker_model
        if _reranker_model is not None:
            return True
        with _reranker_lock:
            if _reranker_model is not None:
                return True
            try:
                from agent.retrieval.reranker.minilm_reranker import MiniLMReranker

                _reranker_model = MiniLMReranker()
                logger.info("[retrieval_daemon] MiniLM reranker loaded for /rerank/batch")
                return True
            except Exception as e:
                logger.exception("[retrieval_daemon] reranker load failed: %s", e)
                return False

    @app.on_event("startup")
    def load_models():
        nonlocal _embedding_model

        _log_rss("process_start")

        # 1. Load and warm embedding model (optional at startup when lazy)
        if not _lazy_embedding_enabled:
            try:
                from sentence_transformers import SentenceTransformer

                _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
                _ = _embedding_model.encode(["warmup text"])
                logger.info("[retrieval_daemon] embedding model loaded and warmed")
            except ImportError:
                logger.warning("[retrieval_daemon] sentence_transformers not installed; /embed disabled")
            except Exception as e:
                logger.warning("[retrieval_daemon] embedding init failed: %s", e)
        else:
            logger.info(
                "[retrieval_daemon] lazy embedding enabled — SentenceTransformer loads on first /embed or /retrieve/vector"
            )
        _log_rss("after_embedding_load")

        # 2. BM25, repo_map, Chroma, graph for project root
        _eager_load = os.getenv("RETRIEVAL_EAGER_LOAD", "1").lower() in ("1", "true", "yes")
        if _skip_warmup:
            logger.info("[retrieval_daemon] RETRIEVAL_DAEMON_SKIP_WARMUP=1 — skipping subsystem warmup")
            warmup_state.clear()
            warmup_state["skipped"] = True
        elif _eager_load:
            try:
                w = initialize_subsystems(daemon_project_root, _embedding_model)
                warmup_state.clear()
                warmup_state.update(w)
            except RuntimeError as e:
                logger.error("[DAEMON] subsystem init rejected: %s", e)
                raise
            except Exception as e:
                logger.warning("[retrieval_daemon] subsystem init failed: %s", e)
        else:
            logger.info("[DAEMON] eager load skipped (RETRIEVAL_EAGER_LOAD=0)")
        _log_rss("after_all_subsystems")

    @app.get("/health")
    def health():
        # embedding_routing_ok: agent may route embed/vector to daemon when True (lazy = will load on demand).
        emb_loaded = _embedding_model is not None
        routing_ok = emb_loaded or _lazy_embedding_enabled
        return {
            "status": "ok",
            "reranker_loaded": _reranker_model is not None,
            "embedding_loaded": emb_loaded,
            "embedding_lazy": _lazy_embedding_enabled and not emb_loaded,
            "embedding_routing_ok": routing_ok,
            "project_root": daemon_project_root,
            "warmup": dict(warmup_state),
            "pid": os.getpid(),
            "uvicorn_workers": 1,
        }

    @app.post("/embed")
    def embed(embed_body: EmbedRequest):
        request_id = uuid4().hex[:8]
        logger.info(
            "[REQUEST] id=%s endpoint=/embed texts=%s pid=%s",
            request_id,
            len(embed_body.texts),
            os.getpid(),
        )
        if not _ensure_embedding_model():
            return {"embeddings": [], "error": "embedding model disabled"}
        try:
            e = _embedding_model.encode(embed_body.texts)
            if hasattr(e, "ndim") and e.ndim == 1:
                e = e.reshape(1, -1)
            return {"embeddings": e.tolist()}
        except Exception as e:
            logger.exception("Embed failed")
            return {"embeddings": [], "error": str(e)}

    @app.post("/rerank/batch")
    def rerank_batch_endpoint(req: RerankBatchRequest):
        request_id = uuid4().hex[:8]
        logger.info(
            "[REQUEST] id=%s endpoint=/rerank/batch slots=%s pid=%s",
            request_id,
            len(req.requests),
            os.getpid(),
        )
        if not req.requests:
            return {"results": []}
        if len(req.requests) > RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS:
            return {
                "results": [],
                "error": (
                    f"max {RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS} (query, docs) slots per batch request"
                ),
            }
        if not _ensure_reranker():
            return {"results": [], "error": "reranker unavailable"}
        batch_reqs = [(s.query, list(s.docs or [])) for s in req.requests]
        try:
            assert _reranker_model is not None
            scored = _reranker_model.rerank_batch(batch_reqs)
        except Exception as e:
            logger.exception("rerank/batch failed")
            return {"results": [], "error": str(e)}
        return {
            "results": [[[d, float(sc)] for d, sc in slot] for slot in scored],
        }

    @app.post("/retrieve/vector")
    def retrieve_vector(req: ProjectQueryRequest):
        request_id = uuid4().hex[:8]
        logger.info("[REQUEST] id=%s endpoint=/retrieve/vector pid=%s", request_id, os.getpid())
        if not _ensure_embedding_model():
            return {"results": [], "query": req.query, "error": "embedding model disabled"}
        q = (req.query or "").strip()
        if not q:
            return {"results": [], "query": req.query}
        top_k = max(1, min(req.top_k, 50))
        try:
            rows, err = _vector_batch_chroma_search([q], req.project_root or None, top_k)
            if err:
                return {"results": [], "query": req.query, "error": err}
            if not rows:
                return {"results": [], "query": req.query, "error": "vector search unavailable"}
            first = rows[0]
            return {"results": first.get("results") or [], "query": first.get("query", req.query)}
        except Exception as e:
            logger.exception("retrieve/vector failed")
            return {"results": [], "query": req.query, "error": str(e)}

    @app.post("/retrieve/vector/batch")
    def retrieve_vector_batch(req: VectorBatchRequest):
        request_id = uuid4().hex[:8]
        logger.info(
            "[REQUEST] id=%s endpoint=/retrieve/vector/batch queries=%s pid=%s",
            request_id,
            len(req.queries),
            os.getpid(),
        )
        if not _ensure_embedding_model():
            return {"results": [], "error": "embedding model disabled"}

        queries = [q.strip() for q in req.queries if q and q.strip()]
        if not queries:
            return {"results": []}
        if len(queries) > RETRIEVAL_DAEMON_VECTOR_BATCH_MAX:
            return {
                "results": [],
                "error": f"max {RETRIEVAL_DAEMON_VECTOR_BATCH_MAX} queries per batch request",
            }

        top_k = max(1, min(req.top_k, 50))

        try:
            per_query_results, err = _vector_batch_chroma_search(
                queries,
                req.project_root or None,
                top_k,
            )
            if err:
                return {"results": [], "error": err}
            return {"results": per_query_results or []}

        except Exception as e:
            logger.exception("retrieve/vector/batch failed")
            return {"results": [], "error": str(e)}

    @app.post("/retrieve/bm25")
    def retrieve_bm25(req: ProjectQueryRequest):
        request_id = uuid4().hex[:8]
        logger.info("[REQUEST] id=%s endpoint=/retrieve/bm25 pid=%s", request_id, os.getpid())
        try:
            from agent.retrieval.bm25_retriever import search_bm25

            rows = search_bm25(
                req.query,
                req.project_root or None,
                top_k=max(1, min(req.top_k, 100)),
            )
            return {"results": rows}
        except Exception as e:
            logger.exception("retrieve/bm25 failed")
            return {"results": [], "error": str(e)}

    @app.post("/retrieve/repo_map")
    def retrieve_repo_map(req: RepoMapRequest):
        request_id = uuid4().hex[:8]
        logger.info("[REQUEST] id=%s endpoint=/retrieve/repo_map pid=%s", request_id, os.getpid())
        try:
            from agent.retrieval.repo_map_lookup import lookup_repo_map

            rows = lookup_repo_map(req.query, req.project_root or None)
            return {"results": rows}
        except Exception as e:
            logger.exception("retrieve/repo_map failed")
            return {"results": [], "error": str(e)}

    if args.daemon:
        logger.info("Listening on %s:%s", host, port)
    else:
        print(f"Retrieval daemon on http://{host}:{port}")
        print("  POST /rerank/batch  {requests: [{query, docs}, ...]}")
        print("  POST /embed   {texts}")
        print("  POST /retrieve/vector   {query, project_root, top_k}")
        print("  POST /retrieve/bm25     {query, project_root, top_k}")
        print("  POST /retrieve/repo_map {query, project_root}")
        print("  POST /retrieve/vector/batch {queries, project_root, top_k}")
        print("  GET  /health")
        print("Ctrl+C to stop")

    logger.info("[DAEMON] uvicorn reload disabled (single-instance enforcement)")
    uvicorn_run(app, host=host, port=port, log_level="info", reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
