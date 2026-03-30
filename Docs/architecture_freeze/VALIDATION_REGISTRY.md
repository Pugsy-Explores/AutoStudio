# Validation registry (single ownership)

**Normative:** All **plan-shaped** and **replan-result-shaped** structural checks against **`SCHEMAS.md`** MUST go through **one** implementation surface. Phase documents MUST NOT each define incompatible copies of the same rules.

---

## Canonical module

| Artifact | Path (implementation target) |
|----------|------------------------------|
| **PlanValidator** | `agent_v2/validation/plan_validator.py` |

**Responsibilities:**

- Validate **`PlanDocument`** after planner output (initial or replan): steps non-empty, **`finish`** present, **`action` / `type` literals**, dependency sanity, bounds (e.g. max steps), **`PlanStep.execution.max_attempts`** set from policy when plan is accepted (see **Retry authority** in **`SCHEMAS.md`**).
- Validate **`ReplanResult`** where **`SCHEMAS.md`** requires **`validation.is_valid`**, **`new_plan`** nullability vs **`status`**, **`changes`** consistency — **or** delegate **`ReplanResult`** to a small **`ReplanResultValidator`** in the **same package** (`agent_v2/validation/`) so all schema-level checks remain **importable from one package**.

**Non-responsibilities:**

- **Tool / dispatcher** normalization (**`ToolResult` → `ExecutionResult`**) stays at the boundary per **`TOOL_EXECUTION_CONTRACT.md`**.
- **Pydantic** on schema models catches parse/shape errors; **PlanValidator** adds **cross-field** and **policy** rules not expressible as field types alone.

---

## Who calls what (no scatter)

| Caller | Calls |
|--------|--------|
| **PlannerV2** (`planner_v2.py`) | **`PlanValidator.validate_plan(plan)`** before returning **`PlanDocument`** |
| **Replanner** (`replanner.py`) | **`PlanValidator`** (or sibling in `validation/`) for **`ReplanResult`** + new **`PlanDocument`** before swap |
| **PlanExecutor** (`plan_executor.py`) | Does **not** re-validate full plan on every step; assumes validated at load. **May** assert **`PlanStep`** invariants in dev. **Mutates** **`PlanStep.execution`** only (retry authority). |
| **Phase 10 hardening** | **Boundary checks** (e.g. `isinstance(result, ExecutionResult)`) + **invoke** shared validators where a full object is received again (e.g. after deserialization) |

**Forbidden:**

```text
- Copy-paste of _validate_plan logic in Phase 7, Phase 10, and planner
- Divergent “finish step required” rules in three files
```

**Allowed:**

```text
- Thin wrappers that call PlanValidator with context (e.g. exploration items for heuristic path checks)
```

---

## Document map

| Doc | Role |
|-----|------|
| **`SCHEMAS.md`** | **What** must hold (truth) |
| **`VALIDATION_REGISTRY.md`** | **Where** validation code lives (this file) |
| **`PHASE_4_PLANNER_V2.md`** | Planner wires **`PlanValidator`** after **`_build_plan`** |
| **`PHASE_7_REPLANNER_CONTROL_LOOP.md`** | Replanner uses same **`validation/`** package for **`ReplanResult`** + new plan |
| **`PHASE_10_HARDENING_PRODUCTION_READINESS.md`** | Enforcement + calls into **`validation/`**, no duplicate business rules |

---

## Related

- **`SUPPORTING_SCHEMAS.md`** — **`ValidationResult`** type for generic pass/fail lists.
- **`PHASE_1_SCHEMA_LAYER.md`** — no validation logic in schema modules; only types.
