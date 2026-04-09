
Here is what was implemented for **Phase 5.1** only.

## 1. Files modified

| File | Change |
|------|--------|
| [`agent_v2/config.py`](agent_v2/config.py) | `Path` import; `get_agent_v2_episodic_log_dir()` — env `AGENT_V2_EPISODIC_LOG_DIR`, default resolved `.agent_memory/episodic`, empty string → `None` (no logs). |
| [`agent_v2/runtime/trace_emitter.py`](agent_v2/runtime/trace_emitter.py) | `ExecutionLogEntry.tool: str`; set from `task.tool` in `record_execution_attempt`. |
| [`agent_v2/runtime/planner_task_runtime.py`](agent_v2/runtime/planner_task_runtime.py) | `TraceEmitter(log_dir=get_agent_v2_episodic_log_dir())` in all three paths (`run_explore_plan_execute`, `run_plan_explore_execute_safe`, `run_plan_only`). |
| [`agent_v2/runtime/dag_executor.py`](agent_v2/runtime/dag_executor.py) | Resolve `trace_log_dir` via `get_agent_v2_episodic_log_dir()` when `None`; store `_trace_log_dir`; fallback `run` / `run_one_step` uses `_trace_emitter_factory(log_dir=self._trace_log_dir)`. |
| [`agent_v2/runtime/runtime.py`](agent_v2/runtime/runtime.py) | `DagExecutor(..., trace_log_dir=get_agent_v2_episodic_log_dir())`. |

## 2. New module

- [`agent_v2/memory/episodic_query.py`](agent_v2/memory/episodic_query.py) — `EpisodicQuery(base_dir).query(tool=..., success=..., limit=...)`.

## 3. Tests

- [`tests/test_episodic_memory.py`](tests/test_episodic_memory.py) — JSONL + `tool`, filter tests, timestamp recency, empty env disables dir.

## 4. Log layout

- **Root:** resolved absolute path from `get_agent_v2_episodic_log_dir()` (default `<cwd>/.agent_memory/episodic`, already under `.gitignore`’s `.agent_memory/`).
- **Per emitter `reset()`:** subdirectory `trace_<uuid>/` (unchanged `TraceEmitter` behavior).
- **Per task attempts:** `trace_<uuid>/<task_id>.jsonl`, one JSON object per line (`ExecutionLogEntry`), including **`tool`**.

## 5. Example query usage

```python
from pathlib import Path
from agent_v2.config import get_agent_v2_episodic_log_dir
from agent_v2.memory.episodic_query import EpisodicQuery

base = Path(get_agent_v2_episodic_log_dir() or ".")
rows = EpisodicQuery(base).query(tool="search", success=False, limit=20)
# rows: list[dict] with log fields + "_trace_subdir", "_log_file"; newest `timestamp` first
```

**Not done (as requested):** no planner changes, no `PlannerPlanContext` updates, no prompt injection.

**Note:** With the default config, runs will write under `.agent_memory/episodic/`. To turn off disk logs: `export AGENT_V2_EPISODIC_LOG_DIR=` (empty). `tests/test_episodic_memory.py` passes (`4 passed`).

# ---updates ---

Here’s what changed.

### 1. Timestamp ordering ([`agent_v2/memory/episodic_query.py`](agent_v2/memory/episodic_query.py))
- Added `_dt_from_log_timestamp()` using `datetime.fromisoformat` after normalizing a trailing `Z` to `+00:00`.
- Naive datetimes are treated as UTC; missing/invalid timestamps map to `datetime.min` (UTC) so they sort to the end when `reverse=True`.

### 2. `trace_id` on each row
- Each result row now includes `trace_id`: the folder name with the `trace_` prefix removed (same as `TraceEmitter.trace_id`).
- `_trace_subdir` and `_log_file` are unchanged for compatibility.

### 3. Bounded directory scan
- Module constant `MAX_TRACES_SCANNED = 20`.
- After sorting trace dirs by `st_mtime_ns` (newest first), only the first 20 are read.

### Tests ([`tests/test_episodic_memory.py`](tests/test_episodic_memory.py))
- Assertions on `trace_id` for existing cases.
- `test_episodic_query_timestamp_sort_parsed_not_lexical`: two events in one trace, Feb vs Oct → October first.
- `test_episodic_query_max_traces_scanned_skips_oldest_dirs`: patches `MAX_TRACES_SCANNED` to `3`, fixes **directory mtimes after** writing JSONL so parent mtimes aren’t scrambled; ensures the oldest trace dir is skipped and the newest is still returned.

All **6** tests in `tests/test_episodic_memory.py` pass.