# Prompt RCA Report — agent_v2 Pipeline vs Prompt Design Specification v1.0

**Date:** 2026-03-25  
**Scope:** All LLM-facing prompts in `agent_v2/`  
**Reference:** `Docs/architecture_freeze/PROMPT_DESIGN_SPECIFICATION.md`  
**Severity scale:** CRITICAL / HIGH / MEDIUM / LOW

---

## Executive Summary

Eight prompt surfaces were audited across the agent_v2 pipeline. Against the seven required prompt components defined in the spec, the pipeline shows **systemic structural debt** rather than isolated gaps. The two most severe findings are:

1. **Role definition is absent in 6 of 8 prompts** — the only exception is the planner. This directly degrades reasoning quality and decision consistency across the most frequent pipeline stages (exploration, scoping, selection, argument generation).

2. **"Relevant" is used where "necessary" is required** — four prompts use semantic framing ("likely relevant", "most relevant", "worth exploring") where the spec mandates causal framing ("directly solves", "necessary to answer"). This is the root cause of the overgeneralization failure mode described in spec §4.2.

A third cross-cutting issue is that the full ReAct prompt exists in an external registry outside `agent_v2/`, making it invisible to prompt audits and violating the observability principle (Architecture Rule 10).

---

## Prompt Inventory — Compliance Matrix

| Prompt | Role | Objective | Context | Constraints | Reasoning Directive | Output Contract | Verification |
|---|---|---|---|---|---|---|---|
| `understanding_analyzer` | ✗ MISSING | ✓ | ✓ | ✓ | ~ partial | ✓ | ✗ MISSING |
| `candidate_selector.select` | ~ functional | ✓ | ~ thin | ~ weak | ✗ MISSING | ✓ | ✗ MISSING |
| `candidate_selector.select_batch` | ~ functional | ✓ | ~ thin | ~ weak | ✗ MISSING | ~ ambiguous | ✗ MISSING |
| `exploration_scoper` | ~ functional | ✓ | ✓ | ~ mixed in | ✗ MISSING | ✓ | ✗ MISSING |
| `query_intent_parser` | ~ functional | ✓ | ✗ thin | ~ minimal | ✗ MISSING | ~ incomplete | ✗ MISSING |
| `planner_v2._build_exploration_prompt` | ✓ | ✓ | ✓ | ✓ | ~ implicit | ✓ | ~ embedded |
| `planner_v2._build_replan_prompt` | ✓ | ✓ | ~ conditional | ✓ | ~ weak | ✓ | ✗ MISSING |
| `plan_argument_generator` | ~ functional | ✓ | ✓ | ✓ | ✗ MISSING | ~ key names only | ✗ MISSING |

**Legend:** ✓ = satisfies spec / ~ = partial / ✗ = absent

**Overall pass rate per component:**

| Component | Pass | Partial | Fail |
|---|---|---|---|
| Role | 2 | 5 | 1 |
| Objective | 8 | 0 | 0 |
| Context | 4 | 3 | 1 |
| Constraints | 3 | 5 | 0 |
| Reasoning Directive | 0 | 2 | 6 |
| Output Contract | 5 | 3 | 0 |
| Verification | 0 | 1 | 7 |

---

## Per-Prompt Findings

---

### 1. `understanding_analyzer.py` — `UnderstandingAnalyzer.analyze`

**Pipeline stage:** Exploration — snippet analysis  
**Frequency:** Called once per explored location; high volume

#### Gaps

**[CRITICAL] GAP-UA-1 — Role missing**

The prompt opens with an imperative sentence: `"Analyze if the snippet is necessary to answer the instruction."` No role is declared. The spec (§2.1) requires an explicit role to establish reasoning depth and consistency.

Consequence: The model applies unspecified reasoning depth. At high call volumes (10–30 per exploration run) this introduces stochastic tone shifts — sometimes the model reasons as a code reviewer, sometimes as a text classifier, producing inconsistent `status` decisions across otherwise similar snippets.

---

**[HIGH] GAP-UA-2 — Causal vs semantic framing in `sufficient` condition**

The constraint `"sufficient" ONLY if the snippet directly implements or defines what the instruction asks` is causal and correct. However the overall task framing (`"Analyze if the snippet is necessary"`) sets up the right expectation.

The gap is that `"partial"` has no causal definition. The model is left to decide what constitutes "partial" without a causal test. This asymmetry (sufficient has a test; partial does not) means the model over-uses `"partial"` as a safe hedge, triggering unnecessary further exploration and inflating cost.

---

**[HIGH] GAP-UA-3 — Silent snippet truncation**

`snippet[:6000]` truncates the input without notifying the model. The model has no way to know the snippet was cut. This can cause the model to confidently conclude `sufficient` or `wrong_target` based on an incomplete view of the code, producing incorrect decisions that cascade downstream.

Consequence: False `sufficient` calls on truncated snippets halt exploration prematurely. False `wrong_target` calls waste exploration budget by re-routing to incorrect locations.

---

**[MEDIUM] GAP-UA-4 — Separation of concerns violated**

The output schema, constraints, and reasoning guidance are inline within a single paragraph. This contradicts spec §3.4. As the prompt evolves, each modification risks silently breaking another concern.

---

**[LOW] GAP-UA-5 — Verification criteria absent**

No `"Stop when..."` or `"This answer is complete if..."` guidance. The model terminates when it produces syntactically valid JSON, not when it reaches a correct conclusion. This is the standard silent failure for all non-planner prompts.

---

### 2. `candidate_selector.py` — `CandidateSelector.select` (single fallback)

**Pipeline stage:** Exploration — single candidate selection  
**Frequency:** Fallback path; lower volume

#### Gaps

**[HIGH] GAP-CS1-1 — Role missing**

`"You are selecting the most relevant code location."` is a functional description, not a role. The spec requires `"senior engineer"`, `"planner"`, `"analyzer"` or equivalent to establish reasoning behavior. Without it, the model does not calibrate what level of judgment to apply.

---

**[CRITICAL] GAP-CS1-2 — Semantic framing: "most relevant"**

`"most relevant"` is semantic. It asks the model to judge semantic closeness, not causal necessity. The spec (§4.2) names this as the direct cause of overgeneralization.

Real consequence: The model selects the file whose name or comments most closely echo the query keywords, rather than the file that is causally required to answer the instruction. This is the primary driver of unnecessary exploration hops.

---

**[HIGH] GAP-CS1-3 — Reasoning directive absent**

No `"Reason step-by-step"`, `"Verify before selecting"` or equivalent. The model produces an immediate selection without structured justification. Single-selection paths are the highest-risk places to skip reasoning directives because there is no rank to fall back on.

---

**[MEDIUM] GAP-CS1-4 — Constraint "Prefer implementation files over tests" is soft, not enforceable**

`"Prefer"` is advisory. The spec (§2.4) requires hard rules: enforceable, non-negotiable. The model can and does ignore soft preferences when test files appear more semantically aligned with the query.

---

### 3. `candidate_selector.py` — `CandidateSelector.select_batch`

**Pipeline stage:** Exploration — batch ranking  
**Frequency:** Primary path; high volume

#### Gaps

**[CRITICAL] GAP-CSB-1 — Output shape decision rule absent**

Two output shapes are defined:
- Shape 1: `{"selected": [...]}`
- Shape 2: `{"selected": [], "no_relevant_candidate": true}`

No rule defines when the model should choose shape 2. The model must infer this from context. The spec (§2.6) requires no ambiguity in output format. This produces inconsistent behavior: some runs return shape 2 when one valid candidate exists, others return shape 1 with marginal candidates.

---

**[HIGH] GAP-CSB-2 — `Limit` parameter mentioned but not enforced**

`"Limit: {limit}"` appears in the context block but no constraint says `"Return exactly N or fewer items."` There is no consequence stated for exceeding the limit. The model frequently returns more items than `limit` when candidates look broadly relevant.

---

**[CRITICAL] GAP-CSB-3 — Causal framing absent (same as GAP-CS1-2)**

`"Rank best-first"` and `"Prefer implementation files"` are both soft and semantic. No causal test is applied. The model ranks by semantic similarity to the instruction, not by causal necessity.

---

### 4. `exploration_scoper.py` — `ExplorationScoper._build_prompt`

**Pipeline stage:** Exploration — breadth reduction  
**Frequency:** Called once per exploration run; gate function

#### Gaps

**[CRITICAL] GAP-ES-1 — "Likely relevant" is the canonical overgeneralization trigger**

The core task directive: `"Return a subset of candidate indices that are likely relevant to solving the instruction."` This is the spec's §4.2 failure mode verbatim. `"likely relevant"` gives the model maximum latitude to include borderline candidates.

Consequence: The scoper is the breadth gate. Overly broad scoping multiplies downstream cost across selector, analyzer, and planner. One overgeneralized scoper decision can add 5–15 unnecessary analyzer calls.

---

**[HIGH] GAP-ES-2 — Constraint "Selecting all candidates is usually a mistake" is unenforceable**

`"Usually a mistake"` is not an enforceable rule. The model can rationalize selecting all candidates by concluding that all are relevant for a broad instruction. This defeats the scoper's purpose entirely.

A hard constraint would be: `"Select at most N candidates. If all appear related, prefer the top N most causally necessary."` with N derived from a configured cap.

---

**[HIGH] GAP-ES-3 — Role missing**

Same failure as UA, CS. The scoper makes gate-level decisions. Role determines whether the model applies strict engineering judgment or loose association.

---

**[MEDIUM] GAP-ES-4 — Guidelines, IMPORTANT block, and output format mixed**

Three concerns — filtering heuristics, index semantics, and output schema — are intermixed without clear section separation. The `IMPORTANT:` block appears after the guidelines block but before the data, creating a mid-prompt context shift that increases the risk of the model misapplying rules.

---

**[LOW] GAP-ES-5 — Reasoning directive absent**

No step-by-step or verify-before-output instruction. The scoper should evaluate each candidate independently before selecting — without a reasoning directive, it typically scores all candidates at once holistically.

---

### 5. `query_intent_parser.py` — `QueryIntentParser.parse`

**Pipeline stage:** Exploration — search intent extraction  
**Frequency:** Once per query; upstream gate

#### Gaps

**[CRITICAL] GAP-QI-1 — Output contract is structurally incomplete**

The prompt defines keys: `symbols, keywords, intents`. It does not define:
- The type of each value (`symbols`: array of strings? single string?)
- The expected number of items per key
- Whether keys are required or optional

The spec (§2.6) requires a strict schema with no ambiguity. Downstream, the retrieval pipeline consumes this output for vector and grep searches. Ambiguous types produce inconsistent search inputs, degrading retrieval quality.

---

**[HIGH] GAP-QI-2 — Context is a single instruction; no grounding**

This is the shortest prompt in the pipeline but operates at the highest-impact stage (search quality determines everything downstream). The context is one field: `{instruction}`. No examples, no constraints on what a "good" extraction looks like, no grounding on what counts as a symbol vs keyword in this codebase.

Consequence: For ambiguous instructions, the model assigns all terms to `keywords` and produces empty `symbols`, causing the retrieval to miss exact-match anchor results that would have constrained exploration.

---

**[HIGH] GAP-QI-3 — Role missing, reasoning directive absent**

Both are missing. For an intent extraction task, the model calibration matters: a "search specialist" produces very different extractions than an "engineer reading code". Without a role, the model defaults to a generic assistant mode.

---

**[MEDIUM] GAP-QI-4 — No causal clarity**

The prompt does not distinguish between extracting what the model thinks is relevant to the instruction vs what is causally required to answer it. `symbols` should only contain identifiers that directly resolve the instruction; instead the model includes adjacently related symbols.

---

### 6. `planner_v2.py` — `_build_exploration_prompt`

**Pipeline stage:** Planning  
**Frequency:** Once per query; critical path

#### Gaps

**[HIGH] GAP-PL-1 — Over-specification risk (spec §4.4)**

The planning prompt has 9 numbered requirements, plus conditional blocks for `deep_extra` and `task_mode_constraint`. This exceeds the minimality threshold. Requirements 7 (Evidence-use) and 8 (Tool-argument correctness) are validation rules that would be better enforced at the schema/code layer. Including them in the prompt produces verbosity and instability: the model attempts to satisfy 9 explicit rules simultaneously and frequently fails to prioritize correctly.

---

**[HIGH] GAP-PL-2 — `deep_extra` and `task_mode_constraint` are mid-prompt injections**

These conditional blocks are appended into the body of the requirements section, not cleanly separated. This violates separation of concerns (spec §3.4). When both are active, the model sees:

```
...requirement 9...
DEEP PLANNING: ...
⚠️ CRITICAL: This is a READ-ONLY task...
REQUIREMENTS: (continued)
```

Conditional injections mid-requirements cause the model to re-interpret earlier requirements in light of the late constraint, producing inconsistent plan structures.

---

**[MEDIUM] GAP-PL-3 — Reasoning directive is implicit, not explicit**

The requirement to plan step-by-step is embedded inside requirements (`"step_id"`, `"dependencies"` fields imply sequential structure) but never stated as a reasoning directive. The spec (§2.5) requires an explicit directive: `"Plan sequentially before outputting. Verify each step has a dependency chain before generating the next."` Without this, the model generates all steps in parallel reasoning and produces plans with inconsistent dependency graphs.

---

**[MEDIUM] GAP-PL-4 — `EXPLORATION SOURCES` statistical block adds noise**

```
EXPLORATION SOURCES:
- symbol reads: {ss_symbol}
- line reads: {ss_line}
- header reads: {ss_head}
```

These are retrieval statistics, not planning context. They consume tokens without contributing to planning quality. The spec (§2.3) requires only relevant information. This block should be removed or moved to a metadata section outside the prompt.

---

**[MEDIUM] GAP-PL-5 — Retry suffix appended to same prompt**

When JSON parsing fails, the retry suffix `"Your previous output was not valid JSON. Reply with ONE JSON object only, no prose or fences."` is appended to the original prompt and resubmitted. This means the model sees:

```
[full planning prompt with 9 requirements]
...
Your previous output was not valid JSON. ...
```

The correct pattern is a dedicated retry prompt with only the error context and output contract. Appending to the original prompt doubles the token cost and can confuse the model about which instructions take precedence.

---

### 7. `planner_v2.py` — `_build_replan_prompt`

**Pipeline stage:** Planning — failure recovery  
**Frequency:** Low; triggered on step failure

#### Gaps

**[HIGH] GAP-RP-1 — Reasoning directive for failure analysis is absent**

The only failure analysis instruction is: `"Address the failure and remaining work; do not repeat failed assumptions."` This is a constraint, not a reasoning directive. The spec (§2.5) would require: `"First, identify the root cause of the failure. Then determine which assumptions must change. Then rebuild only the affected steps."` Without causal failure analysis, the model often replans around the symptom rather than the cause.

---

**[HIGH] GAP-RP-2 — `explore_block` is inconsistently injected**

The replan prompt conditionally includes prior exploration summary. When absent, the model replans without codebase grounding — violating the architectural principle that retrieval must precede reasoning (Architecture Rule 2, spec §7.1). When present, the `explore_block` is placed mid-template without a clear section marker.

---

**[MEDIUM] GAP-RP-3 — No replanning-specific completion criteria**

The output requires `completion_criteria` inherited from the planning schema, but no instruction guides what different completion criteria should look like given that a step already failed. The model reuses the same generic criteria from the original plan.

---

**[MEDIUM] GAP-RP-4 — Evidence-use requirements (requirements 7–8 from planning) absent**

The replan prompt drops requirements 7 and 8 (evidence grounding, tool-argument correctness) from the planning prompt. This inconsistency means replanned steps are less constrained on file path accuracy and tool input correctness than initial plans — exactly when accuracy matters most.

---

### 8. `plan_argument_generator.py` — `PlanArgumentGenerator._build_prompt`

**Pipeline stage:** Execution — tool argument generation  
**Frequency:** Once per plan step; highest cumulative volume

#### Gaps

**[CRITICAL] GAP-AG-1 — Output contract provides key names without value schema**

`"REQUIRED JSON keys: {keys}"` injects a list of key names but provides no type, format, or constraint for each value. For example, for `open_file`, the key is `path` but the model does not know:
- Should this be absolute or relative?
- Must it match exactly a path from exploration results?
- Can it be a glob pattern?

Consequence: The model fills keys with plausible-looking but incorrect values (e.g., relative paths where absolute are required, or inferred paths not present in exploration results). This is a direct cause of tool execution failures downstream.

---

**[HIGH] GAP-AG-2 — "PLAN STEP INPUTS (hints, may be incomplete)" downgrades adherence**

Labeling the plan inputs as `"hints, may be incomplete"` signals to the model that these are optional guidance rather than grounding constraints. The model then supplements hints with inferred values, which is precisely the hallucination failure mode the spec (§2.3) guards against.

The correct framing is: `"PLAN STEP INPUTS: Use these as authoritative starting values. Only infer missing keys, never override provided values."`

---

**[HIGH] GAP-AG-3 — Reasoning directive absent**

No directive to verify argument correctness before output. For argument generation, the appropriate directive is: `"For each required key, first locate the value in PRIOR OBSERVATIONS or PLAN STEP INPUTS. Only if not found, infer from STEP GOAL. Never invent values."` Without this, the model generates arguments top-down from semantic understanding of the goal rather than evidence-first.

---

**[MEDIUM] GAP-AG-4 — `exploration_block` is an optional injection with no structure**

The `{exploration_block}` suffix is appended only when available. There is no section marker, no header, no instructions about how to use exploration data in argument generation. The model may not recognize it as authoritative grounding vs supplementary context.

---

**[LOW] GAP-AG-5 — Role missing**

Argument generation is a precise engineering task. The model should be calibrated as a "tool executor" or "engineer filling a typed function call", not a general assistant. Without role, the model applies creative interpretation to argument values.

---

## Cross-Cutting Findings

---

### XC-1 — Role Defined in 2 of 8 Prompts (CRITICAL)

Only `_build_exploration_prompt` and `_build_replan_prompt` declare `"You are a senior software engineer..."`. All other prompts use functional opening sentences (`"You are selecting..."`, `"You are extracting..."`, `"You are ranking..."`) which describe the task, not the reasoning identity.

The spec (§2.1) is unambiguous: role calibrates reasoning depth and decision consistency. Absence of role is the most common and highest-impact gap found.

**Affected prompts:** All except PlannerV2.

---

### XC-2 — "Relevant" Used Where "Necessary" Is Required (CRITICAL)

| Prompt | Semantic phrase | Required replacement |
|---|---|---|
| `exploration_scoper` | "likely relevant to solving" | "causally necessary to solve" |
| `candidate_selector.select` | "most relevant code location" | "most causally necessary location" |
| `candidate_selector.select_batch` | "no relevant candidate" | "no causally necessary candidate" |
| `understanding_analyzer` | (implicit in "partial" definition) | explicit causal test for partial |

The spec (§4.2) identifies this as the direct cause of overgeneralization. The word "relevant" permits semantic association. The word "necessary" enforces causal dependency. In a retrieval-grounded system, this distinction determines whether the model explores correctly or wanders.

---

### XC-3 — Reasoning Directive Missing in 6 of 8 Prompts (HIGH)

No prompt except the planner contains an explicit reasoning strategy. The spec (§2.5) requires minimal but sufficient guidance. The absence means every non-planner prompt relies on the model's default reasoning behavior, which produces inconsistent and shallow outputs at scale.

The fix is lightweight: a single sentence per prompt is sufficient. Examples:
- `"For each candidate, test: does this file contain the function that directly implements the instruction? Only if yes, include it."`
- `"Identify the value for each key from observations first. Infer only if absent."`

---

### XC-4 — Verification Criteria Absent in 7 of 8 Prompts (HIGH)

Only the planner embeds a `completion_criteria` field in the output contract. All other prompts terminate on syntactic JSON validity, not semantic correctness. The model has no mechanism to self-check its output.

The spec (§2.7) permits implicit validation. The minimum acceptable form is a closing line such as: `"Before returning, verify: does the selected location contain code that directly answers the instruction? If not, reconsider."` This single line is enough to trigger self-verification behavior.

---

### XC-5 — Separation of Concerns Violated in All Prompts (MEDIUM)

Every prompt mixes at least two of: constraints, output schema, context, and reasoning instructions in the same block without sectioning. The spec (§3.4) requires clear scoping of each concern.

The practical consequence is maintainability: when a bug is found in constraint logic, the engineer must carefully edit the middle of a mixed block, risking unintended side effects on adjacent output format rules.

---

### XC-6 — ReAct Prompt Registry Anti-Pattern (HIGH)

The full `react_action` prompt body lives in `agent.prompt_system` registry outside `agent_v2/`. The `bootstrap.py` file constructs the prompt via:

```python
get_registry().get_instructions("react_action", variables={...})
```

This means:
- A prompt audit of `agent_v2/` cannot see the full ReAct prompt
- Changes to the registry affect the ReAct prompt without touching `agent_v2/`
- Langfuse traces record the final assembled prompt, but the source of truth is invisible in the primary codebase path

This violates Architecture Rule 10 (every decision must be observable and traceable at source). The spec (§7.1) states that context quality determines output quality — you cannot improve what you cannot locate.

---

### XC-7 — Output Contract Completeness Varies Widely (MEDIUM)

| Prompt | Output contract completeness |
|---|---|
| `understanding_analyzer` | HIGH — inline JSON schema with value constraints |
| `candidate_selector.select` | HIGH — exact field set |
| `candidate_selector.select_batch` | MEDIUM — two shapes, no decision rule |
| `exploration_scoper` | HIGH — single array |
| `query_intent_parser` | LOW — key names only, no types |
| `planner_v2` | HIGH — full nested schema |
| `plan_argument_generator` | LOW — key names only, no value schema |

The two lowest-scoring prompts (`query_intent_parser`, `plan_argument_generator`) are at the retrieval input stage and the execution stage respectively — the two positions where output quality has the highest downstream impact.

---

## Consequence Summary

| Gap | Failure Mode (per spec §4) | Observable Symptom |
|---|---|---|
| XC-1: Role absent | Shallow reasoning (§2.1) | Inconsistent status decisions across similar snippets; variable plan depth |
| XC-2: Semantic framing | Overgeneralization (§4.2) | Excess exploration hops; scoper includes unrelated files; cost inflation |
| XC-3: No reasoning directive | Semantic Drift (§4.1) | Model selects by surface-level keyword match, not logic path |
| XC-4: No verification | Output Leakage (§4.3) | Syntactically valid but semantically wrong outputs pass downstream |
| GAP-UA-3: Silent truncation | Hallucination (§2.3) | False `sufficient` or `wrong_target` on incomplete snippets |
| GAP-QI-1: Incomplete output type schema | Parsing failures (§2.6) | Search inputs are untyped arrays or raw strings interchangeably |
| GAP-AG-1: No value schema | Invalid tool inputs (§2.6) | Tool execution failures from wrong path format or inferred values |
| GAP-AG-2: Hints framing | Hallucination (§2.3) | Model supplements hints with invented file paths |
| GAP-PL-1: Over-specification | Over-constraint (§4.4) | Model prioritizes later rules over earlier; inconsistent plan structure |
| GAP-PL-5: Retry suffix pattern | Output Leakage (§4.3) | Model reinterprets full requirements on retry; doubled token cost |
| XC-6: Registry anti-pattern | Non-observable (Architecture Rule 10) | Cannot audit, version, or improve the ReAct prompt from codebase |

---

## Prioritized Remediation

### P0 — Fix immediately (systemic, high impact)

1. **Add role declarations to all 6 missing prompts.** One sentence each. Use `"You are a senior software engineer..."` as the baseline.
2. **Replace "relevant" with "necessary" in all exploration-stage prompts.** Add an explicit causal test sentence: `"Necessary means: this file/location contains code that directly implements the logic required by the instruction."`
3. **Complete the output contract for `query_intent_parser`.** Add type schema: `symbols: string[], keywords: string[], intents: string[]` with value constraints.
4. **Complete the output contract for `plan_argument_generator`.** Provide a per-action value schema for each key (path format, query type, etc.) or inject from the tool registry.

### P1 — Fix in next iteration (high impact, moderate effort)

5. **Add one-sentence reasoning directive to all non-planner prompts.** Evidence-first pattern: "For each output field, locate the value in provided context before inferring."
6. **Add inline verification line to all prompts.** Minimum: `"Before returning, verify: [condition]. If not satisfied, revise."`
7. **Move `react_action` prompt into `agent_v2/` or make the registry entry a versioned, auditable file.**
8. **Fix `plan_argument_generator` hints framing.** Rename to `"AUTHORITATIVE INPUTS"` and add `"Do not override provided values."`
9. **Notify model of snippet truncation in `understanding_analyzer`.** Add: `"Note: snippet may be truncated at 6000 characters."` with adjusted confidence guidance.

### P2 — Structural refactoring (medium impact, higher effort)

10. **Enforce separation of concerns across all prompts.** Use consistent section headers: `ROLE`, `TASK`, `CONTEXT`, `CONSTRAINTS`, `OUTPUT FORMAT`, `VERIFICATION`.
11. **Remove `EXPLORATION SOURCES` statistical block from planner prompt.** Move to trace metadata.
12. **Extract retry logic from `_call_llm` into a dedicated retry prompt** instead of appending to original.
13. **Add `explore_block` presence/absence invariant to replanner.** When no exploration exists, the replan prompt must inject a hard constraint: `"Do not reference file paths not present in COMPLETED STEPS."`
14. **Add explicit selection decision rule to `select_batch` output shape.** `"Use shape 2 only when zero candidates satisfy the causal necessity test."`

---

## Spec Alignment Score (Pre-Remediation)

| Dimension | Score | Max |
|---|---|---|
| Role coverage | 2/8 | 8 |
| Objective clarity | 8/8 | 8 |
| Context sufficiency | 5/8 | 8 |
| Constraint explicitness | 5/8 | 8 |
| Reasoning directive | 0/8 | 8 |
| Output contract completeness | 5/8 | 8 |
| Verification presence | 0/8 | 8 |
| **Total** | **25/56** | **56** |
| **Score** | **44%** | |

---

*Generated against `PROMPT_DESIGN_SPECIFICATION.md` v1.0. All gap IDs are stable references for tracking remediation.*
