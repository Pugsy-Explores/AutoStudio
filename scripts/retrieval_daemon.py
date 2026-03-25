#!/usr/bin/env python3
"""Unified retrieval daemon: reranker + embedding + remote retrieval API.

Loads models at startup, optionally warms BM25 / repo_map / Chroma / graph index
for a project root, and serves HTTP endpoints. The agent prefers this daemon
for vector, BM25, and repo_map when RETRIEVAL_REMOTE_FIRST=1 (default), with
in-process fallback if the daemon is down.

Requires: pip install fastapi uvicorn

Usage:
    python scripts/retrieval_daemon.py [--project-root DIR]
    python scripts/retrieval_daemon.py --daemon
    python scripts/retrieval_daemon.py --stop

Endpoints:
    POST /rerank  Body: {"query": "...", "docs": [...]}
    POST /embed   Body: {"texts": [...]}
    POST /retrieve/vector   {"query", "project_root", "top_k"}
    POST /retrieve/bm25      {"query", "project_root", "top_k"}
    POST /retrieve/repo_map {"query", "project_root"}
    GET  /health  reranker_loaded, embedding_loaded, warmup{...}
"""

from __future__ import annotations

# macOS: avoid "single-threaded process forked" crash when daemon loads PyTorch (SentenceTransformer)
# after os.fork(). Must be set before any imports that pull in PyTorch/ObjC.
import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
# This process must not call back into itself via RETRIEVAL_REMOTE_FIRST HTTP.
os.environ.setdefault("RETRIEVAL_SKIP_REMOTE", "1")

import argparse
import atexit
import json
import logging
import sys
from pathlib import Path

# Project root
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.logging_config import configure_logging

try:
    from fastapi import FastAPI
    from pydantic import BaseModel, BeforeValidator
    from typing import Annotated
    from uvicorn import run as uvicorn_run

    _FASTAPI_AVAILABLE = True

    def _coerce_query(v: object) -> str:
        if v is None:
            return ""
        return str(v) if not isinstance(v, str) else v

    def _coerce_docs(v: object) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [_coerce_query(d) for d in v]

    def _coerce_texts(v: object) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [_coerce_query(t) for t in v]

    class RerankRequest(BaseModel):
        """Rerank request. {"query": "...", "docs": [...]}"""
        query: Annotated[str, BeforeValidator(_coerce_query)] = ""
        docs: Annotated[list[str], BeforeValidator(_coerce_docs)] = []

        @classmethod
        def model_validate(cls, obj: object, **kwargs):
            if obj is None:
                obj = {}
            return super().model_validate(obj, **kwargs)

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

except ImportError:
    _FASTAPI_AVAILABLE = False
    ProjectQueryRequest = None  # type: ignore[misc, assignment]
    RepoMapRequest = None  # type: ignore[misc, assignment]

configure_logging(
    level=logging.INFO,
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PID_FILE = _ROOT / "logs" / "retrieval_daemon.pid"


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


def _warmup_subsystems(project_root: str | None, embedding_model) -> dict:
    """Best-effort: BM25 corpus, repo_map, one Chroma query, graph SQLite open."""
    out: dict[str, bool | str] = {}
    if not project_root:
        out["skipped"] = True
        out["reason"] = "no project_root"
        return out
    pr = str(Path(project_root).resolve())
    os.environ["SERENA_PROJECT_DIR"] = pr

    try:
        from agent.retrieval.bm25_retriever import build_bm25_index

        out["bm25"] = bool(build_bm25_index(pr))
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup bm25: %s", e)
        out["bm25"] = False

    try:
        from agent.retrieval.repo_map_lookup import load_repo_map

        out["repo_map"] = load_repo_map(pr) is not None
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup repo_map: %s", e)
        out["repo_map"] = False

    try:
        if embedding_model is not None:
            from agent.retrieval.vector_retriever import vector_search_with_embedder

            def _emb(q: str):
                return embedding_model.encode(q).tolist()

            vector_search_with_embedder("warmup", pr, 1, _emb)
            out["vector_chroma"] = True
        else:
            out["vector_chroma"] = False
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup vector/chroma: %s", e)
        out["vector_chroma"] = False

    try:
        from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
        from repo_graph.graph_storage import GraphStorage

        db = Path(pr) / SYMBOL_GRAPH_DIR / INDEX_SQLITE
        if db.is_file():
            st = GraphStorage(str(db))
            try:
                _ = st.get_all_nodes()
            finally:
                st.close()
            out["graph_index"] = True
        else:
            out["graph_index"] = False
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup graph: %s", e)
        out["graph_index"] = False

    logger.info("[retrieval_daemon] warmup complete: %s", out)
    return out


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
    """Return True if retrieval daemon is already running."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError):
            _remove_pid()
        except OSError:
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
    """Stop the daemon by sending SIGTERM to PID in PID_FILE."""
    if not PID_FILE.exists():
        logger.error("No PID file at %s — daemon not running?", PID_FILE)
        return 1
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 15)
        _remove_pid()
        logger.info("Sent SIGTERM to PID %s", pid)
        return 0
    except (ProcessLookupError, ValueError) as e:
        _remove_pid()
        logger.info("Process already gone: %s", e)
        return 0
    except OSError as e:
        logger.error("Failed to stop: %s", e)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval daemon (reranker + embedding)")
    from config.retrieval_config import RETRIEVAL_DAEMON_PORT

    parser.add_argument("--daemon", action="store_true", help="Run in background")
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

    _preflight_checks()

    port = args.port if args.port is not None else RETRIEVAL_DAEMON_PORT
    host = args.host

    daemon_project_root = str(
        Path(args.project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    )
    logger.info("[retrieval_daemon] project root for warmup: %s", daemon_project_root)

    if _is_daemon_running(port, host):
        logger.info("Retrieval daemon already running on %s:%s — not starting", host, port)
        return 0

    if args.daemon:
        _daemonize()
        _write_pid(os.getpid())
        atexit.register(_remove_pid)
        logger.info("Retrieval daemon started (PID %s)", os.getpid())

    if not _FASTAPI_AVAILABLE:
        logger.error("fastapi/uvicorn required: pip install fastapi uvicorn")
        return 1

    app = FastAPI(title="AutoStudio Retrieval Daemon")

    _reranker = None
    _embedding_model = None
    warmup_state: dict = {}

    @app.on_event("startup")
    def load_models():
        nonlocal _reranker, _embedding_model

        # 1. Load and warm reranker
        try:
            from agent.retrieval.reranker.reranker_factory import create_reranker, init_reranker

            init_reranker()
            _reranker = create_reranker()
            if _reranker:
                _reranker.rerank("warmup query", ["warmup snippet"])
                logger.info("[retrieval_daemon] reranker loaded and warmed")
            else:
                logger.warning("[retrieval_daemon] reranker disabled (model load failed)")
        except Exception as e:
            logger.warning("[retrieval_daemon] reranker init failed: %s", e)

        # 2. Load and warm embedding model
        try:
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            _ = _embedding_model.encode(["warmup text"])
            logger.info("[retrieval_daemon] embedding model loaded and warmed")
        except ImportError:
            logger.warning("[retrieval_daemon] sentence_transformers not installed; /embed disabled")
        except Exception as e:
            logger.warning("[retrieval_daemon] embedding init failed: %s", e)

        # 3. Warm BM25, repo_map, Chroma, graph for project root
        try:
            w = _warmup_subsystems(daemon_project_root, _embedding_model)
            warmup_state.clear()
            warmup_state.update(w)
        except Exception as e:
            logger.warning("[retrieval_daemon] subsystem warmup failed: %s", e)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "reranker_loaded": _reranker is not None,
            "embedding_loaded": _embedding_model is not None,
            "project_root": daemon_project_root,
            "warmup": dict(warmup_state),
        }

    @app.post("/rerank")
    def rerank(req: RerankRequest):
        if _reranker is None:
            return {"results": [(d, 0.0) for d in req.docs], "error": "reranker disabled"}
        try:
            results = _reranker.rerank(req.query, req.docs)
            return {"results": results}
        except Exception as e:
            logger.exception("Rerank failed")
            return {"results": [], "error": str(e)}

    @app.post("/embed")
    def embed(embed_body: EmbedRequest):
        if _embedding_model is None:
            return {"embeddings": [], "error": "embedding model disabled"}
        try:
            e = _embedding_model.encode(embed_body.texts)
            if hasattr(e, "ndim") and e.ndim == 1:
                e = e.reshape(1, -1)
            return {"embeddings": e.tolist()}
        except Exception as e:
            logger.exception("Embed failed")
            return {"embeddings": [], "error": str(e)}

    @app.post("/retrieve/vector")
    def retrieve_vector(req: ProjectQueryRequest):
        if _embedding_model is None:
            return {"results": [], "query": req.query, "error": "embedding model disabled"}
        try:
            from agent.retrieval.vector_retriever import vector_search_with_embedder

            def _emb(q: str):
                return _embedding_model.encode(q).tolist()

            out = vector_search_with_embedder(
                req.query,
                req.project_root or None,
                max(1, min(req.top_k, 50)),
                _emb,
            )
            if out is None:
                return {"results": [], "query": req.query, "error": "vector search unavailable"}
            return out
        except Exception as e:
            logger.exception("retrieve/vector failed")
            return {"results": [], "query": req.query, "error": str(e)}

    @app.post("/retrieve/bm25")
    def retrieve_bm25(req: ProjectQueryRequest):
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
        print("  POST /rerank  {query, docs}")
        print("  POST /embed   {texts}")
        print("  POST /retrieve/vector   {query, project_root, top_k}")
        print("  POST /retrieve/bm25     {query, project_root, top_k}")
        print("  POST /retrieve/repo_map {query, project_root}")
        print("  GET  /health")
        print("Ctrl+C to stop")

    uvicorn_run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
