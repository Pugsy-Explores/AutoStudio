You are a senior staff engineer implementing prompt registry migration and model-based prompt versioning for exploration in Agent V2.

## Context

Audit confirms:

* All exploration prompts are inline (f-strings)
* Prompt Registry exists but is unused in exploration
* LLM model is selected via task (EXPLORATION_V2), not prompt
* CandidateSelector has two distinct prompt variants (single vs batch)

---

## Goal

Refactor exploration prompts to:

1. Use Prompt Registry (no inline prompts)
2. Support model-specific prompt versions
3. Preserve EXACT existing behavior

---

## Constraints (STRICT)

* NO behavior change (byte-for-byte prompt equivalence for default model)
* NO changes to exploration logic (scoper, analyzer, selector, etc.)
* NO model logic inside exploration components
* ONLY extend Prompt Registry for model resolution
* DO NOT modify schemas or LLM execution flow

---

## Phase 1 — Prompt Extraction

For each:

* query_intent_parser.parse
* exploration_scoper._build_prompt
* candidate_selector.select
* candidate_selector.select_batch
* understanding_analyzer.analyze

DO:

1. Extract prompt string into YAML template
2. Replace dynamic values with placeholders:

   * {instruction}
   * {candidates_json}
   * {explored_block}
   * {limit}
   * {file_path}
   * {snippet}

Store under:

```text
agent/prompt_versions/
  exploration.query_intent_parser/v1.yaml
  exploration.scoper/v1.yaml
  exploration.selector.single/v1.yaml
  exploration.selector.batch/v1.yaml
  exploration.analyzer/v1.yaml
```

---

## Phase 2 — Registry Integration

Replace inline prompts with:

```python
template = get_registry().get_instructions(key, model_name=self._model_name)
prompt = template.format(...)
```

Preserve ALL formatting:

* json.dumps usage
* spacing / ordering
* concatenation behavior

---

## Phase 3 — Model-Based Versioning

Extend loader:

```python
load_prompt(name, version="v1", model_name=None)
```

Resolution order:

```text
prompt_versions/{name}/models/{model_name}/v1.yaml
→ prompt_versions/{name}/v1.yaml
```

Normalize model_name (lowercase, safe filename).

---

## Phase 4 — Model Injection

In bootstrap:

* Resolve model used for EXPLORATION_V2
* Pass model_name into ExplorationRunner

In components:

```python
self._model_name
```

No changes to llm_generate_fn signature.

---

## Phase 5 — Regression Safety

Add validation:

* Capture original prompt string
* Compare with new registry-based prompt
* Must be identical for default model

---

## Output

1. Files modified
2. Prompt registry structure
3. Loader changes
4. Example before/after prompt equality proof

---

## Goal

Move from:

```text
inline prompts + no versioning
```

to:

```text
centralized prompts + model-aware selection
```

with zero behavior change.

Implement now.


# 🔄 🔧 Plan Update — ModelConfig Integration

Add this as a **new Phase (before Model Injection)**.

---

## ✅ Phase 3.5 — Model Resolution via modelconfig.json (NEW)

### Goal

Use existing `modelconfig.json` as the **single source of truth** for model selection, and derive prompt variants from it.

---

## 🔍 What to do

### 1. Identify model resolution path

Locate:

```text
model_client.py / call_reasoning_model
→ task_models["EXPLORATION_V2"]
```

👉 This is the **authoritative model name**

---

### 2. Create shared resolver (DO NOT duplicate logic)

Add utility:

```python
def get_model_for_task(task_name: str) -> str:
    return models_config.task_models[task_name]
```

**Implemented:** `agent/models/model_config.py` — `TASK_MODELS` from `models_config.json` with `get_model_for_task(task_name)` using `TASK_MODELS.get(...)` and fallback to `"REASONING"` (aligned with `call_reasoning_model`).

---

### 3. Inject into ExplorationRunner (single source)

In `bootstrap.py`:

```python
model_name = get_model_for_task("EXPLORATION_V2")

ExplorationRunner(
    ...,
    model_name=model_name
)
```

---

### 4. Pass model_name down (no API changes)

In all exploration components:

```python
self._model_name = model_name
```

---

### 5. Registry uses model_name (NO logic in components)

All prompt calls:

```python
template = registry.get_instructions(
    key,
    model_name=self._model_name
)
```

---

## ⚠️ Critical Constraints

* DO NOT hardcode model names anywhere
* DO NOT infer model from prompt
* DO NOT duplicate modelconfig logic
* Registry is the ONLY place doing model → prompt resolution

---

## 🔥 Resolution Flow (final architecture)

```text
modelconfig.json
      ↓
call_reasoning_model (task → model)
      ↓
bootstrap resolves model_name
      ↓
ExplorationRunner(model_name)
      ↓
PromptRegistry.get(key, model_name)
      ↓
correct prompt version loaded
```

---

# 🧠 Why this is the correct design

* Keeps **model selection centralized** (no drift)
* Keeps **prompt selection decoupled**
* Matches real systems:

```text
config → model
registry → prompt
runtime → execution
```

---

# 🔴 What this prevents (very important)

Without this:

```text
prompt versioning becomes inconsistent
model drift across components
hardcoded hacks appear
```

---

# ✅ Minimal impact

* No change to `llm_generate_fn`
* No change to exploration logic
* Only adds **clean dependency injection**

---

# 🔥 One-line truth

```text
Model selection is a system concern — prompt selection is a registry concern.
```

---

## Phase 3.5 — Implementation status (done)

| Area | Path |
|------|------|
| Config source | `agent/models/models_config.json` (`task_models`; env overrides via `model_config` loader) |
| Task → model key | `agent/models/model_config.py` — `TASK_MODELS`, `get_model_for_task` |
| Same lookup for HTTP calls | `agent/models/model_client.py` — `call_reasoning_model` uses `get_model_for_task(task_name or "")` when `model_type` is omitted |
| Bootstrap wiring | `agent_v2/runtime/bootstrap.py` — `model_name=get_model_for_task("EXPLORATION_V2")` for `AgentRuntime` and `create_exploration_runner` |
| Runtime | `agent_v2/runtime/runtime.py` — `AgentRuntime(..., model_name=...)` |
| Runner | `agent_v2/runtime/exploration_runner.py` — `ExplorationRunner(..., model_name=...)` → engine constructors |
| Exploration components | `agent_v2/exploration/query_intent_parser.py`, `exploration_scoper.py`, `candidate_selector.py`, `understanding_analyzer.py` — `self._model_name`; `get_registry().get_instructions(..., model_name=self._model_name)` |
| Registry / loader | `agent/prompt_system/registry.py`, `agent/prompt_system/loader.py` — `model_name` on load paths; `normalize_model_name_for_path` for per-model YAML dirs |
| Versioned prompts | `agent/prompt_versions/exploration.query_intent_parser/`, `exploration.scoper/`, `exploration.selector.single/`, `exploration.selector.batch/`, `exploration.analyzer/` — each with `v1.yaml`; optional `models/<normalized_model_id>/v1.yaml` |
| Tests | `tests/test_exploration_prompt_registry_equivalence.py` — includes `test_get_model_for_task_aligns_with_task_models` |

---
