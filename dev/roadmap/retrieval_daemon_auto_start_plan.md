# Principal Engineer Plan: Retrieval Daemon in Agent Loop with Auto-Start

**Status:** Plan  
**Owner:** Principal Engineer  
**Related:** Phase 17 (Reranker), `scripts/retrieval_daemon.py`, `config/retrieval_config.py`, `agent/orchestrator/agent_controller.py`

---

## 1. Objective

- **Use the unified retrieval daemon** (embedding + reranker service) in the **main agent loop** for all retrieval work when configured.
- **If the daemon is not running**, **start it automatically** (only then); otherwise leave it as-is.
- Preserve existing behavior: when daemon is disabled or unreachable, fall back to in-process reranker and embedding.

---

## 2. Current State

| Component | Behavior |
|-----------|----------|
| **Retrieval daemon** | `scripts/retrieval_daemon.py` — FastAPI app; `POST /rerank`, `POST /embed`, `GET /health`. Can run foreground or `--daemon` (background). PID in `logs/retrieval_daemon.pid`. |
| **Reranker** | `agent/retrieval/reranker/reranker_factory.py`: when `RERANKER_USE_DAEMON=1` and `_check_daemon_health(port)` is true, returns `HttpRerankerClient`; else builds in-process reranker. |
| **Embedding** | `agent/retrieval/vector_retriever.py`: when `EMBEDDING_USE_DAEMON=1` and `_check_daemon_embed()` (GET /health, checks `embedding_loaded`) is true, uses `_encode_via_daemon()`; else in-process SentenceTransformer. |
| **Agent entry** | `run_controller()` in `agent_controller.py` → `run_attempt_loop()` → retrieval used inside step execution (dispatcher → retrieval pipeline). No daemon health check or start at controller entry. |

**Gap:** User must start the daemon manually before agent sessions. If they forget, the agent falls back to in-process models (cold start, higher memory).

---

## 3. Design

### 3.1 Principle

- **Extend, do not replace.** Keep existing retrieval pipeline and daemon script unchanged in contract; add an “ensure daemon” step at agent entry.
- **Single responsibility.** One module/function responsible for “ensure retrieval daemon is reachable; if we want daemon and it’s not up, start it.”
- **Observable.** Log and optionally trace when we attempt start, when we skip (already running), and when we fall back (start failed or disabled).

### 3.2 When to Run “Ensure Daemon”

- **At controller entry** (recommended): in `run_controller()` before `run_attempt_loop()`, after `start_trace()`, when config says “use daemon” and “auto-start allowed.”  
  Rationale: one place, predictable; first SEARCH step already has daemon up; avoids lazy-start latency on first query.

### 3.3 “Ensure” Semantics

1. If **neither** `RERANKER_USE_DAEMON` nor `EMBEDDING_USE_DAEMON` is true → **do nothing** (no daemon desired).
2. If **`RETRIEVAL_DAEMON_AUTO_START` is false** (new config) → **do nothing** (explicit opt-out; user manages daemon).
3. **Check** `GET http://127.0.0.1:{RETRIEVAL_DAEMON_PORT}/health` (reuse existing health URL).
4. If **200 and** `reranker_loaded` or `embedding_loaded` true (per our needs) → **done** (daemon already running).
5. Otherwise **start** the daemon:
   - Run `python scripts/retrieval_daemon.py --daemon` (or equivalent) in a **subprocess** (detached so it outlives the agent process).
   - **Wait** for `GET /health` to return 200 with required capabilities, with a **timeout** (e.g. 60–120 s).
6. If **timeout or start fails** → **log and continue**; agent will fall back to in-process (existing behavior). No exception that aborts the task.

### 3.4 Configuration

- **`RETRIEVAL_DAEMON_AUTO_START`** (default `1`): when `1`, controller may start the daemon if not reachable; when `0`, never start (user must run daemon manually).
- **`RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS`** (default `90`): max wait for daemon to become healthy after start attempt.

Port/host continue to come from existing `RETRIEVAL_DAEMON_PORT` / `RERANKER_DAEMON_PORT` and current daemon script behavior.

---

## 4. Implementation Outline

### 4.1 New Module (recommended)

- **`agent/retrieval/daemon_ensure.py`** (or under `config/` if preferred for “startup” only):
  - `ensure_retrieval_daemon(project_root: str | Path) -> bool`  
    - Returns `True` if daemon is reachable (after optional start), `False` otherwise.
  - Uses `config.retrieval_config`: `RERANKER_USE_DAEMON`, `EMBEDDING_USE_DAEMON`, `RETRIEVAL_DAEMON_PORT`, `RETRIEVAL_DAEMON_AUTO_START`, `RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS`.
  - **Health check:** same as today (GET /health; consider reranker and/or embedding loaded depending on which use_daemon flags are set).
  - **Start:** `subprocess.Popen(..., cwd=project_root, start_new_session=True)` to run `sys.executable -m scripts.retrieval_daemon` (or `python scripts/retrieval_daemon.py --daemon` with script path resolved from project root). Do **not** block the agent on the daemon’s stdout/stderr after it’s up.
  - **Poll:** in a loop, GET /health every N seconds until healthy or timeout; then return True/False.
  - **Logging:** info when skipping (already up), when starting, when timeout/failure (and that fallback to in-process will occur).

### 4.2 Config Additions

- In **`config/retrieval_config.py`**:
  - `RETRIEVAL_DAEMON_AUTO_START = _bool_env("RETRIEVAL_DAEMON_AUTO_START", "1")`
  - `RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS = int(os.getenv("RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS", "90"))`

### 4.3 Controller Integration

- In **`agent/orchestrator/agent_controller.py`**, inside `run_controller()`:
  - After `start_trace(...)` and `root = Path(...).resolve()`,
  - If `RERANKER_USE_DAEMON` or `EMBEDDING_USE_DAEMON` is true:
    - Call `ensure_retrieval_daemon(str(root))`.
    - Optionally `log_event(trace_id, "retrieval_daemon_ensure", {"daemon_reachable": bool_result })` for observability.
  - No change to `run_attempt_loop` or the retrieval pipeline; they already use daemon when health checks pass.

### 4.4 Daemon Script

- **No change required** to `scripts/retrieval_daemon.py` for normal behavior. It already:
  - Checks “already running” via PID file and/or GET /health before starting.
  - Supports `--daemon` and writes PID to `logs/retrieval_daemon.pid`.
- Optional: document in script docstring that the agent may auto-invoke it when `RETRIEVAL_DAEMON_AUTO_START=1`.

---

## 5. Edge Cases and Safety

- **Port in use by non-daemon:** Health check fails; we might try to start and get “address already in use.” Treat as “start failed”; log and continue (in-process fallback).
- **Daemon start failure (e.g. missing deps):** Subprocess exits; health never returns 200. Timeout; log and continue.
- **Double start:** Daemon script’s `_is_daemon_running()` avoids starting a second process; our ensure only starts if health check fails.
- **CI / headless:** Set `RETRIEVAL_DAEMON_AUTO_START=0` (or disable daemon use) to avoid starting a long-lived daemon in CI if not desired.
- **Observability:** Trace event `retrieval_daemon_ensure` plus existing retrieval metrics keep “daemon vs in-process” visible.

---

## 6. Testing

- **Unit:**
  - Mock GET /health (200 vs non-200, `reranker_loaded`/`embedding_loaded`). Assert ensure returns True without starting when healthy.
  - Mock health failing and subprocess; assert ensure either returns True after “start” + healthy or False on timeout; no uncaught exception.
- **Integration (optional):**
  - With real daemon stopped, run controller with auto-start; verify daemon is up and one SEARCH step uses it (e.g. via existing reranker/embedding path logs or trace).

---

## 7. Documentation Updates

- **README.md** — “Retrieval daemon” section: state that when `RERANKER_USE_DAEMON=1` or `EMBEDDING_USE_DAEMON=1` and `RETRIEVAL_DAEMON_AUTO_START=1` (default), the agent will start the daemon if not running.
- **Docs/CONFIGURATION.md** — Add `RETRIEVAL_DAEMON_AUTO_START`, `RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS`.
- **scripts/retrieval_daemon.py** — One-line note in docstring: “May be started automatically by the agent when RETRIEVAL_DAEMON_AUTO_START=1.”

---

## 8. Summary

| Item | Action |
|------|--------|
| **Use daemon in agent loop** | Already the case when daemon is reachable (reranker_factory + vector_retriever). No change. |
| **If not running, start it** | Add `ensure_retrieval_daemon()`; call from `run_controller()` when use_daemon and auto-start are on. |
| **Config** | `RETRIEVAL_DAEMON_AUTO_START`, `RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS`. |
| **New code** | `agent/retrieval/daemon_ensure.py` (or equivalent), small hook in `agent_controller.run_controller()`. |
| **Safety** | Start failure or timeout → log and continue; agent falls back to in-process. |

This keeps the execution engine and retrieval pipeline intact (Rule 1, Rule 11), preserves observability (Rule 10), and extends behavior without replacing it (Rule 17).
