# Stage 37 — Intent Routing Reset

## 0. Scope

Stage 37 is a code-first routing reset for a general software AI assistant.
It does not touch patch generation, eval harnesses, or planner internals.
All changes are confined to `agent/routing/` and `tests/test_intent_routing.py`.

---

## 1. Current Routing Map (pre-Stage 37 audit)

### 1.1 Three independent routing systems, none sharing a schema

| System | Location | Output type | Called by |
|--------|----------|-------------|-----------|
| Legacy model router | `agent/routing/instruction_router.py` | `RouterDecision(category, confidence)` | `plan_resolver.get_plan()` |
| Docs-artifact detector | `agent/orchestrator/plan_resolver._is_docs_artifact_intent()` | `bool` | `plan_resolver.get_plan()` (before router) |
| Two-phase mixed-intent detector | `agent/orchestrator/plan_resolver._is_two_phase_docs_code_intent()` | `bool` | `plan_resolver.get_parent_plan()` |

None of these systems call each other. Each embeds its own string-matching logic.
They produce incompatible output types and cannot be unit-tested against a shared contract.

### 1.2 Legacy RouterDecision categories

| Category | Plan effect | Notes |
|----------|-------------|-------|
| `CODE_SEARCH` | Single SEARCH step (skip planner) | Requires `confidence >= ROUTER_CONFIDENCE_THRESHOLD` |
| `CODE_EXPLAIN` | Single EXPLAIN step (skip planner) | Same threshold guard |
| `INFRA` | Single INFRA step (skip planner) | Same threshold guard |
| `CODE_EDIT` | Planner produces multi-step plan | |
| `GENERAL` | Planner produces multi-step plan | Fallback |

Missing: `DOC`, `VALIDATE`, `COMPOUND`, `AMBIGUOUS`.

### 1.3 Conflicts and accidental rerouting found

**Conflict 1 — Docs bypass happens before the router.**
`plan_resolver.get_plan()` calls `_is_docs_artifact_intent(instruction)` on line 219
*before* calling `route_instruction()`. A docs-matching instruction never reaches the
model router; it gets injected with a hardcoded seed plan. This means:
- DOC intent is not a router category.
- The router has no awareness that docs were detected.
- Telemetry records `docs_seed_plan_used=True` but `router_short_circuit_used=False`
  even though no routing decision was made.

**Conflict 2 — Two-phase logic lives inside the orchestrator, not the router.**
`get_parent_plan()` calls `_is_two_phase_docs_code_intent()`, which re-scans the
instruction using the *same* token lists as `_is_docs_artifact_intent()` but with
an extra code-marker check. If it fires, it builds a two-phase parent plan and
returns — the router is never consulted. Result: the same instruction can go through
completely different paths depending on which string-scan fires first, and there is
no single place to inspect "what did the router decide?"

**Conflict 3 — VALIDATE is not modelled.**
"Run tests" queries route to `CODE_EDIT` or `GENERAL` in the legacy router.
Both send the instruction to the planner, which must infer intent from scratch.
No short-circuit exists for pure validation requests.

**Conflict 4 — COMPOUND is not modelled.**
Multi-intent instructions (e.g. "find the auth module and explain how it works")
produce a single RouterDecision category. The selected category is arbitrary —
whichever the small model happened to weight more. No `decomposition_needed` flag
is set; the planner receives the full instruction with no hint that decomposition
might help.

**Conflict 5 — AMBIGUOUS is silently promoted to GENERAL.**
When the model outputs an unknown category or fails entirely, the code normalises
to `GENERAL` with `confidence=0.0` or `0.5`. GENERAL sends the instruction to
the planner, which is correct, but the low confidence is discarded rather than
surfaced. Downstream observability is lost.

**Conflict 6 — DOC and EXPLAIN conflation in plan_resolver token lists.**
`_NON_DOCS_TOKENS` in `plan_resolver.py` includes `"explain"` and `"flow"`.
This means: if an instruction contains "explain" AND docs tokens, the docs-artifact
detector returns `False` and control falls through to the two-phase detector. The
routing outcome depends on whether additional code markers are present. An
instruction like "Find the docs and explain the architecture" may or may not enter
the docs lane depending on whether "explain" fires the two-phase check. The router
is not involved either way.

---

## 2. New Routing Contract

### 2.1 Intent taxonomy

| Intent | Meaning | Suggested plan shape |
|--------|---------|---------------------|
| `SEARCH` | Locate code, files, symbols | `single_step_search` |
| `DOC` | Read/locate documentation artifacts | `docs_seed_lane` |
| `EXPLAIN` | Explain or describe behaviour | `single_step_explain` |
| `EDIT` | Modify, fix, add, refactor code | `planner_multi_step` |
| `VALIDATE` | Run tests, verify correctness | `single_step_validate` |
| `INFRA` | Docker, CI, build, deploy | `single_step_infra` |
| `COMPOUND` | Two or more distinct intents | `decompose_then_route` |
| `AMBIGUOUS` | No clear intent; needs clarification | `defer_to_planner` |

### 2.2 RoutedIntent schema

```python
@dataclass(frozen=True)
class RoutedIntent:
    primary_intent: str           # One of PRIMARY_INTENTS
    secondary_intents: tuple[str, ...]  # Non-empty only when COMPOUND
    decomposition_needed: bool    # True for COMPOUND and AMBIGUOUS
    confidence: float             # [0, 1]; AMBIGUOUS must not report high confidence
    rationale: str                # Human-readable reason
    matched_signals: tuple[str, ...]  # Lexical signals that fired
    suggested_plan_shape: str     # Hint to plan_resolver; not a binding contract
```

`plan_resolver` is free to ignore `suggested_plan_shape`. It is a hint, not a
directive — the router does not know about retry budgets or phase structures.

### 2.3 Design rules

1. **Single intent → classify directly.** No decomposition, confidence 0.85.
2. **Multiple intents → COMPOUND.** List all as `secondary_intents`. Set
   `decomposition_needed=True`. Confidence 0.6 (lower because signal is mixed).
3. **No match → AMBIGUOUS.** Confidence 0.0. Do not force a label on vague input.
4. **DOC suppresses SEARCH when they are the only two intents.**
   "Where is the README?" fires both `SEARCH("where")` and `DOC("readme")`.
   Without suppression this becomes COMPOUND, which triggers decomposition for a
   trivially docs-navigation request. The suppression rule collapses it to DOC.
   The suppression does NOT apply if any third intent (e.g. EDIT) is also present.
5. **Matched signals are always exposed.** Even AMBIGUOUS results expose `matched_signals=()`
   so callers can log what was tried.

---

## 3. Files Changed

| File | Change |
|------|--------|
| `agent/routing/intent.py` | Added `matched_signals`, `suggested_plan_shape` to `RoutedIntent`; added `PLAN_SHAPE_*` constants; added `default_plan_shape()`; updated `to_dict()`, `routed_intent_from_dict()`, `routed_intent_from_router_decision()` |
| `agent/routing/simple_router.py` | Rewrote signal collection with per-intent fired-signal tracking; added DOC/SEARCH suppression rule; added `matched_signals` and `suggested_plan_shape` to all return paths; expanded `_EDIT_MARKERS` with `refactor/rename/delete/remove/rewrite`; tightened `_INFRA_MARKERS` (`ci ` not `ci` to avoid false matches) |
| `agent/routing/__init__.py` | Exported new `PLAN_SHAPE_*` constants and `default_plan_shape` |
| `tests/test_intent_routing.py` | Expanded from 28 to 29 tests; 8 simple/8 compound/8 ambiguous; added contract tests for new fields; added confusion-style summary at bottom |

**Not changed:**
- `instruction_router.py` — legacy model router, no schema change needed
- `router_registry.py` — no change
- `plan_resolver.py` — no change (see Section 5, Remaining Risks)

---

## 4. Test Coverage

### 4.1 Group A — Simple single-intent (8 cases)

| Test | Instruction | Expected |
|------|-------------|----------|
| `test_simple_search_where` | "Where is the login function defined?" | SEARCH |
| `test_simple_search_find` | "Find all usages of fetch_user" | SEARCH |
| `test_simple_search_list` | "List all API endpoints in the codebase" | SEARCH |
| `test_simple_doc_readme` | "What's in the README?" | DOC |
| `test_simple_doc_installation` | "Show the installation docs" | DOC |
| `test_simple_doc_where_readme_suppressed_to_doc` | "Where is the README?" | DOC (suppression rule) |
| `test_simple_explain_how_does` | "How does the auth flow work?" | EXPLAIN |
| `test_simple_validate_pytest` | "Run pytest on tests/unit" | VALIDATE |

### 4.2 Group B — Compound (8 cases)

| Test | Instruction | Expected |
|------|-------------|----------|
| `test_compound_find_and_explain` | "Find the auth module and explain how it works" | COMPOUND(SEARCH, EXPLAIN) |
| `test_compound_fix_and_validate` | "Fix the bug and run tests to validate the fix" | COMPOUND(EDIT, VALIDATE) |
| `test_compound_docs_and_edit` | "Find the README and update the version number" | COMPOUND(…, EDIT) |
| `test_compound_explain_and_add` | "Explain the current flow and add logging…" | COMPOUND(EXPLAIN, EDIT) |
| `test_compound_search_fix_validate` | "Find the failing test, fix it, and run pytest" | COMPOUND(≥2 intents) |
| `test_compound_infra_and_explain` | "Set up Docker and explain how the containers…" | COMPOUND(INFRA, EXPLAIN) |
| `test_compound_edit_and_validate` | "Refactor the parser module and check that all tests pass" | COMPOUND(EDIT, VALIDATE) |
| `test_compound_has_matched_signals` | "Find the auth module and explain how it works" | matched_signals ≥ 2 |

### 4.3 Group C — Ambiguous / borderline (8 cases)

| Test | Instruction | Expected |
|------|-------------|----------|
| `test_ambiguous_empty` | "" | AMBIGUOUS, confidence=0.0 |
| `test_ambiguous_whitespace_only` | "   " | AMBIGUOUS |
| `test_ambiguous_no_markers` | "The thing that does stuff" | AMBIGUOUS |
| `test_ambiguous_vague` | "Something is wrong with the code" | AMBIGUOUS |
| `test_ambiguous_pronoun_only` | "Make it work" | AMBIGUOUS |
| `test_ambiguous_no_confidence_inflation` | "The thing is broken" | AMBIGUOUS, confidence < 0.5 |
| `test_borderline_single_word_refactor` | "refactor" | EDIT or AMBIGUOUS (either acceptable) |
| `test_borderline_update_without_target` | "update the code" | EDIT, COMPOUND, or AMBIGUOUS |

### 4.4 Confusion summary

```
DOC vs SEARCH
  "Where is the README?" fires DOC+SEARCH.
  Fix: suppression rule collapses to DOC when no third intent fires.
  Risk: "Find the README and diff it" keeps COMPOUND because EDIT fires too.

EXPLAIN vs EDIT
  "Describe and refactor the auth module" → COMPOUND(EXPLAIN, EDIT). Correct.
  Risk: "Explain the change you just made" could fire EXPLAIN only (desired) but
  might also fire EDIT("change"); currently classified COMPOUND. Acceptable for
  keyword router; a model router could resolve this.

VALIDATE vs EDIT
  "Fix the failing test" → EDIT only (correct; no run-test marker).
  "Fix the tests and run pytest" → COMPOUND(EDIT, VALIDATE). Correct.

INFRA vs EDIT
  "Update the Dockerfile" → COMPOUND(INFRA("dockerfile"), EDIT("update")). Intentional:
  the user is editing an infra artifact. No suppression applied.

AMBIGUOUS
  Any instruction without a matched marker lands AMBIGUOUS. Conservative and correct.
  Do not add heuristics to force a label.
```

---

## 5. Remaining Risks

### Risk 1 — `plan_resolver.py` is unchanged; routing is still split

The three routing systems in `plan_resolver` (docs bypass, two-phase detector,
legacy router) are untouched. Production routing is still driven by `RouterDecision`,
not `RoutedIntent`. The new contract and `simple_router` are available but not wired in.

**Next step:** Add an optional `route_intent(instruction) -> RoutedIntent` path to
`plan_resolver.get_plan()`. When present, use `suggested_plan_shape` to select the
plan branch instead of the legacy category mapping.

### Risk 2 — DOC is still not a legacy router category

The model router (`instruction_router.py`) outputs `CODE_SEARCH/CODE_EDIT/CODE_EXPLAIN/
INFRA/GENERAL`. It has no `DOC` or `VALIDATE` output. Until the model is retrained or
the prompt updated, DOC requests will continue to bypass the router via
`_is_docs_artifact_intent()`.

**Next step:** Add DOC to the legacy router prompt and to `ROUTER_CATEGORIES`. Map it
to `RoutedIntent.primary_intent = INTENT_DOC` in `routed_intent_from_router_decision`.

### Risk 3 — COMPOUND decomposition is not implemented

When `decomposition_needed=True`, the system currently sends the full instruction to
the planner unchanged. The planner must infer intent without knowing it is compound.
No sub-instruction splitting exists.

**Next step:** When `primary_intent == COMPOUND`, split the instruction at the connector
("and", "then", "before") and route each sub-instruction independently. Requires planner
support for multi-goal plans.

### Risk 4 — `suggested_plan_shape` is ignored downstream

`plan_resolver` does not read `suggested_plan_shape`. The field is present and tested
but has no production effect yet.

**Next step:** Thread `RoutedIntent` through `get_plan()`. Map `suggested_plan_shape`
to the existing plan branches (single-step, docs-seed, planner, etc.).

### Risk 5 — Keyword router cannot distinguish SEARCH subject domain

"Find the login function" (code search) and "Find the README" (docs) both fire
`SEARCH("find")`. The suppression rule handles the second case when "readme" also
fires, but a query like "Find the setup information" would route SEARCH because
"setup" alone fires `_DOC_MARKERS` but "find" fires `_SEARCH_MARKERS` — two intents,
so COMPOUND. Whether that is correct depends on whether "setup information" refers
to a docs artifact or a code symbol. A model router can resolve this; the keyword
router cannot without risking over-specificity.
