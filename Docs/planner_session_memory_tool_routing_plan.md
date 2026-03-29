# PlannerV2 evolution: session memory + tool awareness

**Status:** design (pre-implementation)  
**Constraints:** No redesign of runtime loop. Do not modify `ExplorationEngineV2` or `PlanExecutor`. Refactor + extension only.

---

## Final architecture (after fixes)

```
Exploration → (truth)
Memory      → (context)
Planner     → (decision + routing)
Executor    → (action)
```

Precedence when signals conflict: **exploration is the latest source of truth**; session memory provides continuity and grounding for vague user text, not facts that override fresh retrieval.

---

## 1. Gap analysis

**Planner lacks**

- **Session memory:** No structured turn-local bundle in `PlannerV2.plan`; only `instruction`, exploration/replan, `task_mode`.
- **Explicit tool awareness:** `step.action` maps to executor primitives but there is no first-class planner **tool** id + closed catalog in the prompt.
- **Environment awareness:** No structured cwd/buffers/last-error unless buried in exploration text.

**Context gaps**

- **Vague instructions** (“do it”, “fix this”, “now check logs”): Need durable **intent anchor** plus `current_task` / `last_user_instruction` — high-level task string alone is insufficient.
- **Continuity:** Orchestration may hold state, but the planner contract does not yet accept a session snapshot.

**Schema limitations**

- **`step`:** Structured but may need optional **`metadata`** for tool-specific fields without breaking minimal JSON.
- **`search_web` / browser:** Not native `PlanStep.action` values; requires explicit policy and mapping (or phase-1 disable).

---

## 2. Session memory schema

**Purpose:** Small, structured, session-scoped (no vector DB), serialized as one JSON block in the user message.

### 2.1 Required fields

| Field | Type | Notes |
|--------|------|--------|
| `session_id` | string | Optional; log correlation |
| `current_task` | string | High-level goal (≤ ~200 chars) |
| **`intent_anchor`** | **object** | **Precise grounding (see below)** |
| `last_user_instruction` | string | Verbatim last user text (trimmed, capped) |
| `last_decision` | `act \| explore \| replan \| stop \| ""` | After each planner call |
| `last_tool` | string | Planner tool id or `""` |
| `active_file` | string \| null | Repo-relative path if known |
| `active_symbols` | string[] | Max 3–5 short names |
| `recent_steps` | `CompressedStep[]` | FIFO-compressed history |
| `explore_streak` | int | Consecutive `explore` decisions (loop guard) |
| `updated_at` | string | ISO optional |

### 2.2 Intent anchor (critical)

**Problem:** `current_task` + `last_user_instruction` alone lose what “it” refers to across turns (e.g. “fix auth bug” → “do it” → “now check logs”).

**Fix:** Add structured grounding separate from high-level task:

```json
"intent_anchor": {
  "goal": "fix bug",
  "target": "auth middleware",
  "entity": "KeyError"
}
```

| Subfield | Meaning |
|----------|---------|
| `goal` | What class of work (fix, explain, refactor, test, …) |
| `target` | Primary location or subsystem (file, module, area) |
| `entity` | Symbol, error type, ticket id, or other concrete handle (may be empty string) |

**Rules**

- **Planner:** For vague instructions, resolve using `intent_anchor` + `active_file` + last compressed step **before** choosing `tool` / `step`.

**Intent anchor initialization (locked — implementation)**

- **Source:** **User instruction only** (first substantive turn and when the user supplies a new concrete goal). **Do not** derive `intent_anchor` from exploration output — exploration is **facts**, not **intent**.
- **Mechanism:** Simple deterministic heuristic on the user string (no LLM), e.g. lightweight pattern / keyword extraction or a small rules table maintained in orchestration (`SessionMemory` update on user input).
- **Example:** User: `Fix auth bug in middleware` → `goal`: `"fix bug"`, `target`: `"auth middleware"`, `entity`: `""` (entity filled later only if the **user** names a symbol, error type, or id in text — not from retrieval).

**Subsequent turns:** On vague follow-ups (“do it”), **retain** prior `intent_anchor` until the user message clearly starts a new goal (then re-run the same user-only heuristic).

### 2.3 CompressedStep

```json
{ "t": "act|explore|stop|replan", "tool": "open_file", "summary": "opened foo.py" }
```

- `summary`: one line, ≤ 120 chars, written by **runtime** after planning/execution.

### 2.4 Example instance

```json
{
  "session_id": "run-8f3a",
  "current_task": "Fix authentication failure in API",
  "intent_anchor": {
    "goal": "fix bug",
    "target": "src/auth/middleware.py",
    "entity": "KeyError"
  },
  "last_user_instruction": "do it",
  "last_decision": "act",
  "last_tool": "open_file",
  "active_file": "src/auth/middleware.py",
  "active_symbols": ["validate_token"],
  "recent_steps": [
    { "t": "explore", "tool": "explore", "summary": "retrieval: auth + KeyError" },
    { "t": "act", "tool": "open_file", "summary": "open src/auth/middleware.py" }
  ],
  "explore_streak": 0
}
```

### 2.5 Size constraints

- **Target:** ~400–600 tokens injected (~1.5–2k chars). **Hard cap:** ~2.5k chars after pruning.
- **Pruning:** Always keep `intent_anchor`, `current_task`, `last_user_instruction`, `last_decision`, `last_tool`, `active_file`; cap `active_symbols` at 5; keep last **N = 4** `recent_steps`; truncate long strings with `…`.

### 2.6 Update policy

| Event | Behavior |
|--------|----------|
| New user message | Set `last_user_instruction`; refresh `current_task` / `intent_anchor` using **user-only** heuristic when the message contains a new concrete intent; on vague message, **retain** prior `intent_anchor` (**do not** overwrite anchor from exploration) |
| After planner returns | Set `last_decision`, `last_tool`; bump `explore_streak` if `explore` else reset to 0 |
| After executor completes | Append `CompressedStep`; update `active_file` / `active_symbols` when known |
| Replanner | Append summary; do not wipe memory |

Scalars overwrite; `recent_steps` append + FIFO trim.

### 2.7 Memory vs exploration (prompt rule — strengthened)

**If session memory conflicts with exploration findings → ALWAYS trust exploration** (latest source of truth for factual repo state). Memory is for **continuity and reference resolution**, not for overriding retrieval evidence.

### 2.8 Orchestration tick order (locked)

Memory updates and planner/validate/execute ordering must follow the seven-step sequence in **[planner_session_memory_tool_routing_implementation_plan.md](./planner_session_memory_tool_routing_implementation_plan.md) §3.4** (user input → planner call → override inside planner → validate → memory decision/streak → execute → compressed step). Prevents subtle state bugs from reordering updates.

---

## 3. Tool schema (planner-facing)

**Principle:** Closed enum in prompt + schema validation → no hallucinated tools.

### 3.1 Tools

| `tool` id | Purpose | Args |
|-----------|---------|------|
| `explore` | Deeper repo exploration (retrieval pipeline) | `query` |
| `open_file` | Read file | `path` |
| `search_code` | Search within repo | `query` |
| `run_shell` | Run command | `command` |
| `search_web` | External web lookup | `query` |
| `analyze_code` | Reason over snippet | `snippet` |

Uniform shape:

```json
{ "tool": "search_code", "args": { "query": "..." } }
```

Validate max lengths (e.g. query 500, snippet 2k, command 500).

### 3.2 Tool selection rules (prompt-level, required)

Include verbatim in planner prompt as **TOOL SELECTION RULES**:

```
TOOL SELECTION RULES (follow order):

- If a concrete file path or stable symbol to open is known → use open_file
- If searching within the repository (symbols, strings, patterns) → use search_code
- If repository context is missing or insufficient to act → use explore
- If the problem requires external knowledge not in the repo → use search_web (only if enabled by policy)
- NEVER use explore if sufficient context already exists to choose open_file or search_code
- Prefer the simplest tool that satisfies the next step; do not explore “by default”
```

This reduces random flipping between `search_code` and `explore` on small models.

**Hard constraint (locked — prompt wording):** Add verbatim:

```text
You MUST follow TOOL SELECTION RULES.
Do NOT choose a different tool if a rule applies.
```

### 3.3 Web usage policy (explicit)

**Recommendation: disable `search_web` in phase 1**; enable later behind policy.

If enabled, add **WEB USAGE RULE** to the prompt:

```
WEB USAGE RULE:

Only use search_web if ALL are credible:
- The information is NOT present in the repository (or exploration shows it is absent)
- Exploration confidence is low or gaps explicitly require external docs
- The user problem explicitly requires external knowledge (APIs, versions, vendor behavior)

Do not use search_web to bypass repo context or when exploration already answers the question.
```

Phase 1 implementation: omit `search_web` from `Literal[...]` and prompt tool list; add in phase 2 with policy flag.

### 3.4 Prompt exposure

- Section **ALLOWED_TOOLS:** id, one-line purpose, arg key.
- Closing line: **`tool` MUST be exactly one of: [...]. Any other value is invalid.**

### 3.5 Anti-hallucination (locked)

- Pydantic `Literal[...]` for `tool`.
- **Invalid tool:** **no silent fallback.** One **model retry** with a short repair prompt; if still invalid → **log + raise** (fail loudly).

### 3.6 Executor mapping (PlanExecutor unchanged)

- `explore` → `decision=explore`, `query` from args.
- `open_file` / `search_code` / `run_shell` / `analyze_code` → map to existing `PlanStep.action` + synthesized `inputs`.
- `search_web` when disabled → not selectable; when enabled → map per product policy (e.g. whitelisted shell, or future tool) without changing executor in phase 1 if disabled.

---

## 4. Planner output schema

### 4.1 Fields

```json
{
  "decision": "act | explore | replan | stop",
  "tool": "explore | open_file | search_code | run_shell | search_web | analyze_code | none",
  "reason": "short",
  "query": "",
  "step": {
    "action": "search | open_file | edit | run_tests | shell | analyze",
    "input": "",
    "metadata": {}
  }
}
```

- **`tool`:** Required for new prompts; legacy omit → infer from `decision` + `step.action`. Use `"none"` for `stop` / `replan`.
- **`step.metadata`:** Optional object for tool-specific structure (`path`, `command`, `snippet` keys) while keeping `input` as primary string for 7B and backward compatibility. Empty `{}` when unused.

### 4.2 Consistency

| `tool` | Typical `step.action` |
|--------|------------------------|
| `open_file` | `open_file` |
| `search_code` | `search` |
| `run_shell` | `shell` |
| `analyze_code` | `analyze` |
| `explore` | no step |
| `search_web` | policy-dependent when enabled |

Mismatch → **retry once**, then **fail loudly** (log + raise) for invalid tool; optional deterministic align only for benign `tool`/`step` drift with explicit warning log (implementation plan).

### 4.3 PlanExecutor

- Still **0–2 steps:** at most one non-`finish` + `finish`.
- `planner_decision_mapper` unchanged in precedence (`engine.decision` first); optional later: pass `tool` on `PlannerDecision` for telemetry.

### 4.4 Backward compatibility

- Phase A: default `tool` to `none` / infer from `step` only when flag allows; inferred value must remain a **valid** enum member. Phase B: missing/invalid → retry then raise (§3.5).
- `metadata` optional; omit in JSON when empty.

---

## 5. Prompt integration (sections) — **order locked**

**Final section order (maximize relevance for small models):**

1. **Rules** — role; STOP / SUFFICIENCY / MINIMALITY / conditional `query`; task mode (read-only vs write); **truth precedence** — if memory conflicts with exploration → **always trust exploration**.
2. **Tools** — ALLOWED_TOOLS; TOOL SELECTION RULES (§3.2) **including hard constraint** (§3.2); WEB disabled line or WEB USAGE RULE (§3.3).
3. **Exploration summary** — unchanged producer (facts / truth).
4. **SESSION MEMORY** — fenced pruned JSON (including **`intent_anchor`**) **immediately before** the user instruction block.
5. **User instruction** — latest message.
6. **Output** — strict JSON schema snippet (`decision`, `tool`, `reason`, `query`, `step` with optional `metadata`).

Keep tools + memory + rules within budget; exploration remains the primary **factual** signal; memory sits next to the user line for reference resolution.

---

## 6. Minimal implementation plan

| Item | Action |
|------|--------|
| New | `session_memory.py` (or under `schemas/`): model, `to_prompt_block()`, update hooks |
| `PlannerPlanContext` | Optional `session: SessionMemory | None` |
| `plan.py` | `tool` on `PlannerEngineOutput`; optional `metadata` on step spec |
| `planner_v2.py` | Inject memory + tool rules + web rule; validate tool/query/step; synthesis reads `metadata`; **sole owner** of `explore_streak` cap override (`_apply_explore_cap_override`) after engine parse |
| `planner_decision_mapper.py` | Optional: attach `tool` to `PlannerDecision` |
| Orchestration | Build/update `SessionMemory` each turn |

**Do not touch:** `ExplorationEngineV2`, `PlanExecutor`.

**Migration:** Ship with optional `tool` + inferred defaults; strict `tool` + phase-1 `search_web` off; then enable web behind flag.

---

## 7. Explore cap enforcement (runtime — not model-only) — **locked**

**Problem:** Prompt-only behavior is weak for 7B; explore loops still occur.

**`explore_streak`** lives in `SessionMemory`; **all override logic lives in `PlannerV2` only** (see implementation plan): **`PlannerV2._apply_explore_cap_override`** runs **immediately after** parsing engine JSON and **before** plan validation + step synthesis. **Do not** duplicate override logic in `session_memory.py` or `planner_task_runtime.py`.

**Rule (deterministic):**

```text
if explore_streak >= 3 and model outputs decision == "explore":
    ALWAYS force decision = "act"
    tool = "search_code"
    query = (intent_anchor.target.strip() or last_user_instruction.strip() or instruction)
    (synthesize act step + finish accordingly)
```

**Do not** automatically choose `stop` in this override path — always emit **`act`** with **`search_code`** so the loop can make forward progress.

---

## 8. Edge cases

| Case | Expected behavior |
|------|---------------------|
| Vague “do it” | Bind via **`intent_anchor`** + `active_file` + recent steps; choose minimal tool per TOOL SELECTION RULES |
| Memory vs findings conflict | **Trust exploration**; adjust memory next turn if needed |
| Empty exploration | `explore` with concrete `query` or `replan` if insufficiency flag; avoid empty `stop` unless appropriate |
| Explore loops | **`explore_streak >= 3` + model `explore` → forced `act` + `search_code`** (PlannerV2 only) |
| Invalid `tool` in JSON | Retry LLM once; then **raise** (no silent `search_code` fallback) |
| Web noise | Phase 1: **disable search_web**; phase 2: WEB USAGE RULE + policy |

---

## 9. Checklist summary

- [ ] `intent_anchor` { goal, target, entity } on session memory  
- [ ] TOOL SELECTION RULES in prompt  
- [ ] WEB USAGE RULE **or** search_web disabled phase 1  
- [ ] Runtime **forced `act` + `search_code`** when `explore_streak >= 3` and model returns `explore` (PlannerV2-only override)  
- [ ] Telemetry: `decision`, `tool`, `explore_streak`, `override_triggered`  
- [ ] Optional `step.metadata` for richer tool args  
- [ ] Explicit prompt: memory vs exploration → **always trust exploration**  
- [ ] Architecture: Exploration = truth, Memory = context, Planner = route, Executor = act  
