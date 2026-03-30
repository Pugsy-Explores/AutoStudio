# Stage 35 — Broader Paired Evaluation and Release-Gating Policy Closeout

## Summary

Stage 35 expands paired evaluation beyond paired4 to a representative cross-section of task types (paired8), extends comparison artifacts with task-type-level agreement and variability, and produces a concrete gating policy recommendation.

---

## 1. Broader Paired Suite: paired8

**`tests/agent_eval/suites/paired8.py`**

| Task Type | Task IDs |
|-----------|----------|
| repair | core12_mini_repair_calc, core12_pin_typer_repair |
| feature | core12_mini_feature_flags, core12_pin_typer_feature |
| docs_consistency | core12_mini_docs_version, core12_pin_click_docs_code |
| explain_artifact | core12_pin_requests_explain_trace |
| multi_file | core12_pin_click_multifile |

8 tasks total. Same task set for offline and live_model.

---

## 2. Extended Comparison Artifacts

| Field | Description |
|-------|-------------|
| `task_type_agreement_rate` | Per-task-type agreement (repair, feature, docs_consistency, etc.) |
| `task_type_judgments` | Per-type: predictive (≥90%), partially_predictive, misleading (<50%) |
| `per_task_flip_rate` | Fraction of tasks where offline ≠ live on success |
| `live_variability_by_task_type` | Min per-task agreement across live runs, by type |
| `retry_replan_variability` | Retries/replans mean and std across live runs |
| `gating_policy` | Concrete policy key |
| `gating_policy_wording` | Human-readable policy wording |

---

## 3. Gating Policy Options

| Policy Key | Wording |
|------------|---------|
| `offline_primary_nightly_live_spot_check` | Offline primary. Nightly live spot check on paired8 to validate offline remains predictive. |
| `offline_primary_selective_live_gate_edit_multifile` | Offline primary; selective live gate for EDIT/multi_file (or specific task types). Nightly live spot check. |
| `live_too_unstable_to_gate` | Live model too unstable to gate. Use offline as primary; do not gate on live. |
| `live_primary_specific_task_classes` | Live primary for task classes where offline is misleading. Gate on live for those; offline for others. |

---

## 4. Runner Updates

**`run_paired_real`**

- `--suite paired4 | paired8` (default: paired8)
- `--live-repeats` clamped to 3–5 (default 4)
- Output includes `gating_policy` and `gating_policy_wording`

---

## 5. Decision Questions Answered

### Is offline predictive overall?

- **Overall judgment:** offline_is_predictive / offline_is_partially_predictive / offline_is_misleading
- From agreement rate and per-task flips

### For which task types is offline predictive, partially predictive, or misleading?

- **Per-type:** repair, feature, docs_consistency, explain_artifact, multi_file
- **Judgment thresholds:** predictive ≥90% agreement, misleading <50%, else partially_predictive

### Is live_model stable enough to be used as a gate?

- **Live success std** and **min per-task agreement** across runs
- If std ≥ 1.0 or min_agreement < 1.0 with ≥2 live runs → live_too_unstable_to_gate

### What exact gating policy should be adopted now?

- **`gating_policy`** and **`gating_policy_wording`** in comparison.json

---

## 6. Concrete Gating Recommendation (Policy-Grade)

**Adopted policy:** **offline_primary_nightly_live_spot_check**

**Wording:** Offline primary. Nightly live spot check on paired8 (or subset) to validate offline remains predictive.

**Evidence (from sample run):**

- Overall judgment: offline_is_predictive
- Agreement rate: 100% (single-task sample)
- Per-task flip rate: 0%
- Live variability: std 0, min_task_agreement 100%
- Task-type judgment (repair): predictive

**Caveats:**

1. **Sample size:** Single-task run; full paired8 with 4 live repeats gives stronger evidence.
2. **Model-dependent:** Different models may change judgments.
3. **If live becomes unstable:** Re-run paired8; if std ≥ 1.0 or min_agreement < 1.0, switch to `live_too_unstable_to_gate`.
4. **If task-type disagreement appears:** Use `offline_primary_selective_live_gate_edit_multifile` for types with agreement <90%.

**Actionable steps:**

1. **CI:** Gate on offline (paired8 or audit6).
2. **Nightly:** Run `python3 -m tests.agent_eval.run_paired_real --suite paired8 --live-repeats 4`.
3. **Review:** Inspect `comparison.json` for `gating_policy` and `task_type_judgments`.
4. **Escalation:** If `gating_policy` becomes `live_too_unstable_to_gate`, do not gate on live; investigate variability.

---

## 7. Files Changed/Added

| File | Change |
|------|--------|
| `tests/agent_eval/suites/paired8.py` | **New** — 8-task policy-grade suite |
| `tests/agent_eval/suite_loader.py` | +paired8 |
| `tests/agent_eval/summary_aggregation.py` | +paired8 suite labels |
| `tests/agent_eval/paired_comparison.py` | +task_type_agreement, per_task_flip_rate, live_variability_by_type, retry_replan_variability, derive_gating_policy |
| `tests/agent_eval/run_paired_real.py` | +--suite paired8, live_repeats 3–5, gating_policy output |

---

## 8. Production Impact

- **None.** All changes are under `tests/agent_eval/`.
