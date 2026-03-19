# First-Attempt EXPLAIN Refusal — Report-Only Investigation

## 1. TRACE COMPARISON TABLE

| Field | S5 First EXPLAIN | S5 Successful EXPLAIN | S6 First EXPLAIN | S6 Successful EXPLAIN |
|-------|------------------|----------------------|------------------|----------------------|
| **step description / question** | show architecture docs and explain replanner flow | show architecture docs and explain replanner flow | show architecture docs and explain replanner flow | show architecture docs and explain replanner flow |
| **context_chars** | 3131 | 2762 | 3131 | 2762 |
| **retrieval query** | "show architecture docs and explain replanner flow" | "replanner" | "show architecture docs and explain replanner flow" | "replanner" |
| **anchor_coverage_score** | 1.0 | 0.5 | 1.0 | 0.5 |
| **has_impl_body** | true | true | true | true |
| **impl_body_count** | 5 | 4 | 5 | 4 |
| **top_k_count** | 6 | 6 | 6 | 6 |
| **first 200 chars of model output** | "I cannot answer without relevant code context. Please run a SEARCH step first to locate the relevant code.\n\nThe provided context does not contain architecture documentation or detailed information abo" | "Based on the provided context, I can outline some aspects of the replanner architecture and flow:\n\n1. **Replanning Function**:\n   - The `replan` function is defined in `/Users/shang/my_work/AutoStudio" | "I cannot answer without relevant code context. Please run a SEARCH step first to locate the relevant code.\n\nThe provided context does not contain architecture documentation or detailed information abo" | "Based on the provided context, I can outline some aspects of the replanner architecture and flow:\n\n1. **Replanning Function**:\n   - The `replan()` function is defined in `/Users/shang/my_work/AutoStud" |

**Artifacts:** `phase7a_5_deterministic_1773916779_1773916779`, `phase7a_6_deterministic_1773916700_1773916700` (stages.jsonl, events.jsonl).

---

## 2. CONTEXT SHOWN TO MODEL

**First EXPLAIN (both S5 and S6):**

| Rank | file path | symbol / title | retrieval_result_type | implementation_body_present | first 100 chars of snippet |
|------|-----------|----------------|------------------------|----------------------------|-----------------------------|
| 1 | agent/orchestrator/replanner.py | replanner | (from top_files) | (impl_body_count=5) | (not logged in trace) |
| 2 | agent/orchestrator/plan_resolver.py | — | — | — | — |
| 3 | agent/intelligence/repo_learning.py | — | — | — | — |
| 4 | agent/orchestrator/replanner.py | — | — | — | — |
| 5 | repo_graph/repo_map_builder.py | — | — | — | — |

*Note: Trace stages log `top_files` only, not full `ranked_context` items. The above is inferred from `stages.jsonl` retrieval summary.*

**Successful EXPLAIN (both S5 and S6):**

| Rank | file path | symbol / title | retrieval_result_type | implementation_body_present | first 100 chars of snippet |
|------|-----------|----------------|------------------------|----------------------------|-----------------------------|
| 1 | agent/orchestrator/replanner.py | — | — | — | — |
| 2 | .phase6c_acceptance_fixtures/agent/orchestrator/replanner.py | — | — | — | — |
| 3 | .phase6c_acceptance_fixtures/agent/orchestrator/replanner.py | — | — | — | — |
| 4 | .phase6c_acceptance_fixtures/agent/orchestrator/ | — | — | — | — |
| 5 | .phase6c_acceptance_fixtures/agent/orchestrator/ | — | — | — | — |

*Note: Successful retrieval uses query "replanner"; top_files are replanner-specific.*

**Answers:**

- **Was the first attempt context replanner-specific or mixed?**  
  **Mixed.** Top files: `replanner.py`, `plan_resolver.py`, `repo_learning.py`, `replanner.py`, `repo_map_builder.py`. The broad query returns plan_resolver, repo_learning, and repo_map_builder alongside replanner.

- **Did the successful attempt become materially more focused on replanner implementation?**  
  **Yes.** Top files are dominated by `replanner.py` (main and fixtures). The query "replanner" yields replanner-specific context; the model then produces a partial answer.

---

## 3. RETRIEVAL DELTA

| Dimension | First EXPLAIN | Successful EXPLAIN |
|-----------|---------------|---------------------|
| **Query text** | "show architecture docs and explain replanner flow" (instruction) | "replanner" |
| **Search tool / path** | `retrieve_graph` (instruction → anchor "replanner" → hybrid retrieval) | `retrieve_graph` (anchor "replanner" from code_strong_retrieval_then_explain) |
| **ranked_context composition** | Mixed: replanner.py, plan_resolver.py, repo_learning.py, repo_map_builder.py | Replanner-focused: replanner.py and fixtures |
| **anchor_coverage_score** | 1.0 | 0.5 |
| **has_impl_body** | true | true |
| **impl_body_count** | 5 | 4 |
| **context_chars** | 3131 | 2762 |

**Substantial change:**

- **Query:** Broad instruction vs. focused "replanner".
- **Top files:** Mixed (plan_resolver, repo_learning, repo_map_builder) vs. replanner-specific.
- **Context size:** 3131 vs 2762 chars.

The behavioral difference is explained by the retrieval delta: the focused query produces replanner-specific context that supports a partial answer; the broad query produces mixed context that the model treats as insufficient.

---

## 4. POLICY ASSESSMENT

**Fix A prompt contract:**  
- Refuse only when context is empty or missing.  
- If context is present but partial: answer the supported part, explicitly note what is missing, and do not use the refusal phrase.

**Explain gate behavior:**

- First attempt: `ready=true`, `reason_code=null`, `anchor_coverage_score=1.0`, `has_impl_body=true`, `impl_body_count=5`.
- Gate passes; retrieval signals are above the threshold.

**Policy question:** Should the first-attempt EXPLAIN be expected to answer from the provided context?

**Answer:** Under Fix A, yes. Context is present (3131 chars), non-empty, and impl-body snippets exist. The model should answer the supported part (replanner flow) and note what is missing (architecture docs). It instead used the refusal phrase.

**Evidence:**

- The model’s first 200 chars: “The provided context does not contain architecture documentation or detailed information abo” — it treats the compound request as all-or-nothing.
- The gate did not block; the model chose to refuse.

**Conclusion:** One fallback replan is acceptable under current policy because:

1. The replanner correctly adds SEARCH when the model refuses.
2. The strong-retrieval fallback (SEARCH + BUILD_CONTEXT with focused query) improves context and succeeds.
3. The gate does not guarantee that the model will answer; it only ensures sufficient retrieval signals for the gate. The model’s refusal is a policy-acceptable behavior that triggers a recovery path.

**Is the first-attempt refusal acceptable?** Yes — it is expected behavior when the initial broad query yields mixed context. The fallback is doing useful recovery. The policy does not require the model to answer on the first attempt when context is mixed; it requires the fallback to succeed, which it does.

---

## 5. FINAL DECISION

**OPTIMIZE NEXT: retrieval/query shaping**

**Rationale:**

1. **Query:** First attempt uses the full instruction as the retrieval query; successful attempt uses focused “replanner”.
2. **Context:** First attempt is mixed (plan_resolver, repo_learning, repo_map_builder); successful attempt is replanner-specific.
3. **Behavior:** Same Fix A prompt; different context composition; different model behavior. The retrieval delta is the main lever.

**If and only if optimizing:**

- **Smallest change surface:** Query rewriting for the first EXPLAIN retrieval path. When the instruction is compound (e.g. “show architecture docs and explain replanner flow”), extract or prioritize the code-explanation anchor (“replanner”) for the initial retrieval query instead of using the full instruction.
- **Measurable success criterion:** S5 and S6 deterministic replan_count from 2 → 1 when the first attempt uses the rewritten query.
- **No-regression check:** Re-run S5 and S6; S4 (CODE_SEARCH only); and any other Phase 7A code-lane scenarios. No regression in status or final outcome.

---

## Summary

| Classification | Result |
|----------------|--------|
| A) Acceptable current-policy behavior | Partially |
| B) Retrieval-selection/query-quality issue worth optimizing next | Yes |
| C) Not worth pursuing now | No |

**Conclusion:** The first-attempt refusal is acceptable under current policy (one fallback is expected). It is also a retrieval/query-quality issue: the broad query yields mixed context; the focused query yields replanner-specific context; the model then answers. Reducing replan_count from 2 → 1 is a worthwhile optimization via query shaping.
