# --cursor prompt --
You are a principal engineer auditing and planning Phase 5.5: Connecting memory to the planner.

DO NOT implement anything yet.

---

## GOAL

Understand how memory should be injected into the planner safely,
and propose a minimal, controlled integration plan.

---

## CONTEXT (CURRENT SYSTEM)

Memory system exists:

* Working memory → state.memory["working"]
* Session memory → state.memory["session"] + conversation store
* Episodic memory → JSONL logs + EpisodicQuery
* Semantic memory → fact store (JSONL)

Planner currently uses:

* PlannerPlanContext
* exploration outputs
* session summaries (existing)

Memory is NOT yet injected into planning.

---

## STEP 1 — PLANNER INPUT FLOW (CRITICAL)

Trace:

* where PlannerPlanContext is constructed
* where planner prompt is assembled (PlannerV2)

Identify:

* all inputs used for planning
* how session memory is already included
* where new fields could be injected

---

## STEP 2 — MEMORY USAGE OPPORTUNITIES

For each memory type:

### Episodic

* can we inject:

  * recent failures?
  * recent runs?

### Semantic

* can we inject:

  * project facts?
  * file-level knowledge?

### Session

* already partially used — verify completeness

---

## STEP 3 — INJECTION POINTS

Identify EXACT points where memory can be added:

* PlannerPlanContext construction
* Prompt assembly layer

Check:

* is PlannerPlanContext extendable?
* where summaries are formatted

---

## STEP 4 — RISKS (CRITICAL)

Identify:

* context explosion (too much memory)
* conflicting signals (episodic vs semantic)
* planner instability
* token bloat

---

## STEP 5 — MINIMAL INJECTION STRATEGY

Propose:

* how to inject memory WITHOUT breaking planner

Constraints:

* bounded size (max ~200–300 tokens per memory type)
* optional fields (not required)
* no schema explosion

---

## STEP 6 — PHASED IMPLEMENTATION PLAN

Design sub-steps:

Phase 5.5a:

* inject episodic (failures only)

Phase 5.5b:

* inject semantic facts

Phase 5.5c:

* refine session usage (if needed)

Each step must be:

* independently testable
* reversible

---

## STEP 7 — OUTPUT

Provide:

1. Current planner input flow
2. Where memory fits
3. Top risks
4. Minimal injection plan (phased)
5. Exact integration points (files/functions)

---

## RULES

* DO NOT implement
* DO NOT redesign planner
* DO NOT introduce new systems

Focus:
👉 safe, minimal memory → planner connection


# ---audit --
/Users/shang/my_work/AutoStudio/Docs/deepresearchplanned/phase5-5-planner-memory-integration-audit-plan.md

