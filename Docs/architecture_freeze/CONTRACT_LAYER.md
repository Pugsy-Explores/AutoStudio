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
```

---

## Final note

This is the **contract layer** — must be frozen before coding.

---

## Next step

Say: **“define PlanDocument schema”** — lock schemas one-by-one (strict, production-grade).
