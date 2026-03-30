# Phase 12.6 — Exploration control semantics (completion, policy, planner gating)

**Scope:** Authoritative **Phase 12.6** specification. Does **not** replace **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`**; tightens **control semantics** on top of Phase 12.5. **Not executable** — implementation follows **`SCHEMAS.md`**, **`SUPPORTING_SCHEMAS.md`**, **`CONTRACT_LAYER.md`**, **`ARCHITECTURE_FREEZE.md`**, and **`README.md`** (architecture freeze index).

**Problem statement (why 12.6 exists)**

- **`status == "sufficient"` must not be unconditional “exit exploration.”** Relevance ≠ structural completion.
- **Planner must not run** until the **system** asserts completion via **`ExplorationResult.metadata`** — not LLM-only `stop`.
- **Loop exit** = **`should_stop(state, decision)`** (system), not **`next_action == "stop"`** alone.

**LLM suggests; system decides** — termination, expansion eligibility, planner gating.

---

## Alignment with frozen docs

| Doc | Constraint Phase 12.6 respects |
|-----|--------------------------------|
| **`ARCHITECTURE_FREEZE.md`** | No new execution-engine architecture; exploration stays read-only; retrieval **pipeline order** not reordered — discovery does not become a graph-first stage. |
| **`CONTRACT_LAYER.md`** | **`ExplorationRunner`** remains integration surface; **`ExplorationEngineV2`** optional behind it; planner input stays **Schema 4 `ExplorationResult`** (and **4c** union). |
| **`SCHEMAS.md`** | **Schema 4** stays normative: top-level **`items`**, **`summary`**, **`metadata`** — see **§ Contract layer (Schema 4)** below. Internal runtime types are not a second public planner contract. |
| **`README.md`** | Phase sits in phased rollout; amends **SCHEMAS** / **SUPPORTING_SCHEMAS** when locking new fields. |

---

## Relationship to Phase 12.5

| Topic | Phase 12.5 | Phase 12.6 |
|-------|------------|------------|
| Stages | DISCOVERY → SELECTION → INSPECTION → UNDERSTANDING → branches | Same; **exit rules**, **queue**, **graph-only expansion** |
| `ExplorationDecision` | `status`, `needs`, `reason` | + optional **`next_action` suggestion** — **not** sole authority |
| Termination | Branch routing | **`should_stop(state, decision)`** + **metadata completion** |
| State | `seen_*`, counters | **`ExplorationTarget` queue**, **`expanded_symbols`**, **system flags** |
| Planner | After exploration returns | **Only if** `metadata` records **system completion** |

---

## What the prior audit got right (retain)

1. V2 already has a loop — gap is **termination semantics**, not “missing loop.”
2. **Planner boundary** after exploration — **gating required**.
3. **`status` + `needs` → routing** is brittle without **system completion**.
4. **State**, **graph**, **minimal changes** — still valid.

---

## 1. Final runtime state (NOT exposed to LLM)

**Location:** `agent_v2/schemas/exploration.py` (or dedicated runtime-only module if split — still register in **`SUPPORTING_SCHEMAS.md`**).

**Principles**

- Deterministic fields only; **no LLM-generated blobs** on this struct.
- Sufficient for **traversal + termination**; does **not** duplicate **Schema 4 `ExplorationResult`** (that is built at end from state + evidence).
- **`instruction`** anchors the run; LLM never mutates this object as “truth.”

### `ExplorationTarget`

```python
class ExplorationTarget(BaseModel):
    file_path: str
    symbol: str | None = None
    line: int | None = None
    source: Literal["discovery", "expansion"]
```

### `ExplorationState` (final shape)

```python
class ExplorationState(BaseModel):
    # ---- Identity ----
    instruction: str

    # ---- Traversal control ----
    pending_targets: list[ExplorationTarget] = Field(default_factory=list)
    current_target: ExplorationTarget | None = None

    # ---- Visited tracking ----
    seen_files: set[str] = Field(default_factory=set)
    seen_symbols: set[str] = Field(default_factory=set)

    # ---- Expansion control ----
    expanded_symbols: set[str] = Field(default_factory=set)

    # ---- Evidence collection (runtime; mapped into Schema 4 items/summary) ----
    findings: list[dict] = Field(default_factory=list)

    # ---- Loop control ----
    steps_taken: int = 0
    backtracks: int = 0

    # ---- Termination signals (SYSTEM ONLY) ----
    primary_symbol: str | None = None
    relationships_found: bool = False

    # ---- Debug / trace ----
    last_decision: str | None = None
```

**Why**

| Field | Role |
|-------|------|
| `pending_targets` | **Controlled work queue** — replaces ad-hoc “random” iteration |
| `seen_*` | Prevents re-read / re-expand loops |
| `expanded_symbols` | Caps **graph explosion** |
| `primary_symbol` | Anchor for “what we are exploring” |
| `relationships_found` | Input to **exploration completeness** (with `primary_symbol` + `sufficient`) |
| `findings` | Internal evidence rows; **`_build_result`** maps → **`items`** + **`summary.key_findings`** (Schema 4) |

---

## 2. Pending queue design

**Model:** Controlled **BFS-style** traversal over the code graph (queue = frontier).

### Rules

**PUSH (enqueue)**

- **Discovery** returns candidates → after **ranking**, push top **K** (e.g. 3–5) as `ExplorationTarget(..., source="discovery")`.
- **Expansion** returns neighbors → push as `source="expansion"` (after dedupe + policy).

**POP (selection)**

- `target = pending_targets.pop(0)` (FIFO v1; priority queue is a later optimization).
- **Do not** let the LLM pick an arbitrary mid-queue candidate; LLM **ranks before enqueue**, not **random pop**.

**LLM role**

- **Rank / shortlist** candidates **before** push.
- **Understand** snippet → `ExplorationDecision` (relevance + suggested `next_action`).
- **Not** authoritative for: pop order, termination, planner invocation.

### Flow

```text
DISCOVERY → candidates → LLM rank top 3–5 → extend pending_targets

LOOP:
  pop target → inspect → understand → should_stop?
  optional: expand (policy) → push neighbors | refine → push new discovery targets
  steps_taken += 1 (per policy; avoid silent infinite continue)
```

---

## 3. Graph integration (strict, non-leaky)

### Rule

- **Graph edge resolution** runs **only** in the **EXPANSION** phase **after** an anchor target exists.
- **Do not** call graph APIs as the primary **DISCOVERY** mechanism (discovery = existing **search / grep / vector** per freeze; graph **refines** after anchor).

### `GraphExpansionResult` (internal DTO)

```python
class GraphExpansionResult(BaseModel):
    callers: list[ExplorationTarget]
    callees: list[ExplorationTarget]
    related: list[ExplorationTarget]
```

Each row maps to `ExplorationTarget(..., source="expansion")` with `file_path` / `symbol` / `line` from storage.

### Implementation steps

1. **Resolve symbol** — `graph_lookup` and/or repo graph storage / Serena symbol match (implementation picks one deterministic path per repo index availability).
2. **Fetch edges** — `find_referencing_symbols`, graph adapters, or **`repo_graph`** APIs — **no** NL reinterpretation of the query string in the lookup primitive (see `agent/retrieval/graph_lookup.py` contract).
3. **Convert** each edge row → `ExplorationTarget`.

### Hard limits (config / env)

```text
MAX_GRAPH_NODES = 10        # cap merged callers + callees + related per expansion call
MAX_EXPANSION_DEPTH = 1     # v1: single hop from current anchor unless extended later
```

### Push discipline

```text
for bucket in (callers, callees, related):
  for target in bucket[:remaining_budget]:
    if target.symbol and target.symbol not in state.expanded_symbols:
      state.pending_targets.append(target)
```

After successful expansion that yielded ≥1 edge, set **`state.relationships_found = True`**.

**Important:** Graph **does not** replace search. Search discovers candidates; graph **refines** after anchor.

---

## D. Unified bounded read contract (bound-before-I/O) — **doc-only change**

**Purpose:** Fix the remaining core failure after Phase 12.6 termination/queue semantics: exploration still performs **full-file reads** via `open_file` / filesystem paths and truncates **after** I/O. This violates a critical property:

- **Bound-before-I/O guarantee**: exploration must never read an entire file body unless explicitly requested by a higher-level, policy-approved path. Exploration inspection must be **bounded at read time**, not post-hoc truncated.

### Final RCA (merged)

**What Phase 12.6 fixed (retain)**

- Control loop ✅
- System-driven stopping ✅
- Queue + traversal semantics ✅

**What remained broken (must fix here)**

1. Exploration reads = full file (via `open_file`) ❌
2. Retrieval reads = bounded (but separate path, not used by exploration) ❌
3. No unified read contract ❌
4. No bound-before-I/O guarantee ❌

**Root cause (final form):** there are **two parallel systems** that never converge:

- **Exploration** → ReAct-style steps → `open_file` (**FULL READ**)
- **Retrieval** → bounded read primitives (**NOT USED**)

### Goal

Implement **adaptive exploration inspection**:

```text
selection → inspect (bounded) → expand/refine → repeat
```

With **bounded context at READ TIME** (not after), while preserving freeze constraints:

- LLM never chooses the read mode
- Dispatcher remains the tool entry point
- Trace remains complete and observable
- No new public planner-facing exploration contract (Schema 4 remains the only one)

### Contract: `ReadRequest` (internal; NOT exposed to LLM)

Add an internal request object used by the exploration engine to request bounded reads deterministically:

```python
class ReadRequest(BaseModel):
    path: str
    symbol: str | None = None
    line: int | None = None
    window: int = 80
```

### Single routing function: `read(request)`

System-owned routing logic chooses the bounded strategy **in one place**:

```python
def read(request: ReadRequest):
    if request.symbol:
        return read_symbol_body(...)
    if request.line:
        return read_region_bounded(...)
    return read_file_header(...)
```

**Key invariant:** the LLM never selects `read_symbol_body` vs `read_region_bounded` vs `read_file_header`. The system selects based on the current `ExplorationTarget` (`symbol`/`line`/fallback).

### Inspection integration (most important)

**Replace (conceptually)**

- `open_file(path)` (full read)

**With**

```python
request = ReadRequest(
    path=target.file_path,
    symbol=target.symbol,
    line=target.line,
)
snippet = read(request)
```

This keeps Phase 12.5/12.6 staged traversal intact while eliminating full reads in the inspection step.

### Keep the ReAct / external contracts untouched

To respect **`ARCHITECTURE_FREEZE.md`** and avoid global contract breakage:

- Do **not** modify `react_schema`, `open_file`, or action validation globally.
- The exploration engine may **bypass** `open_file` internally for inspection, while still routing through the dispatcher/tool registry and emitting trace events.

### Merge retrieval primitives into exploration inspection (without reordering retrieval pipeline)

Do **not** call the entire retrieval pipeline inside inspection.
Instead, **reuse bounded read primitives** already present in retrieval code paths:

```text
if target.symbol: read_symbol_body
elif target.line: read_region_bounded
else: read_file_header
```

This converges the two parallel systems (exploration + retrieval reads) without creating a new execution path or changing the retrieval pipeline order.

### Enforcement: “no full file read in exploration”

Add an explicit policy guarantee for Phase 12.6:

- **Forbidden in exploration inspection:** any full-file read path (e.g. `open_file`, raw filesystem reads) that loads the entire body.
- **Allowed:** bounded strategies only (`read_symbol_body`, `read_region_bounded`, `read_file_header`).
- **Enforce** with a system-side assertion / policy check that fails fast if inspection attempts a full read.

### Understanding prompt adjustment (small but important)

To make expansion precise (not random), the LLM output should include a structured suggestion (still non-authoritative):

```json
{
  "next_action": "expand | refine | stop",
  "target": { "symbol": "...", "file": "..." }
}
```

System policy still gates expand/refine/stop via `should_stop` and expansion/refine policies.

---

## E. Exploration source signals (item-level) + legacy convergence (corrected, zero-heuristic)

**Status:** Spec-only addition; Phase 12.6.E is a **strict, zero-heuristic** refinement.  
**Goal:** Expose **structure + origin** of exploration reads (facts), not correctness/value judgments.

### Principal-engineer rules (non-negotiable)

```text
System provides FACTS.
LLM interprets VALUE.

No heuristics:
- no quality scoring
- no ranking
- no filtering
- no top-k selection
- no reorder-by-anything
```

### Objective (refined)

```text
Expose exploration STRUCTURE and ORIGIN of data.
NOT infer correctness.
```

### Design (single source of truth; Schema 4 remains authoritative)

```text
ExplorationResult stays: items + summary + metadata
Planner consumes the same shape.

We extend items with:
- snippet (bounded, deterministic safety cap)
- read_source  (symbol | line | head) — deterministic origin signal
```

### Scope (strict)

**P0 (schema + production safety; no planner behavior changes)**

1. Extend `ExplorationItem` with `snippet` + `read_source` (**facts only**)
2. Tag `read_source` at **read time** (system-owned; deterministic)
3. Add `metadata.source_summary` counts (metadata-only extension)
4. Enforce snippet cap `MAX_SNIPPET_CHARS = 600` (system limit; safety)
5. Preserve **deterministic pipeline ordering** (see Ordering note below)
6. Keep deduplication as a state-consistency constraint (no duplicate reads)
7. Preserve `completion_status` gate semantics (termination unchanged)
8. Legacy path remains present but disabled by default (default to V2; safe rollout)

**P1 (minimal planner prompt change; no planner logic branching)**

9. Planner prompt includes `source_summary`
10. Planner prompt includes raw items as-is (no filtering) and then summary (no “evidence first” bias)

---

### 1) Contract update: extend `ExplorationItem` (planner-facing; safe)

**Location:** `agent_v2/schemas/exploration.py` (Schema 4 item model)

```python
class ExplorationItem(BaseModel):
    ...
    # Existing fields remain (do not remove/rename)

    # NEW (additive)
    snippet: str

    read_source: Literal["symbol", "line", "head"]
```

**Hard rules**

```text
- Do NOT add a parallel evidence list.
- Do NOT add quality/score/rank fields in this phase.
- snippet MUST be bounded (cap below) and must never be full-file content.
- Tagging is system-owned: LLM does not set `read_source`.
- Do NOT rename or override existing `ExplorationItem.source` (planner depends on `source.ref`).
```

---

### 2) Source tagging at read time (system-owned; deterministic)

**Read-source taxonomy (final for 12.6.E):**

```text
symbol → symbol-body bounded read
line   → bounded line-window read
head   → bounded file-head read
```

Notes:

```text
- “structure mode” is explicitly OUT OF SCOPE for 12.6.E.
  If it exists in runtime later, it must be reintroduced only by spec amendment.
```

**Hard rule**

```text
No full-file read mode in exploration.
```

---

### 3) Metadata-only extension: `source_summary` (counts; structural only)

```json
{
  "completion_status": "complete | incomplete",
  "termination_reason": "string",
  "source_summary": {
    "symbol": 0,
    "line": 0,
    "head": 0
  }
}
```

Rules:

```text
- Counts are derived from items (not a second truth system).
- No quality_summary, no scoring.
```

---

### 4) Snippet cap (system safety limit; NOT a heuristic)

```text
MAX_SNIPPET_CHARS = 600
```

Rules:

```text
- Apply cap uniformly to every emitted snippet.
- This is a deterministic resource/safety constraint, not a value judgment.
```

---

### 5) Ordering + deduplication (strict)

**Ordering (PE call — align with current V2 behavior)**

```text
Items order is deterministic and reflects the exploration pipeline.
It may not be strict insertion order of every low-level step.

Allowed: structural pipeline-driven ordering (e.g., surfacing inspection/expansion before discovery)
Forbidden: heuristic ordering (quality/relevance-based ranking, filtering, top-k, etc.)
```

**Deduplication (state consistency)**

```text
If (file_path, symbol) already seen → skip.
```

Rationale:

```text
Duplicate reads do not add new information; this prevents loops and noise.
```

---

### 6) Planner impact (P1; minimal, clean; no heuristics)

Modify only prompt construction (`PlannerV2._build_exploration_prompt()`):

```text
Exploration Sources:
- symbol reads: X
- line reads: Y
- header reads: Z

Exploration Items:
- file: <item.source.ref>
  read_source: <symbol|line|head>
  snippet: <bounded>
  summary: <item.content.summary>

Exploration Summary:
<summary>
```

Rules:

```text
- Do NOT filter items.
- Do NOT reorder items.
- Do NOT top-k.
- “Items then summary” is allowed (structural), but do not introduce any quality-first framing.
```

---

### 7) Termination (unchanged)

```text
Keep completion_status semantics from Phase 12.6.
No “high quality evidence” or knowledge-based termination rules.
```

---

### 8) Legacy-path convergence (explicit, safe rollout)

**Phase A (P0):**

```text
- Legacy path kept
- Default to V2 (legacy disabled by default via flag/config)
- Legacy remains available as fallback during rollout
```

**Phase B (post-stability):**

```text
- Remove feature flag
- Delete legacy exploration path
- Keep exactly one exploration system
```

---

### 9) Trace visibility (facts only)

Allow trace metadata to include read origin facts, not heuristics:

```json
{
  "read_mode": "symbol | line | head"
}
```

Explicitly forbidden:

```text
No quality fields in trace metadata.
```

---

### 10) Explicit removals from previous 12.6.E drafts (lock-in)

Remove (from spec and implementation scope):

```text
- ExplorationEvidence schema
- ExplorationResult.evidence[]
- quality / quality_summary
- ranking / filtering / top-k / prioritization
- reorder-by-anything
- fallback ladders / progressive read strategies
- structure mode (until re-specified)
```

---

### Alignment notes (freeze docs)

```text
- ARCHITECTURE_FREEZE.md: extend existing path; no parallel system; no new execution-engine architecture
- CONTRACT_LAYER.md: ExplorationRunner emits Schema 4 ExplorationResult; additions are item+metadata additive only
- SCHEMAS.md: preserve Schema 4 top-level shape; additive-only changes must be frozen in SCHEMAS.md before code
- README.md: staged rollout; legacy removal only after stability evidence
```

---

## 4. Control loop (integration sketch)

Illustrative — real code must use **`Dispatcher`**, **`should_stop`**, and Schema 4 **`_build_result`**.

```python
def explore(instruction: str) -> ExplorationResult:
    state = ExplorationState(instruction=instruction)

    intent = parse_intent(instruction)
    candidates = discovery(intent)
    ranked = llm_rank_candidates(candidates)
    state.pending_targets.extend(ranked[:5])

    while state.steps_taken < MAX_EXPLORATION_STEPS:
        if not state.pending_targets:
            break

        target = state.pending_targets.pop(0)
        state.current_target = target

        if target.file_path in state.seen_files:
            continue

        snippet = inspect(target)
        state.seen_files.add(target.file_path)
        if target.symbol:
            state.seen_symbols.add(target.symbol)

        decision = understand(snippet)
        state.last_decision = decision.status

        if target.symbol and not state.primary_symbol:
            state.primary_symbol = target.symbol

        if should_stop(state, decision):
            break

        if policy_allows_expand(decision, target, state):
            if target.symbol and target.symbol not in state.expanded_symbols:
                expansion = graph_expand(target.symbol, target.file_path)
                state.expanded_symbols.add(target.symbol)
                if expansion_has_edges(expansion):
                    state.relationships_found = True
                    enqueue_expansion_targets(state, expansion)

        elif policy_allows_refine(decision, state):
            new_candidates = discovery_refined(intent, decision)
            ranked_new = llm_rank_candidates(new_candidates)
            state.pending_targets.extend(ranked_new[:3])
            state.backtracks += 1  # if using backtrack budget

        state.steps_taken += 1

    return build_exploration_result(state)
```

**Notes**

- **`policy_allows_expand` / `policy_allows_refine`** incorporate **`next_action` suggestion** + caps + `expanded_symbols` — LLM does not bypass policy.
- **`graph_expand`** returns `GraphExpansionResult` or empty; empty → do not set `relationships_found` unless other edges already recorded.

---

## 5. Termination (`should_stop`) — system only

```python
def should_stop(state: ExplorationState, decision: ExplorationDecision) -> bool:
    if state.steps_taken >= MAX_EXPLORATION_STEPS:
        return True

    if not state.pending_targets:
        return True

    if (
        state.primary_symbol
        and state.relationships_found
        and decision.status == "sufficient"
    ):
        return True

    return False
```

**Ordering note:** Empty `pending_targets` exits the **loop** early; `should_stop` may also return true when queue drains mid-iteration — keep **one** authoritative exit path in implementation to avoid double semantics.

**`next_action == "stop"`** may be logged as a **suggestion**; **does not** override `should_stop` unless explicitly folded into **policy** (default: **no**).

---

## 6. Contract layer (Schema 4 compliance)

**Do not break** **`SCHEMAS.md` Schema 4** top-level shape:

```text
ExplorationResult:
  exploration_id
  instruction
  items[]          # NOT renamed "findings" at top level
  summary          # overall, key_findings, knowledge_gaps, ...
  metadata         # total_items, created_at, PLUS Phase 12.6 fields below
```

**Add only via `metadata` amendment** (names illustrative — finalize in **`SCHEMAS.md`**):

```json
{
  "completion_status": "complete | incomplete",
  "termination_reason": "string (enum in implementation)",
  "explored_files": 0,
  "explored_symbols": 0
}
```

Optional bool: **`exploration_complete`** aligned with **`completion_status == "complete"`** (single source of truth — pick one in schema amendment).

**Do not expose on `ExplorationResult`**

- `pending_targets`
- full raw `ExplorationState`
- per-step internal queue dumps (trace may summarize counts only)

---

## Loop control (replacement pattern)

**Replace**

```text
if next_action == "stop": break
if status == "sufficient": break   # unconditional — wrong
```

**With**

```text
after inspect + understand + state updates:
  if should_stop(state, decision): break
```

---

## What Phase 12.6 adds (summary)

1. **Separation:** relevance (`ExplorationDecision.status`) vs **completion** (system flags + `should_stop`).
2. **`next_action`:** suggestion; **policy** gates expand/refine.
3. **Queue + targets:** `ExplorationTarget` + `pending_targets` as the traversal engine.
4. **Graph:** **expansion phase only**; `GraphExpansionResult` → enqueue; limits **MAX_GRAPH_NODES**, **MAX_EXPANSION_DEPTH**.
5. **Planner gating:** **`metadata.completion_status` / `exploration_complete`** set by engine — **not** LLM alone.

---

## Implementation plan (ordered; minimal; reversible)

1. **Schemas** — `ExplorationTarget`, final `ExplorationState`, `GraphExpansionResult`; amend **`ExplorationResultMetadata`** per **SCHEMAS.md**.
2. **Engine** — queue pop/push, `should_stop`, policy functions, `_build_result` from `findings` → **`items`**.
3. **Graph** — expansion-only adapter; enforce limits.
4. **ModeManager** — planner only when **`metadata`** marks complete (per product rule for incomplete runs).
5. **Tests** — unit + integration for gating and graph-only expansion.

---

## Exit criteria

```text
✅ ExplorationState matches §1; no LLM fields on runtime state object
✅ pending_targets drives work; FIFO pop; LLM ranks before push
✅ Graph only in expansion phase; GraphExpansionResult → targets; limits enforced
✅ should_stop is system-owned; sufficient ≠ sole terminal
✅ Schema 4 preserved; metadata-only completion fields; SCHEMAS.md amended
✅ CONTRACT_LAYER / ARCHITECTURE_FREEZE constraints respected
```

---

## Files (expected touch points — implementation phase)

```text
agent_v2/schemas/exploration.py
agent_v2/exploration/exploration_engine_v2.py
agent_v2/exploration/understanding_analyzer.py
agent_v2/exploration/graph_expander.py
agent_v2/runtime/mode_manager.py
tests/test_exploration_runner.py
tests/test_mode_manager.py
Docs/architecture_freeze/SCHEMAS.md
Docs/architecture_freeze/SUPPORTING_SCHEMAS.md
```

---

## Principal engineer verdict

This is a **controlled graph-traversal engine**: **system** owns the frontier (`pending_targets`), expansion policy, and **termination**. The LLM **ranks** and **judges relevance**; it does **not** own planner entry or unconditional exit. **Schema 4** remains the single planner-facing artifact; Phase 12.6 adds **metadata** and **internal** state types only as registered in the freeze docs.

**Enables next:** multi-hop and richer planners stay incremental — **depth**, **queue policy**, and **metadata** evolve without a second public exploration contract.
