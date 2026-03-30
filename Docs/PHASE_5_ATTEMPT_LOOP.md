# Phase 5: Attempt-Level Learning and Retry Architecture

> **Legacy (REACT_MODE=0).** The primary execution path is ReAct; see [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md).

## Attempt loop flow diagram

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                   run_controller (deterministic)          │
                    └────────────────────────────┬────────────────────────────┘
                                                 │
                                                 ▼
                    ┌─────────────────────────────────────────────────────────┐
                    │                   run_attempt_loop()                      │
                    │  trajectory_memory = TrajectoryMemory()                   │
                    │  critic = Critic()   retry_planner = RetryPlanner()       │
                    │  goal_evaluator = GoalEvaluator()                         │
                    └────────────────────────────┬────────────────────────────┘
                                                 │
         ┌───────────────────────────────────────┼───────────────────────────────────────┐
         │  for attempt in range(MAX_AGENT_ATTEMPTS):                                    │
         │       log_event("attempt_started")                                             │
         │       retry_context = None (attempt 0) or from previous attempt (attempt > 0)  │
         │                              │                                                 │
         │                              ▼                                                 │
         │       ┌─────────────────────────────────────────────────────────────┐         │
         │       │  run_deterministic(instruction, project_root,                │         │
         │       │    retry_context=retry_context)  ◄── single attempt executor │         │
         │       │  (get_plan → step loop → validate → record)                  │         │
         │       └────────────────────────────┬────────────────────────────────┘         │
         │                                    │                                           │
         │                                    ▼                                           │
         │       goal_met = goal_evaluator.evaluate(instruction, state)                    │
         │       trajectory_memory.record_attempt({ plan, step_results, errors,             │
         │         patches_applied, files_modified, goal_met })                            │
         │                                    │                                           │
         │                    ┌───────────────┴───────────────┐                            │
         │                    │ goal_met?                     │                            │
         │                    └───────────────┬───────────────┘                            │
         │                         YES │            │ NO                                   │
         │                              ▼            ▼                                     │
         │                    log_event("attempt_success")   log_event("attempt_failed")   │
         │                    return (state, loop_output)    │                             │
         │                                                   │ if attempt < max-1:         │
         │                                                   │   critic_feedback =         │
         │                                                   │     critic.analyze(...)     │
         │                                                   │   log_event("critic_analysis")│
         │                                                   │   retry_context =           │
         │                                                   │     retry_planner.build_    │
         │                                                   │     retry_context(...)     │
         │                                                   │   log_event("attempt_retry")│
         │                                                   └──► next iteration          │
         │                                                                                │
         └────────────────────────────────────────────────────────────────────────────────┘
                                                 │
                                    (max attempts reached)
                                                 ▼
                    return (state, loop_output)  →  save_task, task_complete, return result
```

## Data flow

- **TrajectoryMemory**: in-memory list of attempt_data (plan with plan_id, step_results, errors, patches_applied, files_modified, goal_met). Each plan has a unique plan_id (Phase 4).
- **Critic.analyze(instruction, attempt_data)**: hybrid — deterministic rules set `failure_reason` and `recommendation`; LLM generates `analysis` and `strategy_hint`. Uses a trajectory summary (not raw StepResult objects) for the LLM via `_summarize_trajectory(plan, step_results)` (max 1000 chars).
- **RetryPlanner.build_retry_context(instruction, trajectory_memory, critic_feedback)**: returns `{ previous_attempts, critic_feedback, strategy_hint }`.
- **Planner**: receives retry_context via `get_plan(..., retry_context=...)` → `plan(instruction, retry_context=...)`. When retry_context is present, prompt order is: **[Strategy Hint]** → **[Previous Attempts]** (plan-structure summary) → **[Planning Guidance]** (diversity: avoid repeating same plan) → **[Instruction]** + critic feedback.

## Trajectory summarization (critic)

The critic does not send raw `StepResult` objects to the LLM. `_summarize_trajectory(plan, step_results)` produces a short structured summary: plan steps (action + description), execution results (action → success/failed, files_modified, patches applied), and "No files modified" when applicable. Capped at 1000 characters.

## Planner diversity guard

When retrying, the planner prompt includes **[Planning Guidance]**: "Avoid repeating the same plan structure as previous attempts. Generate a different strategy if the previous attempt failed. Focus on actions that address the failure reason." **[Previous Attempts]** lists each prior plan as "Plan N: ACTION → ACTION → ..." so the model sees what was already tried.

## Observability events

| Event                         | When |
|-------------------------------|------|
| `attempt_started`             | Start of each attempt (attempt index, max_attempts). |
| `attempt_failed`              | After an attempt when goal_met is False. |
| `attempt_retry`               | Before the next attempt when retrying (attempt index, next_attempt). |
| `attempt_success`             | When goal_met is True; then loop exits. |
| `critic_analysis`             | After a failed attempt (failure_reason, recommendation). |
| `strategy_hint_generated`     | When critic returns a non-empty strategy_hint (attempt, strategy_hint). |
| `trajectory_summary_generated`| After critic runs (attempt, summary_length; full summary not logged). |

## Verification: Phase 1–4 intact

1. **Deterministic runner unchanged** — `run_deterministic` still performs a single plan→steps→validate→record run; only an optional `retry_context` parameter was added and passed to `get_plan`.
2. **Phase 4 (plan identity)** — Every plan has `plan_id`; `completed_steps` are `(plan_id, step_id)`; replanned plans get a new `plan_id`; `next_step()` only considers completions for the current plan.
3. **Attempt loop above deterministic runner** — `run_attempt_loop` is the only caller that passes `retry_context`; it wraps multiple `run_deterministic` calls.
4. **Trajectory memory** — `TrajectoryMemory` stores each attempt’s plan, step_results, errors, patches_applied, files_modified, goal_met.
5. **Critic** — Hybrid: deterministic rules set `failure_reason` and `recommendation`; LLM generates `analysis` and `strategy_hint`. Trajectory summary (not raw step results) is sent to the LLM.
6. **Retry planner** — `RetryPlanner.build_retry_context` returns `{ previous_attempts, critic_feedback, strategy_hint }` for the planner.
7. **Planner receives retry_context** — `get_plan` and `plan()` accept optional `retry_context`; when set, the planner prompt includes strategy hint, previous attempt plans, planning guidance (diversity), instruction, and critic feedback.
8. **Termination** — Loop exits when `goal_met` or when `attempt >= MAX_AGENT_ATTEMPTS - 1` (no further retry).
