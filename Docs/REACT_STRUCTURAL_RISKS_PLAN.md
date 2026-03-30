# ReAct Structural Risks — Detailed Plan (Implemented)

## Risk 1: _generate_patch_once still does targeting ✅

**Problem:** System guesses target file via edit_binding, search_target_candidates, or instruction path hints. Violates "MODEL decides → SYSTEM executes".

**Fix:** Require model to specify file explicitly. No system-side target guessing for ReAct.

| Component | Change |
|-----------|--------|
| `react_schema.py` | Extend EDIT: `["instruction", "path"]` — path required |
| `_dispatch_react` | Validate path present for EDIT; return clear error if missing |
| `_edit_react` | Pass step args (path) to context; use as sole target source |
| `_generate_patch_once` | Use only `context.get("edit_target_path")` or binding from explicit path; **remove** fallback to candidates, instruction hints |
| `build_edit_binding` | For ReAct: when path in args, build binding from that path + ranked_context evidence (optional) |

**Backward compat:** Deterministic path still uses build_edit_binding from ranked_context (no ReAct schema).

---

## Risk 2: Patch generation too abstract ✅

**Problem:** Vague instruction ("fix bug") → patch generator hallucinates.

**Fix:** Prompt-level — strengthen edit_proposal prompts to require grounded, specific instructions.

| File | Change |
|------|--------|
| `edit_proposal_system.yaml` | Add: "VAGUE INSTRUCTIONS: Instructions like 'fix bug' or 'make it work' cause wrong patches. You must have: (1) read the target file, (2) identified the exact location (line/symbol), (3) described the specific change. If the instruction is vague, prefer already_correct or a minimal no-op." |
| `edit_proposal_user.yaml` | Add: "If the instruction does not specify what to change, do not guess. Return already_correct or a minimal safe change." |

---

## Risk 3: Silent targeting errors ✅

**Problem:** Wrong file → patch applies → tests fail. Model doesn't know "wrong file".

**Fix:** Include "Modified file(s): X" in every EDIT observation (success and failure).

| Component | Change |
|-----------|--------|
| `_edit_react` | Always include `files_modified` in output (even on patch_apply_failed we know attempted files from patch_plan) |
| `_build_react_observation` (EDIT) | On any EDIT result, prepend "Modified file(s): {files}" or "Target file: {file}" when available |

---

## Risk 4: No partial success signal ✅

**Problem:** Patch applied + tests fail → returned as generic "failure". Model loses "patch applied correctly, logic wrong".

**Fix:** Differentiate patch_apply success from test success. Return structured signal.

| Component | Change |
|-----------|--------|
| `_edit_react` | When patch applies but tests fail: `output.patch_applied = True`, `output.tests_passed = False`, keep `files_modified` |
| `_build_react_observation` | When `patch_applied` and not `tests_passed`: "Patch applied successfully. Modified file(s): X. Tests failed:\n{output}" instead of "Edit failed" |

---

## Risk 5: Syntax failure opaque ✅

**Problem:** Syntax fail → rollback → return. Model sees failure but not why.

**Fix:** Ensure observation includes the actual syntax error message.

| Component | Change |
|-----------|--------|
| `_edit_react` | On syntax fail: `output.syntax_error = syn.get("error")` explicitly |
| `_build_react_observation` | When `failure_reason_code == "syntax_error"`: use "Syntax error: {reason}" as the main message |

---

## Implementation order

1. **Risk 4 & 5** — Output/observation improvements (no schema change)
2. **Risk 3** — Include modified files in observation
3. **Risk 2** — Prompt updates
4. **Risk 1** — Schema + targeting (breaking change; require path for EDIT)
