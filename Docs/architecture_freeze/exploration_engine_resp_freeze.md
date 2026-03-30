# 🧠 EXPLORATION ENGINE — RESPONSIBILITY FREEZE (FINAL, STAFF VERSION)

Grounded in: 

---

# 🎯 PURPOSE

This document defines **explicit responsibilities, constraints, and trade-offs** for each layer in the exploration engine.

It exists to:

* Prevent over-engineering
* Prevent under-engineering
* Maintain consistency across iterations
* Align system behavior with **small-model (7B/14B) constraints**

---

# 🧭 CORE SYSTEM PRINCIPLE

```text
broad → narrow → precise → explainable
```

### Interpretation

```text
early layers = high recall (accept noise)
middle layers = controlled reduction
final layer = correctness + explanation
```

---

# 🧱 GLOBAL DESIGN PHILOSOPHY

## 1. Recall-first architecture

* Losing signal early = **irreversible failure**
* Carrying noise forward = **recoverable**

👉 Principle:

```text
early noise > early loss
```

---

## 2. Bounded failure (not perfect correctness)

System guarantees:

```text
✔ never confidently wrong
✔ may be incomplete
✔ may be slightly noisy
```

---

## 3. Small model constraint (7B-first design)

Assume:

* Weak abstraction
* Weak ranking
* Weak schema adherence (unless enforced)

Therefore:

```text
structure > intelligence
constraints > cleverness
```

---

## 4. No heuristics policy

System must NOT:

* rely on path-based filtering (e.g. “ignore tests”)
* rely on handcrafted rules
* overfit to dataset patterns

System MUST:

```text
generalize across unknown codebases
```

---

# 🧠 LAYER 1 — QUERY INTENT PARSER (QIP)

---

## 🎯 RESPONSIBILITY

```text
Convert instruction → structured retrieval signals
```

NOT:

```text
❌ exact understanding
❌ precise symbol resolution
```

---

## ✅ EXPECTATIONS

* Always output:

  * intent_type
  * scope
  * focus

* Generate:

  * keywords (primary signal)
  * symbols (optional signal)
  * regex (secondary)

---

## ✅ WHAT IS ALLOWED

```text
✔ Approximate intent classification
✔ Weak / inferred symbols (limited)
✔ Broad keyword expansion
✔ Multi-query generation (batch variants)
```

---

## ❌ WHAT IS NOT REQUIRED

```text
❌ Perfect symbol extraction
❌ Perfect intent classification
❌ Exact mapping to code entities
```

---

## ⚠️ EDGE CASE BEHAVIOR

| Case           | Behavior                               |
| -------------- | -------------------------------------- |
| empty_input    | forced structured output (approximate) |
| low_signal     | broad keywords                         |
| ambiguous      | pick dominant intent                   |
| over_specified | include multiple signals               |
| no_relevance   | still structured output                |

---

## ⚖️ TRADE-OFF

| Choice         | Result             |
| -------------- | ------------------ |
| Strict symbols | low recall ❌       |
| Loose symbols  | better retrieval ✔ |

👉 Final stance:

```text
allow approximation, avoid hallucination spam
```

---

# 🧠 LAYER 2 — SCOPER

---

## 🎯 RESPONSIBILITY

```text
Expand candidate space (recall layer)
```

---

## ✅ EXPECTATIONS

* Include:

  * core implementation
  * related modules
  * relevant neighbors

---

## ✅ WHAT IS ALLOWED

```text
✔ Slight over-selection
✔ Inclusion of weakly relevant files
✔ 2–5 candidates typical
✔ Mixed signal (definition + usage)
```

---

## ❌ WHAT IS NOT ALLOWED

```text
❌ extreme explosion (10+ irrelevant)
❌ missing core file
```

---

## ⚠️ EDGE CASE BEHAVIOR

| Case            | Behavior                    |
| --------------- | --------------------------- |
| empty_pool      | safe empty                  |
| low_signal      | broad inclusion             |
| no_relevance    | weak matches                |
| partial_context | include surrounding modules |
| conflicting     | include both sides          |

---

## ⚖️ TRADE-OFF

| Choice       | Result              |
| ------------ | ------------------- |
| Tight scoper | irreversible loss ❌ |
| Broad scoper | safe downstream ✔   |

👉 Final stance:

```text
slightly over-inclusive scoper
```

---

# 🧠 LAYER 3 — SELECTOR BATCH

---

## 🎯 RESPONSIBILITY

```text
Compress candidates → minimal useful set
```

This is the **control point of the system**.

---

## ✅ EXPECTATIONS

* Select:

  * 1–3 candidates
  * highest signal
  * complementary if needed

---

## ✅ WHAT IS ALLOWED

```text
✔ Slight redundancy
✔ Complementary selection (definition + usage)
✔ Empty selection (if nothing relevant)
```

---

## ❌ WHAT IS NOT ALLOWED

```text
❌ Random selection
❌ Schema violation
❌ Always selecting something
❌ Over-selection without justification
```

---

## ⚠️ EDGE CASE BEHAVIOR

| Case           | Behavior                |
| -------------- | ----------------------- |
| empty_input    | empty selection         |
| no_relevance   | empty selection         |
| low_signal     | minimal selection (0–1) |
| conflicting    | select best subset      |
| over_specified | prune aggressively      |

---

## ⚖️ TRADE-OFF

| Choice             | Result              |
| ------------------ | ------------------- |
| aggressive pruning | miss dependencies ❌ |
| loose selection    | overload analyzer ❌ |

👉 Final stance:

```text
controlled compression
```

---

## 🔴 HARD CONSTRAINTS

```text
✔ selected_indices MUST be integers
✔ valid JSON ALWAYS
✔ allow empty selection
```

---

# 🧠 LAYER 4 — ANALYZER

---

## 🎯 RESPONSIBILITY

```text
Produce final understanding + explanation
```

---

## ✅ EXPECTATIONS

* Determine:

  * sufficient / partial / insufficient
* Provide:

  * explanation
  * gaps (if needed)

---

## ✅ WHAT IS ALLOWED

```text
✔ Partial understanding
✔ Missing internal functions
✔ Conservative reasoning
```

---

## ❌ WHAT IS NOT ALLOWED

```text
❌ hallucinated explanations
❌ confident incorrect answers
❌ ignoring missing core logic
```

---

## ⚠️ EDGE CASE BEHAVIOR

| Case            | Behavior                |
| --------------- | ----------------------- |
| empty_context   | insufficient + gaps     |
| partial_context | partial + targeted gaps |
| no_relevance    | insufficient            |
| conflicting     | conservative            |
| over_specified  | selective reasoning     |

---

## ⚖️ TRADE-OFF

| Choice       | Result                             |
| ------------ | ---------------------------------- |
| conservative | safe but weaker answers            |
| aggressive   | better answers, risk hallucination |

👉 Final stance:

```text
conservative but decisive when core logic is visible
```

---

## 🔧 KEY RULE

```text
If core behavior is visible → mark sufficient
Gaps should not block explanation
```

---

# 🧠 CROSS-LAYER EDGE CASE FLOW

---

## EMPTY INPUT

```text
QIP → structured guess
Scoper → empty
Selector → empty
Analyzer → insufficient
```

✔ acceptable

---

## LOW SIGNAL

```text
QIP → broad
Scoper → broad
Selector → minimal
Analyzer → partial
```

✔ acceptable

---

## NO RELEVANCE

```text
QIP → structured
Scoper → weak
Selector → empty
Analyzer → insufficient
```

✔ critical behavior

---

## PARTIAL CONTEXT

```text
Analyzer → partial + gaps
```

✔ expected

---

## OVER-SPECIFIED

```text
QIP → many signals
Scoper → many
Selector → prune
Analyzer → resolve
```

✔ pipeline absorbs complexity

---

# 🧠 FINAL TRADE-OFF SUMMARY

---

## SYSTEM CHOICES

```text
✔ recall > precision (early)
✔ precision > recall (later)
✔ grounding > hallucination
✔ structure > intelligence
```

---

## ACCEPTED IMPERFECTIONS

```text
✔ imperfect intent classification
✔ weak symbol quality
✔ slight scoper noise
✔ occasional under-selection
✔ conservative analyzer bias
```

---

## NON-NEGOTIABLES

```text
✔ valid structured outputs
✔ no hallucinated final answers
✔ selector schema correctness
✔ ability to return empty when needed
```

---

# 🧠 FINAL STAFF VERDICT

This system is:

```text
✔ recall-first
✔ compression-controlled
✔ explanation-driven
✔ robust under small models
✔ scalable to larger models
```

---

# 🧠 ONE-LINE TRUTH

```text
In small-model systems, correctness comes from pipeline design—not individual model intelligence.
```

---
