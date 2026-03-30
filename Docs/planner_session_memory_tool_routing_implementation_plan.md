# Implementation plan: session memory + tool awareness (PlannerV2)

**Source of truth (architecture + rules):** [planner_session_memory_tool_routing_plan.md](./planner_session_memory_tool_routing_plan.md)

**Scope:** Extend planner + orchestration only. **Out of scope:** `ExplorationEngineV2`, `PlanExecutor`, runtime loop redesign, new frameworks.

---

## Locked engineering decisions (pre-code)

These override earlier draft wording in this document where it conflicted.

1. **Explore-cap override — single owner:** **All** `explore_streak` / explore-cap logic lives in **`PlannerV2._apply_explore_cap_override`** only. **Delete** any override helpers from **`session_memory.py`** and **`planner_task_runtime.py`**. Call order: immediately **after** parsing engine JSON, **before** `PlanValidator` + step synthesis (see §7).
2. **Explore-cap behavior:** If `explore_streak >= 3` and the model returns `decision == "explore"`, **always** coerce to **`act`** with **`tool = search_code`**; `engine.query` / search input = **`intent_anchor.target` or else `last_user_instruction` or else `instruction`** (first non-empty after strip). **Never** auto-`stop` on this path.
3. **Intent anchor:** Initialized **only** from **user text** (simple deterministic heuristic, **no LLM**, **not** from exploration). See architecture doc §2.2.
4. **Invalid `tool`:** **No** silent fallback to `search_code`. **One** LLM retry with repair prompt; if still invalid → **log + raise**.
5. **Orchestration tick order:** Fixed 7-step sequence (§3.4) — no scattered updates.
6. **Prompt section order:** Rules → tools → exploration → **SESSION MEMORY** → **user instruction** → output schema (§6).
7. **Tool rules in prompt:** Include hard lines: “You MUST follow TOOL SELECTION RULES…” (architecture §3.2).
8. **Telemetry (cheap):** Log `decision`, `tool`, `explore_streak`, `override_triggered` (bool) on each planner completion.

---

## 1. Implementation phases

| Phase | Goal | Files touched (primary) | Expected outcome |
|-------|------|-------------------------|------------------|
| **1 — Session memory foundation** | Typed session state, pruning, prompt serialization | New `session_memory.py`; `planner_plan_context.py`; `planner_task_runtime.py` (and/or `exploration_planning_input.py`); optional `AgentState` field if memory lives on state | `SessionMemory` exists; `PlannerPlanContext.session: Optional[SessionMemory]`; memory can be attached with `None` default (no behavior change) |
| **2 — Tool schema + planner output** | Closed `tool` enum; `step.metadata`; mapping to `PlanStep` | `plan.py`; `planner_v2.py` (`_build_plan_from_engine_json`, synthesis helpers); `plan_validator.py` | Pydantic validates `tool` (phase 1 without `search_web`); optional `metadata` on step spec; synthesis unchanged in step count (0–2) |
| **3 — Prompt integration** | Inject memory JSON + tool catalog + rules | `planner_v2.py` (`_build_exploration_prompt`, `_build_replan_prompt`) | Prompt sections per design; bounded size via `SessionMemory.to_prompt_block()` |
| **4 — Runtime safeguards** | `explore_streak`; deterministic explore cap | **`planner_v2.py` only** (`_apply_explore_cap_override`); orchestration only **updates** memory counters | When `explore_streak >= 3` and model emits `explore`, planner coerces to **`act` + `search_code`** (never auto-`stop` here) |
| **5 — Backward compatibility + rollout** | Flags + tests | `agent_v2/config` or constructor flags on `PlannerV2`; tests | Phase A/B/C behavior; legacy JSON without `tool` still parses |

**Additional files (as needed, still in scope):** `agent_v2/runtime/replanner.py` (pass `session` into `PlannerPlanContext` on replan), `tests/test_planner_v2.py`, `tests/test_planner_decision_mapper.py`, new `tests/test_session_memory.py`.

---

## 2. File-level changes (specific)

### 2.1 `agent_v2/runtime/session_memory.py` (new)

| Action | Detail |
|--------|--------|
| **ADD** | `IntentAnchor` (Pydantic): `goal`, `target`, `entity: str` (entity may be `""`). |
| **ADD** | `CompressedStep`: `t`, `tool`, `summary` with length caps. |
| **ADD** | `SessionMemory`: all fields from design §2.1; **`intent_anchor` required** (default empty strings if needed for construction). |
| **ADD** | `SessionMemory.for_prompt() -> str` or `to_prompt_block()`: pruned JSON per §2.5 (~2.5k char cap). |
| **ADD** | `SessionMemory.record_user_turn(...)`, `record_planner_output(decision, tool, ...)`, `record_executor_event(...)` — small pure methods; orchestration calls them per §3.4. Streak is **derived** in orchestration from effective `decision` when updating memory (increment on `explore`, else reset), not via ad-hoc helpers in this module. |
| **DELETE / omit** | **No** `apply_explore_cap_override` (or any explore-cap mutation) in this file — **PlannerV2** owns override logic. |
| **MODIFY** | N/A (new file). |
| **KEEP** | No I/O, no DB, no globals. |

### 2.2 `agent_v2/schemas/planner_plan_context.py`

| Action | Detail |
|--------|--------|
| **ADD** | `session: Optional[SessionMemory] = None` on `PlannerPlanContext`. |
| **MODIFY** | `_one_primary_mode` validator: unchanged; `session` does not affect mode. |
| **KEEP** | Existing exploration / insufficiency / replan rules. |

### 2.3 `agent_v2/schemas/plan.py`

| Action | Detail |
|--------|--------|
| **ADD** | `PlannerPlannerTool` type alias or nested `Literal["explore","open_file","search_code","run_shell","analyze_code","none"]` — **omit `search_web` in phase 1**. |
| **ADD** | `tool` on `PlannerEngineOutput` with default `"none"` and `@field_validator` to coerce missing → `"none"`. |
| **ADD** | `metadata: dict = Field(default_factory=dict)` on `PlannerEngineStepSpec` with shallow size cap (e.g. max keys, max str values length). |
| **MODIFY** | `_coerce_step_legacy_string` unchanged; optionally coerce `metadata` from absent → `{}`. |
| **KEEP** | `decision`, `reason`, `query`, `step`, legacy controller fields on `PlanDocument`. |

### 2.4 `agent_v2/planner/planner_v2.py`

| Action | Detail |
|--------|--------|
| **ADD** | Module-level or class-level **constants** for `ALLOWED_TOOLS` bullet text, `TOOL_SELECTION_RULES`, one line **“search_web: DISABLED (do not output)”** in phase 1, **MEMORY vs EXPLORATION** precedence line. |
| **ADD** | `_format_session_memory_block(session: SessionMemory | None) -> str` — empty string if `None`. |
| **ADD** | `_infer_tool_from_engine(engine: PlannerEngineOutput) -> ...` for Phase A only when `tool` missing/legacy (still subject to valid enum). |
| **ADD** | `_validate_engine_tool_pairing(engine, task_mode)` — e.g. `explore` ⇒ `tool==explore` and non-empty `query`; `act` ⇒ `tool in {open_file, search_code, run_shell, analyze_code}`; `stop`/`replan` ⇒ `tool in {none}`. |
| **ADD** | **`_apply_explore_cap_override(engine: PlannerEngineOutput, memory: SessionMemory, instruction: str, ...) -> tuple[PlannerEngineOutput, bool]`** — returns `(possibly_mutated_engine, override_triggered)`. **Only** place that mutates engine for streak cap. Called **right after** JSON → `PlannerEngineOutput`, **before** pairing validation / synthesis / `PlanValidator`. |
| **MODIFY** | `_build_exploration_prompt` / `_build_replan_prompt`: section order **locked** (§6): rules → tools → exploration → **SESSION MEMORY** → user instruction → output schema. |
| **MODIFY** | `_build_plan_from_engine_json` (or equivalent): parse → **`_apply_explore_cap_override`** → tool validation / **one retry** on invalid tool (see §4) → pairing → synthesis → return `PlanDocument`. **No** explore-cap logic in runtime. |
| **MODIFY** | `_step_spec_to_plan_step` / `_resolve_act_step_spec`: merge `metadata.path` / `command` / `snippet` into `PlanStep.inputs` when `input` empty or as supplement — **must still produce valid `PlanStep` for existing executor**. |
| **KEEP** | `PlanExecutor` contract: still ≤2 steps; `_sync_controller_from_engine`; legacy path without `decision` field. |

### 2.5 `agent_v2/validation/plan_validator.py`

| Action | Detail |
|--------|--------|
| **ADD** | In `_validate_decision_engine_plan` (or helper): optional checks that `engine.tool` aligns with `engine.decision` when strict mode on. |
| **KEEP** | Existing dependency/finish rules. |

### 2.6 `agent_v2/runtime/planner_decision_mapper.py`

| Action | Detail |
|--------|--------|
| **ADD** (optional Phase B) | `PlannerDecision.tool: Optional[str] = None` in `planner_decision.py` + map from `plan_doc.engine.tool` when `engine.decision == "act"` or always mirror for telemetry. |
| **MODIFY** | `_decision_from_engine`: still branch on `decision` only; **no change required** for minimal ship. |
| **KEEP** | Precedence order (engine first). |

### 2.7 `agent_v2/runtime/planner_task_runtime.py`

| Action | Detail |
|--------|--------|
| **ADD** | Hold or accept `SessionMemory` on the object that runs the outer loop (e.g. new attribute `self._session_memory` initialized once per user session, or passed via `state` if already canonical). |
| **MODIFY** | Follow **strict tick order** (§3.4): user-input memory updates **before** planner; post-planner memory updates **after** validated plan returned from `PlannerV2.plan`; executor updates **after** step. Attach `context.session = memory` on `PlannerPlanContext`. |
| **MODIFY** | After `PlanDocument` returned from planner: update memory (`last_decision`, `last_tool`, `explore_streak` per effective engine output). **Do not** patch `plan.engine` here for explore cap. |
| **MODIFY** | After executor step completes: `memory.record_executor_event(...)`. |
| **DELETE / omit** | **No** explore-cap override in this file. |
| **KEEP** | Explore → plan → execute ordering; no new loop types. |

### 2.8 `agent_v2/runtime/exploration_planning_input.py`

| Action | Detail |
|--------|--------|
| **MODIFY** | `exploration_to_planner_context` (or `call_planner_with_context`): accept optional `session` and set on `PlannerPlanContext`. |
| **KEEP** | Exploration conversion logic. |

### 2.9 `agent_v2/runtime/replanner.py`

| Action | Detail |
|--------|--------|
| **MODIFY** | When building `PlannerPlanContext(replan=...)`, pass through same `session` reference so replan prompts see memory. |
| **KEEP** | Replanner semantics. |

---

## 3. Session memory — implementation detail

### 3.1 Creation

- **Where:** Start of a **session** or first planner tick — e.g. `PlannerTaskRuntime` constructor or first `run` call: `SessionMemory(session_id=..., intent_anchor=IntentAnchor(goal="", target="", entity=""), current_task="")`.

### 3.2 Intent anchor initialization (locked)

- **Only from user instruction** (orchestration step 1 in §3.4): deterministic heuristic on the user string — e.g. keyword / pattern table for `goal` (“fix”, “explain”, …), remainder or noun phrase for `target`, optional quoted symbol or `CamelCase` token for `entity`. **No LLM. No exploration input.**
- **Example:** `"Fix auth bug in middleware"` → `goal="fix bug"`, `target="auth middleware"`, `entity=""`.
- **Vague follow-ups:** Keep previous anchor until a **new** user message that matches “new goal” heuristics (implementation-defined, still **user-text-only**).

### 3.3 Prompt injection (placement locked)

- **SESSION MEMORY** block appears **immediately before** the **user instruction** block in the assembled user message (after exploration facts). Same for replan template.
- Body text reminder: read-only for grounding; if conflicts with exploration → trust exploration.

```text
SESSION MEMORY (read-only; if conflicts with exploration above, trust exploration.)
<pruned JSON from SessionMemory.to_prompt_block()>
```

- If `session is None`, omit subsection (Phase A).

### 3.4 Orchestration tick order (locked — prevents state bugs)

Execute **in this order** every turn (matches product spec):

1. **Receive user input** → update memory (`last_user_instruction`, `current_task`, **`intent_anchor`** via user-only heuristic, etc.).
2. **Call planner** — `PlannerV2.plan(...)` (exploration already computed upstream; unchanged).
3. **Apply explore-cap override (if needed)** — **inside** `PlannerV2.plan`, immediately after parsing engine JSON: **`_apply_explore_cap_override`** (not in runtime, not in `session_memory.py`).
4. **Validate plan** — **inside** `PlannerV2.plan`, after full `PlanDocument` built from (possibly overridden) engine: `PlanValidator.validate_plan`.
5. **Return plan to runtime** → update memory: `last_decision`, `last_tool`, **`explore_streak`** (if effective `decision == "explore"` then increment, else reset to 0).
6. **Execute** — `PlanExecutor` unchanged.
7. **After executor** → append `CompressedStep`; update `active_file` / `active_symbols` when known.

**Invalid-tool retry** (§4) occurs inside step 2–4 before returning (one repair `generate_fn` call, then raise if still bad).

---

## 4. Tool schema — implementation detail

- **Closed enum:** Python `Literal[...]` on `PlannerEngineOutput.tool` matching prompt list; phase 1 **exclude** `search_web`.
- **Validation:** Pydantic parse rejects invalid strings; optional second-line validation in `_validate_engine_tool_pairing`.
- **Mapping to `PlanStep` (existing actions only):**

| `tool` | `PlanStep.action` | Primary `inputs` source |
|--------|-------------------|-------------------------|
| `explore` | N/A | `engine.query` |
| `open_file` | `open_file` | `step.input` or `metadata["path"]` |
| `search_code` | `search` | `step.input` or `metadata["query"]` |
| `run_shell` | `shell` | `step.input` or `metadata["command"]` |
| `analyze_code` | `analyze` | `step.input` or `metadata["snippet"]` |
| `none` | N/A | — |

- **Invalid / unknown tool (locked):** After Pydantic parse, if JSON used an invalid tool (should not happen with strict schema) or **post-parse pairing** detects inconsistency treat as failure path: **one** additional `generate_fn` call with a minimal repair prompt (“tool must be one of …”). If second parse still invalid → **`log + raise`** — **no** silent `search_code` fallback.

---

## 5. Planner output upgrade — implementation detail

- **Parsing:** Extend JSON extraction path to populate `tool` and `step.metadata`.
- **Validation:** `_validate_engine_pairing` extended for `tool`/`query`/`step` consistency; `task_mode` read-only still blocks `edit` in `step.action` as today.
- **Synthesis:** `_synthesize_steps_from_engine` unchanged in structure; `_step_spec_to_plan_step` reads `metadata` to fill `inputs` dict keys the executor already understands (match existing conventions in codebase).
- **Backward compatibility (Phase A only):** Missing `tool` → `_infer_tool_from_engine` **only** when flag allows; inferred value must still be a **valid** enum member. Phase B: missing/invalid → retry then raise (same as §4).
- **Tool / `step` mismatch:** Prefer **strict failure + retry** over silent normalize; if a cheap deterministic fix is kept (e.g. align `tool` to `step.action` when exactly one mapping applies), document it and log at **warning** — **never** hide invalid tools.

---

## 6. Prompt integration — checklist (order locked)

Build **one** string per template (exploration + replan) in this **exact** order:

1. **Rules:** role; STOP / SUFFICIENCY / MINIMALITY; task mode; **TRUTH PRECEDENCE** (memory vs exploration → always trust exploration).
2. **Tools:** ALLOWED_TOOLS (phase 1: no `search_web`); TOOL SELECTION RULES from design §3.2; **hard lines (verbatim):**  
   `You MUST follow TOOL SELECTION RULES.`  
   `Do NOT choose a different tool if a rule applies.`  
   WEB: single line “search_web is disabled; do not use.” (phase 1).
3. **Exploration** summary block (existing producer).
4. **SESSION MEMORY** — pruned JSON if `session` present (**immediately before** user instruction).
5. **User instruction** (latest).
6. **Output** JSON schema snippet (`decision`, `tool`, `reason`, `query`, `step` + optional `metadata`).

**Avoid duplication:** shared `_planner_tool_and_rules_block(phase: Literal["explore","replan"])` helper.

---

## 7. Runtime safeguards (`explore_streak`) — **PlannerV2-only override**

### 7.1 Streak semantics

- `explore_streak` on `SessionMemory` = consecutive **`explore`** decisions **committed** after each planner tick (step 5 in §3.4).
- **Update rule:** after returning from `PlannerV2.plan`, if **effective** `engine.decision == "explore"` then `streak += 1`, else `streak = 0`.

**Example:** Three explores in a row → `explore_streak == 3` before the fourth planner call. If the model emits a fourth `explore`, **`_apply_explore_cap_override`** runs inside `PlannerV2` and replaces it before validation/synthesis.

### 7.2 `_apply_explore_cap_override` (single source of truth)

**Location:** `agent_v2/planner/planner_v2.py` only.  
**When:** Immediately after raw `PlannerEngineOutput` is parsed from JSON, **before** tool pairing validation, synthesis, and `PlanValidator`.

**Logic (deterministic, locked):**

- If `session is None` or `explore_streak < 3` → no-op; return `(engine, override_triggered=False)`.
- If `explore_streak >= 3` **and** `engine.decision == "explore"`:
  - Set `decision = "act"`, `tool = "search_code"`.
  - Set `query` = first non-empty of: `strip(intent_anchor.target)`, `strip(last_user_instruction)`, `strip(instruction)` (pass these into `plan()` from context / args).
  - Build `step` = `{ action: "search", input: <same string as query> }` (plus `metadata` empty or consistent).
  - Set `reason` to a short fixed tag e.g. `explore_cap_override` for telemetry.
  - Return `(mutated_engine, override_triggered=True)`.

**Never** set `stop` on this path.

Synthesis + validation run on the **mutated** engine so `PlanDocument` stays self-consistent.

### 7.3 Telemetry

On each planner exit, emit structured log (or trace fields): `decision`, `tool`, `explore_streak` (value **before** this tick’s memory update), `override_triggered` (bool).

---

## 8. Edge cases — implementation mapping

| Case | Implementation |
|------|----------------|
| **1. Vague “do it”** | Prompt + `intent_anchor` in memory; orchestration keeps anchor across turns; planner instructions in SESSION MEMORY block. |
| **2. Memory vs exploration** | Single sentence in prompt; no code merge of conflicting facts — exploration text remains authoritative in the same message. |
| **3. Empty exploration** | Existing insufficiency paths; validator: `explore` requires non-empty `query` or fail + retry (existing pattern). |
| **4. Tool mismatch** | Prefer **retry + raise**; optional deterministic align only if explicitly documented + **warning** log — **no** silent `search_code` for invalid tool enum. |
| **5. Invalid tool JSON** | One repair generation; then **raise** (§4). |
| **6. Explore cap** | Handled only in `_apply_explore_cap_override`; runtime never patches engine. |

---

## 9. Migration / rollout

| Phase | Behavior |
|-------|----------|
| **A** | `session` optional; `tool` optional (infer, still valid enum); `search_web` absent; explore cap **on** when memory attached; override **only** in PlannerV2 |
| **B** | `tool` required in JSON (strict flag); validation errors surface to telemetry; mapper optionally exposes `tool` on `PlannerDecision` |
| **C** | Add `search_web` to `Literal` + prompt + policy flag; WEB USAGE RULE text |

Feature flags: `PLANNER_SESSION_MEMORY=0/1`, `PLANNER_STRICT_TOOL=0/1`, `PLANNER_WEB_TOOL=0/1`.

---

## 10. Test plan

| # | Case | Assert |
|---|------|--------|
| 1 | High-confidence exploration + clear file path in evidence | Model or forced `tool=open_file` or `act` without `explore`; prompt includes TOOL SELECTION RULES + hard “MUST follow” lines |
| 2 | Vague instruction + populated `intent_anchor` | Memory block immediately precedes user instruction; substring contains anchor JSON |
| 3 | Three explores then model returns explore again | `engine.decision == act`, `tool == search_code`, query from `intent_anchor.target` or `last_user_instruction`; `override_triggered == True`; validator passes |
| 4 | Correct tool selection | Table-driven: each `tool` maps to expected `PlanStep.action` |
| 5 | Legacy JSON without `tool` (Phase A) | Parses; inferred `tool` valid and matches `step.action` |
| 6 | `metadata.path` only | `PlanStep.inputs` receives path as executor expects |
| 7 | `session=None` | No regression in `tests/test_planner_v2.py` |
| 8 | Invalid tool twice | Second attempt raises; no silent fallback |
| 9 | Telemetry | Log/trace includes `decision`, `tool`, `explore_streak`, `override_triggered` |
| 10 | Prompt section order | Golden: exploration block before SESSION MEMORY before user instruction |

**Files:** extend `tests/test_planner_v2.py` (include `_apply_explore_cap_override` unit tests), add `tests/test_session_memory.py`; **no** explore-cap tests in `planner_task_runtime` (logic is planner-local).

---

## 11. Runtime flow changes

- **No new branches on loop type** — still: exploration → planner → decision → executor (existing).
- **New data:** `SessionMemory` on `PlannerPlanContext`; orchestration follows §3.4 ordering.
- **New behavior inside planner:** `_apply_explore_cap_override` mutates in-memory `PlannerEngineOutput` before `PlanDocument` is fully built — **not** in `planner_task_runtime.py`.

---

## 12. Risks and mitigation

| Risk | Mitigation |
|------|------------|
| Prompt token bloat | Hard cap in `to_prompt_block()`; shared compact tool rules constant |
| `metadata` confuses 7B | Keep prompt examples showing `input` primary; `metadata` optional |
| Double validation / drift | Single **`_apply_explore_cap_override`** inside PlannerV2; no duplicate override in runtime or `session_memory` |
| Silent tool fallback hides bugs | **Retry + raise** only (§4) |
| Replanner loses memory | Thread `session` through `Replanner` → `PlannerPlanContext` |
| Override breaks validator | Re-validate full `PlanDocument` after override |
| Streak off-by-one | Unit tests for streak 0,1,2,3,4 with explicit expected override |

---

## 13. Reference

- Architecture: [planner_session_memory_tool_routing_plan.md](./planner_session_memory_tool_routing_plan.md)
- Orchestration entry: `agent_v2/runtime/planner_task_runtime.py`
- Planner: `agent_v2/planner/planner_v2.py`
