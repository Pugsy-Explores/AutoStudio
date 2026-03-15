# Cursor Task Plan

## Phase 15 — Trajectory Improvement Loop

Goal: add a **controlled retry loop with critic feedback**.

Do **not redesign the agent**.
Only add the retry logic around the existing pipeline.

---

# Step 1 — Verify existing components

Before writing new code, Cursor must check if these modules already exist:

```
agent/meta/evaluator.py
agent/meta/critic.py
agent/meta/retry_planner.py
agent/meta/trajectory_store.py
```

If they exist:

```
extend them
```

If missing:

```
create minimal versions
```

Do not create duplicate modules.

---

# Step 2 — Add retry controller

Create a new module:

```
agent/meta/trajectory_loop.py
```

Responsibilities:

```
run task attempt
evaluate result
call critic on failure
call retry_planner
retry execution
```

Interface:

```python
class TrajectoryLoop:
    def run_with_retries(self, goal, workspace, max_retries=3):
        attempt = 0
        trajectories = []

        while attempt <= max_retries:
            result = run_single_attempt(goal, workspace)

            evaluation = evaluator.evaluate(result)

            if evaluation == "SUCCESS":
                return result

            diagnosis = critic.diagnose(result)
            strategy = retry_planner.plan_retry(diagnosis)

            workspace.reset_for_retry(strategy)

            attempt += 1
```

---

# Step 3 — Integrate with autonomous agent

Modify:

```
agent/autonomous/agent_loop.py
```

Replace direct run with:

```
TrajectoryLoop.run_with_retries()
```

Flow becomes:

```
goal
→ attempt
→ evaluate
→ critique
→ retry
```

---

# Step 4 — Extend evaluator

Update:

```
agent/meta/evaluator.py
```

Ensure evaluator returns:

```
SUCCESS
FAILURE
PARTIAL
```

Signals:

```
patch_applied
tests_passed
fatal_error
```

---

# Step 5 — Extend critic output

Update:

```
agent/meta/critic.py
```

Critic must return structured output:

```json
{
 "failure_type": "retrieval_miss | wrong_patch | bad_plan | timeout",
 "evidence": "...",
 "suggested_strategy": "rewrite_query | regenerate_patch | expand_search"
}
```

Structured output keeps retry deterministic.

---

# Step 6 — Extend retry planner

Update:

```
agent/meta/retry_planner.py
```

Supported strategies:

```
rewrite_retrieval_query
expand_search_scope
generate_new_plan
retry_edit_with_different_patch
search_symbol_dependencies
```

Return:

```json
{
 "retry_strategy": "expand_search_scope"
}
```

---

# Step 7 — Extend trajectory store

Update:

```
agent/meta/trajectory_store.py
```

Store:

```
goal
attempt
steps
diagnosis
strategy
result
```

Saved to:

```
.agent_memory/trajectories/<task_id>.json
```

Trajectories allow later analysis.

Trajectory analysis is important because many modern agent systems improve by studying the sequence of reasoning steps that led to success or failure. ([Software Lab][2])

---

# Step 8 — Add retry limits

Update config:

```
config/agent_config.py
```

Add:

```
MAX_RETRY_ATTEMPTS = 3
MAX_RETRY_RUNTIME_SECONDS = 120
```

Guardrails prevent infinite loops.

---

# Step 9 — Add telemetry

Extend:

```
agent/prompt_system/observability/prompt_metrics.py
```

Add:

```
attempt_number
retry_strategy
trajectory_length
```

Example log:

```
task_id: 123
attempt: 2
strategy: expand_search
```

---

# Step 10 — Add evaluation script

Create:

```
scripts/run_retry_eval.py
```

Runs autonomous tasks and outputs:

```
success_rate
retry_success_rate
attempts_per_task
```

---

# Step 11 — Add unit tests

Create:

```
tests/test_trajectory_loop.py
```

Test cases:

```
success without retry
retry resolves failure
max_retry_stop
trajectory stored correctly
```

---

# Step 12 — Add config to autonomous loop

Update:

```
agent/autonomous/agent_loop.py
```

Add parameter:

```
max_retries
```

Default:

```
max_retries = 3
```

---

# Expected Result

After implementing this:

```
agent can recover from failures
incorrect plans get corrected
success rate improves significantly
```

Coding agents often improve when they iteratively refine outputs using feedback and evaluation loops rather than relying on single-pass generation. ([aman.ai][1])

---

# Principal Engineer Advice

Keep the loop **simple**:

```
attempt
→ critique
→ retry
```

Do not build a complex multi-agent system yet.

Most high-performing coding agents rely on **a simple retry loop with structured feedback** rather than large agent hierarchies.
