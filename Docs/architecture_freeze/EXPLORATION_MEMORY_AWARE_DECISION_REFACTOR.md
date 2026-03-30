# EXPLORATION_MEMORY_AWARE_DECISION_REFACTOR

## Step 1 — Decision function locations

| Item | File | Function / area |
|------|------|-----------------|
| Gap-driven decision | `agent_v2/exploration/exploration_engine_v2.py` | `_apply_gap_driven_decision` |
| Refine gate | `agent_v2/exploration/exploration_engine_v2.py` | `_should_refine` |
| Next action from decision | `agent_v2/exploration/exploration_engine_v2.py` | `_next_action` (static) |
| Expand gate | `agent_v2/exploration/exploration_engine_v2.py` | `_should_expand` (unchanged) |
| Analyzer → decision | `agent_v2/exploration/exploration_engine_v2.py` | `_explore_inner`: `EngineDecisionMapper.to_exploration_decision` then `_apply_gap_driven_decision` |
| Refine → expand override | `agent_v2/exploration/exploration_engine_v2.py` | `_explore_inner` (after `_apply_refine_cooldown`, before `_should_expand`) |

---

## Output (Step 7)

```json
{
  "functions_modified": [
    "_apply_gap_driven_decision",
    "_should_refine",
    "_explore_inner (refine→expand coercion block only)"
  ],
  "decision_changes": [
    {
      "function": "_apply_gap_driven_decision",
      "before": "Used only analyzer knowledge_gaps; forced refine when has_refine_gap OR decision.status == partial; mapped category none to refine; did not read working memory.",
      "after": "Reads memory.get_summary() for gaps (and relationship count for telemetry). Merges memory gap descriptions with analyzer accepted gaps into a deduped combined list for classification. Caller/callee/flow still force expand (strict). Refine only when has_refine_gap from usage/definition/config/usage_symbol_fallback. No longer maps partial alone to refine. Removed none→refine.",
      "reason": "Make decisions cumulative with stored gaps and prefer graph expansion when relationship gaps remain."
    },
    {
      "function": "_should_refine",
      "before": "Refine allowed on wrong_target, no_improvement_streak>0, or refine action with partial status.",
      "after": "Refine allowed on wrong_target, low relevance in reason (substring), or partial when refine is still the action; blocks refine when memory gaps classify as caller/callee/flow and graph expansion is still mechanically allowed (same depth/symbol checks as expand path). Removed streak-based refine trigger.",
      "reason": "Reduce refine dominance when relationship gaps + expansion are still viable."
    },
    {
      "function": "_explore_inner",
      "before": "Called _should_refine(action, decision, ex_state) without target/memory.",
      "after": "Passes target and memory into _should_refine; inserts a refine→expand coercion when memory gaps indicate caller/callee/flow and expansion is still possible (sets needs + next_action expand).",
      "reason": "Force expand before refine when relationship signal is in memory and expand gate can still pass."
    }
  ],
  "refine_rules": [
    "Refine when decision.status is wrong_target (unchanged).",
    "Refine when reason text contains low relevance (case-insensitive substring).",
    "Refine when next_action is refine and status is partial, unless memory gaps are relationship-oriented (caller/callee/flow) and expansion is still viable (symbol present, depth under cap, symbol not already expanded).",
    "Do not refine solely because no_improvement_streak > 0."
  ],
  "expand_override_logic": "If action is refine after cooldown/oscillation, scan memory.get_summary() gaps for caller/callee/flow via existing _classify_gap_category; if relationship signal and expand is mechanically possible, rewrite decision to next_action=expand with appropriate needs. Separately, _apply_gap_driven_decision merges analyzer + memory gaps so caller/callee/flow from memory steer toward expand before next_action is chosen.",
  "risk_assessment": [
    "Runs without memory gaps behave as before except partial no longer forces refine from gap mapper alone.",
    "Stagnation no longer triggers refine via _should_refine; exploration may rely more on utility stop or other termination paths.",
    "Coercion and _should_refine both use the same substring rules as _classify_gap_category — deterministic but tied to analyzer wording."
  ],
  "test_plan": [
    "tests/test_exploration_engine_v2_control_flow.py",
    "Optional: add a unit test with memory preloaded with a caller gap description and decision next_action refine to assert coercion to expand."
  ]
}
```

---

## Expected outcome

- Refine is no longer the default for `partial` from gap mapping alone.
- Caller/callee/flow gaps from analyzer **or** memory steer toward expand.
- Refine dominance is reduced; relationship gaps + viable expansion skip refine or are coerced to expand before the refine branch.
