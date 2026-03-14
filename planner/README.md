# Planner Module

The planner converts a user instruction into a **sequence of atomic steps**. Each step corresponds to exactly one action category used by the router: **EDIT**, **SEARCH**, **EXPLAIN**, or **INFRA**.

## Architecture

1. **Input**: A single user instruction (e.g. “Find the login handler and update JWT validation”).
2. **LLM**: The instruction is sent to an LLM with `PLANNER_SYSTEM_PROMPT`, which asks for a strict JSON plan.
3. **Parse**: The response is parsed as JSON (markdown code fences are stripped if present).
4. **Validate & normalize**: Steps are validated and actions are normalized to the four allowed categories.
5. **Output**: A structured plan: `{"steps": [{"id", "action", "description", "reason"}, ...]}`.

The planner uses the same endpoint and env vars as the router (`ROUTER_LLM_BASE_URL`, `ROUTER_LLM_MODEL`, `ROUTER_LLM_API_KEY`) but calls the API with a higher `max_tokens` (default 1024, overridable via `PLANNER_MAX_TOKENS`) so multi-step JSON plans are not truncated. No router code is modified.

## Step format

Each step in the plan has:

| Field         | Type   | Description                                      |
|---------------|--------|--------------------------------------------------|
| `id`          | int    | Step index (1-based).                             |
| `action`      | string | One of: `EDIT`, `SEARCH`, `EXPLAIN`, `INFRA`.    |
| `description` | string | Short description of what this step does.        |
| `reason`      | string | Optional rationale for this step.                |

Steps are **atomic**: one step = one action. Order is logical (e.g. SEARCH before EDIT when the user says “find X then change Y”).

## Evaluation metrics

The evaluation script (`planner_eval.py`) reports:

- **step_count_accuracy**: Fraction of examples where the predicted number of steps equals the expected number.
- **action_sequence_accuracy**: Fraction where the ordered list of actions (e.g. `["SEARCH", "EDIT"]`) matches the expected sequence exactly.
- **average_plan_length**: Mean number of steps in the predicted plans over the dataset.
- **latency**: Mean (and optionally P95) time per `plan()` call in seconds.

Additional metrics: step count MAE (mean absolute error), P95 latency.

## Integration with the router

Each planner step maps to **one** router action. A runner can:

1. Call `plan(instruction)` to get a list of steps.
2. For each step, call the existing router with the step’s `description` (or a derived instruction) to get a category and confidence.
3. Execute the appropriate handler (edit, search, explain, infra) per step.

The planner does not call or change the router; it only produces a sequence of steps whose `action` values align with the router’s categories (EDIT, SEARCH, EXPLAIN, INFRA).

## Running evaluation

From the AutoStudio project root:

```bash
python -m planner.planner_eval
```

Optional arguments:

- `--dataset-path PATH`: Use a custom JSON dataset (default: `planner/planner_dataset.json`).
- `--quiet`: Print only the final metrics summary.

## Files

| File                   | Purpose                                                |
|------------------------|--------------------------------------------------------|
| `planner_prompts.py`   | `PLANNER_SYSTEM_PROMPT` for the LLM.                  |
| `planner.py`           | `plan(instruction)` → structured plan dict.            |
| `planner_utils.py`     | `validate_plan`, `normalize_actions`, `extract_step_sequence`. |
| `planner_dataset.json`  | ~50 examples with `instruction` and `expected_steps`.  |
| `planner_eval.py`      | Evaluation script and metrics.                         |
