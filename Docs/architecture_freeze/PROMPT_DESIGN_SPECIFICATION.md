# Prompt Design Specification (v1.0)

## Purpose

This document defines the **ideal structure of prompts** for a general-purpose AI coding agent system.

It is used to:

* Evaluate new prompts
* Detect weaknesses in existing prompts
* Ensure consistency across agent components (exploration, planning, execution)

---

# 1. Core Philosophy

A prompt is a **contract**, not a query.

It must:

* Translate user intent → executable behavior
* Constrain reasoning and output
* Provide sufficient context for correctness

---

# 2. Required Prompt Components

Every production-grade prompt MUST include the following:

---

## 2.1 Role / Execution Context

Defines reasoning level and behavior.

**Requirement:**

* Explicit role (e.g., senior engineer, planner, analyzer)

**Failure if missing:**

* Shallow reasoning
* Inconsistent tone/decisions

---

## 2.2 Objective (Task Definition)

Defines what must be achieved.

**Requirement:**

* Clear, unambiguous task
* No vague phrasing ("analyze", "look at this")

**Test:**

* Can a new engineer understand the task without extra context?

---

## 2.3 Context (Grounding Layer)

Provides necessary information.

**Includes:**

* Code snippets
* State
* Inputs
* Previous steps

**Requirement:**

* Only relevant information (no noise)

**Failure mode:**

* Hallucination
* Irrelevant reasoning

---

## 2.4 Constraints (Hard Rules)

Defines non-negotiable boundaries.

**Examples:**

* No assumptions
* Follow schema
* Do not modify unrelated components

**Requirement:**

* Explicit, enforceable

**Failure mode:**

* Drift
* Invalid outputs

---

## 2.5 Reasoning Directive (Execution Strategy)

Defines how the model should think.

**Examples:**

* step-by-step reasoning
* plan before execution
* verify before output

**Requirement:**

* Minimal but sufficient guidance

**Note:**

* Do NOT over-specify (causes verbosity and instability)

---

## 2.6 Output Contract (CRITICAL)

Defines EXACT output format.

**Requirement:**

* Strict schema (JSON / diff / structured text)
* No ambiguity

**Failure mode:**

* Unusable outputs
* Parsing failures

---

## 2.7 Verification / Completion Criteria

Defines when task is complete.

**Examples:**

* correctness conditions
* success criteria

**Requirement:**

* Explicit or implicit validation

---

# 3. Structural Quality Criteria

A prompt must satisfy:

---

## 3.1 Causal Clarity

The prompt must enforce:

→ "Does this directly solve the task?"

NOT:

→ "Is this somewhat related?"

---

## 3.2 Minimality

* No redundant instructions
* No repeated constraints

Goal:
→ Maximum signal, minimum tokens

---

## 3.3 Determinism

Same input → same structure of output

Achieved via:

* strict output contract
* explicit constraints

---

## 3.4 Separation of Concerns

Prompt should NOT mix:

* reasoning instructions
* output format
* system logic

Each must be clearly scoped

---

# 4. Common Failure Modes

---

## 4.1 Semantic Drift

Cause:

* vague objective
* weak constraints

Symptom:

* model answers adjacent problems

---

## 4.2 Overgeneralization

Cause:

* "relevant" instead of "necessary"

Symptom:

* incorrect "sufficient" decisions

---

## 4.3 Output Leakage

Cause:

* reasoning not separated from output

Symptom:

* extra text before JSON

---

## 4.4 Over-constraint

Cause:

* too many rules

Symptom:

* brittle or confused responses

---

# 5. Evaluation Checklist (for Cursor)

When reviewing a prompt, verify:

### Structure

* [ ] Role defined
* [ ] Objective clear
* [ ] Context sufficient
* [ ] Constraints explicit
* [ ] Output format strict

### Reasoning

* [ ] Enforces causal relevance (not semantic)
* [ ] Encourages structured thinking (not verbosity)

### Output

* [ ] No ambiguity in format
* [ ] No room for extra text

### Efficiency

* [ ] No redundant instructions
* [ ] Token usage minimal

---

# 6. Agent-System Alignment

Prompts must align with system architecture:

| System Stage | Prompt Requirement                |
| ------------ | --------------------------------- |
| Exploration  | relevance + discovery constraints |
| Scoping      | filtering logic                   |
| Selection    | ranking clarity                   |
| Analysis     | causal correctness                |
| Planning     | structured reasoning              |
| Execution    | deterministic output              |

---

# 7. Advanced Principles

---

## 7.1 Context Engineering > Prompt Engineering

* Prompt is only part of system
* Context quality determines output quality

---

## 7.2 Plan-First Behavior

* Always prefer:
  → plan → execute

Never:
→ immediate action

---

## 7.3 Output as API

* Treat model output as machine-consumable
* Never rely on free text

---

# 8. Final Standard

A prompt is production-ready if:

* It is minimal
* It is unambiguous
* It enforces causal correctness
* It produces deterministic structured output
* It aligns with system architecture

---

# End of Specification
