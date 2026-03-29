# Agent v2 — tool policy layer (planning)

Staff-engineer design: restrict planner-visible tools during planning **without** changing PlanExecutor, dispatcher architecture, or PlanValidator’s structural role.

**Related:** `Docs/agent_v2_phase1_tool_contract_audit.md` (what exists); this doc adds **which** of those tools may appear in a given planner **mode**.

---

## Step 1 — Audit: where to enforce?

| Location | Role | Policy here? |
|----------|------|----------------|
| **PlannerV2** (`_build_plan_from_engine_json`) | Parse `PlannerEngineOutput`, normalize tool, synthesize steps | **Yes — single enforcement point** |
| `planner_decision_mapper` | Maps `PlanDocument` → `PlannerDecision` after plan exists | No (too late; plan already committed) |
| `PlanValidator` | Step graph, actions vs SCHEMAS | No duplicate tool *intent* rules (keeps structural validation only) |
| `PlanExecutor` | Runs `PlanStep.action` | Unchanged — user requirement |

**Best single place:** **PlannerV2**, immediately after `PlannerEngineOutput` parse + `_normalize_engine_tool`, **before and after** `_apply_explore_cap_override`, then `_validate_engine_tool_pairing`. The second `apply_tool_policy` keeps policy authoritative if the override ever changes tools.

**Duplication / drift if done elsewhere too:** repeating allowlists in validator + executor causes drift; shell rules would belong in three places.

---

## Step 2 — `ToolPolicy` schema

Defined in `agent_v2/runtime/tool_policy.py`:

```python
@dataclass(frozen=True)
class ToolPolicy:
    mode: Literal["plan", "act"]
    allowed_act_tools: frozenset[str]
    shell_first_token_allowlist: Optional[frozenset[str]]
```

- **`allowed_act_tools`:** `engine.tool` values permitted when `decision == "act"`.
- **`shell_first_token_allowlist`:** if set, `run_shell` commands must parse to a first argv0 basename in this set; if `None`, no token check (phase-1 act mode).

---

## Step 3 — Locked policy: **plan** mode

**Allowed act tools:** `search_code`, `open_file`, `run_tests`, `run_shell` (with shell constraints below).

**Disallowed act tools:** `edit`, anything not in the allowlist, unknown tools (unknowns also fail Pydantic when not in `PlannerPlannerTool`).

**Shell (plan mode only):** first command token basename must be one of **`ls`, `rg`, `grep`, `cat`**. Rejects e.g. `rm`, `mv`, `chmod`, and any other argv0.

Constant: `PLAN_MODE_TOOL_POLICY`.

**Act mode (full phase-1 act surface):** `ACT_MODE_TOOL_POLICY` — includes `edit`; `shell_first_token_allowlist=None`.

---

## Step 4–5 — Implementation & shell logic

| File | Change |
|------|--------|
| `agent_v2/runtime/tool_policy.py` | `ToolPolicy`, `ToolPolicyViolationError`, `apply_tool_policy()`, shell helpers, `SHELL_FORBIDDEN_SUBSTRINGS` |
| `agent_v2/planner/planner_v2.py` | `tool_policy` ctor; `apply_tool_policy` ×2 around explore override; prompts; repair **skips** `ToolPolicyViolationError`; telemetry |

Shell (plan-mode `run_shell`): reject if command contains any of **`&&`, `;`, `|`, `` ` ``** (substring check). Then: first token basename ∈ allowlist (`ls`, `rg`, `grep`, `cat`).

---

## Step 6 — Prompt alignment

Injected via **`_planner_tool_catalog_block()`** in both `_build_exploration_prompt` and `_build_replan_prompt`, replacing the old static “ALLOWED_TOOLS” block:

- **Plan mode:** `_PLAN_MODE_TOOL_CATALOG` (explicit allow/deny + shell rule).
- **Act mode:** `_ACT_MODE_TOOL_CATALOG` (full phase-1 list including `edit`).

`OUTPUT FORMAT` tool/action lines are built by **`_engine_json_output_format()`** so plan mode omits `edit` from the schema snippet.

---

## Step 7 — Validation flow (decision-first path)

```
Planner JSON
→ parse (Pydantic)
→ normalize_engine_tool
→ apply_tool_policy
→ apply_explore_cap_override
→ apply_tool_policy          # again — post-override authority
→ _validate_engine_tool_pairing / pairing / task_mode
→ synthesize steps
→ PlanValidator.validate_plan (structural)
→ execute (unchanged)
```

Policy failures raise **`ToolPolicyViolationError`** (subclass of `PlanValidationError`). The planner **does not** run the LLM tool-repair loop on these — fail immediately. Other tool/pairing errors may still get one repair attempt.

Success path logs **`tool_policy_mode`** on `planner_telemetry`. Violations log **`tool_policy_violation`** with nested `tool`, `reason`, `command` plus `tool_policy_mode`.

**Legacy multi-step JSON** (`_build_plan_legacy_steps`) does not run this policy (unchanged contract).

---

## Step 8 — Tests

| # | Case | Expected |
|---|------|----------|
| 1 | Plan mode, `edit` | `ToolPolicyViolationError` (no repair) |
| 2 | Plan mode, `run_shell` + `rm …` or `ls && rm` | `ToolPolicyViolationError` |
| 3 | Plan mode, `search_code` | Pass |
| 4 | Plan mode, `run_tests` | Pass |
| 5 | Act mode, `edit` | Pass |

Implemented in `tests/test_tool_policy.py` (unit + `PlannerV2` integration).

---

## Defaults

- **`PlannerV2(..., tool_policy=None)`** → **`PLAN_MODE_TOOL_POLICY`**.
- Pass **`tool_policy=ACT_MODE_TOOL_POLICY`** when the runtime should allow edits and unrestricted shell at the planner (future “act” phases / tests).
