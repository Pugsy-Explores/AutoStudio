# Exploration Refine/Expand Audit (RCA)

Scope audited from code paths in:
- `agent_v2/exploration/exploration_engine_v2.py`
- `agent_v2/exploration/graph_expander.py`
- `agent_v2/exploration/understanding_analyzer.py`
- `agent_v2/exploration/decision_mapper.py`
- `agent_v2/exploration/inspection_reader.py`
- `agent_v2/runtime/dispatcher.py`
- `agent_v2/exploration/exploration_working_memory.py`
- `agent_v2/exploration/read_router.py`
- `agent_v2/schemas/exploration.py`

---

## Step 1 - Exact Loop Structure

```json
{
  "loop_structure": [
    "1) Parse intent via intent_parser.parse(instruction).",
    "2) Run initial discovery via _run_discovery_traced -> _discovery -> dispatcher.search_batch(symbol|regex|text).",
    "3) Ingest discovery candidates into working memory (tier=2), then _enqueue_ranked (scoper optional, selector mandatory) to seed pending_targets.",
    "4) Iterate while steps_taken < EXPLORATION_MAX_STEPS:",
    "   a) Pop next pending target; skip duplicate (canonical file_path + symbol) via explored_location_keys.",
    "   b) Pre-stop gate _should_stop_pre().",
    "   c) Inspect target via inspection_reader.inspect_packet -> dispatcher.execute(READ/read_snippet).",
    "   d) Enforce bounded read tool (metadata.tool_name must be read_snippet) else terminate policy_violation_full_read.",
    "   e) Build evidence delta key (canonical_path, symbol, read_source) and decide meaningfulness.",
    "   f) If meaningful: analyzer.analyze(context_blocks) -> understanding; map with decision_mapper.to_exploration_decision(); then override via _apply_gap_driven_decision().",
    "   g) Write memory: add_evidence (tier=0), add_gap(s), maybe add relationships/evidence from expansion later.",
    "   h) Utility stop gate via _update_utility_and_should_stop().",
    "   i) Action resolution: _next_action(decision), then _apply_refine_cooldown().",
    "   j) If _should_expand(...): graph_expander.expand(...) -> prefilter -> enqueue expanded targets -> expansion_depth += 1 -> continue loop.",
    "   k) Else if _should_refine(...): rerun discovery with same QueryIntent plus optional discovery_keyword_inject from gap mapping; enqueue ranked subset.",
    "   l) Else stop checks continue until queue exhausts or stop condition hits.",
    "5) Finalize completion_status/termination_reason and build result from working memory."
  ],
  "decision_points": [
    "Initial retry decision (_classify_initial_failure_reason + _has_retry_improvement).",
    "Candidate enqueue acceptance (_may_enqueue + selector no_relevant_candidate behavior).",
    "Meaningful evidence key test (_is_meaningful_new_evidence).",
    "Analyzer decision mapping (understanding -> ExplorationDecision).",
    "Gap-driven override (_apply_gap_driven_decision) changing next_action/needs/direction.",
    "Action normalization (_next_action, _apply_refine_cooldown).",
    "Branch gates (_should_expand, _should_refine, _should_stop/_should_stop_pre)."
  ],
  "termination_conditions": [
    "no_relevant_candidate (selector returns None and queue empty).",
    "pending_exhausted (queue drained).",
    "stalled (duplicate/location or no-new-evidence streak reaches EXPLORATION_STAGNATION_STEPS).",
    "max_steps (pre/post stop gate).",
    "primary_symbol_sufficient / relationships_satisfied.",
    "policy_violation_full_read (inspection tool mismatch).",
    "no_improvement_streak (utility stop)."
  ]
}
```

---

## Step 2 - Contracts Between Components

```json
{
  "analyzer_contract": {
    "input": {
      "instruction": "original user instruction",
      "intent": "joined intent strings",
      "context_blocks": "bounded blocks from _build_context_blocks_for_analysis()"
    },
    "output_type": "UnderstandingResult",
    "output_fields_used_by_engine": [
      "relevance",
      "confidence",
      "sufficient",
      "evidence_sufficiency",
      "knowledge_gaps",
      "summary"
    ],
    "decision_mapping_path": [
      "EngineDecisionMapper.to_exploration_decision(understanding)",
      "then _apply_gap_driven_decision(decision, understanding, ex_state)"
    ],
    "actual_decision_drivers": [
      "sufficient/evidence_sufficiency -> status=sufficient,next_action=stop",
      "relevance=low -> status=wrong_target,next_action=refine,needs=[different_symbol]",
      "else -> status=partial,needs=[more_code],next_action=(expand if relevance=high else stop)",
      "knowledge_gaps may override this through gap-driven mapping"
    ]
  },
  "expander_contract": {
    "engine_to_expander_input": {
      "symbol": "target.symbol",
      "file_path": "target.file_path",
      "max_nodes": "EXPLORATION_EXPAND_MAX_NODES",
      "max_depth": "EXPLORATION_EXPAND_MAX_DEPTH",
      "direction_hint": "ex_state.expand_direction_hint (callers|callees|both|None)",
      "skip_files": "seen + explored + pending canonical paths",
      "skip_symbols": "seen + pending symbols"
    },
    "expander_output": {
      "expanded_targets": "list[ExplorationTarget] for enqueue",
      "execution_result": {
        "summary": "graph expansion summary text",
        "data.results": "combined selected targets",
        "data.callers": "bucket",
        "data.callees": "bucket",
        "data.related": "bucket",
        "data.direction_hint": "echoed hint",
        "metadata.tool_name": "graph_lookup (graph path)"
      }
    }
  },
  "tool_contracts": {
    "discovery_search": {
      "input": "dispatcher.search_batch(queries, mode=symbol|regex|text)",
      "output_expected": "ExecutionResult.output.data.results|candidates[] rows with file/file_path, optional symbol,snippet/content,score",
      "engine_use": "merge by (canonical file, symbol-or-__file__), keep per-channel score breakdown, keep max score"
    },
    "inspection_read": {
      "input": "dispatcher.execute READ with _react_action_raw=read_snippet and {path,symbol,line,window}",
      "output_expected": "data={file_path,start_line,end_line,content,mode}",
      "mode_values": ["symbol_body", "line_window", "file_head"],
      "engine_use": "ReadPacket + read_source mapping + evidence delta key"
    },
    "graph_expand": {
      "input": "GraphExpander.expand(symbol,file_path,state,...)",
      "output_expected": "ExecutionResult with results/callers/callees/related arrays",
      "engine_use": "enqueue targets + add expansion evidence + add relationships from callers/callees/related"
    }
  }
}
```

---

## Step 3 - Expand Logic Audit

```json
{
  "expand_trigger_logic": [
    "Primary gate: _should_expand(action, decision, target, ex_state).",
    "wants_expand when action=expand OR (decision.status=sufficient and relationships_found is false).",
    "Hard blockers: missing target.symbol, depth cap reached, symbol already expanded, and needs must include callers/callees OR status=partial.",
    "Gap-driven mapping can force next_action=expand and add callers/callees needs + direction hint."
  ],
  "direction_handling": [
    "Direction is only passed as hint to GraphExpander.expand(direction_hint).",
    "GraphExpander classifies rows into callers/callees/related by snippet substring match ('caller'/'callee').",
    "If hinted bucket is empty, expander falls back to related (and for both, fallback to related when combined empty).",
    "Engine then prefilters by _may_enqueue and attempted_gap_targets; does not enforce semantic direction post-filter."
  ],
  "target_selection": [
    "Discovery and expansion both converge into _enqueue_targets.",
    "Queue prioritization score is shallow: +1 unseen symbol, +1 unseen file, +0.5 if source=expansion.",
    "No score contribution from gap category, analyzer confidence, relation type, or discovery channel score.",
    "Prefilter only removes duplicates/excluded/attempted triplets; no relevance ranking over expanded set."
  ],
  "issues": [
    "Hypothesis validated partially: expansion is no longer purely symbol/file based because gap-driven mapping can set callers/callees and direction hint; however execution still anchors on current target symbol/file and not an explicit gap->target mapping function.",
    "Direction hints are weakly enforced: they are heuristics inside GraphExpander and can degrade to related fallback, which broadens traversal.",
    "Graph relation typing depends on snippet text markers ('caller'/'callee'); this is brittle and can misbucket rows.",
    "Expanded target prioritization is weak (novelty-first only), so broad or semantically weak nodes can enter queue ahead of gap-critical nodes."
  ]
}
```

---

## Step 4 - Refine Logic Audit

```json
{
  "refine_trigger": [
    "_should_refine(action, decision, ex_state): true if action=refine or decision.status=wrong_target, bounded by EXPLORATION_MAX_BACKTRACKS.",
    "Action can be forced from gap mapping for usage/definition/config categories (next_action=refine)."
  ],
  "refine_behavior": [
    "Refine path reruns _run_discovery_traced('refine', intent, state, ex_state).",
    "Core QueryIntent is unchanged; only additive variability is ex_state.discovery_keyword_inject (max 2 tokens) merged into text queries once then cleared.",
    "After discovery, candidates are ingested and _enqueue_ranked(limit=3) selects next targets."
  ],
  "iteration_difference": [
    "Difference between refine iterations is usually candidate pool drift from search nondeterminism, queue state, and optional 1-2 injected keywords.",
    "There is no explicit parser-level intent rewrite inside refine loop (unlike initial one-time retry logic before main loop).",
    "Refine cooldown can force expand immediately after a refine step if expand is eligible."
  ],
  "issues": [
    "Hypothesis validated: refine often does not materially change queries; same intent channels are reused, with only small keyword injection.",
    "Refine can re-hit similar discovery surfaces; dedupe prevents exact re-enqueue, but semantic stagnation still occurs and is only stopped by stagnation/no_improvement counters.",
    "Backtrack cap is low (default 2), so refine has limited chance to recover from weak query shaping."
  ]
}
```

---

## Step 5 - Tool Usage Audit

```json
{
  "tools": [
    "search (discovery path via dispatcher.search_batch)",
    "read_snippet (inspection path via dispatcher.execute READ)",
    "graph_lookup + SEARCH fallback (expansion path in GraphExpander)"
  ],
  "tool_input_output": {
    "search_discovery": {
      "input_shape": {
        "queries": "intent.symbols / intent.regex_patterns / intent.keywords (+ optional inject)",
        "mode": "symbol|regex|text",
        "parallelism": "ThreadPoolExecutor per mode + dispatcher internal parallel batch"
      },
      "output_shape": {
        "ExecutionResult.output.data.results|candidates": "list[dict]",
        "row_fields_used": ["file|file_path", "symbol", "snippet|content", "score"]
      }
    },
    "read_snippet_inspection": {
      "input_shape": {
        "path": "target file",
        "symbol": "optional target symbol",
        "line": "optional target line",
        "window": "EXPLORATION_READ_WINDOW"
      },
      "output_shape": {
        "file_path": "canonical/absolute path",
        "start_line/end_line": "bounds",
        "content": "bounded snippet",
        "mode": "symbol_body|line_window|file_head"
      }
    },
    "graph_expand": {
      "input_shape": {
        "symbol": "anchor symbol",
        "file_path": "anchor file",
        "direction_hint": "callers|callees|both|None",
        "skip_files/skip_symbols": "dedupe skip sets"
      },
      "output_shape": {
        "results": "combined target list",
        "callers/callees/related": "typed buckets",
        "warnings": "graph warnings",
        "anchor_file": "echo anchor",
        "direction_hint": "echo normalized hint"
      }
    }
  },
  "filtering_logic": [
    "Discovery: dedupe by (canonical file, symbol-or-__file__), keep max row score; cap top K.",
    "Pre-enqueue: _may_enqueue excludes already explored, excluded_paths, and already pending.",
    "Expansion prefilter: applies _may_enqueue + attempted_gap_targets triplet filter.",
    "No semantic filter stage exists after expansion buckets; no gap relevance score attached to targets."
  ],
  "issues": [
    "Hypothesis validated: output breadth is constrained by dedupe/caps but prioritization is weakly semantic (max score at discovery, novelty score at enqueue).",
    "Duplicates are prevented structurally, but near-duplicate semantics can still leak due to file/symbol-key granularity.",
    "Expansion bucket quality depends on snippet keyword parsing, so callers/callees precision is tool-data dependent and not strongly validated."
  ]
}
```

---

## Step 6 - Memory Interaction

```json
{
  "memory_writes": [
    "Discovery candidates -> add_evidence(source=discovery,tier=2,tool=search).",
    "Inspection/analyzer step -> add_evidence(source=analyzer or inspection,tier=0,tool=read_snippet).",
    "Analyzer gaps -> add_gap(classified_type, description) with generic-gap rejection.",
    "Expansion -> add_expansion_evidence_row(tier=1) and add_relationships_from_expand(callers/callees/related)."
  ],
  "memory_usage_in_next_step": [
    "Direct control uses ex_state (pending/seen/explored/exclusions/direction/backtracks/streaks), not working memory.",
    "Working memory is primarily for final output assembly via ExplorationResultAdapter.build().",
    "One control-side bridge exists indirectly: analyzer knowledge_gaps are read immediately for gap-driven decisioning; memory-stored gaps are not queried to drive later prioritization.",
    "No retrieval from memory relationships/gaps to reprioritize queue or action selection in later iterations."
  ],
  "issues": [
    "Hypothesis validated: memory captures rich evidence/relationships/gaps but does not actively steer subsequent target ranking or action beyond same-step gap handling.",
    "Control plane and output memory are partially decoupled; loop decisions rely more on ex_state and fresh analyzer output than accumulated memory semantics."
  ]
}
```

---

## Step 7 - Core Architectural Gaps (No Noise)

- Weak gap-to-expansion binding: gap categories influence action/direction, but target ordering remains novelty-based instead of gap-utility based.
- Direction enforcement is soft: hinted caller/callee expansion can degrade to related fallback and broaden traversal.
- Refine diversification is limited: repeated intent channels with minimal keyword injection lead to low-entropy refine loops.
- Prioritization shallow across phases: discovery max-score and enqueue novelty are not fused with gap relevance/relationship quality.
- Memory underused for control: accumulated gaps/relationships are output-rich but not used to guide next-step scheduling.

---

## Step 8 - Minimal High-Impact Fix Plan

```json
{
  "critical_gaps": [
    "Weak gap->target prioritization after expansion/discovery.",
    "Under-enforced direction semantics in expansion output handling.",
    "Refine loop lacks strong query diversification.",
    "Working memory not feeding control decisions beyond immediate gap mapping."
  ],
  "fix_plan": [
    {
      "name": "Gap-weighted target scoring in existing _enqueue_targets",
      "change": "Extend _target_priority_score to include weights from current gap categories/direction and relation bucket (caller/callee/related) without changing architecture.",
      "impact": "Moves gap-relevant nodes earlier; reduces broad traversal."
    },
    {
      "name": "Post-expand direction guard",
      "change": "After GraphExpander returns, enforce direction_hint in engine by dropping mismatched bucket entries when hinted bucket is non-empty; only allow related fallback when hinted bucket is empty.",
      "impact": "Prevents direction drift while preserving current fallback behavior."
    },
    {
      "name": "Refine diversification using existing parser retry pattern",
      "change": "On consecutive refine with low improvement, invoke intent_parser.parse(...previous_queries..., failure_reason='low_relevance') inside refine branch (bounded attempts), then run discovery.",
      "impact": "Increases query entropy and lowers stagnation risk without new systems."
    },
    {
      "name": "Memory-informed queue suppression",
      "change": "Use working-memory relationship/gap signatures to suppress enqueue of targets already covered by same (gap_bundle, relation_type, file,symbol) semantics.",
      "impact": "Reduces redundant exploration while reusing current memory + attempted_gap_targets structures."
    }
  ]
}
```

---

## Hypothesis Validation Summary

- Expansion still symbol/file anchored: **Validated with nuance** (gap-driven override exists, but execution anchor is still current target symbol/file; no explicit gap->candidate mapper).
- Direction hints underutilized: **Validated** (hint is soft, heuristic, and may fallback to related).
- Tool outputs weakly filtered: **Validated partially** (structural dedupe/caps are present; semantic filtering/prioritization is weak).
- Refine stagnation due to weak query changes: **Validated** (same intent reused; minor keyword inject only).
- Memory written but not guiding decisions: **Validated** (rich memory used mostly for final result, not iterative control).

