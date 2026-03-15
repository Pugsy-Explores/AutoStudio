Phase 12 — Developer Workflow Integration
Objective

Turn AutoStudio from:

coding agent

into:

developer teammate

The agent should operate inside the real software development loop:

issue
→ agent solution
→ PR
→ CI
→ review
→ merge
Phase-12 Architecture

Add a new layer:

agent/workflow/

Modules:

issue_parser.py
pr_generator.py
code_review_agent.py
ci_runner.py
developer_feedback.py
workflow_controller.py
Step 1 — Issue → Task Interface

Create:

agent/workflow/issue_parser.py

Purpose:

Convert GitHub/GitLab issues into structured tasks.

Example input:

Issue #152:
Retry logic fails when StepExecutor hits timeout.

Output:

task:
  type: bug_fix
  module: execution
  symbol: StepExecutor
  priority: medium

Pipeline:

issue text
→ intent classifier
→ symbol detection
→ structured task
Step 2 — Pull Request Generator

Create:

agent/workflow/pr_generator.py

Purpose:

Automatically create a clean PR.

PR template:

Title:
Fix retry logic in StepExecutor

Description:
The retry logic failed when timeout occurred.

Changes:
- Updated retry condition
- Added logging
- Added regression test

Files modified:
executor.py
test_executor.py

The PR should include:

patch
reasoning summary
test results
trace reference
Step 3 — CI Execution Agent

Create:

agent/workflow/ci_runner.py

Purpose:

Run validation automatically.

Commands:

pytest
lint
type check
format

Pipeline:

patch
→ run tests
→ collect failures
→ send to critic

CI/CD integration is critical because real coding agents must interact with version control and automated pipelines during development.

Step 4 — Code Review Agent

Create:

agent/workflow/code_review_agent.py

Purpose:

Add a review layer before PR submission.

Checks:

style violations
security risks
large diffs
missing tests

Example review output:

Review Summary:
- patch valid
- tests added
- logging consistent
- minor style issue

This mimics tools like automated AI code-review systems used in modern dev pipelines.

Step 5 — Developer Feedback Loop

Create:

agent/workflow/developer_feedback.py

Purpose:

Capture human feedback.

Example:

Developer comment:
"Retry should be exponential backoff."

Agent response:

critic → retry planner → improved patch

This closes the human-AI loop.

Step 6 — Workflow Controller

Create:

agent/workflow/workflow_controller.py

This orchestrates:

issue
↓
task generation
↓
agent solve
↓
PR creation
↓
CI validation
↓
review
↓
developer feedback
Full Phase-12 Flow

Example task:

GitHub issue:
Fix retry bug in executor

Execution:

issue_parser
↓
supervisor_agent
↓
planner
↓
localization
↓
edit
↓
test
↓
critic
↓
pr_generator
↓
ci_runner
↓
review_agent
↓
PR ready
CLI Commands (Developer UX)

Add commands:

autostudio issue <issue_id>
autostudio fix
autostudio pr
autostudio review
autostudio ci

Example:

autostudio issue 152

Output:

task parsed
plan generated
running agent...
PR created
Observability Updates

Add trace events:

issue_parsed
pr_generated
ci_started
ci_passed
review_completed
developer_feedback

This maintains inspectability.

Evaluation Metrics

Add to:

dev/evaluation/metrics.md

New metrics:

PR success rate
CI pass rate
developer acceptance rate
average retries per task
PR merge latency
Dataset for Phase-12

Create:

tests/workflow_tasks.json

Examples:

fix failing test issue
implement small feature request
refactor module request
add missing logging

Measure:

issue_to_pr_success
ci_success
review_pass_rate
Phase-12 Safety Rules

Add limits:

MAX_FILES_PER_PR = 10
MAX_PATCH_LINES = 500
MAX_CI_RUNTIME = 10 minutes

This prevents destructive edits.

Phase-12 Exit Criteria

Phase-12 is complete when AutoStudio can:

• read GitHub issues
• generate patches
• create PRs
• pass CI
• respond to review comments

That means the system can perform real engineering work.

Architecture After Phase-12
Developer Interface
CLI / IDE

Workflow Layer
issue parser
PR generator
CI runner
review agent

Orchestration
supervisor + role agents

Reflection
critic
retry planner

Repository Intelligence
architecture map
impact analyzer

Localization
dependency traversal

Execution
editing pipeline

Observability
trace logger
metrics