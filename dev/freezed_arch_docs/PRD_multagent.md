Good. Now we’re locking this properly — no half measures.

You’ve made the **correct choices**:

* ✅ Option B (multi-mode runtime)
* ✅ Layered tool system (Devin-style primitives + structured tools)

Now I’m giving you a **production-grade PRD + Architecture Freeze**.

This is not a sketch — this is what you build against.

---

# 📘 PRD — General-Purpose Coding Agent (ReAct + Multi-Mode)

---

# 1. 🎯 Product Goal

Build a **general-purpose autonomous coding agent** that can:

* Understand large codebases
* Plan and execute multi-step software tasks
* Modify, test, and validate code
* Operate iteratively with minimal supervision

Inspired by:

* Claude Code (tool-driven loop, terminal-native execution) ([Tech Jacks Solutions][1])
* Devin (autonomous planning + execution + environment interaction) ([Wikipedia][2])

---

# 2. 🧠 Core Design Principles (NON-NEGOTIABLE)

Derived from real systems:

### 1. Tools > Prompting

* Structured tools outperform formatting instructions ([DEV Community][3])

---

### 2. Few, Deep Tools

* Limit tool count (≈5–10 max) to maintain reliability ([DEV Community][3])

---

### 3. Agent Builds Context

* Prefer search tools over pre-injected context ([DEV Community][3])

---

### 4. Terminal-Native Execution

* Agent must interact with real environment (shell, filesystem) ([Tech Jacks Solutions][1])

---

### 5. Iterative Loop

* Plan → Act → Verify → Repeat ([Augment Code][4])

---

# 3. 🧭 System Overview

## Final Architecture (FROZEN)

```text
AgentRuntime
  ├── AgentState
  ├── ModeManager
  │     ├── ACT (ReAct Loop)
  │     ├── PLAN
  │     └── DEEP_PLAN
  ├── ToolRegistry (LLM-facing)
  ├── Dispatcher
  ├── PrimitiveLayer
  │     ├── Shell
  │     ├── Editor
  │     └── Browser
  └── Observability (trace, logs)
```

---

# 4. 🧩 Modes (CORE FEATURE)

## 4.1 ACT MODE (DEFAULT)

### Purpose:

* Execute tasks step-by-step

### Behavior:

* ReAct loop:

  * think → act → observe → repeat

### Tools available:

* search
* open_file
* edit
* run_tests
* finish

---

## 4.2 PLAN MODE

### Purpose:

* Generate execution plan only

### Output:

* structured steps

### No tool execution

---

## 4.3 DEEP PLAN MODE

### Purpose:

* Architecture-level reasoning

### Capabilities:

* multi-file planning
* dependency-aware reasoning
* system design changes

---

# 5. 🛠 Tool System (FROZEN)

---

## 5.1 Layered Tool Architecture

### 🔷 Layer 1 — Primitive Tools (NOT exposed)

| Tool    | Purpose                        |
| ------- | ------------------------------ |
| shell   | Execute commands, run programs |
| editor  | Read/write/patch files         |
| browser | External info / docs           |

👉 These power the system internally

---

### 🔷 Layer 2 — Agent Tools (LLM-facing)

| Tool      | Purpose            |
| --------- | ------------------ |
| search    | find relevant code |
| open_file | read code          |
| edit      | modify code        |
| run_tests | validate           |
| finish    | terminate          |

---

## 🔥 Rule

> LLM NEVER directly controls shell (unless future expert mode)

---

## 5.2 Tool Registry (SINGLE SOURCE OF TRUTH)

```python
ToolDefinition:
  name
  description
  required_args
  handler(args, state) -> result
```

---

### Derived from registry:

* validation
* dispatch
* prompt schema

---

# 6. 🔁 Execution Model (ACT MODE)

```text
loop:
  action = LLM(state)

  validate(action)

  result = dispatcher(action)

  observation = build_observation(result)

  update_state()

until finish
```

---

# 7. 🧠 Agent State (CRITICAL)

## Replace:

❌ react_history list

## With:

```python
AgentState:
  instruction
  history
  context
  current_plan
  step_results
  metadata
```

---

## Why:

* enables planning modes
* enables workflows later
* supports memory

---

# 8. 🔄 Dispatcher (FROZEN DESIGN)

```text
Dispatcher
  → lookup tool in registry
  → execute handler
  → return structured result
```

---

## ❌ No logic here:

* no planning
* no policy
* no branching

---

# 9. 🧪 Execution Flow (EDIT EXAMPLE)

```text
edit →
  handler →
    editor (apply patch)
    shell (run validation/tests)
```

---

# 10. 📊 Observability

Each run must capture:

* steps
* tool calls
* errors
* files modified

👉 already aligned with your trace system

---

# 11. ⚠️ What We EXCLUDE (IMPORTANT)

## ❌ NOT in this architecture:

* multi-agent system
* workflow engine
* tool search
* JSON schema system
* programmatic tool execution
* ToolGraph in core loop

---

## Why:

These require a stable runtime first

---

# 12. 🧱 Extensibility Model

To add a tool:

```text
1. define ToolDefinition
2. register_tool()
```

No other changes.

---

# 13. 🧪 Success Metrics

| Metric            | Target            |
| ----------------- | ----------------- |
| Task success rate | >70% simple tasks |
| Tool correctness  | >90% valid calls  |
| Loop efficiency   | <20 steps avg     |
| Failure loops     | <5% infinite      |

---

# 14. 🚨 Architecture Freeze Rules

## After this:

### ❌ No changes to:

* loop structure
* tool interface
* dispatcher contract

---

### ✅ Only allowed:

* new tools
* better prompts
* internal optimizations

---

# ⚡ Cursor Prompt (Implementation Start)

```text
Goal: Implement frozen architecture for multi-mode ReAct agent.

Tasks:
1. Introduce AgentState structure.
2. Implement ModeManager with ACT, PLAN, DEEP_PLAN.
3. Create ToolRegistry (single source of truth).
4. Refactor execution_loop to use:
   - registry-based dispatch
   - state-based updates
5. Ensure tools use primitive layer (shell/editor internally).
6. Remove duplicate schema definitions.

Constraints:
- Do NOT introduce new features (subagents, workflows).
- Maintain current behavior.
- Preserve trace system.

Output:
- Clean, minimal, extensible runtime aligned with PRD.
```

---

# 🧠 Final Take (Principal Engineer)

This design:

* matches Claude (tool-driven loop + terminal execution)
* matches Devin (core primitives + iterative execution)
* avoids overengineering
* gives you a **real foundation**

---

# Next Step

If you’re serious about freezing:

👉 I’ll now do **Architecture Review v2 (component-by-component)**
→ mapping this PRD directly to your current codebase
→ identifying EXACT refactors

Say: **“start mapping to codebase”**

[1]: https://techjacksolutions.com/ai/coding/what-is-claude-code-2/?utm_source=chatgpt.com "What is Claude Code: How Agentic AI Is Rewriting ..."
[2]: https://en.wikipedia.org/wiki/Devin_AI?utm_source=chatgpt.com "Devin AI"
[3]: https://dev.to/alexchen31337/7-principles-for-ai-agent-tool-design-from-claude-code-real-world-systems-3dcd?utm_source=chatgpt.com "7 Principles for AI Agent Tool Design (From Claude Code + ..."
[4]: https://www.augmentcode.com/tools/best-devin-alternatives?utm_source=chatgpt.com "6 Best Devin Alternatives for AI Agent Orchestration in 2026"
