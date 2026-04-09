# --cursor prompt ---
You are a staff engineer auditing the model configuration and routing readiness.

Focus ONLY on model config, model selection logic, and usage across AgentV2.

Do NOT implement new routing yet.

---

## GOAL

Check if current system can support:

* task-based model selection
* local vs hosted vs frontier models
* future routing without refactor

---

## STEP 1 — MODEL CONFIG STRUCTURE

Analyze:

* where models are defined
* schema of model config

Check:

* does each model have:

  * name
  * provider
  * context window
  * cost (optional)
  * capabilities (reasoning, coding, fast, etc.)

---

## STEP 2 — USAGE ANALYSIS

Find:

* where model is selected today

Check:

* is selection:

  * hardcoded?
  * passed via config?
  * embedded inside planner/executor?

---

## STEP 3 — ROUTING READINESS

Evaluate if current system supports:

* selecting model per task (ExecutionTask-level)
* switching between:

  * local models
  * API models
* injecting model choice at runtime

---

## STEP 4 — GAPS

Identify:

* missing fields in model config
* tight coupling (planner/executor tied to specific model)
* lack of abstraction layer

---

## STEP 5 — MINIMAL DESIGN (NO IMPLEMENTATION)

Propose:

* simplest possible routing interface:

```plaintext
select_model(task, context) -> model
```

* where it should live
* how it plugs into execution (before dispatch)

---

## OUTPUT

Provide:

1. Current model config summary
2. Where model selection happens
3. Key limitations (top 3–5)
4. Minimal routing design (no overengineering)

---

Rules:

* Keep it simple
* No scoring systems
* No ML-based routing
* No cost optimization yet

Focus:
👉 readiness for simple rule-based routing


# ---audit----
# Model Configuration & Routing Readiness Audit

## 1. CURRENT MODEL CONFIG SUMMARY

**Location:** `agent/models/models_config.json`

**Schema Structure:**

```json
{
  "models": {
    "MODEL_KEY": {
      "name": "Display Name",
      "endpoint": "OpenAI-compatible HTTP URL"
    }
  },
  "task_models": {
    "task_name": "MODEL_KEY"
  },
  "task_params": {
    "task_name": {
      "temperature": 0.0,
      "max_tokens": 32456,
      "request_timeout_seconds": 600,
      "frequency_penalty": 0.5,
      "presence_penalty": 0.2
    }
  },
  "model_call": {
    "temperature": 0.0,
    "max_tokens": null,
    "request_timeout_seconds": 600
  }
}
```

**Current Models:**
- `SMALL`: Qwen2B (localhost:8082)
- `REASONING`: Qwen2.5-Coder-7B (localhost:8081)
- `REASONING_V2`: Qwen14B (localhost:8003)
- `GOIN_2_57B`: GOIN 2.57B (localhost:8081)

**Entry Points:**
- `call_small_model(..., task_name: str)` → resolves model from `TASK_MODELS[task_name]`
- `call_reasoning_model(..., task_name: str, model_type: str | None)` → resolves model from `TASK_MODELS[task_name]` or `model_type`
- `get_model_for_task(task_name: str) → str` → returns model key from config
- `get_endpoint_for_model(model_key: str) → str` → returns HTTP endpoint

---

## 2. WHERE MODEL SELECTION HAPPENS

**Static config-driven selection:**

```18:22:agent/models/model_config.py
_DEFAULT_MODELS = {
    "SMALL": {"name": _DEFAULT_SMALL_NAME, "endpoint": _DEFAULT_SMALL_ENDPOINT},
    "REASONING": {"name": _DEFAULT_REASONING_NAME, "endpoint": _DEFAULT_REASONING_ENDPOINT},
    "REASONING_V2": {"name": _DEFAULT_REASONING_V2_NAME, "endpoint": _DEFAULT_REASONING_V2_ENDPOINT},
}

_DEFAULT_TASK_MODELS = {
    "query rewriting": "SMALL",
    "validation": "SMALL",
    "EXPLAIN": "REASONING",
    "routing": "SMALL",
    "planner": "REASONING",
    "PLANNER_DECISION_PLAN": "REASONING",
    "PLANNER_DECISION_ACT": "REASONING",
    # ... many more tasks
}
```

**Runtime selection in PlannerV2:**

```251:264:agent_v2/planner/planner_v2.py
def _resolve_planner_model_task_name(self, ctx: PlannerPlanContext) -> str:
    """``models_config.json`` task key for this planner call (``task_models`` + ``task_params``).

    - Failure replans (``PlannerPlanContext.replan``): ``PLANNER_REPLAN_PLAN`` or
      ``PLANNER_REPLAN_ACT`` (matches ``tool_policy.mode``).
    - Safe / read-only tool policy (CLI ``--mode plan``): ``PLANNER_DECISION_PLAN``
    - Act / execute tool policy (CLI ``--mode act``): ``PLANNER_DECISION_ACT``
    """
    if ctx.replan is not None:
        return "PLANNER_REPLAN_PLAN" if self._tool_policy.mode == "plan" else "PLANNER_REPLAN_ACT"
    if self._tool_policy.mode == "plan":
        return "PLANNER_DECISION_PLAN"
    return "PLANNER_DECISION_ACT"
```

**Model call sites across AgentV2:**
- `agent_v2/planner/planner_v2.py` - planner calls via `call_reasoning_model(model_task=...)`
- `agent_v2/runtime/exploration_runner.py` - exploration LLM calls
- `agent_v2/validation/answer_validator.py` - validation LLM calls
- `agent_v2/exploration/answer_synthesizer.py` - answer synthesis

---

## 3. KEY LIMITATIONS (TOP 5)

### **1. No ExecutionTask-level model selection**
- `ExecutionTask` schema has no `model_key` field
- Model selection is hardcoded to `task_name` string matching
- Cannot override model per execution task

### **2. Tight coupling: task_name → model_key**
- `call_small_model` raises `ValueError` if `task_name` not in `TASK_MODELS`
- No flexibility to pick different model for same task
- All routing must go through central config

### **3. Missing context window tracking**
- No `context_window` field in model config
- No validation that prompt fits model capacity
- No automatic prompt truncation or model switching based on length

### **4. No provider/hosted vs local abstraction**
- Endpoint URL is the only provider indicator
- No `provider: "openai" | "anthropic" | "local"` field
- Cannot easily switch between API providers

### **5. No capabilities metadata**
- No `capabilities: ["reasoning", "coding", "fast"]` schema field
- Cannot select model based on required capabilities
- All knowledge is implicit via task → model mapping in JSON

---

## 4. MINIMAL ROUTING DESIGN (NO IMPLEMENTATION)

### **Interface**

```python
#agent/models/model_router_v2.py (NEW MODULE)

def select_model(
    task_name: str | None,
    tool: str | None = None,
    context_size_estimate: int | None = None,
    execution_context: dict | None = None,
) -> str:
    """
    Simple rule-based model selection.
    
    Selection priority:
    1. Configured task_models[task_name] if exists
    2. Heuristic fallback based on tool/action type
    3. Default to REASONING
    
    Args:
        task_name: Known task name (e.g., "PLANNER_DECISION_ACT")
        tool: Tool name for heuristic fallback (e.g., "edit", "search")
        context_size_estimate: Token count estimate (for context window checks)
        execution_context: Runtime context for future routing decisions
    
    Returns:
        Model registry key (e.g., "SMALL", "REASONING", "REASONING_V2")
    """
```

### **Where it plugs in**

**Integration points:**

```1:agent/models/model_client.py
# BEFORE:
def call_small_model(..., task_name: str):
    model_key = TASK_MODELS[task_name]  # FAILS if not in dict
    endpoint = get_endpoint_for_model(model_key)
    # ...

# AFTER:
def call_small_model(..., task_name: str, model_key: str | None = None):
    model_key = model_key or select_model(task_name)
    endpoint = get_endpoint_for_model(model_key)
    # ...
```

**ExecutionTask injection:**

```1:agent_v2/schemas/execution_task.py
class ExecutionTask(BaseModel):
    id: str
    tool: str
    input_hints: dict[str, Any]
    model_key: str | None = None  # ADD THIS FIELD
    # ...
```

**Executor integration:**

```565:599:agent_v2/runtime/dag_executor.py
def _dispatch_once(self, task: ExecutionTask, state: Any) -> ExecutionResult:
    # BEFORE: executor passes through, no model override
    dispatch_dict = _to_dispatch_step(task, merged)
    res = self.dispatcher.execute(dispatch_dict, state)
    
    # AFTER: pass model_key to dispatcher
    dispatch_dict["model_key"] = task.model_key or select_model(
        task_name=None,  # Or derive from task.metadata
        tool=task.tool,
    )
    res = self.dispatcher.execute(dispatch_dict, state)
```

### **Routing logic (simple rule-based)**

```python
def select_model(task_name, tool, context_size_estimate, execution_context):
    # PRIORITY 1: Use configured task_models mapping
    if task_name and task_name in TASK_MODELS:
        model_key = TASK_MODELS[task_name]
        
        # Basic context window check
        if context_size_estimate:
            max_tokens = _get_context_window(model_key)
            if context_size_estimate > max_tokens:
                logger.warning(f"Context {context_size_estimate} exceeds {model_key} window {max_tokens}; upgrading to REASONING_V2")
                return "REASONING_V2"
        return model_key
    
    # PRIORITY 2: Heuristic based on tool type
    tool_heuristics = {
        "shell": "SMALL",           # Command execution uses small model
        "search": "REASONING",      # Search reasoning uses larger model
        "open_file": "SMALL",       # File reading uses small model
        "edit": "REASONING",        # Code editing uses reasoning model
        "run_tests": "SMALL",       # Test output parsing uses small model
    }
    if tool and tool in tool_heuristics:
        return tool_heuristics[tool]
    
    # PRIORITY 3: Default to REASONING
    return "REASONING"
```

### **Minimal extension to model config**

```json
{
  "models": {
    "SMALL": {
      "name": "Qwen2B",
      "endpoint": "http://localhost:8082/v1/chat/completions",
      "context_window": 32768      // ADD THIS
    },
    "REASONING": {
      "name": "Qwen2.5-Coder-7B",
      "endpoint": "http://localhost:8081/v1/chat/completions",
      "context_window": 131072    // ADD THIS
    }
  }
}
```

---

## 5. ROUTING READINESS ASSESSMENT

**✓ READY FOR:**
- Task-based model selection via `TASK_MODELS` mapping
- Per-task parameter tuning via `task_params`
- Multiple model endpoints (local vs hosted)
- Simple rule-based routing

**⚠ REQUIRES MINIMAL EXTENSION FOR:**
- ExecutionTask-level model selection (add `model_key` field)
- Context window tracking (add field to config, add validation)
- Runtime model injection (integrate `select_model` in executor)

**✗ NOT READY FOR (NO BLOCKER):**
- Scoring-based routing (not in scope)
- ML-based routing (not in scope)
- Cost optimization (not in scope)

---

## CONCLUSION

The system is **well-structured for rule-based routing** with minimal changes:

1. **Model config structure** is clean, just needs `context_window` field added
2. **Task-based selection** already exists via `TASK_MODELS` mapping
3. **Tight coupling** is the main issue — need to decouple `call_small_model` requiring `task_name`
4. **ExecutionTask injection** is straightforward — add `model_key` field

The proposed `select_model(task, context) -> model` interface can be implemented in a single new module (`agent/models/model_router_v2.py`) with ~50 lines of code, integrated at 3-4 entry points, without breaking existing architecture.

The system is **ready for simple rule-based routing** without refactoring — just extend, don't replace.

# ---- corrections needed ----
🎯 Minimal Phase 3 (correct scope)
1. Add ONLY this
ExecutionTask:
  model_key: Optional[str] = None
2. Modify model call (core change)

Current:

model_key = TASK_MODELS[task_name]

Replace with:

model_key = provided_model_key or TASK_MODELS.get(task_name, "REASONING")
3. Executor injects model_key
model_key = task.model_key or TASK_MODELS.get(task.tool, "REASONING")
dispatch_dict["model_key"] = model_key

👉 No router module yet
👉 No abstraction yet

❌ Remove from plan (for now)
task_type field ❌
select_model(...) abstraction ❌
context window logic ❌
heuristics mapping layer ❌
🧠 Why this is better

You get:

per-task override ✅
backward compatibility ✅
zero complexity increase ✅

And later:

you can ADD routing logic without refactor

🧠 Final simplified model
ExecutionTask → model_key (optional)
            ↓
Executor decides model
            ↓
Model client executes
🧠 Principle

First enable control → then add intelligence