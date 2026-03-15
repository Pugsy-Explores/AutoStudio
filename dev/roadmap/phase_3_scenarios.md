# Phase 3 — Scenario Testing

**Status:** Implementation underway. `tests/agent_scenarios.json`, `run_principal_engineer_suite.py --scenarios`, and `dev/evaluation/metrics.md` in place.

Once pipeline works once, move to scenario testing. Agent testing should mimic real workflows.

## Scenario Dataset

Create: `tests/agent_scenarios.json`

## Example Tasks

| Difficulty | Task |
|------------|------|
| **Easy** | Where is AgentState defined? |
| **Easy** | Explain what context_ranker does. |
| **Medium** | Add logging to execute_step. |
| **Medium** | Fix missing import in foo.py. |
| **Hard** | Add validation to all executor classes. |
| **Hard** | Refactor retry logic. |

## Per-Scenario Tracking

Each scenario should track:

- task_success
- retrieval_success
- edit_success
- latency


Good. If **Phase 2 is finished**, you have achieved something important:

* the pipeline runs end-to-end
* subsystems interact correctly
* retrieval, planner, dispatcher, editing, and observability are integrated

Now we move to the phase where most AI-agent projects **actually discover their real weaknesses**.

That phase is **Phase 3 — Scenario Evaluation**.

This phase mirrors how real AI agents are tested in practice: by running **task scenarios that simulate real developer workflows**, not just unit tests. Evaluation frameworks for agents often focus on **task success, tool use correctness, and reliability across multi-step workflows**. ([Medium][1])

So Phase 3 is where you turn AutoStudio from:

```
working pipeline
```

into

```
a usable coding assistant
```

---

# Phase 3 — Scenario Evaluation (Principal Engineer Plan)

## Objective

Validate the system on **real developer tasks**, progressively increasing complexity.

Exit criteria:

```
20+ real tasks executed
≥70% task success rate
no catastrophic pipeline failures
```

---

# Phase 3 Architecture

Your evaluation loop becomes:

```
scenario task
→ agent execution
→ trace collection
→ success/failure analysis
→ bug logging
→ system improvement
```

This is exactly how agent systems are improved iteratively: repeated evaluation cycles expose failure patterns and guide fixes. ([Anthropic][2])

---

# Step 1 — Build the Scenario Dataset

Open:

```
dev/evaluation/test_tasks.md
```

Convert it into **structured scenario groups**.

### Group 1 — Code Understanding

Tasks:

```
Explain AgentState
Explain retrieval_pipeline
Explain StepExecutor
Find where retry logic exists
Explain context_ranker
```

Goal:

```
validate retrieval + explanation
```

---

### Group 2 — Code Navigation

Tasks:

```
Find where the patch validator is implemented
Find callers of execute_step
Locate symbol graph builder
Find where planner steps are validated
```

Goal:

```
validate repo map + symbol graph
```

---

### Group 3 — Simple Code Edits

Tasks:

```
Add logging to foo()
Add docstring to function
Rename variable in file
Fix missing import
```

Goal:

```
validate editing pipeline
```

---

### Group 4 — Multi-Line Fixes

Tasks:

```
Add validation to function
Add try/except block
Add debug logging to executor
```

Goal:

```
validate AST patching
```

---

### Group 5 — Multi-File Changes

Tasks:

```
Add logging to all executor classes
Add retry limit configuration
Update function across modules
```

Goal:

```
validate graph navigation + editing
```

---

# Step 2 — Run Scenario Execution

Execute tasks manually first.

Example:

```
python -m agent "Add logging to function foo"
```

Observe:

```
router
planner
retrieval
context
editing pipeline
validator
patch execution
```

Record results.

---

# Step 3 — Log Results

Update:

```
dev/evaluation/metrics.md
```

Example:

```
| Task | Success | Failure Reason |
|-----|------|---------------|
Explain AgentState | yes | - |
Add logging foo | no | patch validator failure |
Find retry logic | yes | - |
```

Key metrics:

```
task_success_rate
retrieval_recall
edit_success_rate
latency
```

Agent evaluation frameworks commonly track **task completion rate and efficiency metrics** to understand agent reliability. ([Medium][1])

---

# Step 4 — Log Bugs

When something fails:

```
python scripts/report_bug.py "retrieval returned empty context"
```

Then move the bug through:

```
dev/bugs/backlog
→ dev/bugs/in_progress
→ dev/bugs/resolved
```

Also update:

```
dev/evaluation/failure_patterns.md
```

Example:

```
Failure Pattern:
planner generates invalid edit step

Cause:
missing constraint in planner prompt
```

---

# Step 5 — Build Failure Taxonomy

By the end of Phase 3 you should know your system’s weak spots.

Common categories:

```
retrieval failures
planner hallucinations
context overflow
patch validation errors
multi-file navigation errors
```

Record them in:

```
dev/evaluation/failure_patterns.md
```

This document becomes **the debugging brain of the system**.

---

# Step 6 — Create the First Agent Benchmark

Once you have 20–30 tasks, you effectively have your own **internal benchmark**.

Major coding-agent benchmarks like SWE-bench evaluate systems using sets of real tasks such as bug fixes and feature additions across repositories. ([alphaXiv][3])

Your benchmark is simpler but extremely valuable.

Example categories:

```
code understanding
navigation
editing
multi-file edits
bug fixing
```

---

# Step 7 — Automate Scenario Execution

After manual tests work, use:

```
scripts/run_principal_engineer_suite.py
```

This script should:

```
run tasks
collect traces
compute success rate
generate report
```

Output:

```
reports/eval_report.json
```

---

# Phase 3 Exit Criteria

Move to Phase 4 when:

```
20+ tasks tested
success rate ≥70%
editing pipeline stable
no catastrophic failures
```

---

# What Phase 3 Is NOT

Do not add:

```
autonomous agent loop
VSCode integration
multi-agent planning
project generation
```

You are still stabilizing **core capabilities**.

---

# Principal Engineer Assessment

Based on your repo structure, you are already ahead of most AI-agent projects.

You have:

* deterministic execution engine
* retrieval architecture
* editing safety pipeline
* evaluation harness
* bug tracking
* development workflow

Now Phase 3 will reveal **the real behavior of the system under real tasks**.

That’s where AutoStudio becomes a **real coding agent engine**.

---
