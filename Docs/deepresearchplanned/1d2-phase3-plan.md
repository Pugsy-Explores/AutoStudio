# --- plan ---
Phase 3: Per-Task Model Override

Goal

Enable model selection at ExecutionTask level by adding an optional model_key field and updating the model client to accept optional model overrides.

Changes

1. Add model_key field to ExecutionTask

File: agent_v2/schemas/execution_task.py

Add optional model_key field to the ExecutionTask class:

class ExecutionTask(BaseModel):
    """Single DAG node — the only mutable runtime unit during execution."""

    id: str
    tool: str
    model_key: str | None = None  # ADD THIS LINE
    dependencies: list[str] = Field(default_factory=list)
    # ... rest of fields unchanged

2. Modify call_small_model to accept optional model_key

File: agent/models/model_client.py

Update call_small_model signature and model resolution logic:

def call_small_model(
    prompt: str,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
    system_prompt: Optional[str] = None,
    prompt_name: Optional[str] = None,
    debug_replanner: bool = False,
    model_key: Optional[str] = None,  # ADD THIS PARAMETER
) -> str:
    """Call the model for the given task. Returns model output text.
    When task_name is set, uses params and endpoint from models_config (task_params, task_models).
    When model_key is set, uses that model_key directly (overrides task_models).
    When neither is set, defaults to SMALL model endpoint.
    Guardrails: injection check on prompt before call (always when ENABLE_PROMPT_GUARDRAILS=1)."""
    _run_guardrails_pre(prompt)
    _log_bound_prompt_for_llm_call(task_name)
    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    
    # Use provided model_key, or lookup from task_models, or default to REASONING
    resolved_model_key = model_key or TASK_MODELS.get(task_name, "REASONING")
    endpoint = get_endpoint_for_model(resolved_model_key)
    # ... rest unchanged

Remove the strict validation that raises ValueError (lines 1032-1036).
Replace the model_key lookup (line 1037) with the new resolve logic.

3. Inject model_key in executor dispatch

File: agent_v2/runtime/dag_executor.py

Update _dispatch_once method to inject model_key into dispatch_dict:

def _dispatch_once(self, task: ExecutionTask, state: Any) -> ExecutionResult:
    md = _metadata_dict(state)
    md["executor_dispatch_count"] = int(md.get("executor_dispatch_count", 0)) + 1

    # Validate arguments frozen before execution
    if not task.arguments_frozen and task.tool != "finish":
        logging.warning(f"Task {task.id}: executing with unfrozen arguments")

    # Resolve model_key for this task
    resolved_model_key = task.model_key or TASK_MODELS.get(task.tool, "REASONING")

    # Immutable deep copy of arguments for this execution attempt
    start_time = time.time()
    try:
        merged = json.loads(json.dumps(task.arguments))
    except (TypeError, ValueError) as e:
        logging.warning(f"Task {task.id}: deep copy failed, using shallow copy: {e}")
        merged = dict(task.arguments)

    guard = self._plan_safe_guard(state, task, merged)
    if guard is not None:
        # ... guard logic unchanged ...

    if task.tool == "shell":
        res = self._dispatch_shell(task, merged, state)
        # ... shell logic unchanged ...

    # Inject model_key into dispatch_dict
    dispatch_dict = _to_dispatch_step(task, merged)
    dispatch_dict["model_key"] = resolved_model_key  # ADD THIS LINE
    res = self.dispatcher.execute(dispatch_dict, state)
    # ... rest unchanged ...

Note: Need to import TASK_MODELS at the top of dag_executor.py:

from agent.models.model_config import TASK_MODELS

4. Modify call_reasoning_model for consistency

File: agent/models/model_client.py

Update call_reasoning_model signature similarly (line 1119):

def call_reasoning_model(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
    model_type: Optional[str] = None,
    prompt_name: Optional[str] = None,
    debug_replanner: bool = False,
    model_key: Optional[str] = None,  # ADD THIS PARAMETER
) -> str:
    # ...
    # Use provided model_key, or model_type, or lookup from task_models
    resolved_model_key = model_key or model_type or get_model_for_task(task_name or "")
    endpoint = get_endpoint_for_model(resolved_model_key)
    # ... rest unchanged

Data Flow

flowchart TD
    A[ExecutionTask] -->|model_key optional| B[Executor _dispatch_once]
    B -->|resolve model_key| C{model_key set?}
    C -->|Yes| D[Use provided model_key]
    C -->|No| E[TASK_MODELS.get tool, REASONING]
    D --> F[Inject into dispatch_dict]
    E --> F
    F --> G[Dispatcher.execute]
    G --> H[call_small_model / call_reasoning_model]
    H -->|model_key param| I{model_key provided?}
    I -->|Yes| J[Use provided model_key]
    I -->|No| K[TASK_MODELS.get task_name, REASONING]
    J --> L[get_endpoint_for_model]
    K --> L
    L --> M[_call_chat]

Testing Strategy





Verify backward compatibility: create plans without model_key field



Test model override: set ExecutionTask.model_key = "SMALL" or "REASONING_V2"



Verify fallback: set model_key = None, ensure default behavior works



Verify executor injection: check dispatch_dict contains model_key

Benefits





Per-task model override capability



Zero increase in complexity



Backward compatible (model_key defaults to None)



Foundation for future routing layer without refactor

