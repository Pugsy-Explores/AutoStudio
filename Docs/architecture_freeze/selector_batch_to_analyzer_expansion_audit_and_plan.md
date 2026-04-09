# Selector Batch -> Analyzer Expansion

## Goal

Ensure selector-reduced symbol scope is expanded to analyzer depth in a bounded, deterministic way:

- expand selected symbols before analyzer
- preserve strict context limits
- mirror selector trim semantics when over budget
- avoid selector/analyzer coupling changes outside integration layer

---

## Phase 1: Audit (Current Pipeline)

Pipeline observed:

`Scoper -> SelectorBatch -> enqueue targets -> inspect/read -> build analyzer context -> Analyzer`

### A) Selector output and trim marker origin

- Selector prompt code injection is built in `agent_v2/exploration/candidate_selector.py`, using `prepare_outline_for_selector_prompt(...)` from `agent_v2/exploration/selector_outline_injection.py`.
- `[trimmed]` markers are present in selector prompt payload (`outline_for_prompt[].code`) only.
- Structured selector result forwarded in `SelectorBatchResult`:
  - `selected_symbols: dict[str, list[str]]`
  - `expanded_symbols: list[str]`
  - `selected_top_indices: list[int]`
- Current structured output contains names only; no persisted trim flag per symbol.

### B) Expansion step status

Existing expansion into analyzer context exists in `exploration_engine_v2.py`:

- `_context_blocks_for_selector_symbols(target)` looks up selected symbol names against cached outline and adds full code blocks.

Current limitations:

- scoped to `target.selector_top_index` path
- partial coverage due to per-target caps
- no explicit analyzer-char budget parity with selector trim policy
- no deterministic overflow representation (signature+marker) for analyzer symbol blocks

### C) Analyzer input currently

Analyzer is called with:

- read-derived context blocks from `_build_context_blocks_for_analysis(...)`
- selector symbol full-code blocks appended, then globally sliced:
  - `(context_blocks + selector_symbol_blocks)[:6]`

Implications:

- expanded symbol blocks may be dropped by list slicing, not by deterministic symbol policy
- no explicit `[CODE TRIMMED IN ANALYZER CONTEXT]` marker
- no guaranteed representation of all selected symbols (full or signature)

### D) Gap classification

- missing expansion stage: **No** (stage exists)
- partial expansion: **Yes**
- trimmed symbols not expanded: **Yes** (not guaranteed)
- no size control: **Yes** (for symbol expansion in analyzer path)
- no deterministic ordering: **Yes** (effective outcome depends on append+slicing)

---

## Tightened Decisions (Applied Improvements)

### 1) No schema expansion for trim signal

Do **not** add `trimmed_selected_symbols` to `SelectorBatchResult`.

Use integration-layer reconstruction:

- derive selected symbol set from selector output
- determine which cannot be full under analyzer symbol budget
- mark overflow with `[trimmed]` in analyzer symbol context

This keeps selector/analyzer loosely coupled and avoids model-surface expansion.

### 2) Use all selected symbols (not just one top index)

Analyzer expansion input must be:

- union of `selected_symbols` across all indices
- plus `expanded_symbols`
- dedupe + stable sort

No single `selector_top_index` bottleneck for symbol expansion preparation.

### 3) Remove `[:6]` as selection mechanism

`(context_blocks + selector_symbol_blocks)[:6]` must not decide importance.

Replace with bounded builders before final assembly:

- bounded read blocks
- bounded symbol blocks
- deterministic concatenation

### 4) Separate read vs symbol budgets

Construct analyzer input as:

1. read-context budgeted blocks
2. symbol-context budgeted blocks

Then concatenate in fixed order:

- `final_context = read_blocks_bounded + symbol_blocks_bounded`

This prevents unstable competition between unrelated evidence types.

### 5) Reuse shared trim/sort/signature logic

Avoid duplicate trimming implementations.

Extract / reuse a shared helper:

- `build_bounded_symbol_context(...)`

Used by:

- selector prompt injection path
- analyzer symbol expansion path

Single implementation for ordering, signature generation, overflow marking.

---

## Phase 2: Implementation Plan (File-Level)

## 1) Config

File: `agent_v2/config.py`

- add `MAX_ANALYZER_CONTEXT_CHARS = 45000`
- env override: `AGENT_V2_MAX_ANALYZER_CONTEXT_CHARS`

Optional (recommended) separation:

- `MAX_ANALYZER_READ_CONTEXT_CHARS`
- `MAX_ANALYZER_SYMBOL_CONTEXT_CHARS`

If not split, use one cap with deterministic internal allocation.

## 2) Shared bounded symbol helper

File: `agent_v2/exploration/selector_outline_injection.py` (or shared sibling)

Add/refactor shared function:

- input: symbol rows (name/type/code/start/end/file), max chars, trim marker label
- behavior:
  - alphabetical deterministic order
  - full definitions until budget
  - overflow as signatures only
  - `[trimmed]` prefix at symbol line level
  - one explicit marker on first overflow block

Selector continues using existing behavior through this shared helper.

Analyzer calls same helper with marker:

- `[CODE TRIMMED IN ANALYZER CONTEXT]`

## 3) Integration layer in exploration engine

File: `agent_v2/exploration/exploration_engine_v2.py`

Add method:

- `_build_analyzer_symbol_context_blocks(ex_state, target, state) -> list[ContextBlock]`

Steps:

1. collect symbols as union:
   - all values from `target.selector_batch.selected_symbols`
   - `target.selector_batch.expanded_symbols`
2. resolve rows via outline cache / file mapping
3. deterministic sort
4. trimmed-first expansion priority:
   - symbols that would overflow full-body budget are represented as `[trimmed]` signatures
   - others full code
5. bounded by analyzer symbol-context cap
6. emit `ContextBlock`s in deterministic order

Then replace append+slicing pattern with explicit bounded assembly:

- `read_blocks = ...` (existing routing path, bounded)
- `symbol_blocks = _build_analyzer_symbol_context_blocks(...)` (new bounded)
- `context_blocks = read_blocks + symbol_blocks`

Do not use `[:6]` for prioritization.

## 4) Keep analyzer logic unchanged

File: `agent_v2/exploration/understanding_analyzer.py`

- no semantic/control changes
- only receives improved `context_blocks` payload

---

## Tests (Mandatory)

Add tests in engine/integration-focused test files:

1. all selected symbols are represented before analyzer (full or signature)
2. overflow symbols are `[trimmed]` signatures with line-start prefix
3. over-limit analyzer symbol context emits `[CODE TRIMMED IN ANALYZER CONTEXT]`
4. deterministic ordering across runs
5. no arbitrary loss from `[:6]` truncation path (removed/replaced)
6. read blocks and symbol blocks remain separately bounded then concatenated

---

## Before/After Analyzer Input Example

### Before

- mixed list: read blocks + some selector symbol blocks
- global `[:6]` may drop symbol expansions unpredictably
- no explicit analyzer trim marker

### After

- deterministic:
  - bounded read blocks
  - bounded symbol blocks from all selected symbols
- overflow symbol rows represented as:
  - `[trimmed] def ...`
  - `[trimmed] class ...`
- marker present once when needed:
  - `[CODE TRIMMED IN ANALYZER CONTEXT]`

---

## Key Principle

`Selector narrows breadth -> Analyzer receives depth (bounded + deterministic).`

No randomness, no hidden state, no heuristic truncation.

---

## Final Clean Architecture

Selector ->
    selected_symbols

↓

Expansion Builder (NEW / FIXED) ->
    - collect all symbols
    - sort
    - expand with budget
    - apply trimming
    - mark [trimmed]

↓

Analyzer ->
    gets:
      - full definitions
      - signatures for overflow
      - deterministic ordering

---

## Final Staff Recommendation

### MUST FIX (Blockers)

- Remove `[:6]` truncation
- Expand ALL selected symbols (not just top_index)
- Build bounded context BEFORE analyzer call

### SHOULD FIX

- Prefer runtime trim detection over schema change
- Separate read vs symbol context
- Extract shared trimming helper
