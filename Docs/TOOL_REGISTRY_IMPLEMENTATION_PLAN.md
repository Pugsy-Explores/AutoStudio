# Tool Registry Implementation Plan (As Built)

**Goal:** Minimal central tool registry as single source of truth for ReAct tools, with zero behavior change.

---

## 1. Implemented Architecture

### 1.1 `agent/tools/react_registry.py`

- `ToolDefinition` dataclass: `name`, `description`, `required_args`, `handler`
- `register_tool(tool: ToolDefinition)`
- `get_tool_by_name(name: str) -> ToolDefinition | None`
- `get_all_tools() -> list[ToolDefinition]`
- `initialize_tool_registry()` for explicit one-time setup
- `clear_tool_registry()` for tests

No lazy initialization. No `internal_action` field.

### 1.2 `agent/tools/react_tools.py`

- `register_all_tools()` registers canonical ReAct tools
- Handlers use signature: `handler(args: dict, state) -> dict`
- Registered tools:
  - `search`
  - `open_file`
  - `edit`
  - `run_tests`
  - `finish` (`handler=None`; validated but not dispatched)

### 1.3 `agent/execution/step_dispatcher.py`

- `_dispatch_react()` now:
  1. maps internal action (`SEARCH/READ/EDIT/RUN_TEST`) to registry name
  2. extracts args from `_react_args` (with compatibility fallback)
  3. calls `tool.handler(args, state)`
- Legacy `dispatch()` path remains unchanged.

### 1.4 `agent/execution/react_schema.py`

- `ALLOWED_ACTIONS` is derived from registry (`get_all_tools()`) via proxy
- `validate_action()` reads required args from registry
- Validation checks:
  - unknown action
  - missing required fields
  - `None` required values
  - empty-string required values (`strip()`)
  - unexpected extra fields

### 1.5 Runtime entrypoint initialization

- `initialize_tool_registry()` is called once in `agent/orchestrator/agent_controller.py` at runtime start.

---

## 2. Constraints Confirmed

- No observation formatting in `ToolDefinition`
- No execution loop logic changes beyond dispatch/validation integration
- Legacy dispatch path untouched
- Deterministic explicit initialization (no import side effects)
- Zero intended behavior change

---

## 3. Verification

- `tests/test_react_schema.py` passes
- `tests/test_execution_loop.py` passes
- No lints on edited files
