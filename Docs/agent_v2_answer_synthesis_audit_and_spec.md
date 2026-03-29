# Agent V2 — Answer Synthesis Audit & Implementation Spec

This document captures a codebase-grounded audit for introducing **AnswerSynthesizer**, plus **required improvements** so the component is not a shallow formatter, and a **locked V1 insertion point**.

---

## 1. System reality (what actually happens)

- **Exploration** (`ExplorationEngineV2.explore` → `ExplorationResultAdapter.build`) produces **`FinalExplorationSchema`**: `evidence` (`ExplorationItem` with `snippet`, `read_source`, summaries), `relationships` (from expansion graph), `exploration_summary` (`overall`, `key_findings`, `knowledge_gaps`), `metadata` (`completion_status`, `termination_reason`, `engine_loop_steps`, `source_summary`, …), discrete `confidence`, `query_intent`, optional `key_insights` / `objective_coverage` when optional LLM synthesis runs (`exploration_llm_synthesizer.py`).
- **`UnderstandingResult`** (analyzer) drives **control** via `EngineDecisionMapper` inside the engine loop; **planner-facing output does not carry the full `UnderstandingResult` object**—evidence in memory uses short summaries; adapter **`key_findings`** are built largely from **first evidence items’ `content.summary`** in `_exploration_summary_for_schema4`, not from a dedicated causal-analysis field (`exploration_result_adapter.py`).
- **Planner** (`PlannerTaskRuntime`): receives **`PlannerPlanContext`** from **`FinalExplorationSchema`**. Controller loop can `stop`, `act`, `explore`, `replan`, `progress`. **`stop`** exits **without** any answer synthesis step.

---

## 2. Audit extracts (structured)

### 2.1 Current stop state

```json
{
  "available_inputs_at_stop": {
    "exploration": "FinalExplorationSchema on state.exploration_result and state.context['exploration_result']",
    "exploration_summary_text": "state.context['exploration_summary_text'] — exploration_summary.overall only",
    "planner_artifacts": "PlanDocument on state after loop",
    "session_memory": "SessionMemory in state.context['planner_session_memory']",
    "analyzer_in_planner_contract": "Not as UnderstandingResult; only evidence/gaps/relationships via adapter",
    "tool_logs": "Plan step execution.last_result; trace emitter — not one unified object",
    "code_snippets": "ExplorationItem.snippet (capped in adapter build)"
  },
  "missing_inputs": [
    "Single user-facing final answer string",
    "Explicit causal-chain / verification checklist object",
    "Raw per-step UnderstandingResult history on FinalExplorationSchema"
  ]
}
```

### 2.2 Analyzer output vs planner contract

```json
{
  "is_sufficient_for_answering": false,
  "gaps": [
    "UnderstandingResult.semantic_understanding / relationship_strings are not first-class on FinalExplorationSchema",
    "Adapter key_findings are mostly evidence summary lines, not structured causal analysis",
    "Uncertainties appear via knowledge_gaps and confidence band, not a dedicated uncertainty model"
  ],
  "note": "UnderstandingResult still has rich fields in schemas/exploration.py; downstream flattens to memory + adapter."
}
```

### 2.3 Planner stop behavior

```json
{
  "stop_triggers": [
    "Controller decision.type == 'stop'",
    "plan_executor.run_one_step returns success (finish)",
    "planner_controller_calls budget exhausted",
    "sub_exploration / explore gates"
  ],
  "is_premature_stop_common": true,
  "missing_stop_conditions": [
    "No mandatory synthesize-answer step before user-visible return",
    "exploration_allows_direct_plan_input can allow planning despite weak termination when allow_partial + reason allowlist"
  ]
}
```

### 2.4 Verification

```json
{
  "verification_present": true,
  "where_it_breaks": [
    "Operational enforcement (e.g. read_snippet policy), not a separate 'answer verified' gate",
    "No required cross-file consistency check before planner stop",
    "_sub_exploration_gates_ok checks gaps or low confidence — not tool-based proof"
  ],
  "impact_on_answer_quality": "Risk of plans or reads without a consolidated cited narrative."
}
```

### 2.5 Answer data readiness

```json
{
  "ready_for_synthesis": true,
  "missing_signals": [
    "unified narrative (instruction → conclusion)",
    "explicit coverage of user question vs exploration breadth",
    "per-sentence grounding to evidence IDs (partially inferable)",
    "full analyzer semantics unless explicitly passed through"
  ]
}
```

### 2.6 Hypotheses (validated)

| ID | Verdict | Summary |
|----|---------|--------|
| H1 | Valid | Data often retrieved; gap is synthesis / narrative. |
| H2 | Valid | Analyzer usable but flattened before planner. |
| H3 | Partial | Early exploration stop + planner can still proceed with insufficiency paths. |
| H4 | Valid | Synthesis alone insufficient without coverage/stop signals in the prompt. |

---

## 3. Required input contract (AnswerSynthesizer)

**Minimum viable payload:**

```json
{
  "instruction": "...",
  "key_findings": [],
  "understanding": "derived from exploration_summary + evidence (unless extended)",
  "evidence": [],
  "confidence": "...",
  "coverage": "to be augmented — see §5",
  "open_questions": [],
  "relationships": [],
  "query_intent": null
}
```

**Must explicitly include for v1 (do not rely on `key_findings` alone):**

- **`evidence`** — full `FinalExplorationSchema.evidence` (snippets, file refs, summaries).
- **`relationships`** — `FinalExplorationSchema.relationships` (graph edges).
- **`knowledge_gaps`** — `exploration_summary.knowledge_gaps` (+ empty reason when applicable).

---

## 4. Output requirements

```json
{
  "output_format": {
    "direct_answer": "string",
    "structured_explanation": "sections or markdown",
    "citations": "list of evidence refs (item_id / file / symbol)",
    "uncertainty": "string | null when gaps or weak coverage"
  },
  "must_have_sections": ["Answer", "Supporting evidence", "Gaps / limitations (if any)"]
}
```

---

## 5. Improvements (mandatory for quality)

### 5.1 Don’t make AnswerSynthesizer “just formatting”

**Risk:** Treating it as a pretty-printer over `key_findings` and short summaries.

**Why it fails:**

- `key_findings` ≠ reasoning.
- Analyzer semantics are **partially lost** in the adapter path.

**Fix (minimal, high impact):**

- Pass **relationships + evidence + gaps explicitly** into the synthesizer input (structured fields, not only a single prose blob).
- Prompt the model to **reconstruct reasoning** from evidence and relationships (what connects to what, why it answers the instruction), **not** to restate `key_findings` verbatim.

If this is skipped, answers stay shallow even when retrieval is good.

---

### 5.2 Lightweight coverage signal **before** synthesis

**Problem:** Synthesis can run on **partial / weak** exploration; `termination_reason` may indicate low quality (`stalled`, budget exhaustion, etc.) while evidence still exists.

**Fix:**

- Derive a simple discrete **`coverage`** flag for the synthesizer prompt:

  `coverage ∈ { sufficient | partial | weak }`

  (Derived from e.g. `metadata.completion_status`, `metadata.termination_reason`, `confidence`, evidence count, presence of non-empty gaps—exact mapping is implementation detail, but must be **deterministic** and **documented**.)

- **Feed `coverage` into the synthesizer prompt** so the model is instructed to avoid overconfident answers when `partial` or `weak`.

This prevents confident-sounding wrong answers without a full system redesign.

---

### 5.3 Lock the insertion point (V1)

**Ambiguity to avoid:** “Post-exploration OR post-planner” invites duplicate hooks and inconsistent products.

**Decision — V1 (locked):**

| | |
|--|--|
| **Where** | Immediately **after exploration** completes and **`FinalExplorationSchema`** is available, **before** the planner runs. |
| **Why** | Clean separation; stable contract; **no** coupling to executor or planner loop; easy to test. |
| **V2 (future)** | Optional second synthesizer **after** planner / tools if product needs executor-aware answers—**additive**, not a replacement for the V1 contract. |

**Do not** place synthesis inside the exploration engine, inside the analyzer, or as the planner’s primary JSON output.

---

## 6. Minimal implementation plan (reference)

1. Define **`AnswerSynthesisInput`** (Pydantic): `instruction`, `exploration: FinalExplorationSchema`, **`coverage`** (enum), explicit slices for evidence / relationships / gaps if not only nested access.
2. Implement **`AnswerSynthesizer.synthesize`** with prompts that require **reasoning reconstruction** and **coverage-aware** tone.
3. **Single call site for V1:** `PlannerTaskRuntime` immediately after `exploration_runner.run` (before `call_planner_with_context`), gated by env flag; store result on **`state.context`** (e.g. `answer_synthesis` or `final_answer`).
4. Route LLM calls through the **existing model client / router** (project execution rules).
5. Surface in CLI/UI when present; keep `exploration_summary_text` for backward compatibility.

---

## 7. What NOT to change

- Do **not** redesign the exploration engine.
- Do **not** push final user-facing synthesis into the **planner** JSON as the primary mechanism.
- Do **not** rework the **analyzer** to own “the answer”; keep hypothesis/control separation.
- Do **not** merge AnswerSynthesizer with the optional **`exploration.result_llm_synthesis`** path without a clear product decision—they serve different scopes (insights/coverage vs full answer).

---

## 8. Related code references

- `agent_v2/exploration/exploration_result_adapter.py` — `FinalExplorationSchema` build, `_exploration_summary_for_schema4`
- `agent_v2/runtime/planner_task_runtime.py` — exploration → planner orchestration
- `agent_v2/runtime/exploration_planning_input.py` — `exploration_to_planner_context`
- `agent_v2/schemas/final_exploration.py` — `FinalExplorationSchema`
- `agent_v2/schemas/exploration.py` — `UnderstandingResult`, `ExplorationSummary`
