# Phase 12.5 — Exploration engine V2 (progressive + controlled)

**Scope:** This document is the authoritative Phase 12.5 specification. It formalizes a **deterministic, staged, LLM-assisted** exploration pipeline that replaces ad-hoc exploration with a **system-controlled state machine**, producing **`SCHEMAS.md` Schema 4 `ExplorationResult`** for **PlannerV2** (via **Schema 4c `PlannerInput` union** on the initial pass). It extends **Phase 3** and **`ARCHITECTURE_FREEZE.md` §3.1** without changing top-level flow (**User → exploration → Planner**). **`CONTRACT_LAYER.md`**: **`ExplorationRunner`** remains the **named component**; **`ExplorationEngineV2`** is an **optional implementation** behind that surface. This file is **not** executable.

**See also — Phase 12.6:** **`PHASE_12_6_EXPLORATION_CONTROL_SEMANTICS.md`** — completion vs relevance, **`should_stop`**, **`pending_targets`**, expansion policy, and **planner gating** (system-owned `exploration_complete`).

**Relationship to frozen `ExplorationResult`**

**`SCHEMAS.md` Schema 4 remains normative.** `_build_result` must satisfy **Rules 2–5** (summaries not raw dumps; **`len(items) ≤ 6`**; real **`source` refs**; **`knowledge_gaps` / `knowledge_gaps_empty_reason`** per Rule 5) — see **§5 below** and Schema 4 in **`SCHEMAS.md`**. The abbreviated shapes elsewhere (`findings` / `sources`) are **conceptual only**; do not ship a parallel type that bypasses Schema 4. New internal DTOs (**`QueryIntent`**, **`ExplorationCandidate`**, **`ExplorationDecision`**, **`ExplorationState`**) require **`SUPPORTING_SCHEMAS.md` / `PHASE_1_SCHEMA_LAYER.md`** amendment when frozen.

---

## Objective

```text
Replace ad-hoc exploration with a deterministic, staged, LLM-assisted exploration engine
that produces structured, high-signal ExplorationResult.
```

---

## Position in architecture

### Before

```text
User → ExplorationRunner → (weak summary) → Planner
```

### After

```text
User
  ↓
ExplorationEngineV2 (Phase 12.5)
  ↓
ExplorationResult (Schema 4 — structured evidence)
  ↓
PlannerV2 (Phase 4)
```

---

## Core principle

```text
Exploration = SYSTEM-CONTROLLED STATE MACHINE
LLM = LOCAL DECISION ENGINE (intent, selection, understanding only)
```

**Frozen rules (unchanged)**

```text
NO EDITS — NO WRITES — NO PATCHES during exploration
```

Retrieval pipeline **order** remains immutable; discovery uses existing **graph / grep / vector** capabilities as **sources**, not a new retrieval stage inserted ahead of the pipeline.

---

## Control flow (frozen)

High-level phases:

```text
DISCOVERY → SELECTION → INSPECTION → UNDERSTANDING
                                      ↓
                          ┌───────────┼────────────┐
                          ↓           ↓            ↓
                    DISCOVERY     INSPECTION    EXPANSION
```

Branching is driven by **`ExplorationDecision`** (below), not by free-form LLM “next action.”

---

## Contract layer — new / extended schemas

Implement under **`agent_v2/schemas/`** (exact filenames follow **`PHASE_1_SCHEMA_LAYER.md`** layout). All require **`SCHEMAS.md` / `SUPPORTING_SCHEMAS.md`** amendment when frozen.

### 1. `QueryIntent` (LLM output — minimal)

```python
class QueryIntent(BaseModel):
    symbols: list[str] = []
    keywords: list[str] = []
    intents: list[str] = []  # e.g. find_definition, find_usage (enum later)
```

### 2. `ExplorationCandidate` (align with retrieval outputs)

```python
class ExplorationCandidate(BaseModel):
    symbol: str | None
    file_path: str
    snippet: str | None
    source: Literal["graph", "grep", "vector"]
```

Reuse or map from existing retrieval candidate types where possible to avoid duplicate DTOs.

### 3. `ExplorationDecision` (replaces fuzzy “confidence”)

```python
class ExplorationDecision(BaseModel):
    status: Literal["wrong_target", "partial", "sufficient"]

    needs: list[
        Literal[
            "more_code",
            "callers",
            "callees",
            "definition",
            "different_symbol",
        ]
    ] = []

    reason: str
```

### 4. `ExplorationState` (internal — not passed to LLM)

```python
class ExplorationState(BaseModel):
    seen_files: set[str] = set()
    seen_symbols: set[str] = set()

    current_symbol: str | None = None
    current_file: str | None = None

    steps_taken: int = 0
    backtracks: int = 0
```

Use Pydantic-friendly types (`model_config` / custom serializers for `set` if needed).

### 5. `ExplorationResult` (final output — Schema 4)

The **only** outward contract to the planner is **`ExplorationResult`** as defined in **`SCHEMAS.md` Schema 4**. **`_build_result(state)`** must produce a value that validates **all** Schema 4 field rules, including:

**Rule 2 — No raw dumps**

```text
items[].content holds summaries / key_points — NOT full file bodies.
```

**Rule 3 — Bounded items**

```text
len(items) ≤ 6   (frozen; same as ARCHITECTURE_FREEZE §3.1 constraints)
```

Internal loop limits (`MAX_EXPLORATION_STEPS`, etc.) are **orthogonal**: merge, rank, or cap so **`items[]`** never exceeds **6** entries.

**Rule 4 — Real sources**

```text
Every items[].source.ref MUST correspond to an actual lookup or read performed in this run (no invented paths).
```

**Rule 5 — `knowledge_gaps` vs `knowledge_gaps_empty_reason`**

```text
Populate summary.knowledge_gaps and summary.knowledge_gaps_empty_reason per SCHEMAS.md (mutual exclusion and non-empty reason when gaps are empty).
```

Map internal evidence into:

- **`items[]`** — atomic summarized hits (`type`, `source.ref` / `location`, `content.summary`, `relevance`, `metadata` as required by Schema 4).
- **`summary`** — `overall`, `key_findings`, **`knowledge_gaps`**, **`knowledge_gaps_empty_reason`** (and `metadata.total_items`, `created_at`, etc.).

Illustrative **mapping** (not a second schema):

```python
# Conceptual: internal evidence → items[] + summary (Schema 4), with len(items) ≤ 6
```

---

## Module structure

```text
agent_v2/exploration/
    exploration_engine_v2.py   ← main control loop
    query_intent_parser.py
    candidate_selector.py
    inspection_reader.py
    understanding_analyzer.py
    graph_expander.py
```

Integration: **`ExplorationRunner`** remains the **contract surface** in **`CONTRACT_LAYER.md`**; it **delegates** to **`ExplorationEngineV2.explore(...)`** behind a feature flag or cutover, returning **`ExplorationResult`**.

**Planner input (Schema 4c — frozen union)**

Per **`SCHEMAS.md` Schema 4c**, the planner’s context input is the **union**:

```text
PlannerInput = ExplorationResult | ReplanContext
```

On the **initial** plan after exploration, the value passed to **`PlannerV2`** is an **`ExplorationResult`** instance (not a wrapper DTO). Code may use a parameter name such as `planner_input` or `exploration`; the **type** must enforce **`PlannerInput`**. Replan flows use **`ReplanContext`** — unchanged.

---

## Core engine (control loop)

Illustrative API:

```python
class ExplorationEngineV2:

    def explore(self, instruction: str) -> ExplorationResult:

        state = ExplorationState()

        intent = self._parse_intent(instruction)

        candidates = self._discovery(intent, state)

        while state.steps_taken < MAX_EXPLORATION_STEPS:

            selected = self._select_candidate(candidates, instruction)

            snippet = self._inspect(selected, state)

            decision = self._understand(snippet, instruction)

            next_step = self._decide_next(decision)

            if next_step == "DONE":
                break

            elif next_step == "DISCOVERY":
                state.backtracks += 1
                candidates = self._discovery(intent, state)

            elif next_step == "INSPECTION":
                snippet = self._inspect_more(selected, state)

            elif next_step == "EXPANSION":
                candidates = self._expand(selected, state)

            state.steps_taken += 1

        return self._build_result(state)
```

**Read-only I/O (aligned with `TOOL_EXECUTION_CONTRACT.md` + execution boundaries)**

Use **`ToolRegistry` / dispatcher** (or the same adapters **`ExplorationRunner`** uses today) for **`search`**, **`open_file`**, read-only **`shell`** — **no** direct `Path.read_text` shortcuts that bypass policy, tracing, or normalization. Same **allowed actions** as **`ARCHITECTURE_FREEZE.md` §3.1**; **no edit tools**.

---

## Phase implementation details

### Step 1 — Query intent (LLM)

**Prompt:** strict JSON only; **input context:** `instruction` only (no file contents, no history).

See **Prompts — §1** below.

### Step 2 — Discovery (system only)

Uses:

- graph lookup (symbol)
- ripgrep (regex)
- vector search

**Rules**

```text
- NO full file reads
- NO graph expansion in this subphase
- Deduplicate candidates
```

### Step 3 — Selection (LLM)

Given **top_k** candidates (5–10 max), return **single** `file_path` + `symbol`. See **§2** below.

### Step 4 — Inspection (system)

```text
open_file(file_path, partial=True)
```

**Rules**

```text
- max ~100–200 lines per read (configurable)
- no full-file load unless explicitly justified
```

### Step 5 — Understanding (LLM)

Returns **`ExplorationDecision`** JSON — drives the state machine. See **§3** below.

### Step 6 — System decision (`_decide_next`)

```python
def _decide_next(decision: ExplorationDecision) -> str:

    if decision.status == "wrong_target":
        return "DISCOVERY"

    if decision.status == "partial":

        if "more_code" in decision.needs:
            return "INSPECTION"

        if "callers" in decision.needs or "callees" in decision.needs:
            return "EXPANSION"

        if "different_symbol" in decision.needs:
            return "DISCOVERY"

    if decision.status == "sufficient":
        return "DONE"

    return "DONE"  # defensive default; log when hit
```

### Step 7 — Expansion (graph)

```text
graph_expand(symbol)
→ callers / callees / related symbols
```

**Hard limits**

```text
depth = 1
max_nodes = 20
```

---

## Guardrails (mandatory)

```python
MAX_EXPLORATION_STEPS = 5   # configurable; internal state-machine iterations
MAX_BACKTRACKS = 2
# SCHEMAS.md Schema 4 Rule 3: final ExplorationResult.items length MUST be ≤ 6
```

**Dedup**

```text
- same file not re-opened wastefully
- same symbol not re-expanded
```

**No-op detection**

```text
if no new candidates → STOP (bounded exit)
```

---

## Trace integration

Exploration substeps must be **observable** without pretending each substep is a full plan step.

**Preferred (freeze-safe v1)**

- Emit structured records under **`TraceStep.metadata`** (e.g. `metadata["exploration_v2"] = { "subphase": "discovery" | "selection" | "inspection" | "understanding" | "expansion", ... }`) on relevant steps, **or**
- Use **Langfuse spans / events** (Phase 11) for sub-step granularity.

**Graph UI (Phase 12)**

- Optional **`GraphNode.type="event"`** nodes for exploration substeps, or aggregate under a single exploration **event** — extend only via **`SCHEMAS` / graph model** amendment.

Do **not** introduce a parallel trace format outside **`Trace`** / **`TraceStep`**.

---

## What this fixes

**Before**

```text
- repeated searches
- wrong file selection
- weak summaries
```

**After**

```text
- precise symbol discovery
- minimal context usage
- controlled expansion
- structured evidence → Schema 4 ExplorationResult
```

---

## Implementation order

1. **Schemas first** — `QueryIntent`, `ExplorationCandidate`, `ExplorationDecision`, `ExplorationState` + mapping to **Schema 4 `ExplorationResult`**.
2. **Control loop** with stubs (no external tools).
3. Plug **graph lookup**, **grep**, **vector** into `_discovery` / `_expand`.
4. Add **LLM** calls (model router — no direct vendor SDKs in business logic) for intent, selection, understanding.
5. **Trace / Langfuse** emission for substeps.
6. Feature-flag swap: **`ExplorationRunner`** delegates to **`ExplorationEngineV2`**.
7. **Validation:** assert **`ExplorationResult`** satisfies **Schema 4** (including **≤ 6 items**, **`knowledge_gaps`** / **`knowledge_gaps_empty_reason`**, no raw dumps).

---

## Prompt design principles (locked)

1. No open-ended reasoning.  
2. No long context.  
3. No tool awareness in prompts.  
4. Strict JSON only.  
5. Grounding enforced (snippet + instruction only for understanding).

---

## Prompts (production-shaped)

### 1. Query intent

**Purpose:** Vague instruction → structured search intent.

**Input context:** **`instruction` only.**

```text
You are extracting search intent from a coding task.

Identify:
- exact symbols (class, function, variable names if mentioned or implied)
- keywords (important technical terms)
- intent types (what the user is trying to do)

Return STRICT JSON only:

{
  "symbols": [],
  "keywords": [],
  "intents": []
}

Rules:
- symbols must be exact or near-exact names (e.g., AgentLoop, execute_patch)
- keywords are generic terms (e.g., retry, failure, config)
- intents must be from:
  ["find_definition", "find_usage", "debug", "understand_flow", "locate_logic"]
- Do NOT hallucinate symbols
- Do NOT explain
- Do NOT include anything outside JSON

Instruction:
{instruction}
```

**Example**

Input: `Find where retry logic is implemented in AgentLoop`

Output:

```json
{
  "symbols": ["AgentLoop"],
  "keywords": ["retry", "failure"],
  "intents": ["find_definition", "understand_flow"]
}
```

---

### 2. Candidate selection

**Purpose:** Pick the **single** best candidate.

**Input context:** `instruction` + **top_k** candidates (5–10 max), compact fields only — not full snippets.

```text
You are selecting the most relevant code location.

Given a user instruction and a list of candidates,
choose the SINGLE best match.

Return STRICT JSON:

{
  "file_path": "...",
  "symbol": "..."
}

Rules:
- Prefer exact symbol matches
- Prefer implementation over tests
- Prefer core logic over config or wrappers
- Avoid duplicates or already explored files if possible
- If multiple are similar, pick the one most likely to contain logic

Do NOT explain.

Instruction:
{instruction}

Candidates:
{candidates}
```

**Example**

Candidates include `tests/test_agent_loop.py` vs `agent_v2/runtime/agent_loop.py` → prefer **`agent_v2/runtime/agent_loop.py`**.

---

### 3. Understanding (most important)

**Purpose:** Drive control flow; replaces fuzzy confidence.

**Input context:** `instruction`, `file_path`, `snippet` (100–200 lines max).

```text
You are analyzing a code snippet to determine if it contains relevant logic.

Return STRICT JSON:

{
  "status": "wrong_target" | "partial" | "sufficient",
  "needs": [],
  "reason": ""
}

Definitions:
- wrong_target → this file/snippet is NOT relevant to the instruction
- partial → relevant but missing key details
- sufficient → enough information to answer the instruction

Allowed "needs" values:
- "more_code" → need more lines from same file
- "callers" → need to see who calls this
- "callees" → need to see what this calls
- "definition" → need deeper implementation
- "different_symbol" → wrong symbol chosen

Rules:
- Base reasoning ONLY on the given snippet
- Be precise and short
- Do NOT guess beyond visible code
- If logic is not present, mark wrong_target

Instruction:
{instruction}

File:
{file_path}

Snippet:
{snippet}
```

**Examples**

Wrong target:

```json
{
  "status": "wrong_target",
  "needs": ["different_symbol"],
  "reason": "This file only contains configuration and does not implement retry logic"
}
```

Partial:

```json
{
  "status": "partial",
  "needs": ["callers"],
  "reason": "Retry function is defined but usage flow is not visible"
}
```

Sufficient:

```json
{
  "status": "sufficient",
  "needs": [],
  "reason": "Retry logic is implemented in _retry_loop with backoff handling"
}
```

---

### 4. Optional — refinement (backtracking)

When **`wrong_target`** triggers **DISCOVERY** again:

```text
The previous code location was not relevant.

Refine the search intent.

Return STRICT JSON:

{
  "symbols": [],
  "keywords": []
}

Rules:
- Avoid previous incorrect symbol
- Focus on alternative interpretations
- Keep output minimal

Instruction:
{instruction}

Previous failure reason:
{reason}
```

---

## Context rules (critical)

**Do**

- Keep inputs small.  
- Isolate each decision.  
- Limit candidates to top 5–10.  
- Truncate snippets.

**Do not**

- Pass entire repo context.  
- Mix multiple steps in one prompt.  
- Include tool descriptions.  
- Include previous reasoning chains.

---

## Why these prompts work

They enforce:

- Local reasoning (not global hallucination)  
- Discrete decisions (machine-parseable)  
- Strict outputs (system-controlled branching)  
- Low token usage (fast + cheap)

Quality is driven primarily by **understanding** + **candidate selection** prompts — structure them before tuning model choice.

---

## Principal engineer note

```text
Phase 12.5 is not a cosmetic feature.
It is the foundation of reasoning quality for planning.
```

```text
Weak exploration → planner and executor inherit garbage.
Strong exploration → planner + executor become tractable.
```
