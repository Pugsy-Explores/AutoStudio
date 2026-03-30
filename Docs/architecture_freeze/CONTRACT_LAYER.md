# Contract layer (pre-implementation)

Define all contracts first. No explanations — complete, production-grade enumeration.

---

## Components (all that must exist)

```text
AgentRuntime
ModeManager
ExplorationRunner
Planner
PlanExecutor
AgentLoop (execution engine)
Dispatcher
Validator
ActionGenerator (LLM interface)
ObservationBuilder
ToolRegistry
ToolHandlers (search, open_file, edit, shell, etc.)
StateManager
TraceFormatter
TraceEmitter (Langfuse / UI)
Replanner (failure recovery)
ContextManager (exploration memory + selection)
```

**ExplorationEngineV2** (Phase 12.5 — **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`**): optional implementation behind **`ExplorationRunner`**; must still emit **`SCHEMAS.md` Schema 4 `ExplorationResult`** only.

---

## Schemas (all required)

### Core state

```text
AgentState
```

---

### Exploration

```text
ExplorationResult
ExplorationItem
```

**Phase 12.6.E additive extensions (contract-safe)**

```text
ExplorationItem adds:
- snippet (bounded excerpt; capped; not full file)
- read_source ("symbol" | "line" | "head")  # how snippet was obtained (system-owned)

ExplorationResult.metadata adds:
- source_summary {symbol,line,head} counts (structural only)
```

**Phase 12.5 — internal only (not planner-facing):** `QueryIntent`, `ExplorationCandidate`, `ExplorationDecision`, `ExplorationState` — see **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`**; amend **`SUPPORTING_SCHEMAS.md`** when locked. **`ExplorationEngineV2`** implements the staged loop; **`ExplorationRunner`** remains the integration surface and may delegate.

---

### Planning

```text
PlanDocument
PlanStep
PlanMetadata
CompletionCriteria
RiskItem
```

---

### Execution

```text
ExecutionStep
ExecutionResult
StepResult
RetryState
```

---

### Tooling

```text
ToolDefinition
ToolCall
ToolResult
ToolError
```

---

### LLM interface

```text
ActionRequest
ActionResponse
Message
```

---

### Context / memory

```text
ContextWindow
ContextItem
SourceReference
```

---

### Tracing

```text
Trace
TraceStep
TraceSpan
TraceMetadata
```

---

### Control / orchestration

```text
Mode
ExecutionPolicy
FailurePolicy
ReplanRequest
ReplanResult
```

---

### Validation

```text
PlanValidator (single implementation surface — see VALIDATION_REGISTRY.md)
ValidationResult
ActionValidation
PlanValidation
```

---

### Output

```text
FinalOutput
PlanOutput
ExecutionSummary
```

---

## Optional (strongly recommended)

```text
DiffPatch
FileChange
TestResult
MemoryEntry
```

**`MemoryEntry` (Phase 16):** distilled episodic/semantic store for **Planner** input — distinct from **`ContextItem`** (retrieval). See **`SUPPORTING_SCHEMAS.md`** §10a.

---

## Final note

This is the **contract layer** — must be frozen before coding.

---

## Next step

Say: **“define PlanDocument schema”** — lock schemas one-by-one (strict, production-grade).
