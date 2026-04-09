# Exploration Signal Integration — RCA + Minimal Fix Plan

## Scope

This document captures:

1. RCA for planner over-exploration.
2. RCA for missing expansion-target integration.
3. Minimal, wiring-only fix plan (no architecture redesign).

Constraints respected:

- No pipeline redesign.
- No heuristic scoring system.
- No blind model behavior changes.
- Reuse existing state/wiring surfaces.

---

## Findings (Current State)

### 1) Planner over-explores

- Planner prompt has tool selection order, but planner does not receive strong structured symbol-state context from exploration internals.
- Planner sees mostly summarized exploration outputs, not explicit symbol readiness/missingness for action selection.
- Result: planner often defaults to `explore` even when actionable file/symbol context is already available.

### 2) Expansion-target signals are incomplete end-to-end

- Selector batch currently emits `selected_symbols` but not a first-class `expanded_symbols` contract.
- Analyzer emits textual gaps but not a first-class `required_symbols` contract.
- Engine has partial hooks but signal flow is not explicit and fully consumed in graph expansion sequencing.

### 3) Integration mismatch points

- Selector symbols are used for some symbol-aware operations, but not promoted as stable expansion intent.
- Analyzer gaps drive high-level control but symbol-level expansion requests are not consistently structured.
- Planner does not consume explicit `available_symbols`/`missing_symbols` constraints in a deterministic way.

---

## RCA

## A) Planner over-exploration RCA

Primary cause:

- Missing structured planner-facing symbol signals needed to choose `open_file` / `search_code` confidently.

Secondary cause:

- Prompt rule exists, but not grounded with explicit machine-readable `available_symbols` and `missing_symbols`.

Net effect:

- Planner falls back to `explore` under uncertainty more often than intended.

## B) Expansion signals RCA

Classification:

- Selector: partially generated, but not explicitly typed as expansion targets.
- Analyzer: mostly unstructured text for symbol needs.
- Propagation: partial and implicit.
- Consumption: incomplete in deterministic graph expansion order.

Net effect:

- System degrades to broad exploration instead of directed expansion.

---

## Minimal Fix Plan (Updated)

## 1) Add structured fields

### Selector output

In `SelectorBatchResult`:

- `expanded_symbols: list[str] = []`

Semantics:

- "What we currently have as plausible symbol anchors from selection."

### Analyzer output

In `UnderstandingResult`:

- `required_symbols: list[str] = []`

Semantics:

- "What is still needed to close knowledge gaps."

---

## 2) Priority-aware merge (critical)

Do not treat selector/analyzer signals as equal.

Priority contract:

1. `required_symbols` (Analyzer): MUST-expand candidates.
2. `expanded_symbols` (Selector): SHOULD-expand candidates.

Implementation rule:

- Build `final_expansion_symbols` in stable order:
  1. deduped `required_symbols`
  2. deduped `expanded_symbols` not already included

---

## 3) Deterministic graph expansion

In exploration engine, materialize:

- `pending_expansion_symbols: list[str]`

Deterministic behavior:

- Stable order as defined above.
- Hard cap per iteration/run (for example, top `N=8`; make config-driven).
- No randomization, no score-based ranking.

---

## 4) Analyzer extraction safety fallback

Risk:

- Model may return empty `required_symbols` while still producing non-empty gaps.

Safety rule:

- If `knowledge_gaps` is non-empty AND `required_symbols` is empty:
  - run lightweight symbol extraction from gap text
  - populate `required_symbols` fallback list

Intent:

- Prevent silent degradation.
- Keep fallback simple and bounded.

---

## 5) Close planner decision loop explicitly

Planner context fields:

- `available_symbols: list[str]`
- `missing_symbols: list[str]`

Prompt constraint update (minimal):

- If `missing_symbols` is non-empty:
  - prefer `search_code` or `open_file` before `explore`.
- If `available_symbols` has relevant match:
  - do not choose `explore`.

This is a hard routing constraint, not a heuristic score.

---

## 6) Observability (lightweight, required)

Emit trace metadata for each exploration/planning cycle:

- `selector_expanded_symbols`
- `analyzer_required_symbols`
- `final_expansion_symbols`

Purpose:

- Fast diagnosis of signal drop points.
- Validate priority merge and deterministic expansion behavior.

---

## File-Level Change Plan

Schema:

- `agent_v2/schemas/exploration.py`
  - add `expanded_symbols` to `SelectorBatchResult`
  - add `required_symbols` to `UnderstandingResult`
  - add engine state field for pending expansion symbols (if needed in `ExplorationState`)

Selector:

- `agent_v2/exploration/candidate_selector.py`
  - populate `expanded_symbols` from validated selector symbols

Analyzer:

- `agent_v2/exploration/understanding_analyzer.py`
  - parse `required_symbols`
  - fallback extraction when gaps exist and list is empty

Prompts:

- `agent/prompt_versions/exploration.analyzer/v1.yaml`
- `agent/prompt_versions/exploration.analyzer/models/qwen2.5-coder-7b/v1.yaml`
  - include optional `required_symbols` field in JSON output contract

Engine integration:

- `agent_v2/exploration/exploration_engine_v2.py`
  - priority merge (`required` then `expanded`)
  - deterministic cap/order
  - pass symbols into graph expansion path
  - attach observability metadata

Planner context + prompt:

- `agent_v2/schemas/planner_plan_context.py`
  - add `available_symbols`, `missing_symbols`
- `agent_v2/planner/planner_v2.py`
  - populate these fields from latest exploration state/result
- `agent/prompt_versions/planner.decision.act/v1.yaml`
  - add explicit constraints for non-empty `missing_symbols` and relevant `available_symbols`

---

## Expected End State

Signal flow is explicit and preserved:

- Selector -> `expanded_symbols` (what exists)
- Analyzer -> `required_symbols` (what is missing)
- Engine -> `final_expansion_symbols` (priority-aware, deterministic)
- Graph retrieval -> focused expansion
- Planner -> grounded on `available_symbols` + `missing_symbols`
- Decision -> `open_file/search_code` preferred, `explore` fallback only

If any stage fails, observability fields expose where the signal dropped.

---

## Planner Prompt Patch (Verbatim)

Apply this patch to planner decision prompt text.

Placement:

- Insert AFTER `TOOL SELECTION RULES`.
- Do not modify existing rules.

```text
DECISION PRIORITY (STRICT — FOLLOW IN ORDER):

You MUST evaluate actions in this order:

1. STOP
   - If CURRENT UNDERSTANDING + KEY FINDINGS answer the instruction
   - OR no meaningful action remains
   → choose "stop"

2. ACT (PREFERRED OVER EXPLORE)
   - If a concrete next step exists using available repository context
   - Especially when:
     - a file path is known → use open_file
     - relevant symbols/functions are known → use search_code
   → choose "act"

3. REPLAN
   - If current approach has failed or is invalid

4. EXPLORE (LAST RESORT ONLY)
   - ONLY if no other action is possible
   - ONLY when a critical knowledge gap blocks progress

You MUST NOT skip higher-priority options.

--------------------------------

AVAILABLE vs MISSING SIGNALS (IMPORTANT):

CONTEXT may include:

- available_symbols: symbols/functions already discovered
- missing_symbols: symbols/functions required but not yet retrieved

You MUST use them as follows:

- If relevant available_symbols exist:
  → DO NOT explore
  → use open_file or search_code

- If missing_symbols exist:
  → prefer search_code first
  → use explore ONLY if repository search cannot resolve them

--------------------------------

DO NOT EXPLORE IF:

- The answer is already derivable from current findings
- A file path or symbol is already known
- search_code or open_file is sufficient
- The instruction is empty, invalid, or out of scope

In these cases:
→ choose "act" or "stop"

--------------------------------

EXPLORE QUERY RULE (STRICT):

If decision = "explore":

- Query MUST target missing information
- Query MUST NOT repeat the instruction verbatim
- Query MUST include concrete signals:
  - class name, function name, or file/module hint

Bad:
"How does this work?"

Good:
"ExplorationScoper implementation in agent_v2"

--------------------------------

TOOL BINDING (STRICT):

- "explore" → tool MUST be "explore"
- "act" → tool MUST be one of:
  open_file | search_code | run_shell | edit | run_tests
- "stop" or "replan" → tool MUST be "none"

--------------------------------

SCHEMA CLARIFICATION (CRITICAL):

- tool = "search_code"
- step.action = "search"

DO NOT use "search_code" as step.action
```

