I’ll give you a **staff-level responsibility freeze + system shape** that:

* keeps your current system intact
* aligns with Anthropic / Cursor practices
* adds **session memory + tool awareness**
* avoids redesign (only refactor)

---

# 🧠 PART 1 — Planner Responsibility Freeze

This is the most important thing. If you get this wrong, system drifts.

---

## 🎯 Planner = Decision + Routing Layer

```text
Planner MUST ONLY:
- decide next action
- select tool
- use memory + context
- produce minimal structured output
```

---

## ❌ Planner MUST NOT:

```text
- do retrieval (exploration does that)
- execute tools
- maintain long-term memory
- generate full multi-step plans by default
```

---

## ✅ Planner Responsibilities (final contract)

### 1. Decision making

```text
decide:
- act
- explore
- replan
- stop
```

---

### 2. Tool selection (NEW)

```text
decide WHICH tool:
- exploration
- file
- shell
- browser
- analysis
```

---

### 3. Context fusion

Planner consumes:

```text
- exploration output
- session memory
- current instruction
- plan_state (optional)
```

---

### 4. Minimal step synthesis

```text
produce:
- 0–2 steps max
```

---

### 5. Memory usage (NEW)

Planner must:

```text
- resolve ambiguous instructions
- refer to prior steps
- maintain task continuity
```

---

# 🧠 PART 2 — Session Memory (what you asked)

This is critical for real-world usability.

---

## 🧩 What session memory is (in your system)

```text
Short-term conversational + task memory
```

NOT:

* vector DB
* long-term storage

---

## 🧠 What it should contain

### 1. Interaction history (compressed)

```text
- last user instructions
- last planner decisions
- last tool actions
```

---

### 2. Active task state

```text
- current goal
- current file / area of focus
- unresolved gaps
```

---

### 3. References

```text
- file paths
- symbols
- entities
```

---

## 🧠 Example

User:

```text
"open that file"
```

Planner uses memory:

```text
last_file = "exploration_scoper.py"
→ resolves correctly
```

---

## 🧠 Implementation constraint (important)

Keep it:

```text
SMALL + STRUCTURED
```

For Qwen 7B:

```text
~10–20 lines max
```

---

## 🧠 Memory injection into planner

Add to prompt:

```text
SESSION MEMORY:

- Current task: ...
- Last action: ...
- Active file: ...
- Recent decisions:
    - ...
```

---

# 🧠 PART 3 — Tool Surface (what planner must see)

You need to expose tools explicitly.

---

## 🔧 Minimal tool set (production-aligned)

### 1. Exploration (already exists)

```text
explore(query)
```

---

### 2. File system

```text
open_file(path)
search_code(query)
```

---

### 3. Shell

```text
run_shell(command)
```

---

### 4. Browser / external knowledge

```text
search_web(query)
```

---

### 5. Analysis (optional but useful)

```text
analyze_code(snippet)
```

---

## 🧠 Important

Planner must NOT hallucinate tools.

So prompt MUST include:

```text
AVAILABLE TOOLS:
...
```

---

# 🧠 PART 4 — Planner decision schema (refined)

You already have:

```json
{
  "decision": "...",
  "reason": "...",
  "query": "...",
  "step": "..."
}
```

---

## 🔥 Upgrade (minimal, no redesign)

```json
{
  "decision": "act | explore | replan | stop",
  "tool": "explore | file | shell | browser | analysis",
  "reason": "...",
  "query": "...",
  "step": {
    "action": "...",
    "input": "..."
  }
}
```

---

## Why this matters

```text
decision = what
tool = how
step = execution hint
```

---

# 🧠 PART 5 — Planner behavior rules (Anthropic-style)

These are **non-negotiable rules**.

---

## 1. Sufficiency rule (strong)

```text
If enough info → DO NOT explore
```

---

## 2. Minimality rule

```text
Prefer smallest action
```

---

## 3. Tool correctness

```text
Choose correct tool for task
```

---

## 4. Memory resolution

```text
Resolve vague instructions using session memory
```

---

## 5. No redundancy

```text
Do not repeat exploration or actions
```

---

# 🧠 PART 6 — Cases planner MUST handle

---

## ✅ Case 1 — Vague instruction

```text
"do it"
```

Planner:

```text
→ uses memory
→ resolves context
→ act
```

---

## ✅ Case 2 — Missing knowledge

```text
→ explore
```

---

## ✅ Case 3 — Code understanding

```text
→ file tool
```

---

## ✅ Case 4 — External info needed

```text
→ browser tool
```

---

## ✅ Case 5 — Already sufficient

```text
→ act or stop
```

---

## ✅ Case 6 — Wrong direction

```text
→ replan
```

---

# ⚠️ Edge cases (important)

---

## 🔴 1. Conflicting memory

```text
multiple previous targets
```

→ planner must choose latest

---

## 🔴 2. Empty exploration + vague query

```text
→ explore with broad query
```

---

## 🔴 3. Repeated explore loop

```text
→ must stop or act
```

---

## 🔴 4. Tool misuse

```text
wrong tool selection
```

→ prompt must constrain

---

# 🧾 PART 7 — Final architecture (your system after this)

```text
User
→ Exploration (context)
→ Planner (decision + tool + memory)
→ Executor (unchanged)
→ Loop
```

---

# 🚀 Final takeaway

```text
You are NOT redesigning.
You are:
- upgrading planner intelligence
- adding memory
- adding tool awareness
```

---

# 👉 Next step

Now we do the actual implementation design.

Say:

```text
design planner v2 (memory + tools)
```

