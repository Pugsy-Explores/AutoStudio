This is the right move — you’re now defining **system contracts**, not just prompts.
I’ll write this like a **Staff-level design doc you can reuse later with Cursor**.

---

# 🧠 📄 RESPONSIBILITY FREEZE DOCUMENT

**System: Auto Studio Planner (Decision + Reasoning + Validation)**

---

# 🎯 1. GOAL

Design a **stateful, iterative reasoning system** that:

* avoids large-context degradation
* improves Tier 3 / Tier 4 accuracy
* enforces correctness via validation
* enables controlled multi-step reasoning

---

# 🎯 2. OBJECTIVES

* Separate **decision / reasoning / validation responsibilities**
* Introduce **stateful reasoning loop**
* Replace “context accumulation” with **state compression**
* Ensure **deterministic control flow (no loops / drift)**

---

# 🚫 3. NON-GOALS

* ❌ No redesign of exploration layer
* ❌ No rewriting synthesizer
* ❌ No heavy memory system
* ❌ No long chain-of-thought storage

---

# 🧠 4. CORE IDEA

```text
Reason in steps → compress → validate → decide next step
```

Instead of:

```text
big context → one-shot answer ❌
```

---

# 🧩 5. SYSTEM ARCHITECTURE

```text
Decision (controller)
        ↓
Retrieve (tools)
        ↓
Reason (synthesizer)
        ↓
Compress state
        ↓
Validate
        ↓
Decision (next step)
```

---

# 🧠 6. SHARED STATE (CRITICAL)

This is the **core of the system**.

## 🔹 State Schema

```json
{
  "current_hypothesis": "...",
  "key_findings": [
    "...",
    "..."
  ],
  "relevant_entities": [
    "function A",
    "class B"
  ],
  "open_questions": [
    "missing caller of X",
    "test coverage for Y"
  ],
  "confidence": "low | medium | high"
}
```

---

## 🔥 RULES

* MUST be **compact (< 1–2K tokens)**
* MUST NOT include raw code
* MUST NOT include full history
* MUST be updated every iteration

---

# 🧠 7. MODULE RESPONSIBILITY FREEZE

---

# 🔷 A. DECISION MODULE (Planner Controller)

## 🎯 Responsibility

* Orchestrate loop
* Decide next action:

  * `explore`
  * `synthesize`
  * `stop`
  * `replan`

---

## ✅ MUST DO

* Read:

  * instruction
  * current state
  * validation_feedback

* Enforce:

  * no synthesize after failed validation
  * exploration when gaps exist

---

## ❌ MUST NOT DO

* ❌ No deep reasoning
* ❌ No answer generation
* ❌ No context expansion logic

---

## 🧠 Decision Logic

```text
IF validation incomplete:
    → explore using missing_context

ELIF confidence high AND no gaps:
    → stop

ELSE:
    → synthesize OR explore (based on state)
```

---

# 🔷 B. ANSWER SYNTHESIZER (Reasoning Engine)

## 🎯 Responsibility

* Perform **step-level reasoning**
* Produce:

  * answer
  * structured explanation

---

## ✅ MUST DO

* Use:

  * current context
  * current state

* Generate:

```json
{
  "answer": "...",
  "explanation": "...",
  "new_findings": [...],
  "updated_hypothesis": "...",
  "remaining_gaps": [...]
}
```

---

## ❌ MUST NOT DO

* ❌ No validation
* ❌ No completeness judgment
* ❌ No loop control

---

## 🔥 Key Rule

> Each call = **ONE reasoning step**, not full solution

---

# 🔷 C. VALIDATION MODULE

## 🎯 Responsibility

* Evaluate correctness and completeness
* Decide if more work is needed

---

## ✅ MUST DO

* Compare:

  * instruction
  * context
  * answer
  * state

* Output:

```json
{
  "is_complete": boolean,
  "issues": [...],
  "missing_context": [...],
  "confidence": "low | medium | high"
}
```

---

## ❌ MUST NOT DO

* ❌ No answer generation
* ❌ No rewriting
* ❌ No exploration

---

## 🔥 Key Behavior

> MUST be **strict and adversarial**

---

# 🧠 8. STATE UPDATE (CRITICAL LAYER)

After each reasoning step:

---

## Input:

* previous state
* synthesizer output

---

## Output:

* updated compressed state

---

## Update Rules

```text
- Merge new_findings into key_findings
- Update hypothesis
- Replace open_questions with remaining_gaps
- Update confidence (based on validation later)
```

---

## ❗ IMPORTANT

DO NOT:

* append history
* keep raw reasoning traces

---

# 🔄 9. LOOP EXECUTION

```text
[1] Decision
        ↓
[2] Retrieve (if needed)
        ↓
[3] Synthesize (reason step)
        ↓
[4] Update State (compress)
        ↓
[5] Validate
        ↓
   ┌───────────────┐
   │ complete?     │
   └──────┬────────┘
          │ YES → STOP
          │
          ▼
      next iteration
```

---

# 🧨 10. HARD CONSTRAINTS

## 1. No context accumulation

```text
Only pass:
- state
- relevant context
```

---

## 2. No repeated synthesize

```text
If validation fails:
→ MUST explore
```

---

## 3. Bounded iterations

```text
max_steps ≤ N
```

---

## 4. State size limit

```text
≤ 1–2K tokens
```

---

# 📊 11. EXPECTED IMPACT

| Area   | Improvement  |
| ------ | ------------ |
| Tier 2 | near perfect |
| Tier 3 | +15–25%      |
| Tier 4 | +20–30%      |

---

# 🧠 12. IMPLEMENTATION STRATEGY

## Phase 1

* Add state schema
* store in planner state

## Phase 2

* update synthesizer output format

## Phase 3

* add state update logic

## Phase 4

* integrate with validator

## Phase 5

* plug into decision context

---

# 🔥 13. STAFF-LEVEL INSIGHT

> This system works because it shifts:
>
> ❌ memory → tokens
> ✅ memory → structured state

---

# 🎯 FINAL TAKEAWAY

* Decision = control
* Synthesizer = thinking
* Validator = correctness
* State = memory

---

# 🚀 HOW YOU’LL USE THIS

Later, you can ask Cursor:

> “Implement stateful reasoning based on this contract”

And it will:

* map this to your codebase
* integrate cleanly
* avoid breaking existing system

Add this at the bottom — tight and implementation-ready:

---

# 🔧 ADDITIONAL CONTROL RULES (EXTENSIONS)

## 1. Validation → Retrieval Trigger (MANDATORY)

```text
IF validation.is_complete == false:
  → MUST convert missing_context → structured retrieval queries
```

### Rule:

* Each `missing_context` item must map to:

```json
{
  "query": "...",
  "type": "symbol | caller | file | test | edge_case"
}
```

👉 Avoid vague queries like “find more info”
👉 Prefer: “callers of validate_plan”, “test files using X”

---

## 2. Re-synthesize vs Explore Decision

```text
IF validation fails:
  IF missing_context is non-empty:
    → explore
  ELSE IF issues are reasoning-only:
    → allow ONE re-synthesize
  ELSE:
    → explore
```

### Hard constraint:

```text
Max consecutive re-synthesize = 1
```

👉 Prevents local reasoning loops

---

## 3. Confidence Update Rule (State Stability)

After validation:

```text
IF validation.confidence == low:
  → state.confidence = low

ELIF validation.confidence == medium:
  → state.confidence = min(previous, medium)

ELIF validation.confidence == high:
  → state.confidence = high
```

---

## 4. Retrieval Scope Control

```text
Each iteration MUST:
- restrict retrieval to missing_context
- avoid re-fetching already covered entities
```

👉 Use:

```text
state.relevant_entities
```

to prune retrieval

---

## 5. Convergence Signal (Stop Condition 강화)

```text
STOP if:
- validation.is_complete == true
- AND no new findings in last iteration
- AND state.confidence == high
```

---

## 6. Degeneracy Guard (Failure Mode)

```text
IF same missing_context repeats ≥ 2 times:
  → force replan OR broaden retrieval scope
```
