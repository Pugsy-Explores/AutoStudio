# --cursor prompt --
You are a staff engineer implementing Phase 5.1: Episodic Memory Access from @/Users/shang/my_work/pugsy_ai/.cursor/plans/agentv2_memory_phase_5_6ebe61fb.plan.md

You MUST follow the existing Phase 5 plan (already created in plan mode).
ONLY implement Phase 5.1.

Do NOT touch Phase 5.2, 5.3, or 5.4.

---

## GOAL

Make execution history (trace logs) queryable and usable.

Keep implementation minimal and deterministic.

---

## STEP 1 — ENABLE LOG PERSISTENCE (CRITICAL FIRST)

Currently TraceEmitter exists but log_dir is not wired.

Tasks:

1. Add config:

   * In agent_v2/config.py
   * Add: AGENT_V2_EPISODIC_LOG_DIR (default: ".agent_memory/episodic/")

2. Wire log_dir:

* In planner_task_runtime.py:

  * When creating TraceEmitter → pass log_dir per run:
    <base_dir>/<trace_id>/

* In runtime.py / DagExecutor:

  * Ensure same behavior if execution bypasses planner

3. Ensure:

* Directory auto-created
* Uses gitignored path (.agent_memory/)

---

## STEP 2 — ENRICH EXECUTION LOG ENTRY

File: trace_emitter.py

Add field:

```python
tool: str
```

Populate:

* from ExecutionTask.tool inside record_execution_attempt

Do NOT add extra fields.

---

## STEP 3 — BUILD EPISODIC QUERY LAYER

Create new file:
agent_v2/memory/episodic_query.py

Implement:

```python
class EpisodicQuery:
    def __init__(self, base_dir: Path): ...

    def query(
        self,
        tool: Optional[str] = None,
        success: Optional[bool] = None,
        limit: int = 10,
    ) -> list[dict]:
```

Behavior:

* scan recent trace directories only (sorted by mtime)
* read JSONL logs
* filter:

  * tool
  * success
* return most recent first
* stop at limit

IMPORTANT:

* NO similarity search
* NO embeddings
* NO scoring
* ONLY filtering + recency

---

## STEP 4 — KEEP IT ISOLATED

Do NOT:

* modify planner
* inject into prompts
* change PlannerPlanContext

This phase = infrastructure only

---

## STEP 5 — VALIDATION

Write tests:

1. Log creation test:

   * run a task
   * assert JSONL files created

2. Query test:

   * create fake logs
   * query by tool
   * query by success/failure

3. Recency test:

   * ensure most recent returned first

---

## STEP 6 — OUTPUT

Provide:

1. Files modified
2. New module created
3. How logs are structured now
4. Example query usage

---

## RULES

* minimal implementation only
* no abstractions
* no performance optimization
* no new frameworks

Focus:
👉 make episodic memory EXIST and QUERYABLE
