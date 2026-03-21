# Stage 34 — Real Paired Evaluation and Decision-Grade Gap Audit Closeout

## Summary

Stage 34 executes paired4 with the real configured model endpoint: 1 offline baseline + N live_model runs (default 3). Produces a decision-grade comparison artifact with agreement rate, live variability, and a blunt recommendation for gating strategy.

---

## 1. Execution Flow

**`python3 -m tests.agent_eval.run_paired_real --output artifacts/agent_eval_runs/stage34_paired`**

- Runs 1 offline baseline (stubs + plan injection)
- Runs N live_model runs (default 3) with real model
- If first live run fails with ConnectionError/Timeout/refused → records `model_endpoint_missing_or_broken` and exits 1
- Writes `comparison.json` and `comparison.md` to output dir

---

## 2. Comparison Artifact (Stage 34)

| Field | Description |
|-------|-------------|
| `summary_deltas` | success_count, validation_pass_count, model_call_count_total, retries, replans |
| `per_task_deltas` | Per-task success/validation/structural deltas |
| `failure_bucket_deltas` | Failure bucket histogram deltas |
| `semantic_rca_cause_deltas` | Semantic RCA cause histogram deltas |
| `integrity` | run_valid_for_live_eval, invalid_live_model_task_count |
| `agreement_rate` | Fraction of tasks where offline and live agree on success |
| `live_variability` | success_count mean/std/min/max, per_task_agreement, min_task_agreement |
| `judgment` | offline_is_predictive / offline_is_partially_predictive / offline_is_misleading |
| `decision_recommendation` | offline_primary / offline_primary_selective_live_gate / live_primary / live_too_unstable_to_gate |

---

## 3. Decision Recommendation Logic

| Recommendation | Condition |
|----------------|-----------|
| **live_too_unstable_to_gate** | ≥2 live runs and (success_std ≥ 1.0 or min_task_agreement < 1.0) |
| **offline_primary** | judgment=offline_is_predictive and agreement_rate ≥ 0.75 |
| **live_primary** | judgment=offline_is_misleading and live stable (std < 0.5, min_agreement=1) |
| **offline_primary_selective_live_gate** | judgment=offline_is_partially_predictive; use offline as primary, gate selectively on live |

---

## 4. Model Endpoint Handling

- If first live run raises ConnectionError, TimeoutError, or error string containing "connection", "refused", "timeout", "unreachable", "404", "502", "503" → outcome `model_endpoint_missing_or_broken`
- Writes failure artifact to comparison.json with `decision_recommendation: live_too_unstable_to_gate`
- Exit code 1

---

## 5. Real Run Outcome (Sample)

A sample run with configured model produced:

- **Offline:** 0 success (edit_grounding_failure)
- **Live:** 0 success (edit_grounding_failure; guardrail validation failed for planner)
- **Agreement rate:** 100%
- **Judgment:** offline_is_predictive
- **Recommendation:** offline_primary

*(Outcome depends on model, config, and task behavior. Re-run with full paired4 for full suite.)*

---

## 6. Blunt Recommendation

**Recommendation:** **offline_primary** (with caveats)

**Evidence from this run:**

- Offline and live agreed on the single task (both failed).
- Live run used real model (2 reasoning calls); integrity valid.
- With only 1 live run, variability cannot be assessed; recommendation defaults to offline_primary when judgment is predictive and agreement is high.

**Caveats:**

1. **Single live run:** Use `--live-repeats 3` (or more) to assess live stability. If live success varies across runs (std ≥ 1 or min_task_agreement < 1), recommendation switches to **live_too_unstable_to_gate**.
2. **Single task:** Full paired4 (4 tasks) gives a more representative agreement rate.
3. **Model-dependent:** Different models may change judgment and recommendation.

**Actionable guidance:**

- **If model is configured and reachable:** Run `python3 -m tests.agent_eval.run_paired_real --live-repeats 3` for full suite + variability.
- **If model is missing/broken:** Stage records outcome and exits 1; no live runs.
- **If live shows high variance:** Use **offline_primary_selective_live_gate** or **live_too_unstable_to_gate** per the artifact.

---

## 7. Files Changed/Added

| File | Change |
|------|--------|
| `tests/agent_eval/paired_comparison.py` | +compute_agreement_rate, +compute_live_variability, +derive_decision_recommendation, +build_multi_live_comparison_artifact |
| `tests/agent_eval/run_paired_real.py` | **New** — Stage 34 real paired runner |
| `tests/agent_eval/test_stage34_paired_real.py` | **New** — 7 regression tests |

---

## 8. Production Impact

- **None.** All changes are under `tests/agent_eval/`.
