# Stage 36 — Evidence-Aware and Honest Policy Closeout

## Summary

Stage 36 makes the gating policy evidence-aware and honest. Agreement can be dominated by all-fail outcomes and small samples; the comparison artifact now distinguishes outcome types, reports evidence quality, and derives a usefulness judgment. Policy support is conditional on evidence strength.

---

## 1. Outcome Matrix

| Outcome | Meaning |
|---------|---------|
| **pass_pass** | Both offline and live succeeded |
| **fail_fail** | Both offline and live failed |
| **offline_pass_live_fail** | Offline succeeded, live failed (offline overstates) |
| **offline_fail_live_pass** | Offline failed, live succeeded (offline understates) |

Raw agreement = (pass_pass + fail_fail) / total. When all outcomes are fail_fail, raw agreement is 100% but evidence is weak.

---

## 2. Representative Agreement Rate

**Representative agreement** = agreement among nontrivial outcomes only (excludes fail_fail).

- **None** when all outcomes are fail_fail (no nontrivial outcomes).
- **pass_pass / (pass_pass + offline_pass_live_fail + offline_fail_live_pass)** when nontrivial outcomes exist.

This measures whether offline predicts live when at least one mode passes.

---

## 3. Evidence-Quality Metrics

| Metric | Description |
|-------|-------------|
| `task_count` | Number of tasks in comparison |
| `live_repeat_count` | Number of live_model runs |
| `nontrivial_success_count` | pass_pass + offline_pass_live_fail + offline_fail_live_pass |
| `task_type_count` | Number of task types represented |
| `task_type_coverage` | Fraction of canonical types (repair, feature, docs_consistency, explain_artifact, multi_file) represented |

---

## 4. Usefulness Judgment

| Judgment | Condition |
|----------|-----------|
| **predictive_and_useful** | judgment=offline_is_predictive, strong evidence (task_count≥6, live≥3, nontrivial≥2 or coverage≥0.6), agreement≥0.75 |
| **predictive_but_low_evidence** | judgment=offline_is_predictive or partially_predictive but evidence weak |
| **insufficient_evidence** | task_count<4, or live_repeat_count<2, or (nontrivial=0 and task_count<8) |
| **misleading** | judgment=offline_is_misleading |

---

## 5. Policy Support

| Support | Condition |
|---------|-----------|
| **strongly_supported** | usefulness=predictive_and_useful |
| **provisionally_supported** | Otherwise |

Policy wording now includes "(strongly supported)" or "(provisionally supported)".

---

## 6. When Agreement Is High But Evidence Is Weak

### Example 1: All fail/fail, 1 task

- outcome_matrix: pass_pass=0, fail_fail=1, offline_pass_live_fail=0, offline_fail_live_pass=0
- Raw agreement: 100%
- Representative agreement: None (no nontrivial outcomes)
- usefulness_judgment: **insufficient_evidence**
- policy_support: **provisionally_supported**

### Example 2: All fail/fail, 8 tasks

- outcome_matrix: fail_fail=8
- Raw agreement: 100%
- Representative agreement: None
- usefulness_judgment: **insufficient_evidence** (nontrivial=0, task_count<8)
- policy_support: **provisionally_supported**

### Example 3: 4 tasks, 2 live runs, 1 pass_pass

- task_count=4, live_repeat_count=2, nontrivial=1
- strong_evidence requires live≥3, so not met
- usefulness_judgment: **predictive_but_low_evidence**
- policy_support: **provisionally_supported**

### Example 4: 8 tasks, 4 live runs, 3 pass_pass, 2 fail_fail, 2 off_pass_live_fail, 1 off_fail_live_pass

- Representative agreement = 3/6 = 50% (among nontrivial)
- Raw agreement = 5/8 = 62.5%
- If judgment=offline_is_partially_predictive: usefulness=**predictive_but_low_evidence**

---

## 7. Final Recommendation

**Is the current offline-primary policy strongly supported or only provisionally supported?**

**Answer: Provisionally supported.**

**Evidence:**

- Typical runs so far: 1–4 tasks, 2–3 live repeats, often all fail/fail or very few nontrivial outcomes.
- usefulness_judgment in these runs: **insufficient_evidence** or **predictive_but_low_evidence**.
- policy_support: **provisionally_supported**.

**To achieve strongly_supported:**

1. Run full paired8 (8 tasks) with ≥4 live repeats.
2. Ensure nontrivial outcomes: at least 2 tasks where offline or live (or both) pass.
3. Maintain agreement ≥75% among nontrivial outcomes.
4. Ensure task_type_coverage ≥0.6 (at least 3 of 5 canonical types).

**Actionable guidance:**

- **Current policy:** Offline primary, nightly live spot check — **provisionally supported**.
- **Do not** treat high raw agreement from all-fail runs as strong evidence.
- **Do** run paired8 with 4 live repeats and inspect `usefulness_judgment` and `representative_agreement_rate` before upgrading to strongly_supported.

---

## 8. Artifact Schema Additions

| Field | Type | Description |
|-------|------|-------------|
| `outcome_matrix` | dict | pass_pass, fail_fail, offline_pass_live_fail, offline_fail_live_pass counts |
| `evidence_quality` | dict | task_count, live_repeat_count, nontrivial_success_count, task_type_coverage |
| `representative_agreement_rate` | float \| null | Agreement among nontrivial outcomes; null when all fail/fail |
| `usefulness_judgment` | str | predictive_and_useful, predictive_but_low_evidence, insufficient_evidence, misleading |
| `usefulness_explanation` | str | Human-readable explanation |
| `policy_support` | str | strongly_supported, provisionally_supported |

---

## 9. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/paired_comparison.py` | +outcome_matrix, evidence_quality, representative_agreement, usefulness_judgment, policy_support; derive_gating_policy now evidence-conditional |
| `tests/agent_eval/run_paired_real.py` | +policy_support, usefulness_judgment in stderr output |
| `tests/agent_eval/test_stage34_paired_real.py` | +6 Stage 36 regression tests |

---

## 10. Production Impact

- **None.** All changes are under `tests/agent_eval/`.
