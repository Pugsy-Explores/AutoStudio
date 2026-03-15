# Test Tasks

<!-- Evaluation test task definitions. Canonical source: tests/agent_scenarios.json -->

The structured scenario dataset is in **`tests/agent_scenarios.json`** (40 tasks across 8 groups: code_understanding, navigation, simple_edits, multi_line_fixes, multi_file, bug_fixing, feature_addition, refactoring). Phase 5 developer tasks: **`tests/dev_tasks.json`** (40 tasks across bug_fixing, feature_addition, refactoring, code_generation).

Run: `python scripts/run_principal_engineer_suite.py --scenarios`

Original task list (now superseded by agent_scenarios.json):

1. Explain AgentState
2. Explain StepExecutor
3. Find where retry logic exists
4. Explain retrieval_pipeline
5. Explain context_ranker
