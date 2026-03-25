# Tool System RCA — Centralization and Extensibility

**Date:** 2026-03-23  
**Goal:** Root-cause analysis of the current tool system and recommendations to centralize it, inspired by Claude, Devin, and Cursor.

---

## 1. Current State — Root Cause Analysis

### 1.1 Tool Definitions Are Scattered

| Location | Content | Role |
|----------|---------|------|
| `agent/core/actions.py` | `Action` enum: SEARCH, READ, EDIT, EXPLAIN, INFRA, RUN_TEST, WRITE_ARTIFACT | Planner/guardrail vocabulary |
| `agent/execution/react_schema.py` | `ALLOWED_ACTIONS`: search, open_file, edit, run_tests, finish | ReAct validation |
| `agent/execution/tool_graph.py` | `ToolGraph.GRAPH`: retrieve_graph, read_file, build_context, … | Legacy mode: allowed transitions |
| `agent/execution/tool_graph_router.py` | `ACTION_TO_PREFERRED_TOOL` | Action → tool name mapping |
| `agent/orchestrator/execution_loop.py` | `_REACT_TO_STEP`: search→SEARCH, open_file→READ, … | ReAct action → internal step |
| `agent/execution/step_dispatcher.py` | `_dispatch_react()`, `dispatch()` | Hardcoded if/elif chains per action |
| `agent/prompt_versions/react_action/v1.yaml` | Tool schema in YAML | LLM prompt |

**Problem:** Adding a new tool (e.g. `run_shell` or `web_search`) requires edits in 6+ files. No single source of truth.

### 1.2 Dual Execution Paths

- **ReAct mode:** `_dispatch_react()` → direct tool execution; no policy_engine, no tool graph.
- **Legacy mode:** `dispatch()` → ToolGraph → tool_graph_router → policy_engine → `_search_fn`, `_edit_fn`, etc.

Each path has its own action vocabulary, validation, and observation formatting. Logic duplication and divergence risk.

### 1.3 No Declarative Tool Contract

- Tools live in `agent/tools/*` with varying signatures: `search_candidates(query, state)`, `read_file(path)`, `run_command(cmd)`.
- step_dispatcher wires them manually. No `Tool(name, description, input_schema, handler)` abstraction.
- Observation formatting (`_build_react_observation`) is a giant if/elif per action in execution_loop.py.

### 1.4 Prompt and Schema Drift

- ReAct schema in `react_schema.py` and in `react_action/v1.yaml` must stay in sync manually.
- No code generation from a single definition to prompt text, validation, or dispatch.

---

## 2. Industry Patterns (Claude, Devin, Cursor)

### 2.1 Claude (Anthropic)

**Sources:** [Anthropic tool use docs](https://docs.anthropic.com/en/docs/build-with-claude/tool-use/implement-tool-use), [Advanced tool use blog](https://www.anthropic.com/engineering/advanced-tool-use)

| Pattern | Description |
|---------|-------------|
| **Declarative schema** | Each tool: `name`, `description`, `input_schema` (JSON Schema), optional `input_examples` |
| **Tool Search Tool** | On-demand discovery; tools with `defer_loading: true` are not loaded until needed. Reduces context tokens. |
| **Programmatic Tool Calling** | Model writes code that invokes tools; orchestration (loops, conditionals) in code, not NL round-trips |
| **Tool Use Examples** | Standard for demonstrating correct usage beyond schema |
| **MCP integration** | External tools via Model Context Protocol; `tool` decorator, `createSdkMcpServer` |

**Takeaway:** Central tool definitions with JSON Schema; rich descriptions; optional deferred loading and code-based orchestration.

### 2.2 Devin (Cognition Labs)

**Sources:** [Introducing Devin](https://www.cognition-labs.com/blog/introducing-devin), [Devin 2.0](https://www.cognition-labs.com/blog/devin-2)

| Pattern | Description |
|---------|-------------|
| **Core tools** | Shell, Code Editor, Browser — same tools a human developer uses |
| **Model + Tools + Memory + Planning** | Four pillars; tools are first-class |
| **Devin Search / Wiki / Review** | Agentic tools built on top (codebase search, architecture docs, code review) |
| **Long-term memory** | Context across sessions |

**Takeaway:** Minimal, high-level tools (Shell, Editor, Browser); specialized tools are layered on top. Clear separation of tool interface from orchestration.

### 2.3 Cursor

**Sources:** [Cursor agent overview](https://cursor.com/docs/agent/overview), [Subagents](https://cursor.com/docs/subagents.md), [Plugins](https://cursor.com/docs/plugins.md)

| Pattern | Description |
|---------|-------------|
| **Instructions + Tools + Model** | Agent = instructions + tools + model selection |
| **Tools by category** | Semantic search, file ops, terminal, web search, browser, image gen, rules, clarifying questions |
| **Subagents** | Explore (codebase), Bash (commands), Browser — each with own context, prompts, tool access |
| **Plugins** | Rules, skills, agents, MCP, automation; distributable packages |
| **Skills** | Single-purpose tasks without separate context window |

**Takeaway:** Tools and agents are composable. Subagents scope tools per task type. Plugins extend the system without changing core code.

---

## 3. Recommendations — Centralized Tool System (Implemented)

### 3.1 Single Tool Registry (Declarative)

Introduce a **central tool registry** as the single source of truth:

```python
# agent/tools/react_registry.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class ToolDefinition:
    name: str
    description: str
    required_args: list[str]
    handler: Callable[[dict, AgentState], dict] | None

_by_name: dict[str, ToolDefinition] = {}

def register_tool(tool: ToolDefinition) -> None:
    _by_name[tool.name] = tool

def get_tool_by_name(name: str) -> ToolDefinition | None:
    return _by_name.get(name)

def get_all_tools() -> list[ToolDefinition]:
    return list(_by_name.values())

def initialize_tool_registry() -> None:
    from agent.tools.react_tools import register_all_tools
    register_all_tools()
```

- **Source of truth:** All tools are registered in one place via `agent/tools/react_tools.py`.
- **Generation:** Build `ALLOWED_ACTIONS`, prompt schema, and `_dispatch_react` routing from the registry.

### 3.2 Tool Registration Pattern

Each tool module registers itself:

```python
# agent/tools/react_tools.py
from agent.tools.react_registry import register_tool, ToolDefinition

def _search_handler(args: dict, state: AgentState) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"success": False, "output": {}, "error": "query required"}
    raw = _search_react(query, state)  # existing logic
    return {"success": True, "output": raw, "error": None}

register_tool(ToolDefinition(
    name="search",
    description="Search the codebase for relevant files and snippets. Use query with 2+ meaningful terms.",
    required_args=["query"],
    handler=_search_handler,
))
```

**Adding a new tool:** Add a handler and `register_tool(...)` in `agent/tools/react_tools.py`. Registry drives validation and dispatch.

### 3.3 Derived Artifacts From Registry

| Artifact | How |
|----------|-----|
| `react_schema.ALLOWED_ACTIONS` | `{t.name: t.required_args for t in get_all_tools()}` |
| `validate_action()` | Look up tool by name, check required args and reject `None` / empty-string values |
| `_dispatch_react()` | Map internal step action to registry tool name, then call `tool.handler(args, state)` |
| Runtime initialization | `initialize_tool_registry()` at `run_controller()` startup |
| Prompt schema in `react_action/v1.yaml` | Manually aligned with registry tool names/args |

### 3.4 Optional: JSON Schema (Claude-style, Not Implemented)

Extend `ToolDefinition` with `input_schema` (JSON Schema dict) for richer validation and LLM hints:

```python
input_schema = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "description": "Search terms"}
    },
    "required": ["query"]
}
```

Use for validation and for building Claude-compatible tool definitions if integrating with MCP or external APIs.

### 3.5 Optional: Deferred Loading (Claude Tool Search, Not Implemented)

If the tool set grows large, add `defer_loading: bool` to `ToolDefinition`. Core tools (search, open_file, edit, run_tests, finish) stay loaded; others are discovered on demand. Requires a "tool search" meta-tool.

### 3.6 Preserve ToolGraph as Policy Layer

- ToolGraph remains optional for legacy/planner mode: restricts *which* tools are allowed from each node.
- Registry defines *what* tools exist; ToolGraph defines *when* they can be used.
- ReAct mode can bypass ToolGraph (as today) or use it for optional restrictions.

---

## 4. Migration Path (Status)

| Phase | Scope | Risk |
|-------|-------|------|
| **1. Registry + existing tools** | Create `agent/tools/react_registry.py`, register current 5 ReAct tools | Completed |
| **2. Refactor dispatch** | `_dispatch_react` uses registry lookup instead of if/elif | Completed |
| **3. Refactor validation** | `validate_action` reads from registry | Completed |
| **4. Explicit init** | Remove lazy init, add `initialize_tool_registry()` at runtime entry | Completed |
| **5. Tool module split** | Move ReAct tool handlers to `agent/tools/react_tools.py` | Completed |
| **6. Add new tools** | Future extensions via `react_tools.py` + registry | Open |

---

## 5. Summary

| Problem | Recommendation |
|---------|----------------|
| Definitions in 6+ files | Single `ToolDefinition` registry |
| Adding a tool = many edits | One `register_tool()` call |
| Schema/prompt drift | Generate validation and prompt from registry |
| Dual ReAct/Legacy paths | Registry for both; Legacy keeps ToolGraph + policy_engine |
| No standard tool interface | `handler(args, state) -> dict` |

**Inspiration:** Claude’s declarative schema + Tool Search; Devin’s minimal core tools; Cursor’s composable tools and subagents. The registry pattern aligns with all three while fitting AutoStudio’s existing dispatcher and execution loop.
