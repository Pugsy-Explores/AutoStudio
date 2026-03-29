# Retrieval Daemon — Guided Implementation Plan

> Author: Staff Engineer review  
> Date: 2026-03-28  
> Source: `docs/retrieval_daemon_memory_audit.md`  
> Objective: Reduce RSS from ~10 GB → ≤ 3.5 GB. Fix N+1 HTTP loops. Prevent duplicate processes.

---

## How to Use This Document

Work through phases **in order**. Do not skip to Phase 4 before Phase 2 is done.
Each step has:
- **What** — the change
- **Where** — the exact file(s)
- **Why** — the reason from the audit
- **Verify** — how to confirm it worked before moving on

Mark steps `[DONE]` as you complete them. Every step is independently shippable.

---

## Phase 1 — Instrumentation

> **Goal**: See exactly what is happening before touching production logic.
> Do this first. It costs nothing and gives you data to validate every later fix.

---

### Step 1.1 — Add daemon identity log

**Where**: `scripts/retrieval_daemon.py` — top of `main()`, before anything else.

**What**: Log the process identity every time the daemon starts.

```python
# Add at the very top of main(), line ~307, before the argparse block
logger.info(
    "[DAEMON] starting pid=%s ppid=%s cwd=%s",
    os.getpid(), os.getppid(), os.getcwd(),
)
```

**Why**: If multiple processes are running, this will show multiple lines with different PIDs in the log.

**Verify**:
```bash
# Start daemon twice in quick succession
python scripts/retrieval_daemon.py &
python scripts/retrieval_daemon.py &
grep '\[DAEMON\] starting' logs/daemon.log
# Should see ONLY ONE unique pid= value if single-instance is working
# Currently you will see two — this is the bug
```

---

### Step 1.2 — Add memory checkpoints

**Where**: `scripts/retrieval_daemon.py` — inside `load_models()` startup event.

**What**: Add a helper and structured `[MEMORY]` logs at each subsystem load.

```python
# Add helper near the top of the file, after imports
def _log_rss(stage: str) -> None:
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
            rss = -1
    logger.info(
        "[MEMORY] stage=%s rss_mb=%s pid=%s",
        stage, rss, os.getpid(),
    )
```

Then inside `load_models()`, add calls:

```python
@app.on_event("startup")
def load_models():
    _log_rss("process_start")

    # ... existing reranker load code ...
    if _reranker:
        _reranker.rerank("warmup query", ["warmup snippet"])
        logger.info("[retrieval_daemon] reranker loaded and warmed")
    _log_rss("after_reranker_load")      # ADD THIS

    # ... existing embedding load code ...
    _log_rss("after_embedding_load")     # ADD THIS

    # ... existing warmup code ...
    _log_rss("after_all_subsystems")     # ADD THIS
```

Also add at the top of each endpoint handler — **request correlation**: generate `request_id = uuid4().hex[:8]` per request and include it in every `[REQUEST]` line so one call can be traced across logs.

```python
from uuid import uuid4

@app.post("/embed")
def embed(embed_body: EmbedRequest):
    request_id = uuid4().hex[:8]
    logger.info("[REQUEST] id=%s endpoint=/embed texts=%s pid=%s",
                request_id, len(embed_body.texts), os.getpid())
    # ... rest unchanged

@app.post("/rerank")
def rerank(req: RerankRequest):
    request_id = uuid4().hex[:8]
    logger.info("[REQUEST] id=%s endpoint=/rerank docs=%s pid=%s",
                request_id, len(req.docs), os.getpid())
    # ... rest unchanged

@app.post("/retrieve/vector")
def retrieve_vector(req: ProjectQueryRequest):
    request_id = uuid4().hex[:8]
    logger.info("[REQUEST] id=%s endpoint=/retrieve/vector pid=%s",
                request_id, os.getpid())
    # ... rest unchanged

@app.post("/retrieve/bm25")
def retrieve_bm25(req: ProjectQueryRequest):
    request_id = uuid4().hex[:8]
    logger.info("[REQUEST] id=%s endpoint=/retrieve/bm25 pid=%s", request_id, os.getpid())
    # ... rest unchanged

@app.post("/retrieve/repo_map")
def retrieve_repo_map(req: RepoMapRequest):
    request_id = uuid4().hex[:8]
    logger.info("[REQUEST] id=%s endpoint=/retrieve/repo_map pid=%s", request_id, os.getpid())
    # ... rest unchanged
```

`POST /retrieve/vector/batch` is added in Step 4.1 — use the same `request_id` pattern there (see Step 4.1).

**Why**: Pinpoints exactly which subsystem costs the most memory, and confirms whether two processes are handling requests simultaneously. `id=` lets you correlate one HTTP request across multiple log lines.

**Verify**:
```bash
grep '\[MEMORY\]' logs/daemon.log
# Expected output example:
# [MEMORY] stage=process_start rss_mb=180 pid=12345
# [MEMORY] stage=after_reranker_load rss_mb=620 pid=12345
# [MEMORY] stage=after_embedding_load rss_mb=740 pid=12345
# [MEMORY] stage=after_all_subsystems rss_mb=2650 pid=12345

grep '\[REQUEST\] id=' logs/daemon.log | head -3
# Each request line should include id=<8 hex chars> for correlation
```

---

### Step 1.3 — Add subsystem init detail logs

**Where**: `scripts/retrieval_daemon.py` — inside `_warmup_subsystems()`.

**What**: Replace generic warmup logs with structured `[INIT]` lines.

```python
def _warmup_subsystems(project_root: str | None, embedding_model) -> dict:
    out: dict[str, bool | str] = {}
    if not project_root:
        out["skipped"] = True
        out["reason"] = "no project_root"
        return out
    pr = str(Path(project_root).resolve())
    os.environ["SERENA_PROJECT_DIR"] = pr

    try:
        logger.info("[INIT] loading bm25 project_root=%s", pr)        # ADD
        from agent.retrieval.bm25_retriever import build_bm25_index
        index = build_bm25_index(pr)
        out["bm25"] = bool(index)
        if index:                                                       # ADD block
            corpus_size = getattr(index, 'corpus_size', '?')
            logger.info("[INIT] bm25 loaded corpus_size=%s", corpus_size)
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup bm25: %s", e)
        out["bm25"] = False

    try:
        logger.info("[INIT] loading repo_map project_root=%s", pr)    # ADD
        from agent.retrieval.repo_map_lookup import load_repo_map
        rm = load_repo_map(pr)
        out["repo_map"] = rm is not None
        if rm is not None:                                             # ADD block
            logger.info("[INIT] repo_map loaded ok")
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup repo_map: %s", e)
        out["repo_map"] = False

    try:
        if embedding_model is not None:
            logger.info("[INIT] loading chroma project_root=%s", pr)  # ADD
            from agent.retrieval.vector_retriever import vector_search_with_embedder

            def _emb(q: str):
                return embedding_model.encode(q).tolist()

            vector_search_with_embedder("warmup", pr, 1, _emb)
            # Log vector count                                         # ADD block
            try:
                from agent.retrieval.vector_retriever import _get_client, COLLECTION_NAME
                client = _get_client(pr)
                if client:
                    coll = client.get_collection(COLLECTION_NAME)
                    logger.info("[INIT] chroma loaded vectors=%s", coll.count())
            except Exception:
                pass
            out["vector_chroma"] = True
        else:
            out["vector_chroma"] = False
    except Exception as e:
        logger.warning("[retrieval_daemon] warmup vector/chroma: %s", e)
        out["vector_chroma"] = False

    # ... graph section unchanged, add same [INIT] pattern ...

    logger.info("[INIT COMPLETE] pid=%s project_root=%s result=%s",   # ADD
                os.getpid(), pr, out)
    return out
```

**Verify**:
```bash
grep '\[INIT\]' logs/daemon.log
# [INIT] loading bm25 project_root=/path/to/repo
# [INIT] bm25 loaded corpus_size=1842
# [INIT] loading chroma project_root=/path/to/repo
# [INIT] chroma loaded vectors=14520
# [INIT COMPLETE] pid=12345 ...
```

---

### Step 1.4 — Add worker count to /health

**Where**: `scripts/retrieval_daemon.py` — `health()` endpoint.

**What**: Add a constant field confirming single-worker mode.

```python
@app.get("/health")
def health():
    emb_loaded = _embedding_model is not None
    routing_ok = emb_loaded or _lazy_embedding_enabled
    return {
        "status": "ok",
        "reranker_loaded": _reranker is not None,
        "embedding_loaded": emb_loaded,
        "embedding_lazy": _lazy_embedding_enabled and not emb_loaded,
        "embedding_routing_ok": routing_ok,
        "project_root": daemon_project_root,
        "warmup": dict(warmup_state),
        "pid": os.getpid(),              # ADD
        "uvicorn_workers": 1,            # ADD — constant assertion
    }
```

**Verify**:
```bash
curl -s http://127.0.0.1:9464/health | python -m json.tool | grep pid
```

---

### Step 1.5 — Run instrumentation, collect baseline

**What**: Run the daemon under normal Cursor usage for a session. Extract data.

```bash
# Start daemon
python scripts/retrieval_daemon.py --project-root . > logs/daemon.log 2>&1 &

# After a Cursor session, extract data
grep '\[MEMORY\]\|\[DAEMON\]' logs/daemon.log

# Count unique PIDs that served requests
grep '\[REQUEST\]' logs/daemon.log | grep -oP 'pid=\K[0-9]+' | sort -u | wc -l

# If that count > 1: duplicate process problem confirmed → go to Phase 2
# If count == 1: memory is from subsystems → go to Phase 3
```

**Decision gate**: Do not proceed until you have this data. Phase 2 and Phase 3 both depend on it.

---

## Phase 2 — Single-Instance Enforcement

> **Goal**: Make it physically impossible for two daemon instances to coexist.  
> **Prerequisite**: Phase 1 complete. Baseline data collected.

---

### Step 2.1 — Add fcntl flock exclusive lock

**Where**: `scripts/retrieval_daemon.py` — `main()`, before everything else.

**What**: Acquire an OS-level exclusive lock as the first action. Second instance fails immediately.

```python
def main() -> int:
    # ── Step 1: acquire exclusive lock (prevents duplicate instances) ──────────
    _lock_fd = None
    try:
        import fcntl as _fcntl
        _lock_path = Path(
            os.getenv(
                "RETRIEVAL_DAEMON_LOCK_FILE",
                str(_ROOT / "logs" / "retrieval_daemon.lock"),
            )
        )
        _lock_path.parent.mkdir(parents=True, exist_ok=True)
        _lock_fd = open(_lock_path, "w")
        _fcntl.flock(_lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        logger.info("[DAEMON] flock acquired lock_file=%s pid=%s", _lock_path, os.getpid())
    except BlockingIOError:
        logger.info("[DAEMON] already running (flock held) — exiting cleanly")
        return 0
    except ImportError:
        logger.warning("[DAEMON] fcntl not available — relying on HTTP health check only")

    logger.info(
        "[DAEMON] starting pid=%s ppid=%s cwd=%s",
        os.getpid(), os.getppid(), os.getcwd(),
    )
    # ── End lock block ──────────────────────────────────────────────────────────

    parser = argparse.ArgumentParser(...)
    # ... rest of main() unchanged
```

**Why**: `fcntl.LOCK_EX | fcntl.LOCK_NB` is atomic at the kernel level. Unlike the PID file + HTTP check, there is no window between the check and the lock acquisition. The OS releases the lock automatically when the process dies, so stale locks are impossible.

**Verify**:
```bash
# Start daemon
python scripts/retrieval_daemon.py --project-root . &
FIRST_PID=$!

# Try to start a second instance
python scripts/retrieval_daemon.py --project-root . &
SECOND_PID=$!
sleep 1

# Check: second should have exited cleanly
ps -p $SECOND_PID > /dev/null 2>&1 && echo "FAIL: second still running" || echo "PASS: second exited"
grep 'already running (flock held)' logs/daemon.log && echo "PASS: flock worked"

kill $FIRST_PID
```

---

### Step 2.2 — Update _is_daemon_running() to use flock probe

**Where**: `scripts/retrieval_daemon.py` — `_is_daemon_running()` function.

**What**: Replace the PID file check with a flock probe. The HTTP health check stays as a secondary fallback.

```python
def _is_daemon_running(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if retrieval daemon is already running."""
    # Primary: flock probe — try to acquire the same lock; if it fails, daemon is running
    try:
        import fcntl as _fcntl
        _lock_path = Path(
            os.getenv(
                "RETRIEVAL_DAEMON_LOCK_FILE",
                str(_ROOT / "logs" / "retrieval_daemon.lock"),
            )
        )
        if _lock_path.exists():
            with open(_lock_path, "r+") as f:
                try:
                    _fcntl.flock(f, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    # Lock acquired → nobody holds it → not running
                    _fcntl.flock(f, _fcntl.LOCK_UN)
                    return False
                except BlockingIOError:
                    return True  # Someone holds the lock → daemon is running
    except ImportError:
        pass

    # Fallback: HTTP health check (non-POSIX systems, or lock file doesn't exist yet)
    try:
        import urllib.request
        req = urllib.request.Request(f"http://{host}:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False
```

**Why**: Removes the TOCTOU window entirely. PID file was never atomically linked to process liveness.

**Verify**:
```bash
# With daemon running:
python -c "
import sys; sys.path.insert(0, '.')
from scripts.retrieval_daemon import _is_daemon_running
print('Running:', _is_daemon_running(9464))  # should print True
"
```

---

### Step 2.3 — Remove _write_pid / _remove_pid as control logic

**Where**: `scripts/retrieval_daemon.py`

**What**: The flock file body now stores the PID for `--stop`. The old PID file functions can stay as dead code or be deleted.

1. Remove `PID_FILE` usage from `_is_daemon_running()` (done in Step 2.2).
2. Remove `_write_pid()` call from the `--daemon` path (we no longer daemonize after Step 2.4).
3. Remove `atexit.register(_remove_pid)`.
4. Keep `_stop_daemon()` but update it to read PID from the lock file body:

```python
def _stop_daemon() -> int:
    """Stop the daemon by sending SIGTERM to PID in lock file."""
    lock_path = Path(
        os.getenv(
            "RETRIEVAL_DAEMON_LOCK_FILE",
            str(_ROOT / "logs" / "retrieval_daemon.lock"),
        )
    )
    if not lock_path.exists():
        logger.error("No lock file at %s — daemon not running?", lock_path)
        return 1
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 15)  # SIGTERM
        logger.info("Sent SIGTERM to PID %s", pid)
        return 0
    except (ProcessLookupError, ValueError) as e:
        logger.info("Process already gone: %s", e)
        return 0
    except OSError as e:
        logger.error("Failed to stop: %s", e)
        return 1
```

**Verify**:
```bash
python scripts/retrieval_daemon.py --project-root . > logs/daemon.log 2>&1 &
sleep 3
python scripts/retrieval_daemon.py --stop
# Should print: Sent SIGTERM to PID <n>
```

---

### Step 2.4 — Deprecate --daemon / remove _daemonize()

**Where**: `scripts/retrieval_daemon.py`

**What**: The double-fork daemonization is the origin of the TOCTOU bug chain. Remove it.

```python
# In the argparse section, keep --daemon but handle it:
parser.add_argument("--daemon", action="store_true", help="[DEPRECATED] use nohup instead")

# In main(), after args = parser.parse_args():
if args.daemon:
    import warnings
    warnings.warn(
        "--daemon is deprecated. Use: nohup python scripts/retrieval_daemon.py > logs/daemon.log 2>&1 &",
        DeprecationWarning,
        stacklevel=2,
    )
    logger.warning(
        "[DAEMON] --daemon flag is deprecated. "
        "Run with nohup or a process supervisor instead. Continuing in foreground."
    )
    # Do NOT call _daemonize() — just continue in foreground
```

Also create `scripts/start_retrieval_daemon.sh`:
```bash
#!/usr/bin/env bash
# Replacement for --daemon flag. Runs the daemon in background with logging.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs"
nohup python "$ROOT/scripts/retrieval_daemon.py" "$@" \
    > "$ROOT/logs/daemon.log" 2>&1 &
echo "Retrieval daemon started (PID $!). Logs: $ROOT/logs/daemon.log"
```

**Why**: Removes the double-fork entirely. Startup failures are now visible in logs. The lock file approach (Step 2.1) handles all duplicate prevention.

**Verify**:
```bash
chmod +x scripts/start_retrieval_daemon.sh
./scripts/start_retrieval_daemon.sh --project-root .
sleep 3
pgrep -f retrieval_daemon | wc -l   # must be 1
tail logs/daemon.log                # must show startup logs, not silent
```

---

### Step 2.5 — Explicit `reload=False` on uvicorn

**Where**: `scripts/retrieval_daemon.py` — bottom of `main()`, where `uvicorn_run(...)` is called.

**What**: Pass `reload=False` explicitly and log it. Uvicorn reload mode can spawn a watcher/child process that does **not** hold the flock from Step 2.1, silently defeating single-instance enforcement.

```python
logger.info("[DAEMON] uvicorn reload disabled (single-instance enforcement)")
uvicorn_run(app, host=host, port=port, log_level="info", reload=False)
```

**Why**: If reload were ever enabled (env, future default change, or misconfiguration), the lock would be held by a parent while the serving process would be a child without the lock — duplicate instances and confusing RSS.

**Verify**:
```bash
grep 'uvicorn reload disabled' logs/daemon.log
ps aux | grep retrieval_daemon
# confirm no --reload in process args
```

---

### Phase 2 Checkpoint

Before moving on, confirm all five things are true:

```bash
# 1. Only one process
pgrep -f retrieval_daemon | wc -l
# Expected: 1

# 2. Second start attempt exits cleanly
python scripts/retrieval_daemon.py & sleep 1
pgrep -f retrieval_daemon | wc -l
# Expected: still 1

# 3. --stop works
python scripts/retrieval_daemon.py --stop
# Expected: "Sent SIGTERM to PID <n>"

# 4. Restart works after stop
./scripts/start_retrieval_daemon.sh --project-root .
sleep 3
pgrep -f retrieval_daemon | wc -l
# Expected: 1

# 5. Reload explicitly disabled (see Step 2.5)
grep 'uvicorn reload disabled' logs/daemon.log
```

---

## Phase 3 — Structured Init + Chroma Strict Mode

> **Goal**: Make subsystem loading observable and prevent Chroma double-loading in the agent process.  
> **Prerequisite**: Phase 2 complete. Confirmed single instance.

---

### Step 3.1 — Replace _warmup_subsystems with initialize_subsystems

**Where**: `scripts/retrieval_daemon.py`

**What**: Replace the existing function with one that is bounded, logged, and guarded.

```python
_registered_roots: set[str] = set()


def initialize_subsystems(project_root: str | None, embedding_model) -> dict:
    """Structured eager initialization. Replaces _warmup_subsystems."""
    out: dict[str, bool | str] = {}
    if not project_root:
        out["skipped"] = True
        out["reason"] = "no project_root"
        return out

    pr = str(Path(project_root).resolve())

    # Single-project mode (default): one distinct project_root per daemon process.
    # Opt out with RETRIEVAL_SINGLE_PROJECT=0 for multi-repo workflows (higher memory).
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

    # BM25
    try:
        logger.info("[INIT] loading bm25 project_root=%s", pr)
        from agent.retrieval.bm25_retriever import build_bm25_index
        index = build_bm25_index(pr)
        out["bm25"] = bool(index)
        corpus_size = getattr(index, 'corpus_size', '?') if index else 0
        logger.info("[INIT] bm25 loaded corpus_size=%s", corpus_size)
    except Exception as e:
        logger.warning("[INIT] bm25 failed: %s", e)
        out["bm25"] = False
    _log_rss("after_bm25")

    # Repo map
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

    # Chroma/vector — only if embeddings directory exists
    from config.repo_graph_config import SYMBOL_GRAPH_DIR  # adjust import path if needed
    chroma_path = Path(pr) / ".symbol_graph" / "embeddings"
    if chroma_path.exists() and embedding_model is not None:
        try:
            logger.info("[INIT] loading chroma project_root=%s", pr)
            from agent.retrieval.vector_retriever import (
                _get_client, COLLECTION_NAME, vector_search_with_embedder
            )
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

    # Graph
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

    logger.info("[INIT COMPLETE] pid=%s rss_mb=<see above> result=%s", os.getpid(), out)
    return out
```

Then in `load_models()`, replace the `_warmup_subsystems()` call:

```python
# Replace:
#   w = _warmup_subsystems(daemon_project_root, _embedding_model)
# With:
_eager_load = os.getenv("RETRIEVAL_EAGER_LOAD", "1").lower() in ("1", "true", "yes")
if _eager_load and not _skip_warmup:
    try:
        w = initialize_subsystems(daemon_project_root, _embedding_model)
        warmup_state.update(w)
    except RuntimeError as e:
        logger.error("[DAEMON] subsystem init rejected: %s", e)
        raise  # hard fail — do not start with wrong root
    except Exception as e:
        logger.warning("[DAEMON] subsystem init failed: %s", e)
else:
    logger.info("[DAEMON] eager load skipped (RETRIEVAL_EAGER_LOAD=0 or SKIP_WARMUP=1)")
```

**Verify**:
```bash
python scripts/retrieval_daemon.py --project-root . > logs/daemon.log 2>&1 &
sleep 10
grep '\[INIT\]' logs/daemon.log
# Should show each subsystem load with size data

# Test RETRIEVAL_SINGLE_PROJECT guard (manual, default=1):
# In a Python shell, import and try to register a second distinct root — should raise RuntimeError
# With RETRIEVAL_SINGLE_PROJECT=0, multiple roots are allowed (document memory tradeoff).
```

---

### Step 3.2 — Remove Chroma fallback from agent process

**Where**: `agent/retrieval/vector_retriever.py` — `search_by_embedding()` function.

**What**: When the daemon is the designated vector owner, a transient failure returns an explicit error. It does NOT silently load Chroma in-process.

```python
def search_by_embedding(
    query: str,
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict | None:
    if not query or not query.strip():
        return {"results": [], "query": query}

    from agent.retrieval.daemon_retrieval_client import remote_retrieval_enabled, try_daemon_vector_search

    if remote_retrieval_enabled():
        remote = try_daemon_vector_search(query, project_root, top_k)
        if remote is not None:
            return remote
        # CHANGED: do NOT fall through to local Chroma when daemon is configured.
        # Silently loading Chroma here doubles memory (confirmed: +0.5–1.5 GB).
        logger.warning(
            "[vector_retriever] [CHROMA FALLBACK BLOCKED] "
            "daemon returned None but RETRIEVAL_REMOTE_FIRST=1. "
            "Returning empty results. Check daemon health."
        )
        return {"results": [], "query": query, "error": "retrieval daemon unavailable"}

    # Daemon not configured (RETRIEVAL_REMOTE_FIRST=0): in-process path is valid.
    if not _check_vector_available():
        return None

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    client = _get_client(str(root))
    if not client:
        return None

    use_daemon = daemon_embed_available()
    model = _get_model() if not use_daemon else None
    if not use_daemon and not model:
        return None

    def _embed(q: str) -> list[float] | None:
        if use_daemon:
            emb_list = encode_via_daemon([q])
            return emb_list[0] if emb_list else None
        assert model is not None
        return model.encode(q).tolist()

    return vector_search_with_embedder(query, str(root), top_k, _embed)
```

**Why**: Previously, any transient daemon hiccup loaded Chroma in the agent process alongside the daemon — a silent +1–1.5 GB. Now it fails loudly.

**Verify**:
```bash
# Set daemon up, then temporarily kill it
python scripts/retrieval_daemon.py --project-root . > logs/daemon.log 2>&1 &
sleep 5
kill $(pgrep -f retrieval_daemon)

# Run a vector search from the agent
python -c "
import os; os.environ['RETRIEVAL_REMOTE_FIRST'] = '1'
from agent.retrieval.vector_retriever import search_by_embedding
result = search_by_embedding('test query', '.')
print(result)  # Should show {'results': [], 'error': 'retrieval daemon unavailable'}
               # NOT a valid result set (which would mean Chroma loaded silently)
"
```

---

### Phase 3 Checkpoint

```bash
# Confirm no silent Chroma load in agent
grep '\[CHROMA FALLBACK BLOCKED\]' logs/daemon.log

# Confirm bounded init (single-project violation only fires if wrong root)
grep 'RETRIEVAL_SINGLE_PROJECT' logs/daemon.log  # only fires on violation

# Confirm INIT COMPLETE line shows correct RSS
grep '\[INIT COMPLETE\]' logs/daemon.log
```

---

## Phase 4 — Caller Batching + Batch Endpoint

> **Goal**: Eliminate N+1 HTTP loop in `search_batch()`. 5 vector queries → 1 HTTP call.  
> **Prerequisite**: Phases 1–3 complete. Single daemon confirmed.

---

### Step 4.1 — Add POST /retrieve/vector/batch to daemon

**Where**: `scripts/retrieval_daemon.py` — after the existing `/retrieve/vector` handler.

**What**: Accept a list of queries, embed them all at once, query Chroma once.

```python
class VectorBatchRequest(BaseModel):
    """Batch vector search: multiple queries in one call."""
    queries: list[str] = []
    project_root: Annotated[str, BeforeValidator(_coerce_query)] = ""
    top_k: int = 10

    @classmethod
    def model_validate(cls, obj: object, **kwargs):
        if obj is None:
            obj = {}
        return super().model_validate(obj, **kwargs)


@app.post("/retrieve/vector/batch")
def retrieve_vector_batch(req: VectorBatchRequest):
    """Batch vector search. Embeds all queries in one model.encode() call."""
    request_id = uuid4().hex[:8]
    logger.info(
        "[REQUEST] id=%s endpoint=/retrieve/vector/batch queries=%s pid=%s",
        request_id, len(req.queries), os.getpid(),
    )

    if not _ensure_embedding_model():
        return {"results": [], "error": "embedding model disabled"}

    queries = [q.strip() for q in req.queries if q and q.strip()]
    if not queries:
        return {"results": []}

    # Hard cap to bound Chroma memory allocation
    if len(queries) > 8:
        return {"results": [], "error": "max 8 queries per batch request"}

    top_k = max(1, min(req.top_k, 50))

    try:
        from agent.retrieval.vector_retriever import _get_client, COLLECTION_NAME
        from agent.retrieval.retrieval_expander import normalize_file_path

        root_str = req.project_root or daemon_project_root
        client = _get_client(root_str)
        if not client:
            return {"results": [], "error": "chroma client unavailable"}

        coll = client.get_collection(COLLECTION_NAME)

        # Single model.encode() call for all queries — dedicated boundary for diagnostics
        try:
            embeddings = _embedding_model.encode(queries)
        except Exception as e:
            logger.exception("[DAEMON] batch embed failed queries=%s", len(queries))
            return {"results": [], "error": f"embedding failed: {e}"}
        # embeddings shape: (len(queries), embedding_dim)

        raw = coll.query(
            query_embeddings=embeddings.tolist(),
            n_results=top_k,
        )

        # raw["documents"][i] = hits for queries[i]
        per_query_results = []
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

        return {"results": per_query_results}

    except Exception as e:
        logger.exception("retrieve/vector/batch failed")
        return {"results": [], "error": str(e)}
```

Ensure `from uuid import uuid4` is available in `retrieval_daemon.py` (same as Step 1.2).

**Why (encode boundary)**: The outer `except` catches everything; a failed `encode()` gets a specific log line and `error` payload so operators can distinguish embedding failures from Chroma failures.

**Verify**:
```bash
# Start daemon
python scripts/retrieval_daemon.py --project-root . > logs/daemon.log 2>&1 &
sleep 10

# Test batch endpoint
curl -s -X POST http://127.0.0.1:9464/retrieve/vector/batch \
  -H 'Content-Type: application/json' \
  -d '{"queries": ["find auth function", "database connection"], "project_root": ".", "top_k": 3}' \
  | python -m json.tool

# Should return: {"results": [{"query": "...", "results": [...]}, {"query": "...", "results": [...]}]}
```

---

### Step 4.2 — Add batch client function in daemon_retrieval_client.py

**Where**: `agent/retrieval/daemon_retrieval_client.py`

**What**: Add a thin wrapper for the new batch endpoint.

```python
import time as _time


def try_daemon_vector_search_batch(
    queries: list[str],
    project_root: str | None,
    top_k: int,
) -> list[dict] | None:
    """POST /retrieve/vector/batch. Returns list of {query, results} or None to fall back.

    Each element corresponds to the input query at the same index.
    Returns None if daemon is unavailable or endpoint returns error/404.
    """
    if not remote_retrieval_enabled():
        return None
    if not queries:
        return []
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    data = None
    for attempt in range(2):
        data = _post_json(
            "/retrieve/vector/batch",
            {"queries": queries, "project_root": root, "top_k": top_k},
            timeout=90.0,
        )
        if data:
            break
        if attempt == 0:
            _time.sleep(0.05)   # 50 ms — absorbs transient daemon hiccup
    if not data:
        return None
    if data.get("error"):
        logger.debug("[daemon_retrieval_client] batch vector error: %s", data["error"])
        return None
    results = data.get("results")
    if results is None:
        return None
    return results
```

**Why (retry)**: With Chroma fallback removed in the agent (Step 3.2), a single failed HTTP call yields empty results for the whole batch. Two attempts with 50 ms between them absorb brief daemon/network hiccups at negligible cost vs. the 90 s timeout.

**Verify**:
```bash
python -c "
from agent.retrieval.daemon_retrieval_client import try_daemon_vector_search_batch
results = try_daemon_vector_search_batch(['test query'], '.', 3)
print(type(results), len(results) if results else 0)
"
```

---

### Step 4.3 — Fix search_batch() N+1 in vector_retriever.py

**Where**: `agent/retrieval/vector_retriever.py` — `search_batch()` function.

**What**: Replace the N-call loop with a single batch request, with fallback.

```python
def search_batch(
    queries: list[str],
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict | None]:
    """
    Batch semantic search. Returns list of {results, query} per query.

    Optimization: sends a single POST /retrieve/vector/batch instead of N individual
    /retrieve/vector calls. Falls back to sequential if batch endpoint unavailable.
    """
    if not queries:
        return []

    # Path 1: daemon batch endpoint (1 HTTP call for N queries)
    from agent.retrieval.daemon_retrieval_client import (
        remote_retrieval_enabled,
        try_daemon_vector_search_batch,
    )
    if remote_retrieval_enabled():
        batch_results = try_daemon_vector_search_batch(queries, project_root, top_k)
        if batch_results is not None:
            # Validate shape before trusting the response (partial/malformed daemon reply)
            if not isinstance(batch_results, list) or len(batch_results) != len(queries):
                logger.warning(
                    "[vector_retriever] search_batch: invalid batch response "
                    "shape=%s expected=%s — falling back",
                    type(batch_results).__name__
                    if not isinstance(batch_results, list)
                    else len(batch_results),
                    len(queries),
                )
                # fall through to Path 2 / Path 3
            else:
                out: list[dict | None] = []
                for item in batch_results:
                    if isinstance(item, dict) and "results" in item:
                        out.append(
                            {"results": item["results"], "query": item.get("query", "")}
                        )
                    else:
                        out.append(None)
                return out

    # Path 2: local Chroma + single batch embed call (daemon available for embed only)
    if _check_vector_available() and daemon_embed_available():
        root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
        client = _get_client(str(root))
        if client:
            try:
                coll = client.get_collection(COLLECTION_NAME)
                valid_queries = [q.strip() for q in queries if q and q.strip()]
                if valid_queries:
                    from agent.retrieval.daemon_embed import encode_via_daemon
                    embeddings = encode_via_daemon(valid_queries)   # 1 HTTP call
                    if embeddings:
                        raw = coll.query(query_embeddings=embeddings, n_results=min(top_k, 20))
                        from agent.retrieval.retrieval_expander import normalize_file_path
                        out = []
                        docs_list = raw.get("documents", [])
                        metas_list = raw.get("metadatas", [])
                        for i, q in enumerate(valid_queries):
                            docs = docs_list[i] if i < len(docs_list) else []
                            metas = metas_list[i] if i < len(metas_list) else []
                            hits = []
                            for doc, meta in zip(docs, metas or []):
                                meta = meta or {}
                                path = normalize_file_path(meta.get("path", ""))
                                if path:
                                    hits.append({
                                        "file": path,
                                        "symbol": meta.get("symbol", ""),
                                        "line": meta.get("line", 0),
                                        "snippet": (doc or "")[:500],
                                    })
                            out.append({"results": hits, "query": q})
                        return out
            except Exception as e:
                logger.debug("[vector_retriever] batch local path failed, falling back: %s", e)

    # Path 3: sequential fallback (old behavior — used only when above paths fail)
    logger.debug("[vector_retriever] search_batch using sequential fallback for %d queries", len(queries))
    return [search_by_embedding(q, project_root, top_k) for q in queries]
```

**Why**: Before this change, `search_batch(5 queries)` made 5 HTTP calls serially (~75 ms). After, it makes 1 call (~20 ms). **Shape validation** prevents silently mapping wrong per-query results when the daemon returns a shorter list or a non-list (version skew or partial failure).

**Verify**:
```bash
python -c "
import time
from agent.retrieval.vector_retriever import search_batch

queries = ['auth function', 'database connection', 'error handler', 'config loader', 'test fixture']
t0 = time.time()
results = search_batch(queries, project_root='.')
elapsed = (time.time() - t0) * 1000
print(f'Queries: {len(queries)}, Results: {[len(r[\"results\"]) if r else 0 for r in results]}, Time: {elapsed:.0f}ms')
# Before: ~75ms (5 sequential calls)
# After:  ~20ms (1 batch call)
"
```

---

### Step 4.4 — Parallelize multi_root_fetch.fetch_merged()

**Where**: `agent/retrieval/multi_root_fetch.py` — `fetch_merged()` function.

**What**: Replace the serial root loop with concurrent execution.

```python
def fetch_merged(
    fetch_fn: RowMerger,
    query: str,
    roots: tuple[str, ...],
    top_k: int,
    *,
    max_rows: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Call fetch_fn(query, root, top_k) for each root; dedupe; cap length.

    Multiple roots are fetched concurrently (max 2 threads) instead of serially.
    """
    if len(roots) <= 1:
        r0 = roots[0] if roots else ""
        return fetch_fn(query, r0, top_k)

    merged: list[dict] = []
    warnings: list[str] = []

    # Concurrent fetch — conservative max_workers=2 for single-user system
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(len(roots), 2)) as ex:
        future_to_root = {
            ex.submit(fetch_fn, query, root, top_k): root
            for root in roots
        }
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

    deduped = _dedupe_rows(merged)
    cap = max_rows if max_rows is not None else top_k * max(1, len(roots))
    cap = max(cap, top_k)
    out = deduped[:cap]
    logger.debug(
        "[multi_root_fetch] roots=%d merged_in=%d deduped=%d out=%d",
        len(roots), len(merged), len(deduped), len(out),
    )
    return out, warnings
```

**Why**: With 2 roots and 20 ms per fetch: before = 40 ms serial, after = ~20 ms parallel. Low risk — `max_workers=2` shares process memory, no duplication. **`as_completed(..., timeout=len(roots)*5)`** and **`fut.result(timeout=5)`** prevent one stuck root from blocking the merge indefinitely; **`TimeoutError`** is logged and that root is skipped.

**Verify**:
```bash
python -c "
import time
from agent.retrieval.multi_root_fetch import fetch_merged
from agent.retrieval.bm25_retriever import search_bm25

def bm25_fetch(query, root, top_k):
    rows = search_bm25(query, root, top_k) or []
    return rows, []

t0 = time.time()
rows, warns = fetch_merged(bm25_fetch, 'test query', ('.', '.'), top_k=5)
print(f'Time: {(time.time()-t0)*1000:.0f}ms Rows: {len(rows)}')
"
```

---

### Phase 4 Checkpoint

```bash
# Confirm single HTTP call for 5-query batch
grep '\[REQUEST\] endpoint=/retrieve/vector/batch' logs/daemon.log | head -5
# Should show: queries=5

# Confirm old single-query endpoint still works
curl -s -X POST http://127.0.0.1:9464/retrieve/vector \
  -H 'Content-Type: application/json' \
  -d '{"query": "test", "project_root": ".", "top_k": 3}'
# Should return results
```

---

## Phase 5 — Validation

> **Goal**: Confirm memory and process targets are met. Do not ship without this data.

---

### Step 5.1 — Memory validation

```bash
# Restart daemon fresh
pkill -f retrieval_daemon || true
sleep 2
./scripts/start_retrieval_daemon.sh --project-root .
sleep 15  # let it fully initialize

# Extract memory progression
grep '\[MEMORY\]' logs/daemon.log

# Expected progression (medium repo):
# stage=process_start    rss_mb ~ 180
# stage=after_reranker   rss_mb ~ 600
# stage=after_embedding  rss_mb ~ 720
# stage=after_bm25       rss_mb ~ 1200
# stage=after_chroma     rss_mb ~ 2400
# stage=after_graph      rss_mb ~ 2650

# Confirm within target
grep 'after_all_subsystems\|after_graph' logs/daemon.log | grep -oP 'rss_mb=\K[0-9]+'
# Must be ≤ 3500
```

| Stage | Target | Pass/Fail |
|---|---|---|
| `process_start` | ≤ 300 MB | |
| `after_reranker_load` | ≤ 900 MB | |
| `after_all_subsystems` | ≤ 3500 MB | |
| Steady state | ≤ 3500 MB | |

---

### Step 5.2 — Single instance validation

```bash
# Attempt concurrent starts (simulates Cursor + CLI)
for i in 1 2 3; do
    python scripts/retrieval_daemon.py --project-root . > /dev/null 2>&1 &
    sleep 0.2
done
sleep 3

COUNT=$(pgrep -f retrieval_daemon | wc -l)
echo "Process count: $COUNT"
[ "$COUNT" -eq 1 ] && echo "PASS" || echo "FAIL: $COUNT processes running"
```

---

### Step 5.3 — Batch latency validation

```bash
python -c "
import time, statistics
from agent.retrieval.vector_retriever import search_batch

queries = ['auth function', 'database connection', 'error handler', 'config loader', 'test fixture']
samples = []
for _ in range(5):
    t0 = time.time()
    search_batch(queries, project_root='.')
    samples.append((time.time() - t0) * 1000)

print(f'search_batch(5 queries) — p50={statistics.median(samples):.0f}ms  p99={max(samples):.0f}ms')
# Target: p50 ≤ 30ms (was ~75ms with N+1)
"
```

---

### Step 5.4 — No Chroma double-load validation

```bash
# Kill daemon, attempt vector search, confirm no in-process Chroma load
pkill -f retrieval_daemon

python -c "
import os, tracemalloc
os.environ['RETRIEVAL_REMOTE_FIRST'] = '1'
tracemalloc.start()
from agent.retrieval.vector_retriever import search_by_embedding
result = search_by_embedding('test', '.')
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print('Result:', result.get('error'))         # should be 'retrieval daemon unavailable'
print('Peak memory delta:', peak // 1024 // 1024, 'MB')  # should be tiny (< 50 MB)
# If Chroma loaded, peak would be 300+ MB
"
```

---

### Final Validation Summary

```bash
echo "=== Final Validation ==="
echo ""
echo "1. Process count:"
pgrep -f retrieval_daemon | wc -l
echo "   Expected: 1"
echo ""
echo "2. Steady-state RSS:"
grep 'after_all_subsystems\|after_graph' logs/daemon.log | tail -1
echo "   Expected: rss_mb ≤ 3500"
echo ""
echo "3. CHROMA FALLBACK BLOCKED logs (should be 0 in normal usage):"
grep -c '\[CHROMA FALLBACK BLOCKED\]' logs/daemon.log || echo "0"
echo ""
echo "4. Batch endpoint available:"
curl -s http://127.0.0.1:9464/health | python -m json.tool | grep status
```

---

## Quick Reference — Files Changed

| File | Phase | Change |
|---|---|---|
| `scripts/retrieval_daemon.py` | 1 | `_log_rss()`, `[MEMORY]` + `[REQUEST] id=...` (request correlation) + `[INIT]` logs |
| `scripts/retrieval_daemon.py` | 2 | `fcntl.flock` lock, updated `_is_daemon_running()`, updated `_stop_daemon()` |
| `scripts/retrieval_daemon.py` | 2 | Deprecate `--daemon`, remove `_daemonize()` call |
| `scripts/retrieval_daemon.py` | 2 | `uvicorn_run(..., reload=False)` + log (Step 2.5) |
| `scripts/retrieval_daemon.py` | 3 | Replace `_warmup_subsystems()` with `initialize_subsystems()`, `RETRIEVAL_SINGLE_PROJECT` guard (default on) |
| `scripts/retrieval_daemon.py` | 4 | Add `POST /retrieve/vector/batch` + batch `encode` try/except + `VectorBatchRequest` model |
| `scripts/start_retrieval_daemon.sh` | 2 | New file — nohup wrapper |
| `agent/retrieval/vector_retriever.py` | 3 | Remove Chroma fallback when `remote_retrieval_enabled()` |
| `agent/retrieval/vector_retriever.py` | 4 | Rewrite `search_batch()` + batch response shape validation |
| `agent/retrieval/daemon_retrieval_client.py` | 4 | Add `try_daemon_vector_search_batch()` + 2-attempt retry (50 ms) |
| `agent/retrieval/multi_root_fetch.py` | 4 | Parallelize roots + `as_completed` / `fut.result` timeouts |

**Files NOT changed** (no modifications needed):
- `agent/retrieval/reranker/http_reranker.py` — already correct
- `agent/retrieval/daemon_embed.py` — already accepts list; callers fixed upstream
- `agent/memory/task_index.py` — single-use embed, acceptable
