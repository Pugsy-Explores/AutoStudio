# Query Shaping — Final Acceptance and Residual-Risk Report

## 1. ACCEPTANCE DECISION

**The query-shaping patch is accepted for rollout.**

**Why accepted despite S5 still having one replan:** The success criterion was replan_count ≤ 1 for S5 and S6. S5 meets that (1 ≤ 1). The patch reduced S5 from 2 to 1 replans. The remaining replan is within the target and does not block acceptance.

**S6 performed better than target:** Target was replan_count = 1. S6 achieved replan_count = 0. The first EXPLAIN succeeded on the first attempt with the shaped query. This exceeds the stated goal.

---

## 2. BEFORE/AFTER IMPACT

| Scenario | Before replan_count | After replan_count | Delta | Outcome |
|----------|---------------------|--------------------|-------|---------|
| S5 | 2 | 1 | −1 | passed |
| S6 | 2 | 0 | −2 | passed |
| S4 | N/A | 0 | unchanged | failed (CODE_SEARCH only; pre-existing) |

---

## 3. WHAT THE PATCH PROVED

**Minimum proven claims:**

1. **First-attempt retrieval query shaping is a real causal lever for replan reduction.** Changing the first retrieval query from the full compound instruction to a focused target ("replanner") reduced replans in both S5 and S6.

2. **Mixed-context retrieval from broad compound instructions was part of the extra-replan cause.** The investigation showed that the broad query returned mixed files (plan_resolver, repo_learning, repo_map_builder); the focused query returned replanner-specific context. The behavioral difference correlates with this retrieval delta.

3. **Validator, replanner, and execution-loop changes were not required to get this improvement.** The fix is limited to query shaping on the first EXPLAIN retrieval path. No changes to validation, replanning, or stall logic.

---

## 4. RESIDUAL BEHAVIOR

**What remains:** S5 still has one replan after shaped retrieval. The first EXPLAIN uses the shaped query ("replanner") and receives context, but the model still refuses ("I cannot answer without relevant code context"). The replanner then adds SEARCH and a second EXPLAIN succeeds.

**Classification:** **Acceptable residual.** The target was replan_count ≤ 1; S5 meets it. The one residual replan is within the acceptance criterion. It is not a blocker. It is a candidate for future optimization if the goal is to reduce S5 replans further, but it does not block rollout.

---

## 5. RISK ASSESSMENT

**Heuristic extraction risk:** The shaping uses regex patterns. Unusual phrasings may not match or may yield wrong targets. Mitigation: when extraction fails, the original instruction is used unchanged. Fallback is safe.

**False shaping risk:** If the heuristic extracts a target that is not the main code-explanation target, retrieval could be less relevant. Mitigation: scope is limited to the first EXPLAIN retrieval path when `has_context` is False. Other paths (SEARCH, BUILD_CONTEXT, replanner fallbacks) use the existing logic. No shaping is applied to CODE_SEARCH or docs-lane paths.

**Why scope limitation keeps risk acceptable:** The patch only affects the code-lane EXPLAIN path when there is no prior context. Non-code lanes, CODE_SEARCH, and all replanner-driven flows are unchanged. The change surface is small and the fallback is safe.

**Non-code lanes and CODE_SEARCH:** Intentionally unchanged. The shaping is gated by `artifact_mode == "code"` and only runs when injecting a search for the first EXPLAIN. CODE_SEARCH (e.g. S4) uses the policy engine’s query rewriter; no shaping is applied.

---

## 6. NEXT RECOMMENDATION

**A) Stop here and move to next milestone.**

**Justification:** The success criteria are met. S5 and S6 both pass with replan_count ≤ 1. S6 achieved replan_count = 0. The patch is proven and low-risk. S5’s residual replan is within the acceptance band and does not block rollout. A further investigation into the S5 refusal would be a separate optimization, not a blocker. Moving to the next milestone is the right choice.
