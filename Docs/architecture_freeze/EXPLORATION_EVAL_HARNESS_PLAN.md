# Exploration Eval Harness Plan (Minimal, Trace-Based)

**Goal:** Validate exploration **behavior** (expand/refine/stop, memory feedback, scoper influence, loop health), not just final text outputs.

**Constraints honored:**
- No new framework
- No new execution infrastructure
- Trace-first behavioral checks
- Small, modular, incremental

---

## 1) Audit: Current Trace Surfaces (What We Can Reuse)

Existing code already exposes most required behavior signals inside `ExplorationEngineV2` and related components.

### Trace capture points (current system)

1. **Analyzer output**
   - Produced by `UnderstandingAnalyzer.analyze(...)` in loop.
   - Contains: `relevance`, `sufficient`, `knowledge_gaps`, `summary`, `confidence`.
2. **Decision object**
   - `EngineDecisionMapper.to_exploration_decision(...)` output.
   - Potentially overridden by `_apply_gap_driven_decision(...)`.
3. **Action taken**
   - Derived by `_next_action(...)`, then altered by:
     - `_apply_refine_cooldown(...)`
     - oscillation guard
     - refine->expand coercion logic from memory relationship gaps.
   - Executed via `_should_expand(...)` / `_should_refine(...)`.
4. **Tool calls**
   - Discovery: `_run_discovery_traced(...)` -> `dispatcher.search_batch(...)`.
   - Inspect: `_inspection_reader.inspect_packet(...)` -> bounded read tool.
   - Expand: `_graph_expander.expand(...)`.
5. **Memory updates**
   - `ExplorationWorkingMemory` updates from discovery, inspection/analyzer, expand relationships, gaps.
   - `memory.get_summary()` already returns compact evidence/relationships/gaps.
6. **Final result**
   - `ExplorationResultAdapter.build(...)` output as `FinalExplorationSchema`.
   - Metadata includes termination/completion signals.

### Existing hooks/logs to leverage

- **Langfuse spans/events**
  - Spans: `exploration.*` (`query_intent`, `discovery`, `inspect`, `analyze`, `expand`, `scope`, `select`).
  - Events: routing, context feedback, utility signal, gap filter, query refinement, refine cooldown/coercion.
- **Structured internal logging**
  - `_log_exploration_context_feedback_trace(...)`
  - discovery and coercion logs.
- **Existing eval precedent**
  - `scripts/live_expansion_refinement_loop_eval.py` already monkeypatches narrow methods and captures loop telemetry.
  - This is the right baseline pattern for a minimal harness.

---

## 2) Eval Case Structure (Minimal Schema)

Use lightweight dataclass/Pydantic (no framework). Keep fields behavior-centric.

```python
@dataclass
class EvalCase:
    id: str
    instruction: str
    focus_area: str  # expand | refine | memory_feedback | scoper | failure
    expected_behavior: dict[str, Any]
    scripted_signals: dict[str, Any] | None = None
```

`expected_behavior` shape:

```python
{
  "expected_actions": ["expand", "expand", "stop"],  # order-sensitive when needed
  "step_expectations": {
    "step_1": ["must_expand"],
    "step_2": ["must_not_refine"],
  },
  "expected_patterns": [
    "must_expand_on_caller_gap",
    "must_refine_on_wrong_target",
    "memory_must_influence_next_decision",
    "must_avoid_repeated_queries",
  ],
  "max_loop_depth": 6,   # optional
}
```

Notes:
- Keep schema intentionally small.
- `step_expectations` is the minimal granularity upgrade for precise failure localization.
- `scripted_signals` optionally drives deterministic analyzer/parser outputs in offline harness mode.

---

## 3) Trace Capture Design

### Function contract

```python
def run_eval_case(case: EvalCase) -> Trace
```

### Trace object (per-step behavioral trace)

```python
{
  "case_id": "...",
  "steps": [
    {
      "step_index": 0,
      "analyzer": {...},         # relevance/sufficient/gaps/summary/confidence
      "decision_pre_override": {...},
      "decision_post_override": {...},
      "action_selected": "expand|refine|stop",
      "action_executed": "expand|refine|stop|none",
      "decision_execution_alignment": {
        "status": "aligned|diverged_explainable|diverged_unexpected",
        "reason": "e.g. depth cap / no symbol / backtrack cap / guard"
      },
      "tool_calls": [
        {"phase": "discovery", "tool": "search", "query_count": 3},
        {"phase": "inspect", "tool": "read_snippet", "target": "..."},
        {"phase": "expand", "tool": "graph_query", "symbol": "..."},
      ],
      "memory_summary": {
        "evidence_count": 4,
        "gap_count": 2,
        "gap_delta": -1,
        "gap_trend": "decreasing|stagnant|increasing",
        "relationship_count": 3,
        "gaps": [...],
      },
      "query_signature": "...",  # for repeated query detection
    }
  ],
  "final_output": {
    "termination_reason": "...",
    "completion_status": "...",
    "result_summary": "...",
  }
}
```

### Minimal capture strategy

- Reuse current monkeypatch/wrapper approach (as done in live loop eval):
  - wrap `_next_action`, `_apply_gap_driven_decision`, `_should_expand`, `_should_refine`, `_run_discovery_traced`, `_update_utility_and_should_stop`.
- Read `engine.last_working_memory.get_summary()` after each step (or at action boundaries) for memory feedback validation.
- Keep one `TraceCollector` helper with append-only records.

No changes to core architecture required.

---

## 4) Grader Design (Critical) — Three-Layer Stack

The harness uses **three layers**. Rule-based and structural graders are deterministic; the LLM judge is **mandatory** semantic validation. The LLM does **not** replace rules or structure.

| Layer | Role |
|-------|------|
| **1. Rule-based** | Hard correctness (gaps, actions, alignment, step expectations) |
| **2. Structural** | System behavior (loops, redundancy, gap deltas, hygiene) |
| **3. LLM judge** | Semantic validation on a **condensed** trace only |

### A) Rule-based grader (hard behavioral assertions)

Examples:
- If analyzer gap contains caller signal -> at least one subsequent executed action must be `expand`.
- If decision indicates `wrong_target` -> next action must be `refine` (unless documented coercion rule applies).
- If `expected_actions` provided -> compare exact prefix match.
- If `step_expectations` provided -> evaluate per-step assertions (`must_expand`, `must_not_refine`, etc.).
- Decision-vs-execution alignment check:
  - `decision_post_override.next_action` must align with `action_executed`
  - OR divergence must be explicitly explainable by deterministic guard rails.

Output:

```python
{"pass": bool, "checks": [{"name": "...", "pass": bool, "details": "..."}]}
```

### B) Structural grader (loop/memory hygiene)

Checks:
- Memory influence present:
  - decision/action changes after memory gaps/relationships accumulate.
- Repeated query avoidance:
  - no excessive duplicate intent signatures/discovery query sets.
- No obvious loop pathology:
  - loop depth <= threshold, no stagnant repeated action bursts.
- Gap delta classification (no scoring):
  - classify per-case trend as `decreasing | stagnant | increasing`.

Output same pass/checks format.

### C) Mandatory LLM judge (semantic evaluator)

The LLM judge is **not** optional. It is the third grader layer and participates in **CI gating** (see §4b).

**Responsibilities (evaluate only; no generation):**

- Whether **actions align with gaps** at each step (semantic fit, not duplicate of rule checks).
- Whether **reasoning is consistent with memory updates** (evidence/gaps/relationships trajectory).
- Whether the system **avoided redundant exploration** (repeated useless pivots vs necessary backtracking).
- Whether the **trajectory shows progress** toward resolving gaps (even if termination is early).

**The LLM judge does NOT:**

- Generate answers to the user instruction.
- Suggest next actions or tool calls.
- Explore the repo or call tools.

It **only** evaluates the provided trace artifact.

**Input to the LLM judge (structured, condensed — not raw logs):**

- `instruction`
- `expected_behavior` (including `expected_patterns`, `step_expectations` as relevant)
- Per-step **summaries** only:
  - action taken (executed)
  - gaps at step (short)
  - memory changes (delta summary: e.g. new gaps, reduced count, new relationships)
- Final outcome (termination, completion, brief result summary)

Do **not** pass full raw Langfuse dumps, full prompts, or unbounded log text.

**Output schema (deterministic — structured JSON, not free-form essays):**

```json
{
  "semantic_alignment": "correct | partial | incorrect",
  "decision_quality": "good | weak | poor",
  "loop_behavior": "efficient | redundant | stuck",
  "gap_handling": "resolved | partially_resolved | not_resolved",
  "final_verdict": "pass | fail",
  "reason": "short explanation"
}
```

**Consistency constraint (mandatory):** The judge must explicitly assess whether observed behavior **matches `expected_patterns`** (and related expectations in `expected_behavior`). Verdicts must not rest on vague “looks reasonable”; failure to align with stated patterns should drive `final_verdict: fail` when patterns are materially violated.

**Guards against LLM drift:**

- Sampling **temperature** in `0–0.2` (low).
- **Fixed prompt template** (deterministic structure; version the prompt key).
- **No chain-of-thought** required in the output; only the schema above.

Parse and validate LLM output against the schema; treat parse/validation failure as **fail** for that case.

### 4b) Final case result (CI signal)

A case passes the harness only when **all three** layers pass:

```text
final_case_pass =
  rule_based_pass
  AND structural_pass
  AND (llm_judge.final_verdict == "pass")
```

The LLM judge is **gating**, not advisory-only logging.

---

## 5) Initial Eval Suites (4 minimal suites, 3-5 cases each)

Keep to 3 cases per suite for first cut (12 cases total).

1. **`expand_cases`**
   - Caller gap should trigger expand
   - Callee/flow gap should trigger expand
   - Gap-driven expand direction routing behavior

2. **`refine_cases`**
   - Wrong-target must refine
   - Low-relevance refine path
   - Definition/config gap leads refine (not expand)

3. **`memory_feedback_cases`**
   - Memory gaps alter next action
   - Relationships in memory coerce refine->expand
   - Query refinement uses context feedback and reduces repetition

4. **`failure_cases`**
   - Stagnation/no-improvement early stop
   - Duplicate target/query loop avoidance
   - Pending exhausted with unresolved gaps behavior

---

## 6) Metrics (Simple, No Composite Scoring)

Track only:

- **Action correctness rate**
  - `% expected rule checks satisfied across cases`
- **Loop depth**
  - `steps per case`, with max/avg
- **Repeated queries**
  - count of repeated query signatures beyond first occurrence
- **Gap trend (delta-based)**
  - classify each case as `decreasing`, `stagnant`, or `increasing` from step-wise `gap_delta`
- **Gap resolution success**
  - cases where trend is `decreasing` and run terminates with non-failure reason

No weighted score, no benchmark framework.

---

## 7) Minimal Runner Design

Single script + tiny modules, built on existing exploration components.

Suggested shape:

- `scripts/exploration_behavior_eval.py`
  - load suites
  - `run_eval_case`
  - run rule-based → structural → **mandatory LLM judge** (condensed trace in)
  - compute `final_case_pass` per §4b
  - print compact JSON/markdown report
- `tests/behavior/test_exploration_behavior_eval.py`
  - smoke test on 1-2 deterministic cases
- optional fixtures:
  - `tests/fixtures/exploration_eval_cases.py`

Execution mode:

1. **Deterministic scripted mode with mandatory LLM judge**
   - patched parser/analyzer/selector signals.
   - LLM judge always-on and CI-gating via `final_case_pass`.

---

## 8) Incremental Implementation Steps

1. Extract `TraceCollector` from current live eval pattern.
2. Implement `EvalCase` + 3 suites (`expand/refine/memory_feedback`) with 3 cases each.
3. Add `failure_cases` suite with 3 loop-pathology cases.
4. Implement rule-based + structural graders (strictly deterministic).
5. Add metrics aggregation/report (`action correctness`, `loop depth`, `repeats`, `gap resolution`).
6. Implement **mandatory** LLM semantic judge: condensed trace input, fixed schema output, low temperature, `expected_patterns` consistency check in prompt contract.
7. Wire `final_case_pass` (rules AND structural AND `llm_judge.final_verdict == "pass"`) as the CI signal.
8. Wire into one lightweight script command; keep tests minimal.

---

## 9) Final Plan Output (Requested Format)

```json
{
  "trace_capture_points": [
    "UnderstandingAnalyzer.analyze output (relevance/sufficient/gaps/summary/confidence)",
    "EngineDecisionMapper decision and post _apply_gap_driven_decision decision",
    "_next_action result and final executed branch (_should_expand/_should_refine)",
    "Tool calls from discovery/search, inspect/read_snippet, expand/graph_query",
    "ExplorationWorkingMemory summaries per step (evidence/gaps/relationships)",
    "FinalExplorationSchema metadata (termination_reason, completion_status, summary)"
  ],
  "eval_case_schema": {
    "EvalCase": {
      "id": "str",
      "instruction": "str",
      "focus_area": "str",
      "expected_behavior": {
        "expected_actions": ["expand|refine|stop"],
        "step_expectations": {
          "step_n": ["must_expand|must_refine|must_not_refine|must_stop"]
        },
        "expected_patterns": ["behavior rules"],
        "max_loop_depth": "int (optional)"
      },
      "scripted_signals": "dict | None"
    }
  },
  "grader_design": {
    "rule_based": [
      "caller gap => expand must occur",
      "wrong_target => refine must occur (unless explicit coercion rule)",
      "expected_actions prefix check where provided",
      "step_expectations checks for step-level debuggability",
      "decision_execution_alignment: decision == action_executed OR explainable divergence"
    ],
    "structural": [
      "memory must influence later decision/action",
      "avoid repeated query signatures",
      "detect stagnation/loop pathology via repeated actions and depth",
      "gap_delta trend classification: decreasing|stagnant|increasing"
    ],
    "llm_judge_mandatory": [
      "semantic evaluator only: actions vs gaps, reasoning vs memory, redundancy, progress toward gap resolution",
      "does NOT generate answers, suggest actions, or explore — evaluates trace only",
      "input: condensed structured trace (instruction, step summaries, final outcome, expected_behavior) — no raw log dump",
      "output: fixed JSON schema (semantic_alignment, decision_quality, loop_behavior, gap_handling, final_verdict pass|fail, reason)",
      "must assess alignment with expected_patterns (not vague plausibility)",
      "drift guards: temperature 0-0.2, deterministic prompt, no CoT in output",
      "gating: final_case_pass requires final_verdict == pass alongside rule_based and structural passes"
    ]
  },
  "final_case_pass": "rule_based_pass AND structural_pass AND llm_judge.final_verdict == \"pass\"",
  "eval_suites": [
    "expand_cases (3 cases)",
    "refine_cases (3 cases)",
    "memory_feedback_cases (3 cases)",
    "failure_cases (3 cases)"
  ],
  "minimal_runner_design": "Single script runner using monkeypatch/wrapper trace collection around ExplorationEngineV2 control points; deterministic scripted mode; mandatory LLM semantic judge; run three graders; final_case_pass gates CI; JSON-first report with simple aggregate metrics.",
  "implementation_steps": [
    "Build TraceCollector and run_eval_case(case)->Trace",
    "Define EvalCase schema + 4 suites",
    "Implement rule-based grader with step_expectations",
    "Implement structural grader with decision_execution_alignment and gap_delta trend",
    "Implement mandatory LLM judge with condensed input, schema-validated output, expected_patterns consistency, low temperature",
    "Emit final_case_pass and simple metrics/report",
    "Add one minimal CI-friendly behavior test covering three-layer pass"
  ]
}
```

---

## 10) Why This Is Minimal but Sufficient

- Reuses current engine and observability surfaces.
- Validates **decisions and transitions**, not only final answer text.
- Creates deterministic behavior checks for expand/refine/memory/scoper pathways.
- Adds a **mandatory** semantic layer that still **does not** replace rules: CI requires rule + structural + LLM `final_verdict == pass`.
- Establishes a thin foundation for iterative tuning without architectural churn.

---

## 11) Implementation (repo)

| Artifact | Purpose |
|----------|---------|
| `agent_v2/exploration/exploration_behavior_eval_harness.py` | Trace capture, rule + structural graders, mandatory deterministic schema-validated `llm_judge_fn` using `call_reasoning_model`, `run_eval_case` / `run_eval_suite` |
| `tests/fixtures/exploration_behavior_eval_cases.py` | Four suites x 3 cases (expand / refine / memory_feedback / failure) |
| `scripts/exploration_behavior_eval.py` | CLI: JSON summary; exit 1 if any case fails; mandatory LLM judge always enabled |
| `tests/test_exploration_behavior_eval.py` | CI integration test: no stub judge; monkeypatches `call_reasoning_model` at model boundary for deterministic offline verification |
