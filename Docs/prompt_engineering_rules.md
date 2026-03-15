# Prompt Engineering Rules

Governance rules for prompts in AutoStudio (Phase 13).

## 1. One Prompt = One Capability

Each prompt should have a single, well-defined responsibility. Do not combine multiple capabilities (e.g. planning + validation) into one prompt.

## 2. No Prompt > 300 Lines

Keep prompts concise. If a prompt exceeds 300 lines, split it into focused sub-prompts or use the skills library to compose capabilities.

## 3. All Prompts Versioned

All prompts must live in `agent/prompt_versions/{name}/` with explicit version files (e.g. `v1.yaml`, `v2.yaml`). Use `PromptRegistry.get(name, version="latest")` to load.

## 4. All Prompts Evaluated

Every prompt must have corresponding test cases in `tests/prompt_eval_dataset.json`. Run `scripts/run_prompt_ci.py` before merging prompt changes.

## 5. All Prompts Logged

Failures must be logged via `agent/prompt_eval/failure_analysis/failure_logger.py`. Use `log_failure(FailureRecord(...))` when a prompt produces invalid output or a run fails.

## 6. Every Prompt Has Eval Coverage

Every prompt must have at least one test case in `tests/prompt_eval_dataset.json` covering its primary use case. Run `scripts/run_prompt_ci.py --prompt <name>` to verify.

## 7. Context Budget Compliance

Prompt `instructions` must not exceed the model's context budget minus system overhead. Use `ContextBudget.for_model(model_name).allocate(system_tokens=len(instructions)//4)` to verify before deployment.

---

## Summary

| Rule | Enforcement |
|------|-------------|
| 1 prompt = 1 capability | Code review |
| No prompt > 300 lines | Lint / CI check |
| All prompts versioned | Loader requires `prompt_versions/` |
| All prompts evaluated | `run_prompt_ci.py` |
| All prompts logged | `failure_logger.log_failure()` |
| Eval coverage per prompt | `run_prompt_ci.py --prompt <name>` |
| Context budget | `ContextBudget.for_model()` |

---

## Quick Links

| Action | Command / API |
|--------|---------------|
| Load prompt | `get_registry().get("planner")` or `get_instructions("planner", variables={...})` |
| Load with injection guard | `get_registry().get_guarded("planner", user_input=...)` |
| Validate response | `get_registry().validate_response("planner", response, user_input)` → `(is_valid, error_message)` |
| **Guardrails (LLM boundary)** | `call_small_model(..., prompt_name="planner")` / `call_reasoning_model(..., prompt_name="planner")` — injection check always; output validation when `prompt_name` provided |
| Compose with skill | `get_registry().compose("planner", skill_name="planner_skill", repo_context=...)` |
| List versions | `agent.prompt_system.versioning.list_versions("planner")` |
| A/B test prompts | `from agent.prompt_system.versioning import run_ab_test; run_ab_test(name, "v1", "v2", run_fn)` |
| Compare versions | `agent.prompt_system.versioning.prompt_diff.compare_prompts("planner", "v1", "v2")` |
| Run prompt CI | `python scripts/run_prompt_ci.py` |
| Save baseline | `python scripts/run_prompt_ci.py --save-baseline` |
| Log failure | `from agent.prompt_eval.failure_analysis import log_failure; log_failure(FailureRecord(...))` |
| Generate metrics report | `from agent.prompt_system.observability import generate_report; generate_report()` |

---

## API Reference

### PromptRegistry

```python
from agent.prompt_system import get_registry

r = get_registry()
r.get("planner", version="latest")           # PromptTemplate
r.get_instructions("planner")               # str
r.get_instructions("router", variables={"task_description": "..."})
r.get_guarded("planner", user_input="...")  # PromptTemplate (runs injection guard on user_input)
r.validate_response("planner", response, user_input="...")  # (is_valid, error_message)
# Primary enforcement: model_client runs guardrails at LLM boundary. Pass prompt_name for JSON prompts.
r.compose("planner", skill_name="planner_skill", repo_context="...")
r.get_skill("planner_skill")                # dict
r.get_model_type("planner")                 # ModelType
```

### FailureRecord

```python
from agent.prompt_eval.failure_analysis import FailureRecord, log_failure
from datetime import datetime

record = FailureRecord(
    prompt_name="planner",
    version="v1",
    model="gpt-4",
    context="...",
    response="...",
    error_type="invalid_json",  # bad_retrieval | invalid_json | wrong_tool | bad_patch
    timestamp=datetime.now().isoformat(),
)
log_failure(record)  # Writes to dev/failure_logs/planner/{date}.jsonl
```

### PromptTemplate

```python
@dataclass
class PromptTemplate:
    name: str
    version: str
    role: str
    instructions: str
    constraints: list[str]
    output_schema: dict | None
    extra: dict[str, str] | None  # For multi-part (main, end)
```
