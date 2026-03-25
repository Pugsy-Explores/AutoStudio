# Architecture freeze — normative index

## Normative architecture

The following files are the **source of truth** for types, contracts, and phase-1 schema shapes:

1. **`SCHEMAS.md`** — frozen JSON contracts (`PlanDocument`, `PlanStep`, `ExecutionResult`, `ReplanRequest`, etc.)
2. **`PHASE_1_SCHEMA_LAYER.md`** — Pydantic/dataclass mapping and file layout under `agent_v2/schemas/`
3. **`VALIDATION_REGISTRY.md`** — single ownership for **plan / replan-result** validation (where **`PlanValidator`** lives; who calls it)

All other documents in this folder (**including `ARCHITECTURE_FREEZE.md`**) are **explanatory** (narrative, diagrams, onboarding), except **`VALIDATION_REGISTRY.md`** which is **normative for validation ownership** alongside **`SCHEMAS.md`**.

### Conflict resolution

```text
If any conflict exists → SCHEMAS.md wins
```

Then align **`PHASE_1_SCHEMA_LAYER.md`** to match **`SCHEMAS.md`**.

### v2 freeze note

This folder is the **v2** contract baseline: **`README.md`** hierarchy, **`SCHEMAS.md`** (`PlanStep` / `PlannerInput` / `ErrorType` / `ReplanRequest` / `ReplanResult` / exploration rules / retry authority), **`VALIDATION_REGISTRY.md`**, **`PHASE_1_SCHEMA_LAYER.md`**, **`PHASED_IMPLEMENTATION_PLAN.md`** (Phases 1–12), aligned **`PHASE_7`**, **`PHASE_9`**, **`PHASE_10`**, **`SUPPORTING_SCHEMAS.md`**, **`TOOL_EXECUTION_CONTRACT.md`**, and narrative **`ARCHITECTURE_FREEZE.md`** pointer to schemas.
