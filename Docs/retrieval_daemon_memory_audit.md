# Retrieval Daemon — Memory Audit & Design Report

> Generated: 2026-03-28  
> Revised: 2026-03-28 (staff-engineer review pass)  
> Scope: `scripts/retrieval_daemon.py` + caller-side audit  
> Goal: Reduce observed ~10 GB RSS → ≤ 3–5 GB

---

## Part 1 — Hypothesis Verification (Code Evidence)

### H1 — Multiple Daemon Processes (CONFIRMED: Primary Suspect)

The `_is_daemon_running()` check has a **TOCTOU race condition**:

```python
# main() — scripts/retrieval_daemon.py lines 337–345
if _is_daemon_running(port, host):
    logger.info("Retrieval daemon already running on %s:%s — not starting", host, port)
    return 0

# Fork BEFORE any import that loads Hugging Face tokenizers / PyTorch
if args.daemon:
    _daemonize()
    _write_pid(os.getpid())   # PID written AFTER fork, 100+ ms after check
```

**Three failure modes:**

1. **TOCTOU gap**: The gap between `_is_daemon_running()` returning `False` and
   `_write_pid()` completing is ≥100 ms (two forks). Two concurrent callers
   (Cursor auto-start + user CLI) both pass the check, both daemonize.

2. **Stale PID file + live orphan**: If a prior instance was killed (`kill -9`, OOM),
   `os.kill(pid, 0)` raises `ProcessLookupError` → PID file deleted → check returns
   `False` → new instance starts. HTTP health fallback only detects live ports.

3. **Port variation**: If `RETRIEVAL_DAEMON_PORT` differs between invocations
   (Cursor env vs. test env), the check on port A passes while an instance is already
   bound to a different port B.

**Probability**: Cursor auto-start + user CLI + test suites each calling
`daemon_ensure.py` → **3–4 instances easily explains 8–10 GB** (3 × 2.7 GB = 8.1 GB,
4 × 2.7 GB = 10.8 GB).

---

### H2 — Multiple Uvicorn Workers (DISPROVED)

```python
# scripts/retrieval_daemon.py line 550
uvicorn_run(app, host=host, port=port, log_level="info")
```

No `workers=` argument. Uvicorn defaults to **1 worker**. No `WEB_CONCURRENCY` or
`UVICORN_WORKERS` env variable is read. **Hypothesis ruled out.**

---

### H3 — Fork Causing Memory Duplication (PARTIALLY RELEVANT, NOT PRIMARY)

The double-fork happens **before** `_preflight_checks()` and before `load_models()`
(the `startup` event). Models load in the grandchild after fork, so CoW invalidation
from PyTorch dirtying pages does not multiply memory between parent and grandchild.

On macOS with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`, ObjC runtime initializes in
the child and dirties ~20–50 MB of CoW pages. **Not the 10 GB driver.**

Risk: if an ML import is ever added at module level above the fork, CoW inflation
becomes a real GB-scale problem. The pattern is fragile.

---

### H4 — Vector DB Loading in Both Processes (CONFIRMED: Secondary Contributor)

`vector_retriever.py` maintains a module-level `_chroma_clients` dict.

Normal path (`RETRIEVAL_REMOTE_FIRST=1`):
- `search_by_embedding()` → `try_daemon_vector_search()` → HTTP to daemon.
  Agent process does NOT load Chroma.

Fallback path (daemon transiently unreachable):
```python
# agent/retrieval/vector_retriever.py lines 167–193
if remote_retrieval_enabled():
    remote = try_daemon_vector_search(query, project_root, top_k)
    if remote is not None:
        return remote
# If daemon returns None (error/down):
client = _get_client(str(root))  # opens PersistentChromaClient IN-PROCESS
```

If the daemon is briefly unreachable, the **agent process loads Chroma alongside the
daemon**: daemon ~0.5–1.5 GB + agent ~0.5–1.5 GB = **+1–3 GB extra** with no process
count increase.

---

### H5 — Eager Warmup Loading All Subsystems (CONFIRMED: Startup Memory Amplifier)

```python
# scripts/retrieval_daemon.py _warmup_subsystems()
build_bm25_index(pr)                      # loads all tokenized source files
load_repo_map(pr)                         # loads repo map structure
vector_search_with_embedder("warmup"...)  # opens PersistentChromaClient + query
GraphStorage(str(db)).get_all_nodes()     # loads all graph nodes
```

All four subsystems load **synchronously at startup** unless
`RETRIEVAL_DAEMON_SKIP_WARMUP=1`. Eager loading itself is acceptable — the problem is
it is **uncontrolled**: no per-subsystem logs, no project root cap, no visibility into
what was actually loaded or how much memory it consumed.

---

## Part 2 — Root Cause Conclusion

**Primary cause: Multiple concurrent daemon processes.**

Evidence chain:
- `_is_daemon_running()` has a provable TOCTOU window (no atomic lock)
- Cursor, CLI, and test harness each trigger daemon startup independently
- No OS-level exclusion mechanism exists
- 3 × 2.7 GB = 8.1 GB, 4 × 2.7 GB = 10.8 GB — matches observed range

**Secondary cause: Chroma fallback loading in agent process** when daemon is
transiently unreachable, adding 0.5–1.5 GB to the agent process.

### Memory Per Component (Single Process, Medium Repo)

| Component | Estimated RSS | Notes |
|---|---|---|
| Python + FastAPI + uvicorn | ~150 MB | baseline |
| Reranker (ONNX cross-encoder) | ~300–600 MB | quantized INT8 is lower end |
| SentenceTransformer MiniLM | ~90–120 MB | after lazy load |
| BM25 corpus index | ~200 MB–1 GB | scales with repo file count |
| Chroma PersistentClient | ~300 MB–1.5 GB | scales with indexed vectors |
| Graph SQLite (all_nodes) | ~50–200 MB | scales with symbol count |
| **Total (single process)** | **~1.1–3.6 GB** | aligns with observed 2.6–2.7 GB |

3 processes × 2.7 GB → **8.1 GB** — the 10 GB floor is fully explained.

---

## Part 3 — Design Decisions (Revised)

### Decision 1 — No Queue-Based Batching (ACCEPTED)

Server-side `EmbedBatchQueue` and `RerankBatchQueue` are **removed from the plan**.

Rationale:
- No concurrent multi-tenant traffic; single-user local system
- Queues add latency, complexity, and a new failure mode
- Performance gains come from fixing the caller (N+1 → batch calls), not from a
  server-side collector
- Reranker already batches docs per query — no change needed there

The only server-side addition is `POST /retrieve/vector/batch` (simple, no queue logic).

---

### Decision 2 — Eager Loading: Keep, but Make Explicit and Bounded (REFINED)

Eager loading is the correct choice for a single-user daemon — fast first request,
predictable memory profile. The existing `_warmup_subsystems()` is kept but replaced
with a structured `initialize_subsystems()` that:

1. Logs a `[INIT]` line per subsystem with size/count metadata
2. Enforces `MAX_PROJECT_ROOTS = 1` — hard error if more than one root is registered
3. Only loads the vector index if embeddings exist on disk (prevents silent no-op Chroma open)
4. Emits a final `[INIT COMPLETE]` line with total RSS

**`RETRIEVAL_EAGER_LOAD` flag** (default `1`): opt-out with `RETRIEVAL_EAGER_LOAD=0`
for environments that prefer first-request load (e.g., test suites, CI). This preserves
future flexibility without adding runtime complexity to the default path.

---

### Decision 3 — Remove Chroma Fallback in Agent Process (CRITICAL FIX)

Introduce `RETRIEVAL_MODE=daemon_only` (default when daemon is configured).

In `search_by_embedding()`:
```python
if remote_retrieval_enabled():
    remote = try_daemon_vector_search(query, project_root, top_k)
    if remote is not None:
        return remote
    # REMOVE the local Chroma fallback entirely:
    # client = _get_client(str(root))  ← deleted
    return {"results": [], "query": query, "error": "retrieval daemon unavailable"}
```

The agent **must not silently load Chroma in-process** when the daemon is designated
as the vector store owner. A transient daemon failure is surfaced as an explicit error,
not swallowed by a memory-doubling fallback.

In-process Chroma is only permitted when `RETRIEVAL_REMOTE_FIRST=0` (daemon not in
use), in which case the daemon is not running and there is no duplication.

---

### Decision 4 — Remove Daemonization (STRONG RECOMMENDATION)

`_daemonize()`, double fork, and the `--daemon` flag are removed.

Rationale:
- Local single-user system; no terminal detachment requirement
- Double fork discards stdout/stderr, making startup failures invisible
- The entire class of TOCTOU bugs originates from the fork + delayed PID write sequence
- Any future ML import above the fork creates GB-scale CoW inflation risk

**Replacement**: `nohup python scripts/retrieval_daemon.py > logs/daemon.log 2>&1 &`

`--daemon` flag emits `DeprecationWarning` for one release cycle, then is removed.
`--stop` is kept; it reads PID from the flock file body and sends SIGTERM.

---

### Decision 5 — Single-Instance via `fcntl.flock` (CRITICAL FIX)

**Chosen mechanism: `fcntl.flock` exclusive lock on a dedicated lock file.**

| Mechanism | Pros | Cons |
|---|---|---|
| PID file (current) | Simple | TOCTOU race; stale PIDs; not atomic |
| `fcntl.flock` on lock file | Atomic; OS releases on crash; no stale state | POSIX only (macOS/Linux — acceptable) |
| Port binding lock | Doubles as liveness check | Port not acquirable until uvicorn binds; window still exists |
| Abstract socket | Linux-only | Excludes macOS |

**Design:**

1. First action in `main()`, before any fork: open `logs/retrieval_daemon.lock` and
   attempt `fcntl.LOCK_EX | fcntl.LOCK_NB`.
2. If lock fails (`BlockingIOError`): daemon is running → log and exit 0.
3. If lock succeeds: hold fd open for process lifetime. Write PID into lock file body
   for `--stop` use. `atexit` closes fd (lock releases automatically on process death
   even without atexit).
4. `RETRIEVAL_DAEMON_LOCK_FILE` env override for test isolation — each test process
   uses a distinct lock path.
5. Add startup identity log: `[DAEMON] pid=<pid> ppid=<ppid> cwd=<cwd> port=<port>`

---

### Decision 6 — Fix Caller N+1 (BIGGEST WIN)

Two changes in `vector_retriever.py`:

**Fix 1 — `search_batch()` uses batch embed + batch Chroma query:**
```python
# Before: for q in queries: search_by_embedding(q)   # N HTTP calls
# After:
embeddings = encode_via_daemon(queries)               # 1 HTTP call
results = coll.query(query_embeddings=embeddings, n_results=top_k)
# split results[0], results[1], ... per query
```

**Fix 2 — new `POST /retrieve/vector/batch` endpoint (server):**
```python
# Minimal — no queue, no async complexity
def retrieve_vector_batch(req: VectorBatchRequest):
    embeddings = _embedding_model.encode(req.queries)
    return chroma.query(query_embeddings=embeddings, n_results=req.top_k)
```

`search_batch()` uses the batch endpoint when daemon is available; falls back to
sequential if endpoint returns 404 (old daemon version).

**Fix 3 — parallelize multi-root fetch:**
```python
# multi_root_fetch.py
with ThreadPoolExecutor(max_workers=min(len(roots), 2)) as ex:
    futures = [ex.submit(fetch_fn, query, root, top_k) for root in roots]
    for fut in as_completed(futures):
        rows, warns = fut.result()
        ...
```

---

### Decision 7 — Reranker: Keep As-Is

`HttpRerankerClient._score_pairs()` already groups docs by distinct query and sends
one POST per query with all docs batched. **No change needed.**

The server-side `_rerank_lock` serializes concurrent calls but this is acceptable for
a single-user system. Removing it is deferred to a later pass if contention is
measured.

---

### Decision 8 — Observability (MANDATORY)

Add structured log lines at every meaningful state transition:

**Startup:**
```
[DAEMON] pid=<pid> ppid=<ppid> cwd=<cwd> port=<port>
[INIT] loading bm25 project_root=<root>
[INIT] bm25 loaded files=<n> size_mb=<n>
[INIT] loading chroma project_root=<root>
[INIT] chroma loaded vectors=<n>
[INIT] loading repo_map project_root=<root>
[INIT] repo_map loaded entries=<n>
[INIT] loading graph project_root=<root>
[INIT] graph loaded nodes=<n>
[INIT COMPLETE] rss_mb=<n> pid=<pid>
[MEMORY] stage=<stage> rss_mb=<n> pid=<pid>
```

**Per request:**
```
[REQUEST] endpoint=/embed texts=<n> pid=<pid>
[REQUEST] endpoint=/rerank docs=<n> pid=<pid>
[REQUEST] endpoint=/retrieve/vector pid=<pid>
[REQUEST] endpoint=/retrieve/vector/batch queries=<n> pid=<pid>
[CHROMA] vectors=<n> project_root=<root>
```

**Memory checkpoints** (using `psutil` or `/proc/self/status` fallback):
- `stage=process_start` — before any ML import
- `stage=after_reranker_load`
- `stage=after_embedding_load`
- `stage=after_all_subsystems`
- `stage=first_embed_request`
- `stage=first_vector_request`

Format: `[MEMORY] stage=<stage> rss_mb=<rss> vms_mb=<vms> pid=<pid> ppid=<ppid>`

---

## Part 4 — Implementation Plan

### Phase 1 — Instrumentation (Run First, Fix After Data)

**Goal**: Measure actual memory per stage, confirm duplicate-process hypothesis.

1. Add `[DAEMON]` identity log at top of `main()`.
2. Add `[MEMORY]` checkpoints at each subsystem load stage.
3. Add `[INIT]` per-subsystem logs with count/size metadata.
4. Add `[INIT COMPLETE]` with total RSS.
5. Add `[REQUEST]` log lines at the top of each endpoint handler.
6. Add `[CHROMA]` log on Chroma collection open (`collection.count()`).
7. Add `"uvicorn_workers": 1` to `/health` response as a constant assertion.

**Run**: 24 hours under normal Cursor usage.
**Extract**: `grep '\[MEMORY\]\|\[DAEMON\]' logs/daemon.log | sort -k3`
**Confirm**: count unique PIDs. If > 1, Phase 2 is the blocker. If == 1, memory is
from subsystem loading, and Phase 3 is the blocker.

---

### Phase 2 — Single-Instance Enforcement + Remove Daemonization

1. **Add `fcntl.flock` exclusive lock** as first action in `main()`:
   ```python
   try:
       import fcntl
       _lock_path = Path(os.getenv("RETRIEVAL_DAEMON_LOCK_FILE",
                                    str(_ROOT / "logs" / "retrieval_daemon.lock")))
       _lock_path.parent.mkdir(parents=True, exist_ok=True)
       _lock_fd = open(_lock_path, "w")
       fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
       _lock_fd.write(str(os.getpid()))
       _lock_fd.flush()
   except BlockingIOError:
       logger.info("[DAEMON] already running (flock held) — exiting cleanly")
       return 0
   except ImportError:
       pass  # non-POSIX: fall through to HTTP health check
   ```

2. **Remove `_daemonize()`** and the `--daemon` arg:
   - Emit `DeprecationWarning` if `--daemon` is passed (one release cycle).
   - Provide `scripts/start_retrieval_daemon.sh`:
     `nohup python scripts/retrieval_daemon.py "$@" > logs/daemon.log 2>&1 &`

3. **Update `--stop`**: read PID from lock file body, send SIGTERM, do not touch lock
   file (dying process releases it).

4. **Update `_is_daemon_running()`**: probe flock only (no PID file check). If flock
   succeeds + immediate release → not running. If flock fails → running.

5. **Remove `_write_pid()` / `_remove_pid()`** as control logic. The lock file body
   serves PID storage for `--stop` only.

---

### Phase 3 — Structured Eager Loading + Chroma Strict Mode

1. **Replace `_warmup_subsystems()` with `initialize_subsystems(project_root)`**:
   - Enforce `MAX_PROJECT_ROOTS = 1`:
     ```python
     if len(_registered_roots) >= MAX_PROJECT_ROOTS:
         raise RuntimeError(
             f"Only one project_root supported in daemon mode. "
             f"Already loaded: {list(_registered_roots)}"
         )
     ```
   - Log `[INIT]` before and after each subsystem load.
   - Load vector index only if the Chroma embeddings directory exists on disk.
   - Log `[INIT COMPLETE] rss_mb=<n>` at the end.

2. **Add `RETRIEVAL_EAGER_LOAD` flag** (default `1`):
   - `RETRIEVAL_EAGER_LOAD=0`: skip `initialize_subsystems()` at startup. Subsystems
     load on first request using the existing per-retriever lazy patterns.
   - This preserves flexibility for test suites and CI without changing the default
     production behavior.

3. **Remove Chroma fallback from `search_by_embedding()`**:
   - When `remote_retrieval_enabled()` is True: treat daemon unavailability as an
     explicit error, not as a trigger for in-process Chroma load.
   - When `remote_retrieval_enabled()` is False (daemon not configured): in-process
     Chroma is still valid and unchanged.
   - Add `[CHROMA FALLBACK BLOCKED]` log if the fallback path is hit to make the
     strict mode visible.

---

### Phase 4 — Caller Batching + Batch Endpoint

1. **Server: `POST /retrieve/vector/batch`** endpoint (minimal, no queue):
   - Request: `{"queries": [...], "project_root": "...", "top_k": 10}`
   - Response: `{"results": [[hits_q1], [hits_q2], ...]}`
   - Implementation: `model.encode(queries)` → `coll.query(query_embeddings=[...])`
   - Cap: `len(queries) ≤ 8`, `top_k ≤ 50` (same as existing vector endpoint)

2. **Client: Fix `search_batch()` N+1** in `vector_retriever.py`:
   - If daemon available: collect all queries → `POST /retrieve/vector/batch` → split
     results. One HTTP call replaces N.
   - If batch endpoint unavailable (404): fall back to sequential `search_by_embedding`.
   - If daemon not available: `encode_via_daemon(queries)` → local
     `coll.query(query_embeddings=embeddings)` — one embed call replaces N.

3. **Client: Fix `multi_root_fetch.fetch_merged()` serial loop**:
   - Replace `for root in roots:` with `ThreadPoolExecutor(max_workers=2)`.
   - `max_workers=2` — conservative for single-user; avoids thread explosion.

---

### Phase 5 — Validation

**Memory targets:**

| Stage | Target RSS |
|---|---|
| Process start (pre-models) | ≤ 300 MB |
| After reranker load | ≤ 900 MB |
| After all subsystems (single root) | ≤ 3.5 GB |
| Steady state | ≤ 3.5 GB |
| Maximum (all subsystems, active requests) | ≤ 5 GB |

**Process count check**:
`pgrep -f retrieval_daemon | wc -l` must equal `1` under concurrent Cursor + CLI usage.

**Throughput check**:
- `search_batch(5 queries)`: before = 5 HTTP calls × ~15 ms = ~75 ms serial.
  After = 1 HTTP call = ~20 ms. Expected: ~3–4× latency reduction.
- `/rerank`: no change expected (already batched correctly).

---

## Part 6 — Caller-Side Audit Findings

### Call Site Inventory

| Endpoint | File | Function | Call Pattern | Batched? |
|---|---|---|---|---|
| `POST /embed` | `agent/retrieval/daemon_embed.py` | `encode_via_daemon` | Accepts `list[str]`, one POST | Yes (protocol supports it) |
| `POST /embed` | `agent/retrieval/vector_retriever.py` | `_embed()` closure | `encode_via_daemon([q])` — **single text** | **No — N+1** |
| `POST /embed` | `agent/memory/task_index.py` | `_encode_for_index` | `encode_via_daemon([text])` — single | No (single use, acceptable) |
| `POST /rerank` | `agent/retrieval/reranker/http_reranker.py` | `HttpRerankerClient._score_pairs` | Groups by query, one POST per distinct query | **Yes (correct, keep as-is)** |
| `POST /retrieve/vector` | `agent/retrieval/daemon_retrieval_client.py` | `try_daemon_vector_search` | One query per POST | No |
| `POST /retrieve/vector` | `agent/retrieval/vector_retriever.py` | `search_batch` | `for q in queries: search_by_embedding(q)` | **N+1 confirmed** |
| `POST /retrieve/bm25` | `agent/retrieval/daemon_retrieval_client.py` | `try_daemon_bm25_search` | One query per POST | No (lower priority) |
| `POST /retrieve/repo_map` | `agent/retrieval/daemon_retrieval_client.py` | `try_daemon_repo_map_lookup` | One query per POST | No (lower priority) |
| `GET /health` | `daemon_client.py`, `daemon_ensure.py`, `http_reranker.py`, `config/startup.py` | Multiple | Liveness probes | n/a |

### Confirmed Anti-Patterns

**Anti-pattern 1 — N+1 embed via `search_batch()` (CRITICAL, FIX IN PHASE 4)**

```python
# agent/retrieval/vector_retriever.py
def search_batch(queries, project_root=None, top_k=DEFAULT_TOP_K):
    out = []
    for q in queries:                         # N iterations
        out.append(search_by_embedding(q))    # N × HTTP POST
    return out
```

For N queries: **N HTTP round-trips**, each with ~5–15 ms latency = N×15 ms serial.
With 5 queries: 75 ms min. After fix: 1 HTTP call ≈ 20 ms.

**Anti-pattern 2 — Single-text embed on every vector search (FIXED BY PATTERN 1)**

```python
# agent/retrieval/vector_retriever.py
def _embed(q: str) -> list[float] | None:
    if use_daemon:
        emb_list = encode_via_daemon([q])     # always single-element list
        return emb_list[0] if emb_list else None
```

Resolved when `search_batch()` is fixed to pass all queries at once.

**Anti-pattern 3 — Multi-root serial calls (FIX IN PHASE 4)**

```python
# agent/retrieval/multi_root_fetch.py
for root in roots:
    rows, warns = fetch_fn(query, root, top_k)   # serial, one call per root
```

For R roots: R × (HTTP + retrieval) serial. Fix: `ThreadPoolExecutor(max_workers=2)`.

### End-to-End Request Flow (Before vs. After)

**Before:**
```
User query with 5 sub-queries
  ├─ 5 × POST /retrieve/vector   (5 calls, serial)
  ├─ 5 × POST /retrieve/bm25     (5 calls, serial)
  ├─ 5 × POST /retrieve/repo_map (5 calls, serial)
  └─ 5 × POST /rerank            (5 calls)
  Total: 20 HTTP calls
```

**After:**
```
User query with 5 sub-queries
  ├─ 1 × POST /retrieve/vector/batch   (1 call, all 5 queries)
  ├─ 5 × POST /retrieve/bm25           (unchanged, lower priority)
  ├─ 5 × POST /retrieve/repo_map       (unchanged, lower priority)
  └─ 5 × POST /rerank                  (unchanged, already batched per query)
  Total: 12 HTTP calls → further reducible to 4 if bm25/repo_map batch endpoints added later
```

---

## Part 7 — Batching Design (Revised: Caller-Side Only)

### Architecture Summary

```
Client (agent process)                 Server (daemon)
─────────────────────                  ───────────────
search_batch([q1,q2,q3])
  → POST /retrieve/vector/batch  ────► embed(queries) → chroma.query(embeddings)
       {"queries": [...]}        ◄──── {"results": [[...],[...],[...]]}
  → split per-query results

encode_via_daemon([t1,t2,t3])
  → POST /embed                  ────► model.encode([t1,t2,t3])
       {"texts": [...]}          ◄──── {"embeddings": [[...],[...],[...]]}

HttpRerankerClient._score_pairs()      (already correct — groups by query)
  → POST /rerank per distinct query
```

**No server-side queue. No collector thread. Batching happens at the call site.**

### Parameters

| Parameter | Value | Applies To |
|---|---|---|
| Max texts per `/embed` | 512 | server validation |
| Max text length | 8192 chars | server pre-encode truncation |
| Max docs per `/rerank` | 200 | server validation |
| Max queries per `/retrieve/vector/batch` | 8 | server validation |
| Max vector top_k | 50 | already enforced |
| Max BM25 top_k | 100 | already enforced |
| Multi-root ThreadPoolExecutor workers | 2 | `multi_root_fetch.py` |

---

## Part 8 — Integration Checks

| Check | Status | Notes |
|---|---|---|
| `/rerank` supports large batches | Yes | Keep `_rerank_lock`; contention unlikely single-user |
| `/embed` with large input | Managed | 512 texts max + 8192 chars/text server validation |
| `/retrieve/vector/batch` memory bound | Managed | 8 queries max; Chroma allocates N×dim×top_k floats |
| Chroma double-load | Eliminated | Fallback path removed when `remote_retrieval_enabled()` |
| Multi-root memory explosion | Eliminated | `MAX_PROJECT_ROOTS = 1` hard error |

---

## Part 9 — Risks

| Risk | Mitigation |
|---|---|
| `fcntl` unavailable on Windows | `try: import fcntl except ImportError: skip to HTTP health check`. macOS/Linux always have it. |
| Lock file on NFS/network FS | Ensure `logs/` is always local. Add startup check: warn if `logs/` is on a network mount. |
| `daemon_only` strict mode breaks test fallback | Tests set `RETRIEVAL_REMOTE_FIRST=0` or `RETRIEVAL_SKIP_REMOTE=1`; in-process Chroma path is unaffected. |
| `MAX_PROJECT_ROOTS=1` breaks multi-workspace tooling | Document the constraint. If multi-root is ever needed, run separate daemons on different ports. |
| `/retrieve/vector/batch` 404 on old daemon | Client falls back to sequential `search_by_embedding` automatically. |
| `ThreadPoolExecutor` in multi-root fetch | `max_workers=2` cap; each worker thread shares process memory, no duplication risk. |
| Removing `--daemon` breaks existing scripts | `DeprecationWarning` for one release; provide `start_retrieval_daemon.sh` wrapper. |
| `RETRIEVAL_EAGER_LOAD=0` in production → cold first request | Document: production should keep default `RETRIEVAL_EAGER_LOAD=1`. |

---

## Final Architecture (Post-Implementation)

```
Server: single process (flock-enforced)
  • Single project root (MAX_PROJECT_ROOTS=1)
  • Eager load at startup (RETRIEVAL_EAGER_LOAD=1 default)
  • Structured [INIT] + [MEMORY] + [REQUEST] logs
  • No daemonization (nohup or process supervisor)
  • No Chroma fallback in agent process (daemon_only strict mode)
  • Endpoints: /embed, /rerank, /retrieve/vector,
               /retrieve/vector/batch (new),
               /retrieve/bm25, /retrieve/repo_map, /health

Client: no N+1 loops
  • search_batch() → POST /retrieve/vector/batch
  • encode_via_daemon(all_texts) not encode_via_daemon([single])
  • multi_root_fetch: ThreadPoolExecutor(max_workers=2)
  • Reranker: already correct, no change

Expected outcome:
  Memory: 1 process × ~2.5–3.5 GB = NEVER 10 GB again
  Latency: 5–10× fewer HTTP calls for batch retrieval
```
