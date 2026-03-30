# Retrieval Handoff Merge — Design Questions (Non-Implementation)

**Type:** Open questions only (Stage 11 documentation)  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Related:** `Docs/STAGE11_DECISION_MEMO.md`, `HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`, `Docs/REPLAN_MEASUREMENT_CRITERIA.md`

---

## Purpose

Collect **unresolved design questions** for a **future** initiative: merging **`prior_phase_ranked_context`** (and related handoff keys: **`prior_phase_retrieved_symbols`**, **`prior_phase_files`**) into **Phase 1** retrieval / ranking / context building inside the **execution pipeline** (`execution_loop`, `step_dispatcher`, possibly `replanner`).

Today, orchestration **injects** these keys on `AgentState.context` via `_build_phase_agent_state` / `_build_phase_context_handoff` in `deterministic_runner.py`, but **consumption** for ranking and candidate selection in Phase 1 is **not** specified in a single merge design — and roadmap §5.5-style docs note execution components **ignore** or under-use handoff for merge semantics.

**This document locks nothing.** It does **not** authorize code changes. Any implementation must be preceded by a **separate** precoding / merge spec memo approved for **frozen** modules.

---

## Events / fields to inspect (read-only, for future measurement)

These exist today and help characterize **whether** merge is worth building **before** touching frozen code:

| Source | Field / event | Use |
|--------|----------------|-----|
| `phase_context_handoff` trace | `ranked_context_items`, `pruned`, `from_phase_index`, `to_phase_index` | Volume and pruning of handoff |
| Phase 0 `phase_completed` | `context_output` / ranked_context size (via final state) | Signal strength leaving Phase 0 |
| Phase 1 `phase_completed` | `failure_class`, `phase_validation` | Whether Phase 1 fails validation despite Phase 0 success |
| `config.agent_config` | `MAX_CONTEXT_CHARS` | Hard cap context for handoff pruning already |

**No new trace fields are required** to *ask* the questions below; they may be required later to *answer* merge effectiveness in production.

---

## Unresolved design questions (not answered here)

### A. Merge locus

1. **Where** in the pipeline should prior-phase ranked context enter: before first `SEARCH`, at `BUILD_CONTEXT`, both, or lane-specific?
2. Should merge apply **only** when `current_phase_index == 1` and handoff keys are present, or also for hypothetical future phases?
3. Is merge **docs-only**, **code-only**, or **both** lanes depending on `dominant_artifact_mode`?

### B. Ordering and deduplication

4. If Phase 1 retrieval returns candidates **overlapping** Phase 0 snippets, do we **dedup** by file path, hash, or text? **Deterministic** order required.
5. Should `prior_phase_ranked_context` be **prepended**, **appended**, or **interleaved** with Phase 1 native `ranked_context`? Who wins on conflict?

### C. Caps and budgeting

6. How does merge interact with **`MAX_CONTEXT_CHARS`** and existing `_build_phase_context_handoff` pruning (already splits budget in `deterministic_runner`)?
7. Is there a **separate cap** for “prior vs current phase” context in the LLM-facing bundle?

### D. Phase validation contract

8. How does merge affect **`require_ranked_context`**, **`min_candidates`**, and **`require_explain_success`** for Phase 1? Could merged prior context **satisfy** validation without Phase 1 retrieval doing meaningful work (gaming the contract)?
9. Should validation **require** a minimum fraction of **Phase 1–native** context vs handoff?

### E. Lane and artifact semantics

10. Docs-ranked snippets merged into **code** lane search: risk of **cross-lane pollution** — acceptable? Filtered how?
11. Should **`prior_phase_files` / `prior_phase_retrieved_symbols`** participate in merge **symmetrically** with `prior_phase_ranked_context` or different rules?

### F. Replanner and step dispatcher

12. Does **`replanner`** need to **see** merged context for replan decisions, or only `execution_loop`?
13. Does **`step_dispatcher`** need a **single** injection point, or per-action hooks?

### G. Determinism and tests

14. What **golden** tests prove merge is stable across OS / ordering?
15. How do we regression-test **without** bloating every `test_two_phase_execution` case?

### H. Observability

16. What **trace fields** would prove merge happened (counts merged, truncated, deduped) without leaking full content?

---

## Hold-expiry criteria (for retrieval merge — informational only)

**Not** a Stage 11 gate like clarification. Rough **future** criteria to justify a **merge implementation** project:

- Repeated Phase 1 **failure** or **poor ranking** in traces where **`phase_context_handoff.ranked_context_items > 0`** and Phase 0 **succeeded** — suggesting Phase 1 ignores useful Phase 0 signal.
- REPLAN/RETRY **not** fixing the issue (policy exhaustion with “wrong retrieval” character).

Exact thresholds TBD in a **future** decision memo after measurement.

---

## Anti-patterns / false positives

1. **Implementing merge to fix parent-policy issues** — exhausted RETRY/REPLAN is **policy**; merge does not substitute for clarification.
2. **Merging without dedup** — duplicates inflate tokens and break determinism.
3. **Touching frozen files for a quick experiment** — violates project policy; need approved spec first.

---

## Explicit non-goals (this document)

| Non-goal | Notes |
|----------|--------|
| **Implementation** | No code |
| **Locked answers** | All questions remain open until a precoding memo |
| **Parent policy** | REPLAN / REQUEST_CLARIFICATION are orthogonal |
| **Compat path** | Handoff does not apply; merge must be **off** for compat |
| **Changing `deterministic_runner.py` handoff builder** | Optional future scope; not assumed |

---

## Relation to Stage 11 / Stage 12

- **`Docs/STAGE11_DECISION_MEMO.md`** — retrieval merge (Candidate B) is **deferred** until spec + frozen-file approval; this file only **captures questions**.
- **`Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`** — **clarification** is a **separate** gate; do not bundle merge + clarification in one PR without explicit approval.

---

*End of retrieval handoff merge design questions.*
