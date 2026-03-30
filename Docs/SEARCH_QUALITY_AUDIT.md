# SEARCH Quality Audit

Production-ready evaluator for code-retrieval search query quality. Runs as a **post-hoc LLM call** after each SEARCH step. Does not interfere with control flow.

## Quick Start

```bash
# Enable audit (env gate)
export ENABLE_SEARCH_QUALITY_AUDIT=1

# Run agent eval — audit events logged to traces
python3 -m tests.agent_eval.runner --suite search_stack --output artifacts/audit_run

# Aggregate from traces
python3 scripts/run_search_quality_audit.py --trace-dir .agent_memory/traces -o artifacts/audit_report.json
```

## What It Evaluates

| Dimension | 0 | 3 |
|-----------|---|---|
| **grounding** | unrelated/generic | strongly grounded in instruction |
| **specificity** | vague | symbols/files/behavior |
| **implementation_bias** | docs/tests | clearly implementation-focused |
| **structural_intent** | none | explicit relationships |
| **result_quality** | junk | strong (impl bodies, relevant files) |

**Red flags:** generic_template_used, too_vague, too_narrow, missing_relationships, test_bias, duplicate_of_previous, irrelevant_terms

**Verdict:** excellent | acceptable | weak | bad

## Derived Metrics

- **effective_search** = grounding + specificity + implementation_bias (0–9)
  - 7–9 → strong
  - 4–6 → usable
  - 0–3 → broken

- **bad_or_weak_rate** = (# verdict in ["bad","weak"]) / total
  - \> 20% → SEARCH is bottleneck
  - \< 10% → move to EXPLAIN quality

## Integration

- **Where:** `agent/execution/step_dispatcher.py` — after SEARCH success, before return
- **Gate:** `ENABLE_SEARCH_QUALITY_AUDIT=1`
- **Model:** Small model (via `call_small_model`, task_name `search_quality_audit`)
- **Logging:** `log_event(trace_id, "search_quality_audit", result)`

## Offline Batch

```bash
# JSONL: one object per line with instruction, search_query, retrieval_summary
echo '{"instruction":"Find auth logic","search_query":"auth login","retrieval_summary":"results_count=3\ntop_files=[src/auth.py]"}' > batch.jsonl
python3 scripts/run_search_quality_audit.py --batch-input batch.jsonl -o report.json
```

## PE-Level Diagnostics

| Pattern | Likely Cause |
|---------|--------------|
| grounding < 2 | planner issue |
| specificity < 2 | prompt/schema issue |
| implementation_bias < 2 | retrieval issue |
| result_quality < 2 | pipeline issue |
| duplicate_of_previous | replanner weakness |
