# Stage 38 — Unified Production Routing Consumption

## Summary

Stage 38 removes split-brain routing in `plan_resolver`: docs detection, two-phase mixed-intent detection, and the legacy model router are orchestrated through **one** production function, `route_production_instruction()`, which returns **`RoutedIntent`**. `get_plan()` and `get_parent_plan()` branch only on `RoutedIntent` (plus `ENABLE_INSTRUCTION_ROUTER`), not on ad hoc duplicate string checks.

---

## 1. Entry point

| Function | Module | Returns |
|----------|--------|---------|
| `route_production_instruction(instruction, *, ignore_two_phase=False)` | `agent/routing/production_routing.py` | `RoutedIntent` |

### Evaluation order (when `ENABLE_INSTRUCTION_ROUTER` is true)

1. **Docs-artifact intent** (`docs_intent.is_docs_artifact_intent`) → `primary_intent=DOC`, `suggested_plan_shape=docs_seed_lane`
2. **Two-phase docs+code** (`docs_intent.is_two_phase_docs_code_intent`), unless `ignore_two_phase=True` → `primary_intent=COMPOUND`, `secondary_intents=(DOC, EXPLAIN)`, `suggested_plan_shape=two_phase_docs_code`
3. **Legacy model router** (`instruction_router.route_instruction`) → `routed_intent_from_router_decision()` with confidence handling:
   - Short-circuit categories (`CODE_SEARCH`, `CODE_EXPLAIN`, `INFRA`) with confidence **below** `ROUTER_CONFIDENCE_THRESHOLD` → effective **`AMBIGUOUS`**, `suggested_plan_shape=planner_multi_step` (same as legacy “treat as GENERAL”)

When `ENABLE_INSTRUCTION_ROUTER` is false, the function returns a **synthetic** `RoutedIntent`: `AMBIGUOUS`, `matched_signals=("router_disabled",)`, `suggested_plan_shape=planner_multi_step`, `rationale="instruction router disabled; defer to planner"` (not user ambiguity).

---

## 2. Modules

| Module | Role |
|--------|------|
| `agent/routing/docs_intent.py` | Token lists + `is_docs_artifact_intent`, `is_two_phase_docs_code_intent` (moved out of `plan_resolver`) |
| `agent/routing/production_routing.py` | Single production routing entrypoint |
| `agent/routing/intent.py` | `RoutedIntent` + `clarification_needed` (Stage 38) |
| `agent/orchestrator/plan_resolver.py` | Consumes `RoutedIntent`; no local docs/two-phase duplicates |

---

## 3. Ambiguity contract (fixed)

| `primary_intent` | `decomposition_needed` | `clarification_needed` | Meaning |
|------------------|------------------------|-------------------------|---------|
| `COMPOUND` | **True** | **False** | Multiple intents; parent or planner may decompose |
| `AMBIGUOUS` | **False** | **True** (typical) | Unclear user intent or legacy GENERAL / confidence fallback |
| Others | False | False | Single clear intent |

System defer (router disabled) uses `AMBIGUOUS` with `clarification_needed=False` so it is not confused with “user must clarify.”

---

## 4. `plan_resolver` behavior

### `get_plan(..., routed_intent=None, ignore_two_phase=False)`

- Builds `routed_intent` via `route_production_instruction` when `routed_intent` is not passed.
- Merges **Stage 38 telemetry** (see §5).
- **`ENABLE_INSTRUCTION_ROUTER` false:** always `plan()`; legacy flags unchanged.
- **`ENABLE_INSTRUCTION_ROUTER` true:**
  - `DOC` + `docs_seed_lane` → `_docs_seed_plan()` (unchanged structure)
  - `SEARCH` / `EXPLAIN` / `INFRA` → single-step plans (unchanged)
  - **`COMPOUND` on flat path:** defers to `plan()` and sets `routing_overridden_downstream=True`, `routing_override_reason="compound_intent_flat_plan_defers_to_planner"` (parent plans should use `get_parent_plan` for two-phase)

### `get_parent_plan()`

1. `ri = route_production_instruction(instruction)` — telemetry merged once.
2. If `ri` is **COMPOUND** and `suggested_plan_shape=two_phase_docs_code` → `_build_two_phase_parent_plan()`; on failure, **`get_plan(..., ignore_two_phase=True)`** (flat fallback, no stale two-phase `RoutedIntent`).
3. Else **`get_plan(..., routed_intent=ri)`** — avoids a second `route_production_instruction` call.

---

## 5. Observability (`get_plan_resolution_telemetry()`)

Populated keys include:

| Key | Description |
|-----|-------------|
| `routed_intent_primary` | `RoutedIntent.primary_intent` |
| `routed_intent_secondary` | list of secondary intents |
| `routed_intent_confidence` | float |
| `routed_intent_matched_signals` | list |
| `routed_intent_suggested_plan_shape` | string |
| `routing_overridden_downstream` | bool (e.g. COMPOUND on flat `get_plan`) |
| `routing_override_reason` | str or None |

Legacy fields (`planner_used`, `router_short_circuit_used`, `docs_seed_plan_used`, `router_category`) are still updated for backward compatibility. `router_category` is derived from `RoutedIntent` via `_legacy_router_category_label()` (`DOC`, `VALIDATE`, `COMPOUND`, `GENERAL`, etc.).

---

## 6. Remaining override points (explicit)

1. **`PLAN_SHAPE_*` are hints** — `plan_resolver` may still choose planner-only paths for safety.
2. **`get_plan` + COMPOUND** — flat execution cannot run parent two-phase; telemetry records override.
3. **`ignore_two_phase`** — used only for two-phase **build failure** fallback and tests; not for normal requests.
4. **Legacy model categories** — still five labels inside `instruction_router`; mapping to `RoutedIntent` is in `routed_intent_from_router_decision` / confidence fallback in `production_routing`.
5. **Patching in tests** — `ENABLE_INSTRUCTION_ROUTER` is imported into `plan_resolver` and `production_routing`; tests must patch **`agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER`** or **`agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER`** respectively, not only `config.router_config`.

---

## 7. Tests

| File | Focus |
|------|--------|
| `tests/test_plan_resolver_routing.py` | Resolver consumption: docs seed, SEARCH short-circuit, router disabled + telemetry, COMPOUND override, `route_production_instruction` two-phase vs docs-only, AMBIGUOUS clarification contract |
| `tests/test_intent_routing.py` | Keyword `simple_router` + schema |
| `tests/test_two_phase_execution.py` | Imports `is_two_phase_docs_code_intent` from `agent.routing.docs_intent` |

---

## 8. Non-goals (unchanged)

- No benchmark / task-id / suite / fixture logic in routing.
- No patch-generation or planner prompt changes beyond branch selection and telemetry.
