# 🧠 PLANNING MODE (CHAT) — FULL ARCHITECTURE FREEZE (WITH MEMORY)

Staff-level design. Aligned with your pipeline + Anthropic-style agents + small-model constraints.

---

# 🎯 OBJECTIVE

Design a **chat-aware planning system** with:

* Persistent memory (session + working)
* Thin orchestrator (Task Planner)
* Iterative tool usage (exploration engine, etc.)
* Explicit stopping + answer synthesis
* Clear separation of responsibilities

👉 This is NOT implementation — this is **architecture freeze + audit plan**

---

# 🧭 CORE PRINCIPLE (UPDATED)

```text
stateful iterative agent
= planner + tools + memory + synthesis
```

And your pipeline remains:

```text
broad → narrow → precise → explainable
```

---

# 🧱 FINAL SYSTEM ARCHITECTURE

```text
User (Chat)
   ↓
Conversation Memory (persistent)
   ↓
Task Planner (Orchestrator, thin)
   ↓
Working Memory (scratchpad / state)
   ↓
Sub-step Planner
   ↓
Tools:
   - Exploration Engine
   - Code Tool (future)
   - Search Tool (future)
   ↓
Analyzer Output
   ↓
Memory Update (both layers)
   ↓
Answer Synthesis
   ↓
Final Response (chat)
```

---

# 🧠 MEMORY SYSTEM (NEW — CRITICAL)

Memory is split into TWO layers.

---

## 1. 🧾 CONVERSATION MEMORY (Persistent)

### 🎯 Purpose

```text
maintain chat continuity across turns
```

### Contains:

* user messages
* assistant responses
* high-level summaries of past steps
* final answers

### NOT for:

```text
❌ raw code snippets
❌ large context blocks
```

---

## 2. 🧠 WORKING MEMORY (Ephemeral, per query)

### 🎯 Purpose

```text
track reasoning state within a task
```

### Structure:

```json
{
  "current_goal": "...",
  "sub_tasks": [],
  "completed_steps": [],
  "tool_outputs": [],
  "accumulated_context": [],
  "analyzer_states": [],
  "iteration_count": 0
}
```

---

### Key property:

```text
reset per top-level instruction
```

---

# 🧠 COMPONENT RESPONSIBILITIES

---

# 1. 🧭 TASK PLANNER (THIN ORCHESTRATOR)

## 🎯 RESPONSIBILITY

```text
- interpret user query (with memory)
- create / update plan
- decide next action
- decide when to stop
```

---

## INPUTS

* current user message
* conversation memory (summary)
* working memory (state)

---

## OUTPUT

```json
{
  "action": "plan | explore | synthesize | stop",
  "sub_task": "...",
  "tool": "exploration | code | search | none"
}
```

---

## ❌ DOES NOT DO

```text
❌ deep code reasoning
❌ retrieval
❌ summarization
```

---

## ⚖️ DESIGN

```text
thin planner > smart planner
```

---

# 2. 🧩 SUB-STEP PLANNER

## 🎯 RESPONSIBILITY

```text
convert sub-task → tool-ready instruction
```

---

## ROLE

```text
planner → sub-step planner → tool
```

---

# 3. 🔍 EXPLORATION ENGINE (UNCHANGED)

Pipeline:

```text
QIP → Scoper → Selector → Analyzer
```

---

## OUTPUT CONTRACT

```json
{
  "understanding": "sufficient | partial | insufficient",
  "gaps": [],
  "signals": [],
  "confidence": "low | medium | high (optional)"
}
```

---

# 4. 🧠 ANALYZER (INTERNAL TO EXPLORATION)

Produces:

* understanding
* gaps
* signals

Feeds into:

```text
working memory + planner decision
```

---

# 5. 🧠 ANSWER SYNTHESIS

## 🎯 RESPONSIBILITY

```text
convert accumulated context → final answer
```

---

## KEY PROPERTY

```text
planner can call ANYTIME
```

---

# 🔄 EXECUTION LOOP (CHAT-AWARE)

---

## LOOP

```text
1. Read user input
2. Load conversation memory
3. Initialize / update working memory

4. Task Planner decides:
   - plan new task
   - continue exploration
   - synthesize answer

5. If tool needed:
   → Sub-step Planner
   → Tool execution
   → Analyzer output

6. Update working memory

7. Check stopping condition

8. If stop:
   → Answer synthesis
   → Update conversation memory
   → return response
```

---

# 🧠 STOPPING CONDITIONS

Planner stops when:

```text
✔ analyzer = sufficient
✔ OR repeated partial with no progress
✔ OR gaps are non-critical
✔ OR iteration limit reached (2–4)
```

---

# ⚠️ EDGE CASE HANDLING

---

## EMPTY / LOW SIGNAL

```text
planner:
  → attempt 1 exploration
  → fallback to synthesis
```

---

## NO RELEVANCE

```text
exploration → weak
planner → stop early
→ synthesis: insufficient context
```

---

## PARTIAL CONTEXT

```text
planner:
  → one more iteration OR synthesize partial answer
```

---

## MULTI-TURN CHAT

```text
planner:
  → uses conversation memory
  → avoids re-exploration if already solved
```

---

# 🧠 MEMORY UPDATE RULES

---

## AFTER EACH TOOL CALL

Update working memory:

```text
- append tool output
- update context
- store analyzer result
```

---

## AFTER FINAL ANSWER

Update conversation memory:

```text
- store final answer
- store summarized reasoning
```

---

# 🧠 STATE FLOW (IMPORTANT)

```text
Conversation Memory → Planner → Working Memory → Tools → Analyzer → Memory Update → Planner
```

---

# ⚖️ TRADE-OFFS

---

## 1. Memory size vs speed

| Choice      | Result         |
| ----------- | -------------- |
| full memory | accurate, slow |
| summarized  | faster, lossy  |

👉 Choose:

```text
summarized conversation memory + rich working memory
```

---

## 2. Iteration depth

| Choice        | Result         |
| ------------- | -------------- |
| deep loops    | better answers |
| shallow loops | faster         |

👉 Choose:

```text
bounded (2–4 iterations)
```

---

## 3. Planner intelligence

| Choice        | Result   |
| ------------- | -------- |
| smart planner | unstable |
| thin planner  | reliable |

👉 Choose:

```text
thin + structured
```

---

# 🧠 WHAT EXISTS vs WHAT IS MISSING

---

## ✅ EXISTS (from your system)

* Exploration Engine (QIP → Scoper → Selector → Analyzer)
* Prompt normalization
* Eval harness
* Structured outputs

---

## ❗ MISSING

* Task Planner (orchestrator)
* Sub-task decomposition logic
* Working memory system
* Conversation memory system
* Stopping logic
* Answer synthesis integration with planner

---

# 🧠 CURSOR PROMPT — ARCHITECTURE AUDIT + PLAN

Use this to verify + generate implementation plan (NOT code).

---

## 🔧 CURSOR PROMPT

```text
Act as a staff software engineer.

Goal:
Audit the current codebase and produce an implementation plan for introducing a chat-aware planning architecture with memory.

Context:
We already have:
- Exploration Engine (QIP → Scoper → Selector → Analyzer)
- Prompt system and eval harness

We want to introduce:
1. Task Planner (thin orchestrator)
2. Working Memory (per-task state)
3. Conversation Memory (persistent chat state)
4. Tool routing layer
5. Stopping logic (based on analyzer output)
6. Answer synthesis integration

Tasks:

1. Identify existing components:
   - exploration engine entrypoints
   - analyzer outputs
   - any existing planner or runner logic

2. Identify missing components:
   - planner
   - memory layers
   - execution loop

3. Propose architecture integration:
   - where planner sits
   - how memory flows
   - how tools are invoked

4. Define data contracts:
   - planner input/output
   - working memory schema
   - conversation memory schema

5. Define execution loop (pseudo-level)

6. Propose file/module structure:
   - planner/
   - memory/
   - orchestrator/
   - tools/

7. Provide step-by-step implementation plan:
   - phase 1: minimal planner
   - phase 2: working memory
   - phase 3: chat memory
   - phase 4: stopping logic
   - phase 5: synthesis integration

Constraints:
- DO NOT implement code
- DO NOT refactor existing exploration engine
- KEEP system compatible with small models (7B/14B)
- KEEP planner thin

Output:
Return a structured implementation plan document.
```

---

# 🧠 FINAL STAFF VERDICT

This architecture is:

```text
✔ stateful
✔ modular
✔ chat-aware
✔ small-model compatible
✔ extensible
✔ aligned with Anthropic-style agents
```

---

# 🧠 ONE-LINE TRUTH

```text
A real agent is not just a planner with tools — it is a planner with memory and controlled iteration.
```
# 🔁 APPENDIX — LOOP STRUCTURE (AGENTIC CONTROL FLOW)

Add this section to the architecture doc. Tight, explicit, no ambiguity.

---

# 🎯 PURPOSE

Define **which components are iterative**, **how loops behave**, and **their stopping conditions**.

This prevents:

* uncontrolled recursion
* redundant exploration
* planner drift

---

# 🧠 LOOP HIERARCHY (TOP → BOTTOM)

```text
Task Planner Loop (outer control loop)
   ↓
Sub-step Planner (stateless, no loop)
   ↓
Exploration Engine Loop (inner bounded loop)
   ↓
(QIP → Scoper → Selector → Analyzer) — linear, no loop inside
```

---

# 🧭 1. TASK PLANNER LOOP (PRIMARY LOOP)

## 🎯 ROLE

```text
global control loop for the entire agent
```

---

## 🔁 BEHAVIOR

```text
while not done:
    decide next action
    call tool OR synthesize
    update working memory
```

---

## 🔢 ITERATION LIMIT

```text
max 2–4 iterations
```

---

## 🛑 STOP CONDITIONS

```text
✔ analyzer = sufficient
✔ repeated partial without improvement
✔ no relevant candidates found
✔ iteration limit reached
```

---

## ❗ NOTES

```text
- ONLY loop that controls execution
- MUST remain bounded
- MUST avoid re-running identical steps
```

---

# 🧩 2. SUB-STEP PLANNER (NO LOOP)

## 🎯 ROLE

```text
instruction transformer (single-shot)
```

---

## 🔁 BEHAVIOR

```text
input → transform → output
```

---

## ❗ NOTES

```text
- NOT agentic
- NO retry logic
- NO iteration
```

---

# 🔍 3. EXPLORATION ENGINE LOOP (SECONDARY LOOP)

## 🎯 ROLE

```text
bounded refinement loop for retrieval
```

---

## 🔁 BEHAVIOR

```text
for i in 1..N (N ≤ 2):
    run QIP → Scoper → Selector → Analyzer
    if sufficient:
        break
    else:
        refine input (optional)
```

---

## 🔢 ITERATION LIMIT

```text
max 1–2 iterations
```

---

## 🛑 STOP CONDITIONS

```text
✔ analyzer = sufficient
✔ no improvement in gaps
✔ no new signals found
```

---

## ❗ NOTES

```text
- controlled refinement, NOT open loop
- prevents over-exploration
```

---

# 🧠 4. INTERNAL PIPELINE (NO LOOPS)

```text
QIP → Scoper → Selector → Analyzer
```

---

## ❗ GUARANTEES

```text
- strictly single-pass
- no recursion
- no retries inside
```

---

# 🧠 LOOP INTERACTION

---

## FLOW

```text
Task Planner Loop
   → Exploration Engine Loop
       → (QIP → Scoper → Selector → Analyzer)
   → Back to Planner
```

---

## KEY PROPERTY

```text
only planner decides whether to re-run exploration
```

---

# ⚠️ EDGE CASE LOOP HANDLING

---

## LOW SIGNAL / AMBIGUOUS

```text
planner:
  → 1 exploration attempt
  → stop early if weak signal
```

---

## NO RELEVANCE

```text
exploration:
  → returns weak/empty

planner:
  → DO NOT retry repeatedly
  → move to synthesis
```

---

## PARTIAL CONTEXT

```text
planner:
  → allow 1 additional iteration
  → then stop
```

---

## OVER-SPECIFIED

```text
planner:
  → break into sub-tasks (sequential, not parallel loops)
```

---

# 🚫 ANTI-PATTERNS (STRICTLY FORBIDDEN)

```text
❌ unbounded loops
❌ nested planner loops
❌ retry-until-success behavior
❌ looping inside selector/scoper/analyzer
❌ repeated identical exploration calls
```

---

# 🧠 FINAL RULE

```text
Loops exist ONLY at:
1. Task Planner (primary)
2. Exploration Engine (bounded secondary)

Everything else is single-pass.
```

---

# 🧠 ONE-LINE TRUTH

```text
A good agent is not the one that loops more — it is the one that knows when to stop.
```
