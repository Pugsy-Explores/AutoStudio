# Phase 3 — Exploration runner

**Scope:** This document is the authoritative Phase 3 specification. It describes the first **bounded, read-only intelligence** stage (exploration before planning). Code lives in `agent_v2/runtime/exploration_runner.py` when this phase is executed; this file is not executable.

---

## Objective (non-negotiable)

Build a **dedicated exploration stage** that:

```text
- runs BEFORE planning
- gathers structured knowledge
- produces ExplorationResult
```

---

## Hard constraint

```text
NO EDITS
NO WRITES
NO PATCHES
```

---

## Role in the system

```text
User Instruction
   ↓
ExplorationRunner   ← THIS PHASE
   ↓
ExplorationResult
   ↓
Planner
```

If exploration is wrong, the planner becomes a **hallucination engine**.

---

## File to create

```text
agent_v2/runtime/exploration_runner.py
```

---

## Design (lock this)

### Allowed tools

```text
search
open_file
shell (READ-ONLY)
```

**Note:** `shell` must be constrained to read-only operations at dispatch or policy layer; exploration must not invoke destructive commands.

### Forbidden

```text
edit
run_tests
write
patch
```

### Max steps

```text
3–6 steps (HARD LIMIT)
```

(Example implementations may use `max_steps = 5`; must not exceed 6.)

### Output

```text
ExplorationResult (STRICT SCHEMA — see PHASE_1_SCHEMA_LAYER.md)
```

---

## Step 1 — Basic structure

**Target:** `agent_v2/runtime/exploration_runner.py`

**Define:**

```python
class ExplorationRunner:

    def __init__(self, action_generator, dispatcher):
        self.action_generator = action_generator
        self.dispatcher = dispatcher

    def run(self, instruction: str) -> ExplorationResult:
        ...
```

- `action_generator`: component that proposes next steps for exploration (e.g. existing **ActionGenerator** with an exploration-specific entrypoint).
- `dispatcher`: executes steps; after **Phase 2**, it returns **`ExecutionResult`** only (not `ToolResult`).

**Imports:** `ExplorationResult` (and related types) from `agent_v2.schemas.exploration`.

---

## Step 2 — Action constraint layer

**Critical:** Restrict LLM / generator behavior so only read paths are eligible.

```python
ALLOWED_ACTIONS = {"search", "open_file", "shell"}

def _is_valid_action(self, action: str) -> bool:
    return action in ALLOWED_ACTIONS
```

Reject or skip any action not in this set before dispatch.

---

## Step 3 — Loop (controlled)

**Inside `run()`:**

- Initialize `items = []` and `max_steps` within **3–6** (e.g. `5`).
- For each iteration:
  - `step = self.action_generator.next_action_exploration(instruction, items)` (or equivalent API).
  - If `not step` or `step.get("action") == "finish"`: **break**.
  - If not `_is_valid_action(step["action"])`: **continue** (do not dispatch).
  - `result = self.dispatcher.execute(step)` — `result` is **`ExecutionResult`** (Phase 2).
  - Append `(step, result)` to `items`.

**Important:**

```text
NO state reuse from AgentLoop
NO history pollution
```

Exploration keeps its own scratch space (`items`); do not mutate global agent state used by the main loop.

---

## Step 4 — Build `ExplorationItem`

**From:** `agent_v2.schemas.exploration` import `ExplorationItem`.

Map each `(step, result)` to an `ExplorationItem`:

- `item_id`: e.g. `f"item_{idx}"`.
- `type`: align with schema `Literal["file","search","command","other"]` — map from `step["action"]` (e.g. `open_file` → `file`, `search` → `search`, `shell` → `command`).
- **source:** `ref` from `_extract_ref(step)`, `location` optional.
- **content:** `summary` from execution output; `key_points` and `entities` populated from structured data when available (placeholder below is minimal).
- **relevance:** `score`, `reason` (starter: fixed score + short reason; may be refined later).
- **metadata:** `timestamp`, `tool_name` from `ExecutionResult.metadata`.

**Helper:**

```python
def _extract_ref(self, step):
    return (
        step.get("path")
        or step.get("query")
        or step.get("command")
        or "unknown"
    )
```

**Implementation note:** Phase 1 uses nested models (`source`, `content`, `relevance`, `metadata`). Construct `ExplorationItem` with the actual Pydantic field types, not ad-hoc dicts unless the model accepts them.

**Illustrative mapping (adjust to schema):**

```python
def _build_item(self, step, result, idx):
    return ExplorationItem(
        item_id=f"item_{idx}",
        type=...,  # map action → file|search|command|other

        source=...,  # ref=_extract_ref(step), location=None

        content=...,  # summary from result.output.summary; key_points, entities

        relevance=...,  # score, reason

        metadata=...,  # timestamp, tool_name from result.metadata
    )
```

Use `result.output.summary` (and `result.output.data`) from **`ExecutionResult`**, not raw tool payloads.

---

## Step 5 — Build final `ExplorationResult`

**From:** `agent_v2.schemas.exploration` import `ExplorationResult`.

```python
def _build_result(self, instruction, items):
    exploration_items = [
        self._build_item(step, result, idx)
        for idx, (step, result) in enumerate(items)
    ]

    return ExplorationResult(
        exploration_id=...,  # unique id per run, not hard-coded "exp_001" in production
        instruction=instruction,
        items=exploration_items,

        summary=...,  # overall, key_findings, knowledge_gaps, knowledge_gaps_empty_reason (SCHEMAS.md Rule 5)

        metadata=...,  # total_items, created_at
    )
```

**Starter content (replace with real summarization later):**

- `summary.overall`: e.g. `"Basic exploration completed"` until a summarizer exists.
- `summary.key_findings`: derived from item content summaries.
- `summary.knowledge_gaps`: e.g. `["Further analysis required"]` or LLM-generated gaps when safe.
- `metadata.created_at`: ISO timestamp; if `exploration_items` is empty, use `""` or now.

---

## Step 6 — Complete `run()`

Wire steps 2–5:

```python
def run(self, instruction: str) -> ExplorationResult:
    items = []
    max_steps = 5  # keep within 3–6

    for i in range(max_steps):
        step = self.action_generator.next_action_exploration(instruction, items)

        if not step or step.get("action") == "finish":
            break

        if not self._is_valid_action(step["action"]):
            continue

        result = self.dispatcher.execute(step)

        items.append((step, result))

    return self._build_result(instruction, items)
```

**Contract:** `action_generator.next_action_exploration` must exist and accept `(instruction, items)` (or document the actual signature in code). Align `step` shape with what **dispatcher** expects.

---

## Step 7 — Validation (mandatory)

**Manual / integration:**

```bash
python -m agent_v2 "Find AgentLoop implementation"
```

**Expect:**

```text
✅ 3–5 exploration steps (within 3–6 cap)
✅ no edit / run_tests / write / patch
✅ summary present on ExplorationResult
```

---

## Common failure modes

```text
❌ Exploration performing edits or writes
❌ Returning raw tool outputs instead of ExplorationResult
❌ Too many steps (>6)
❌ LLM free-roaming (no action restriction)
```

---

## Exit criteria (strict)

```text
✅ ExplorationRunner exists
✅ Only read tools used (search, open_file, read-only shell)
✅ Returns valid ExplorationResult
✅ Max steps enforced (3–6, e.g. 5)
```

---

## Principal verdict

```text
Blind agent ❌ → Informed agent ✅
```

Without exploration, **planner = hallucination**.

---

## Next step

After validation:

👉 **Phase 3 done** (implementation + checks)

Then **Phase 4 — Planner v2 (first-class planning)**. See `PHASED_IMPLEMENTATION_PLAN.md`.
