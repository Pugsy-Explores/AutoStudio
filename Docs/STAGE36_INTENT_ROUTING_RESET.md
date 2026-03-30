# Stage 36 — Intent Routing Reset

## Summary

Stage 36 resets the routing layer for a general software AI assistant. It defines a minimal intent taxonomy, a stable routing contract, a simple keyword-based router for tests, and regression tests. Production plan_resolver continues to use the legacy RouterDecision; the new RoutedIntent schema is available for migration.

---

## 1. Previous Behavior

### Legacy Router Outputs

| Category | Meaning | Plan Effect |
|----------|---------|-------------|
| CODE_SEARCH | Find/locate code | Single SEARCH step (skip planner) |
| CODE_EXPLAIN | Explain code | Single EXPLAIN step (skip planner) |
| INFRA | Infrastructure | Single INFRA step (skip planner) |
| CODE_EDIT | Edit code | Planner produces multi-step plan |
| GENERAL | Fallback | Planner produces multi-step plan |

### Overlap / Conflicts

- **DOC intent** was not a router category. Docs-location requests were handled by `_is_docs_artifact_intent()` in plan_resolver, which bypasses the router entirely and injects a docs seed plan.
- **VALIDATE** (run tests) was not distinguished from EDIT; both could route to CODE_EDIT or GENERAL.
- **COMPOUND** (multiple intents) was not modeled; the router returned a single category.
- **AMBIGUOUS** was modeled as GENERAL; no explicit decomposition_needed flag.

### Implicit Re-Routing

- `plan_resolver.get_plan()` checks `_is_docs_artifact_intent(instruction)` before calling `route_instruction()`. Docs requests never reach the model router.
- When router confidence is below threshold, CODE_SEARCH/CODE_EXPLAIN/INFRA are treated as GENERAL (planner used).
- Two-phase docs+code intent (`_is_two_phase_docs_code_intent`) is detected in `get_parent_plan()`, not in the router.

---

## 2. New Behavior

### Intent Taxonomy

| Intent | Description |
|--------|-------------|
| SEARCH | Find, locate, list, grep |
| DOC | Readme, docs, installation, setup, guide |
| EXPLAIN | Explain, describe, how does |
| EDIT | Fix, add, change, modify, patch, implement |
| VALIDATE | Run tests, pytest, validate |
| INFRA | Docker, CI, build, deploy |
| COMPOUND | Multiple intents in one instruction |
| AMBIGUOUS | Unclear; needs decomposition |

### Routing Contract (RoutedIntent)

```python
@dataclass(frozen=True)
class RoutedIntent:
    primary_intent: str      # One of the taxonomy intents
    secondary_intents: tuple[str, ...] = ()
    confidence: float = 0.0
    rationale: str = ""
    decomposition_needed: bool = False
```

### New Modules

| Module | Purpose |
|--------|---------|
| `agent/routing/intent.py` | Intent constants, RoutedIntent schema, `routed_intent_from_router_decision()` |
| `agent/routing/simple_router.py` | Deterministic keyword-based router (no model) for tests |
| `tests/test_intent_routing.py` | 28 regression tests |

### Legacy Adapter

`routed_intent_from_router_decision(category, confidence)` maps:

- CODE_SEARCH → SEARCH
- CODE_EDIT → EDIT
- CODE_EXPLAIN → EXPLAIN
- INFRA → INFRA
- GENERAL → AMBIGUOUS (decomposition_needed=True)

---

## 3. Regression Tests

| Category | Count | Examples |
|----------|-------|----------|
| Simple SEARCH | 3 | "Where is the login function?", "Find all usages of fetch_user" |
| Simple DOC | 2 | "What's in the README?", "Show the installation docs" |
| Simple EXPLAIN | 2 | "Explain how the auth flow works" |
| Simple EDIT | 2 | "Fix the bug in utils.py", "Add a retry decorator" |
| Simple VALIDATE | 2 | "Run tests for the parser module", "Run pytest on tests/unit" |
| Simple INFRA | 2 | "Set up Docker for the project", "Configure the CI pipeline" |
| Compound | 5 | "Find the auth module and explain how it works" |
| Ambiguous | 6 | "", "The thing that does stuff", "Make it work" |
| Contract | 4 | to_dict, from_dict, from_router_decision |

---

## 4. Remaining Risks

1. **Simple router is keyword-only.** Phrases like "Where is the README?" match both SEARCH ("where") and DOC ("readme") and are classified as COMPOUND. Single-intent doc tests use phrases that match only DOC.

2. **plan_resolver unchanged.** Production still uses `route_instruction()` → RouterDecision. The new RoutedIntent is not yet wired into get_plan(). Migration would require plan_resolver to accept RoutedIntent and map it to plan shapes.

3. **DOC vs SEARCH overlap.** "Where is X?" is inherently a search; "Where is the README?" could be DOC (find docs) or SEARCH (find file). The simple router does not prioritize DOC for docs-named targets.

4. **VALIDATE not in legacy router.** The model/router_eval routers output CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL. VALIDATE would map to CODE_EDIT or GENERAL in legacy flow.

5. **Compound decomposition.** COMPOUND and AMBIGUOUS set decomposition_needed=True, but no decomposition logic exists yet. The planner still receives the full instruction.

---

## 5. Next-Stage Recommendations

1. **Wire RoutedIntent into plan_resolver.** Add an optional `route_intent(instruction) -> RoutedIntent` path; when available, use it to select plan shape instead of RouterDecision.

2. **Add DOC to legacy router categories** (or handle DOC in plan_resolver before router, as today). Ensure "find README" / "show installation docs" get docs lane.

3. **Add VALIDATE handling.** When primary_intent=VALIDATE, consider a dedicated validation plan (run tests, report results) instead of generic EDIT.

4. **Decomposition for COMPOUND.** When decomposition_needed=True, split instruction into sub-instructions and route each. Requires planner support.

5. **Do not add** task-id, suite-name, fixture-path, or benchmark-specific logic to routing.
