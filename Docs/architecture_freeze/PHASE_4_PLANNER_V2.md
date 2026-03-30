# Phase 4 — Planner v2 (first-class)

**Scope:** This document is the authoritative Phase 4 specification. It defines the **production planner** that turns exploration into a strict plan. Code lives in `agent_v2/planner/planner_v2.py` when this phase is executed; this file is not executable.

---

## Objective (non-negotiable)

Create a planner that:

```text
PlannerInput (ExplorationResult | ReplanContext) → PlanDocument (STRICT)
```

---

## Hard rules

```text
- Planner MUST accept PlannerInput per SCHEMAS.md (initial: ExplorationResult; replan: ReplanContext)
- Planner MUST output valid PlanDocument
- Planner MUST NOT call tools
- Planner MUST NOT execute anything
```

---

## Role in the system

```text
ExplorationRunner → ExplorationResult
                     ↓
                 Planner v2   ← THIS PHASE
                     ↓
                 PlanDocument
                     ↓
                 PlanExecutor  (Phase 5)
```

If Phase 4 is wrong: execution becomes chaotic and replanning loses meaning.

---

## File to create

```text
agent_v2/planner/planner_v2.py
```

(Ensure `agent_v2/planner/__init__.py` exists for imports when implementing.)

---

## Step 1 — Basic structure

**Target:** `agent_v2/planner/planner_v2.py`

```python
from agent_v2.schemas.exploration import ExplorationResult
from agent_v2.schemas.plan import PlanDocument


class PlannerV2:

    def __init__(self, llm):
        self.llm = llm

    def plan(
        self,
        instruction: str,
        exploration: ExplorationResult,
        deep: bool = False,
    ) -> PlanDocument:
        ...
```

**Architecture note:** AutoStudio requires model calls through the **model router** (or designated client), not ad-hoc SDK calls in business logic. At implementation time, replace `llm` with the project’s router/client contract while keeping **PlannerV2** free of tool execution.

---

## Step 2 — Prompt builder (critical)

Force **structure + grounding** from `ExplorationResult` (summary, items, sources).

**Add:**

```python
def _build_prompt(
    self,
    instruction: str,
    exploration: ExplorationResult,
    deep: bool,
) -> str:
    return f"""
You are a senior software engineer planning a solution.

TASK:
{instruction}

EXPLORATION SUMMARY:
{exploration.summary.overall}

KEY FINDINGS:
{exploration.summary.key_findings}

KNOWLEDGE GAPS:
{exploration.summary.knowledge_gaps}

SOURCES:
{[item.source.ref for item in exploration.items]}

REQUIREMENTS:

1. Create a step-by-step plan
2. Each step MUST have:
   - step_id
   - type
   - goal
   - action
   - dependencies
3. Allowed actions:
   search, open_file, edit, run_tests, shell, finish

4. Constraints:
   - max 8 steps
   - must include finish step
   - must NOT hallucinate files or functions
   - must reference sources when relevant

5. Include:
   - understanding
   - risks
   - completion_criteria

Return STRICT JSON only.
"""
```

**Refinements (recommended):**

- If `deep` is true, add instructions for deeper decomposition or risk analysis (keep output still strict JSON).
- Serialize `key_findings` / `knowledge_gaps` explicitly (e.g. bullet lines) if f-string representation is ambiguous.

---

## Step 3 — Call LLM

```python
def _call_llm(self, prompt: str) -> dict:
    response = self.llm(prompt)

    # Assume JSON output
    return json.loads(response)
```

**Constraints:**

- Do not over-engineer parsing in v1; **do** handle empty / non-JSON responses in production (see hardening).
- Response must be parseable into fields that `_build_plan` expects (`steps`, `understanding`, `sources`, `risks`, `completion_criteria`, etc.).

---

## Step 4 — Build `PlanDocument`

**From:** `agent_v2.schemas.plan` import `PlanDocument`, `PlanStep`.

Map LLM JSON into strict models. **Illustrative** construction (use actual `PlanStep` nested types from **PHASE_1_SCHEMA_LAYER** — `execution` and `failure` may be sub-models, not plain dicts):

```python
def _build_plan(self, raw: dict, instruction: str) -> PlanDocument:

    steps = []

    for idx, s in enumerate(raw.get("steps", [])):

        step = PlanStep(
            step_id=s["step_id"],
            index=idx,
            type=s["type"],
            goal=s["goal"],
            action=s["action"],
            inputs=s.get("inputs", {}),
            outputs=s.get("outputs", {}),
            dependencies=s.get("dependencies", []),

            execution={
                "status": "pending",
                "attempts": 0,
                "max_attempts": 2,
                "started_at": None,
                "completed_at": None,
                "last_result": None,
            },

            failure={
                "is_recoverable": True,
                "failure_type": None,
                "retry_strategy": "retry_same",
                "replan_required": False,
            },
        )

        steps.append(step)

    return PlanDocument(
        plan_id=...,  # unique per plan; avoid hard-coded "plan_001" in production
        instruction=instruction,
        understanding=raw.get("understanding", ""),
        sources=raw.get("sources", []),
        steps=steps,
        risks=raw.get("risks", []),
        completion_criteria=raw.get("completion_criteria", []),
        metadata={"generated": True},
    )
```

---

## Step 5 — Validator (mandatory)

**Do not** keep ad-hoc `_validate_plan` only inside the planner class. Implement **`PlanValidator`** in **`agent_v2/validation/plan_validator.py`** and call it from **`plan()`** — see **`VALIDATION_REGISTRY.md`** (single ownership; no duplicate rules in Phase 7 / Phase 10).

**Illustrative** checks (full set must match **`SCHEMAS.md`** + registry):

```python
# agent_v2/validation/plan_validator.py — PlanValidator.validate_plan(plan: PlanDocument) -> None

def validate_plan(plan: PlanDocument) -> None:

    assert len(plan.steps) > 0, "Plan must have steps"

    actions = {"search", "open_file", "edit", "run_tests", "shell", "finish"}

    has_finish = False

    for step in plan.steps:
        assert step.action in actions, f"Invalid action {step.action}"

        if step.action == "finish":
            has_finish = True

    assert has_finish, "Plan must include finish step"
```

**Extensions (recommended):**

- Enforce `len(plan.steps) <= 8`.
- Validate `step.type` literals per **PHASE_1_SCHEMA_LAYER**.
- Optionally cross-check cited paths against `exploration.items` / sources to reduce hallucination (heuristic).

---

## Step 6 — Main `plan()` method

**Imports (illustrative):** `from agent_v2.schemas.replan import PlannerInput` · `from agent_v2.validation.plan_validator import PlanValidator`

```python
def plan(
    self,
    instruction: str,
    exploration: PlannerInput,
    deep: bool = False,
) -> PlanDocument:

    prompt = self._build_prompt(instruction, exploration, deep)

    raw = self._call_llm(prompt)

    plan = self._build_plan(raw, instruction)

    PlanValidator.validate_plan(plan)

    return plan
```

---

## Step 7 — Test (mandatory)

**Manual / CLI (when wired):**

```bash
python -m agent_v2 --mode=plan "Explain AgentLoop"
```

**Expect:**

```text
✅ understanding present
✅ 4–8 steps (within max 8)
✅ finish step exists
✅ actions valid
```

---

## Hardening (optional but recommended)

After the initial version works:

```text
- retry on JSON parse failure
- fallback plan generation (minimal safe plan + finish)
- structured output / schema-constrained generation if the stack supports it
```

---

## Common failure modes

```text
❌ Planner ignores exploration
❌ Planner hallucinating files / APIs
❌ Missing finish step
❌ Planner returns free text instead of JSON
```

---

## Exit criteria (strict)

```text
✅ PlannerV2 implemented
✅ Uses ExplorationResult
✅ Returns valid PlanDocument
✅ Passes validation
```

---

## Principal verdict

```text
Reactive agent ❌ → Intent-driven agent ✅
```

Without a strict planner, the system is **blind tool looping**.

---

## Next step

After validation:

👉 **Phase 4 done** (implementation + tests)

Then **Phase 5 — Plan executor (controlled execution engine)**. See `PHASED_IMPLEMENTATION_PLAN.md`.
