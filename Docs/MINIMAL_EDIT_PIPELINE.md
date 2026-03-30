# Minimal Edit Pipeline — Isolation Mode

## Ultra-Minimal Mode (ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE)

Absolute minimum: patch → apply → compile → tests. No validation, no early returns except apply failure.

```bash
export ENABLE_ULTRA_MINIMAL_EDIT_PIPELINE=1
```

Flow: `plan_diff` → `to_structured_patches` → `execute_patch` → `validate_project` (compile) → `run_tests`.

Logging: `[ULTRA_MINIMAL] patch_generated`, `patch_apply_ok`, `compile_ok`, `tests_passed`.

---

## Minimal Mode (ENABLE_MINIMAL_EDIT_PIPELINE)

### Purpose

Bypass all intermediate validation layers to isolate **generation vs validation bottleneck**.

- **Case A:** patch_apply_success ↑ and tests fail → validation layer was blocking valid patches
- **Case B:** patch_apply still fails → generation/grounding is root cause
- **Case C:** tests pass → system is fundamentally correct → reintroduce layers

## Enable

```bash
export ENABLE_MINIMAL_EDIT_PIPELINE=1
```

Default: `0` (disabled).

## What Is Bypassed

- `validate_syntax_plan` → SKIP
- `verify_patch_plan` → SKIP
- `check_structural_improvement` → SKIP
- `failure_state` logic → SKIP
- `semantic_feedback` → SKIP
- Retry loops → single attempt only
- `classify_result` FATAL → always RETRYABLE on EDIT failure

## Flow

1. Generate `patch_plan` (existing `plan_diff` + `to_structured_patches`)
2. `execute_patch(patch_plan, project_root)`
3. `run_tests(project_root, timeout, test_cmd)`
4. Return success/failure

## Logging

```
[minimal_pipeline] patch_generated=True changes_count=1
[minimal_pipeline] patch_apply_ok=True
[minimal_pipeline] tests_passed=False
```

## Run paired8

```bash
ENABLE_MINIMAL_EDIT_PIPELINE=1 python -m tests.agent_eval.run_paired_real --suite paired8 --live-repeats 4
```

## Metrics to Collect

- `convergence_rate`
- `patch_apply_success` (should increase if validation was blocking)
- `validation_tests_failed` (should now appear when patch applies but tests fail)
