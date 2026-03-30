# Bug ID
BUG-011

# Title
Exploration stalls on duplicate targets; plan mode gates on incomplete exploration

# Area
execution

# Severity
high

# Description
Agent v2 exploration can enter a **stagnation loop** where the same discovery candidates are re-ranked and re-queued after **refine**, duplicate `(canonical_path, symbol)` targets are skipped at dequeue, and **`stagnation_counter`** reaches **`EXPLORATION_STAGNATION_STEPS`** → **`termination_reason=stalled`**.

Separately, **`ModeManager._exploration_is_complete`** only allows the planner when **`metadata.completion_status == "complete"`**. Many honest terminations (**`stalled`**, **`no_relevant_candidate`**, etc.) leave **`completion_status=incomplete`**, so **`autostudio run --mode plan`** raises **`RuntimeError`** even when exploration “finished” in a defined way.

Discovery/scoper/selector can also repeatedly pick **irrelevant** files for vague queries (e.g. “exploration scope layer”) because search hits are noisy and LLM stages lack strong exclusion of **analyzer-rejected** locations.

# Steps to Reproduce
1. Run `autostudio run --mode plan` with an instruction that does not align well with grep symbols (e.g. asks about a subsystem name that does not appear literally in code).
2. Observe workflow logs: **ExplorationScoper** / **CandidateSelector** repeat the same file indices; **ExplorationAnalyzer** returns **wrong_target** / **partial**; **refine** re-runs discovery.
3. Observe termination **`stalled`** or **`no_relevant_candidate`**, then **`RuntimeError: Exploration did not complete; planner execution is gated`**.

# Expected Behavior
- Exploration should either surface relevant code or terminate with a **planner-gating-friendly** outcome (e.g. explicit **`completion_status=complete`** for exhausted search / no relevant candidate, or a clear non-throwing path).
- Refine should not re-queue **already inspected (path, symbol)** pairs without new evidence.

# Actual Behavior
- Duplicate or low-value targets can inflate **skip** iterations → **`stalled`**.
- **`no_relevant_candidate`** and **`stalled`** typically leave **`completion_status=incomplete`** → **ModeManager** blocks planner and raises.

# Logs / Trace
```
RuntimeError: Exploration did not complete; planner execution is gated (termination_reason=stalled).
```
Representative workflow pattern (Langfuse / CLI): repeated **`selected_indices`** (e.g. `[10, 13, 14]`), same ranked files, analyzer **wrong_target**, refine, repeat.

# Root Cause
1. **ExplorationEngineV2** (`agent_v2/exploration/exploration_engine_v2.py`): Main loop dedup via **`seen_targets`**; re-enqueue after refine could append **duplicate keys** → dequeue **continue** without progress → **`stagnation_counter`** (also a second path when **`_is_meaningful_new_evidence`** is false repeatedly).
2. **Discovery** (`_discovery`): Refine repeats the **same** search results; no alternate query expansion guarantees new files.
3. **ExplorationScoper** / **CandidateSelector**: LLM steps lack durable **“failed / rejected”** context; same instruction + similar candidate list → **same picks**.
4. **ModeManager** (`agent_v2/runtime/mode_manager.py`): **`_exploration_is_complete`** requires **`completion_status=complete`**; only narrow cases (e.g. **`pending_exhausted`**, sufficient analyzer path) set **complete** — many terminations stay **incomplete**.

# Fix
- (Partial) Persist **explored (canonical_path, symbol)** on **`ExplorationState`**, filter in **`_enqueue_ranked`**, pass **explored locations** into **CandidateSelector** prompt (implemented in codebase; verify in production traces).
- (Follow-up) Treat **`no_relevant_candidate`** (and possibly **`stalled`** after clean stop) as **complete** for planner gating, or add dedicated **`completion_status`** rules in **`_explore_inner`** / **`ModeManager`**.
- (Follow-up) **ExplorationScoper** prompt: exclude already-inspected or analyzer-failed paths.
- (Follow-up) Harden **ExplorationAnalyzer** JSON (**status** must be single enum).

# Resolution (2025-03-25)
Implemented unified enqueue invariant in **`ExplorationEngineV2`**:
- **`_make_location_key`** / **`_may_enqueue`** — single predicate for **`_enqueue_ranked`**, **`_enqueue_targets`**, and **`_discovery`** output filtering.
- **Expansion** no longer bypasses memory: **`_enqueue_targets`** uses **`_may_enqueue`** (same as ranked/discovery).
- Optional **`excluded_paths`** on **`ExplorationState`** when analyzer JSON sets **`wrong_target_scope: "file"`** (explicit only; not inferred from **`wrong_target`** alone).
- Dequeue dedup uses **`explored_location_keys`** only (removed parallel **`seen_targets`** set).

Files: **`agent_v2/exploration/exploration_engine_v2.py`**, **`agent_v2/schemas/exploration.py`**, **`agent_v2/exploration/understanding_analyzer.py`**.

Planner gating / **`ModeManager`** **`completion_status`** behavior unchanged (separate follow-up).

# Status
resolved
