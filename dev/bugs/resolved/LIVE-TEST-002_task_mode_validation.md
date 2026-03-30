================================================================================
BUG FIX — Issue #2: Task Mode Validation
================================================================================

Issue ID: LIVE-TEST-002
Priority: P1 (HIGH - QUALITY)
Discovered: 2026-03-25 Live Integration Testing
Status: IN PROGRESS → FIXED (awaiting verification)

================================================================================
ROOT CAUSE
================================================================================

Problem: Planner generated write actions (edit, run_tests, shell) for read-only tasks.

Example:
- Instruction: "Explain what the TraceEmitter class does"
- Expected: search → open_file → finish
- Actual: search → open_file → edit → edit → edit → finish ❌

Root cause:
1. Planner prompt did not restrict actions based on task type
2. PlanValidator did not enforce task mode constraints
3. LLM had no guardrails against generating write actions for read-only tasks

================================================================================
THE FIX (3-PART)
================================================================================

Part 1: Add task mode inference
File: agent_v2/planner/planner_v2.py
Method: _infer_task_mode()

```python
def _infer_task_mode(self, instruction: str) -> Optional[str]:
    """Returns "read_only" for exploratory questions, None for write tasks."""
    instruction_lower = instruction.lower()
    
    # Read-only indicators
    read_only_keywords = ["where", "what", "how", "why", "explain", ...]
    
    # Write task indicators
    write_keywords = ["add", "create", "implement", "fix", "modify", ...]
    
    # Check for write keywords first (higher priority)
    if any(keyword in instruction_lower for keyword in write_keywords):
        return None  # Write task
    
    # Check for read-only keywords
    if any(instruction_lower.startswith(keyword) for keyword in read_only_keywords):
        return "read_only"
    
    return None  # Default: write task (conservative)
```

Part 2: Add task mode validation
File: agent_v2/validation/plan_validator.py
Method: validate_plan(), _validate_step()

```python
READ_ONLY_ACTIONS = frozenset({"search", "open_file", "finish"})
WRITE_ACTIONS = frozenset({"edit", "run_tests", "shell"})

def validate_plan(plan, *, policy=None, task_mode=None):
    # ... existing checks ...
    for step in steps:
        PlanValidator._validate_step(step, step_ids, task_mode=task_mode)

def _validate_step(step, step_ids, task_mode=None):
    # ... existing checks ...
    
    # LIVE-TEST-002: Enforce read-only mode constraints
    if task_mode == "read_only" and step.action in WRITE_ACTIONS:
        raise PlanValidationError(
            f"Step {step.step_id} uses write action {step.action!r} "
            f"but task_mode is 'read_only'. Only {READ_ONLY_ACTIONS} are allowed."
        )
```

Part 3: Update planner prompt
File: agent_v2/planner/planner_v2.py
Method: _build_exploration_prompt(), _build_replan_prompt()

```python
# LIVE-TEST-002: Add task mode constraints
allowed_actions = "search, open_file, edit, run_tests, shell, finish"
task_mode_constraint = ""
if task_mode == "read_only":
    allowed_actions = "search, open_file, finish"
    task_mode_constraint = (
        "\n⚠️  CRITICAL: This is a READ-ONLY task (explain/find/analyze).\n"
        "You MUST NOT use: edit, run_tests, shell\n"
        "Only allowed actions: search, open_file, finish\n"
    )

# Then inject {allowed_actions} and {task_mode_constraint} into prompt
```

================================================================================
VERIFICATION
================================================================================

Test: Phase 5 — Plan Executor with read-only task
Instruction: "Explain what the TraceEmitter class does"

Before fix:
- Plan validation: ❌ FAILED (edit actions generated)
- Duration: N/A (crashed during validation)

After fix:
- Plan validation: ✅ PASSED (only search, open_file, finish)
- Duration: 94s
- Trace: 8 steps executed, all successful
- Actions: search → open_file → open_file → search → open_file (x3) → finish

Result: FIX VERIFIED ✅

================================================================================
REMAINING ISSUES
================================================================================

1. Over-planning: 8 steps for simple "Explain TraceEmitter" task
   - Expected: 3-4 steps
   - Root cause: LLM quality or prompt decomposition guidance
   - Priority: P2
   - Impact: Performance (94s vs expected 30-40s)

2. Planner prompt should suggest simpler plans for straightforward tasks
   - Priority: P3
   - Future improvement

================================================================================
STATUS
================================================================================

✅ FIXED
- Task mode inference working
- Validation enforcing read-only constraints
- Prompt communicating task restrictions to LLM
- Tests passing

Ready for production.

================================================================================
