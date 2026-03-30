# EXPLORATION_CONTEXT_FEEDBACK_REFACTOR_PLAN

```json
{
  "call_sites_modified": [
    {
      "file": "agent_v2/exploration/exploration_engine_v2.py",
      "function": "_explore_inner",
      "location": "initial retry refine path",
      "current_arguments_passed": [
        "instruction",
        "previous_queries=prev_queries",
        "failure_reason=initial_failure_reason",
        "lf_exploration_parent=exploration_outer",
        "lf_intent_span=refined_intent_span"
      ],
      "new_arguments_passed": [
        "context_feedback=context_feedback"
      ]
    },
    {
      "file": "agent_v2/exploration/exploration_engine_v2.py",
      "function": "_explore_inner",
      "location": "main loop refine branch",
      "current_arguments_passed": [
        "instruction",
        "previous_queries=intent",
        "failure_reason=refine_failure_reason",
        "lf_exploration_parent=exploration_outer"
      ],
      "new_arguments_passed": [
        "context_feedback=context_feedback"
      ]
    },
    {
      "file": "agent_v2/exploration/query_intent_parser.py",
      "function": "parse",
      "location": "parser API",
      "current_arguments_passed": [
        "instruction",
        "previous_queries",
        "failure_reason",
        "lf_exploration_parent",
        "lf_intent_span"
      ],
      "new_arguments_passed": [
        "context_feedback"
      ]
    }
  ],
  "new_arguments_added": {
    "query_intent_parser.parse.context_feedback": {
      "type": "dict[str, Any] | None",
      "default": null,
      "backward_compatible": true
    },
    "context_feedback_payload_shape": {
      "partial_findings": "memory.get_summary()['evidence']",
      "known_entities": {
        "symbols": "sorted(ex_state.seen_symbols)",
        "files": "sorted(ex_state.seen_files)"
      },
      "knowledge_gaps": "memory.get_summary()['gaps']"
    }
  },
  "prompt_changes": "Updated prompt template at agent/prompt_versions/exploration.query_intent_parser/v1.yaml to explicitly include context feedback (partial findings, known entities, remaining gaps) and require refined queries to build on prior progress and avoid repeated paths.",
  "before_vs_after_behavior": {
    "before": "refine query generation mostly used instruction + previous_queries + failure_reason; partial findings and known entities were not explicitly injected.",
    "after": "refine query generation receives instruction + previous_queries + failure_reason + context_feedback derived from working memory and exploration state."
  },
  "risk_assessment": [
    "Larger prompt payload may increase token usage in deeply iterative runs.",
    "If memory evidence is noisy, refinement may inherit that noise; mitigated by bounded memory summary already in use.",
    "No control-flow risk: loop, analyzer, expand logic, and memory schema are unchanged."
  ],
  "test_plan": [
    "Run tests/test_query_intent_parser.py to verify parser API remains backward-compatible and parsing behavior is stable.",
    "Run tests/test_exploration_engine_v2_control_flow.py to verify refine/expand control flow is unchanged.",
    "Run a targeted live refine path evaluation and inspect Langfuse generation input for context_feedback presence.",
    "Confirm no lints on modified files via ReadLints."
  ]
}
```
