# Phase 12 — Developer Workflow Integration

The **workflow layer** (`agent/workflow/`) turns AutoStudio from a coding agent into a developer teammate that operates inside the real software development loop:

```
issue → agent solution → PR → CI → review → merge
```

---

## Overview

| Module | Purpose |
|--------|---------|
| `issue_parser.py` | Convert GitHub/GitLab issue text into structured tasks (type, module, symbol, priority) |
| `pr_generator.py` | Generate PR title and description from workspace, patches, and test results |
| `ci_runner.py` | Run pytest and ruff with configurable timeout |
| `code_review_agent.py` | Review patches for style, security, large diffs, missing tests |
| `developer_feedback.py` | Apply human feedback via critic → retry planner → improved patch |
| `workflow_controller.py` | Orchestrate full flow: issue → parse → run_multi_agent → PR → CI → review |

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `autostudio issue <text>` | Parse issue and run full workflow (issue → task → agent → PR → CI → review) |
| `autostudio fix <instruction>` | Run multi-agent solve only (no PR/CI/review) |
| `autostudio pr` | Display PR from last workflow run |
| `autostudio review` | Review last patch |
| `autostudio ci` | Run CI (pytest, ruff) on project root |

---

## Flow

```
issue text
  → issue_parser.parse_issue() → structured task
  → run_multi_agent(goal, project_root)  [Phase 9 supervisor]
  → pr_generator.generate_pr(workspace, patches, test_results)
  → ci_runner.run_ci(project_root)
  → code_review_agent.review_patch(patches, test_results)
  → [optional] developer_feedback.apply_feedback(comment) → re-run agent
```

---

## Safety Limits

| Constant | Default | Env Override | Description |
|----------|---------|--------------|-------------|
| MAX_FILES_PER_PR | 10 | MAX_FILES_PER_PR | Max files per PR |
| MAX_PATCH_LINES | 500 | MAX_PATCH_LINES | Max patch lines |
| MAX_CI_RUNTIME_SECONDS | 600 | MAX_CI_RUNTIME_SECONDS | CI timeout (pytest, ruff) |

---

## Trace Events

The workflow layer emits events via `log_event()`:

- `issue_parsed` — task structured from issue text
- `pr_generated` — PR title and description created
- `ci_started` / `ci_passed` / `ci_failed` — CI execution
- `review_completed` — code review completed
- `developer_feedback` — human feedback applied

---

## Persistence

- **Last workflow result:** `.agent_memory/last_workflow.json` — written by `issue` and `fix`; read by `pr` and `review`
- **Traces:** `.agent_memory/traces/` — full trace with workflow events

---

## Evaluation

```bash
python scripts/run_workflow_eval.py           # Full eval
python scripts/run_workflow_eval.py --mock    # Mock mode for CI
python scripts/run_workflow_eval.py --limit 3 # Quick validation
```

**Dataset:** `tests/workflow_tasks.json` (8 tasks: fix_failing_test, implement_feature, refactor_module, add_logging)

**Output:** `reports/workflow_eval_report.json`

**Metrics:** `pr_success_rate`, `ci_pass_rate`, `developer_acceptance_rate`, `avg_retries_per_task`, `pr_merge_latency`, `issue_to_pr_success`

---

## See Also

- [dev/roadmap/phase_12_last_stop.md](../dev/roadmap/phase_12_last_stop.md) — Full Phase 12 roadmap
- [CONFIGURATION.md](CONFIGURATION.md) — Phase 12 config variables
- [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) — CLI commands
