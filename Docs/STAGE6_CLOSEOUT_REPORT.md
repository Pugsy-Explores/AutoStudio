# Stage 6 closeout report

**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` line (hierarchical orchestration continuation after Stage 5)

---

## Scope completed

**Stage 6** ships **connector coverage** for `_derive_phase_subgoals` and **`two_phase_near_miss` trace observability** on the compatibility fallback path in `get_parent_plan`—**without** widening `_is_two_phase_docs_code_intent`, **without** changing `run_hierarchical` / `run_deterministic`, and **without** any new hierarchical `loop_output` keys.

---

## Files changed in Stage 6

| Area | File |
|------|------|
| Production | `agent/orchestrator/plan_resolver.py` |
| Tests | `tests/test_two_phase_execution.py` (`TestStage6DetectionConnectors`) |

No other production files. **`deterministic_runner.py` was not modified.**

---

## Shipped semantics

### 1. `_derive_phase_subgoals` — connector tuple (exact order)

Stage 6 extends the connector list from five strings to thirteen. **First-match** behavior, **`len(raw) >= 10` fallback**, and **phase 0** prefix (`"Find documentation artifacts relevant to: " + source[:150]`) are unchanged from Stage 2.

Supported connectors (tuple order):

```text
(
    " and explain ",
    " and describe ",
    " and show how ",
    " and summarize ",
    " and walk through ",
    ", then explain ",
    " then explain ",
    " and tell me about ",
    " and tell me how ",
    " and walk me through ",
    " and illustrate ",
    " before explaining ",
    ", explain ",
)
```

### 2. `get_parent_plan` — `two_phase_near_miss` trace

Emitted **only** when **all** hold:

- Compat fallback is about to run: `_is_two_phase_docs_code_intent(instruction)` is **False** (so two-phase did not “win” for this instruction in this call).
- `log_event_fn` and `trace_id` are both present.
- Lowercased instruction contains at least one token from `_DOCS_DISCOVERY_VERBS` **and** at least one from `_DOCS_INTENT_TOKENS`.

Payload:

```text
{
  "reason": "docs_and_discovery_but_no_code_marker",
  "instruction_preview": (instruction or "")[:200],
}
```

Logging is wrapped in `try/except` so failures do not propagate.

---

## Audit: detection semantics were NOT widened

**Narrow audit (Stage 6 scope):**

| Item | Status |
|------|--------|
| `_is_two_phase_docs_code_intent` | **Not modified.** Same `code_markers` and guards as before Stage 6. |
| Who enters two-phase | Unchanged: only instructions that already matched the heuristic before Stage 6 still match; Stage 6 does **not** add new code-intent tokens or relax `_is_docs_artifact_intent` interactions. |
| Connector / near-miss | **Connector derivation** only affects **Phase 1 subgoal text** when `_build_two_phase_parent_plan` runs (i.e. when two-phase **already** fired). **Near-miss** is **observability only** on the compat path when two-phase did **not** fire. |

Stage 6 intentionally improved **subgoal split quality** for additional natural-language connectors (e.g. `", then explain "`, `" then explain "`) **without** expanding which instructions are classified as two-phase.

---

## Compatibility and hierarchical outputs

- **Compat path:** Unchanged. `run_hierarchical` with `compatibility_mode=True` still delegates only to `run_deterministic` and returns its `(state, loop_output)` with **no** hierarchical-only keys (unchanged from Stages 3–5; Stage 6 did not touch `tests/hierarchical_test_locks.py`).
- **Hierarchical `loop_output`:** **No new keys** in Stage 6. `HIERARCHICAL_LOOP_OUTPUT_KEYS` and per-phase forbidden top-level names were not extended for this stage.

---

## Proof commands

```bash
python3 -m pytest tests/test_two_phase_execution.py -q
```

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

## Proof results (recorded at Stage 6 closeout)

| Command | Result |
|---------|--------|
| `tests/test_two_phase_execution.py` only | **150 passed** |
| Three-file hierarchical slice | **173 passed** |

Run these after checkout to confirm the tree matches the recorded counts (counts may drift if unrelated tests are added).

---

## Audit notes (near-miss behavior)

- **`two_phase_near_miss`** only runs on the **compat fallback** path: immediately before `get_plan(...)`, when `_is_two_phase_docs_code_intent(instruction)` is **False**.
- **No** `two_phase_near_miss` when two-phase **actually** fires and returns a non-compat `ParentPlan` (early return; near-miss block not reached for that success path).
- **No** `two_phase_near_miss` for **pure code** instructions that lack docs-intent tokens (discovery + docs both required).
- **Phase 1 subgoal quality:** For two-phase runs, instructions that use connectors such as `", then explain "` or `" then explain "` now split so Phase 1’s `subgoal` is the tail segment (when `len(raw) >= 10`), instead of falling back to the **full** parent instruction—**without** changing execution_loop, handoff, or retry policy.

---

## Explicit non-goals (preserved)

Stage 6 does **not** ship:

| Non-goal | Notes |
|----------|--------|
| **REPLAN** | Parent policy still `CONTINUE` / `RETRY` / `STOP` only (`deterministic_runner.py` unchanged). |
| **REQUEST_CLARIFICATION** | Not a parent-level outcome. |
| **≥ 3 phases** | `len(phases) != 2` guard unchanged. |
| **Retrieval / prior-phase context wiring** | No changes to `execution_loop`, `step_dispatcher`, `replanner`. |
| **`deterministic_runner.py`** | No changes in Stage 6. |

---

## Recommended next slice — Stage 7 (planning only)

**Do not implement Stage 7 code in the Stage 6 closeout task.**

Recommended next step: **Stage 7 = a parent policy expansion decision memo** (documentation / architecture review first), before any code:

- Compare **REPLAN** vs **REQUEST_CLARIFICATION** vs **configurable retry budgets** (e.g. per–decomposition-type `max_parent_retries` for `two_phase_docs_code`).
- Define invariants: compat path, `phase_results` / `attempt_history`, and when a replan consumes a retry vs emits a terminal outcome.
- **No** Stage 7 implementation in this closeout; branch from the line containing this report when ready.

---

## Why Stage 6 mattered

Before Stage 6, some instructions that **already** qualified as two-phase under `_is_two_phase_docs_code_intent` still fed the **full parent instruction** into Phase 1 planning because `_derive_phase_subgoals` only recognized five connectors. Natural variants (e.g. “docs, then explain …”) did not split, so Phase 1 `subgoal` was coarser than necessary.

Stage 6 fixes **subgoal derivation** for additional connector patterns and adds **near-miss** logging for docs+discovery false negatives—**without** changing execution semantics, hierarchical aggregates, or compat behavior.

---

## Relation to prior stages

- **Stages 3–5** (closeout reports): phase validation, parent retry execution, attempt observability—all in `deterministic_runner.py`.
- **Stage 6** is confined to **`plan_resolver.py`** (+ tests): planning-time subgoals and trace only.

---

*End of Stage 6 closeout report.*
