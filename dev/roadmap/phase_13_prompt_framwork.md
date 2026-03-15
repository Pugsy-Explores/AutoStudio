Cursor Implementation Plan

AutoStudio Prompt Infrastructure Framework

Phase 1 — Prompt Infrastructure Refactor

Goal: separate prompt content from prompt governance

Create a new module:

agent/prompt_system/

Structure:

prompt_system/
    registry.py
    loader.py
    prompt_template.py
    prompt_context_builder.py
    prompt_versioning.py

Tasks:

PromptRegistry

Create a registry mapping:

prompt_name → prompt_file
prompt_name → prompt_version
prompt_name → model_type

Example:

planner → planner_system.yaml
router → model_router.yaml
critic → critic_system.yaml

PromptLoader

Central loader that:

loads YAML
applies template variables
injects guardrails
injects context

PromptTemplate

Create a class:

PromptTemplate
    name
    version
    role
    instructions
    constraints
    output_schema

Prompts should become structured objects instead of plain strings.

Phase 2 — Prompt Versioning Layer

Goal: treat prompts like code

Production teams version prompts the same way they version software.

Create:

agent/prompt_system/versioning/

Files:

prompt_version_store.py
prompt_diff.py
prompt_history.py

Implementation:

prompt_versions/
    planner/
        v1.yaml
        v2.yaml
    router/
        v1.yaml

Capabilities:

get_prompt("planner", version="latest")
get_prompt("planner", version="v1")
compare_prompts(v1, v2)
Phase 3 — Guardrail Layer

Goal: prevent prompt misuse and injection

Guardrails should exist outside prompts, not inside them.

Create:

agent/prompt_system/guardrails/

Modules:

prompt_injection_guard.py
output_schema_guard.py
safety_policy.py
constraint_checker.py

Guardrails should check:

prompt injection
output format
tool usage
unsafe operations

Example:

if output not valid JSON:
    reject
Phase 4 — Prompt Skills Library

Goal: modular prompt capabilities

Create:

agent/prompt_system/skills/

Examples:

planner_skill.yaml
patch_generation_skill.yaml
code_search_skill.yaml
refactor_skill.yaml
test_fix_skill.yaml
architecture_analysis_skill.yaml

Each skill defines:

goal
tools allowed
expected output format
constraints

Skills are composed dynamically.

Example:

planner_prompt
+ refactor_skill
+ repo_context
Phase 5 — Context Engineering Layer

Goal: control context quality

Context engineering is now considered a core discipline in LLM systems.

Create:

agent/prompt_system/context/

Modules:

context_budget_manager.py
context_ranker.py
context_pruner.py
context_summarizer.py

Responsibilities:

limit tokens
remove duplicates
prioritize relevant files
summarize large code blocks
Phase 6 — Prompt Evaluation Framework

Goal: automated prompt testing

Create:

agent/prompt_eval/

Files:

eval_runner.py
prompt_benchmark.py
prompt_score.py
prompt_dataset_loader.py

Dataset:

tests/prompt_eval_dataset.json

Example test case:

task: rename function across module
expected_actions:
    SEARCH
    SEARCH
    EDIT

Evaluation metrics:

action accuracy
JSON validity
tool correctness
task success
Phase 7 — Failure Logging System

Goal: learn from failures

Create:

agent/prompt_eval/failure_analysis/

Files:

failure_logger.py
failure_patterns.py
failure_cluster.py

Failures stored:

prompt
model
context
response
error_type

Example patterns:

bad retrieval
invalid JSON
wrong tool
bad patch
Phase 8 — Retry Strategy Framework

Goal: structured recovery

Create:

agent/prompt_system/retry_strategies/

Examples:

retry_with_stricter_prompt
retry_with_more_context
retry_with_different_model
retry_with_critic_feedback

Example pipeline:

planner fails
→ critic prompt
→ retry planner
Phase 9 — Prompt CI Pipeline

Goal: test prompts before deployment

Create script:

scripts/run_prompt_ci.py

Pipeline:

load prompt
run 100 evaluation tasks
compare with baseline
report regression

Prompt updates should fail CI if:

task_success drops
JSON validity drops
tool misuse increases
Phase 10 — Observability

Goal: see what prompts are doing

Create:

agent/prompt_system/observability/

Metrics:

prompt_usage
prompt_failure_rate
avg_tokens
tool_usage

Log example:

planner_prompt v2
success_rate = 72%
Phase 11 — Prompt Governance Rules

Add rules file:

docs/prompt_engineering_rules.md

Rules:

1 prompt = 1 capability
no prompt > 300 lines
all prompts versioned
all prompts evaluated
all prompts logged
Phase 12 — Integration

Update existing modules:

planner
router
critic
retry_planner

Replace direct prompt loading with:

PromptRegistry.get("planner")
Final Architecture
Prompt System
│
├ prompt_registry
├ prompt_loader
├ prompt_versioning
├ guardrails
├ skills_library
├ context_engineering
├ evaluation
├ retry_strategies
└ observability
What This Gives You

After this framework:

prompts become maintainable
failures become measurable
behavior becomes predictable