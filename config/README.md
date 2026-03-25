# Config Module (`config/`)

Centralized **env-backed** settings: limits, feature flags, and service endpoints for retrieval, editing, and agent execution.

## Responsibilities

- **Single source of truth** for tunables (timeouts, retries, budgets).
- **Safety limits** — patch/file sizes, context caps, exploration step counts (some mirrored in `agent_v2/config.py`).
- **Runtime behavior flags** — notably **`agent_runtime.REACT_MODE`** (default **on**): changes policy/dispatch behavior when **`state.context["react_mode"]`** is true (set by `agent_v2` `ModeManager` on `act`). This does **not** mean “ReAct is the main orchestration loop”; control flow is **`agent_v2` ModeManager**.

## Key files

| File | Role |
|------|------|
| `agent_runtime.py` | Edit/test loop, **`REACT_MODE`**, sandbox, trajectory |
| `agent_config.py` | Attempt and task runtime limits |
| `retrieval_config.py` | Retrieval daemon, caps, reranker |
| `editing_config.py` | Patch budgets |
| `router_config.py` | Instruction router |
| `startup.py` | `ensure_services_ready()` — optional retrieval daemon, model checks |
| `config_validator.py` | `validate_config()` |

## Usage

```python
from config.agent_runtime import REACT_MODE
from config.retrieval_config import MAX_CONTEXT_SNIPPETS
```

## Invariants

- Budgets are enforced downstream; do not bypass by reading env without the same bounds in execution code.
