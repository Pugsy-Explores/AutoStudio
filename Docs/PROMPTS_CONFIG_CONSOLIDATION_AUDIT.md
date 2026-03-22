# System Audit — Consolidate Prompts and Config into Existing Registry

**Date:** 2026-03-22  
**Scope:** agent/edit/, agent/meta/, agent/runtime/, agent/execution/, editing/, config/  
**Constraint:** Audit only; no code modifications.

---

## 1. Full Inventory

### 1.1 Hardcoded Prompts

| # | name | location | type | is_dynamic | uses_context_vars |
|---|------|----------|------|------------|-------------------|
| P1 | PATCH_SYSTEM_PROMPT | agent/edit/edit_proposal_generator.py:90-110 | system | false | — |
| P2 | edit_proposal user prompt | agent/edit/edit_proposal_generator.py:236-254 | user | true | instruction, target_file, symbol, evidence, full_content |
| P3 | retry_planner user prompt | agent/meta/retry_planner.py:112-119 | user | true | goal, diagnosis.failure_type, diagnosis.affected_step, diagnosis.suggestion |
| P4 | answer_grounding eval | agent/execution/step_dispatcher.py:883-891 | system+user | true | instruction, answer_text, context_text (from YAML file) |

**P1–P2:** The edit proposal generator uses a hardcoded system prompt and an f-string user prompt. It does NOT use the registry. The registry has `patch_generator` (agent/prompts/patch_generator.yaml) but that prompt is for unified-diff output; edit_proposal_generator outputs JSON text_sub/insert. **Different format, different use case.**

**P3:** retry_planner uses registry for system (retry_planner_system.yaml) but builds user prompt inline with f-string.

**P4:** step_dispatcher loads YAML from agent/prompt_versions/evaluation/ for answer grounding eval. Uses `.format(instruction=..., answer=..., context=...)`. Already externalized.

---

### 1.2 Config / Constants

| # | name | location | current_value | type | scope |
|---|------|----------|---------------|------|-------|
| C1 | MAX_SEMANTIC_RETRIES | agent/runtime/execution_loop.py:201 | 2 | constant | component |
| C2 | MAX_EDIT_ATTEMPTS | config/agent_runtime.py:14 | int(env, "3") | env | global |
| C3 | MAX_PATCH_FILES | config/agent_runtime.py:23 | int(env, "5") | env | global |
| C4 | MAX_PATCH_LINES | config/agent_runtime.py:22 | int(env, "300") | env | global |
| C5 | MAX_SAME_ERROR_RETRIES | config/agent_runtime.py:26 | int(env, "2") | env | global |
| C6 | TEST_TIMEOUT | config/agent_runtime.py:17 | int(env, "120") | env | global |
| C7 | MAX_STRATEGIES | config/agent_runtime.py:16 | int(env, "3") | env | global |
| C8 | ENABLE_SANDBOX | config/agent_runtime.py:29 | bool(env, "0") | env | global |
| C9 | ENABLE_DIFF_PLANNER | editing/diff_planner.py:17 | env "1" | env | component |
| C10 | MAX_FILES_EDITED | config/editing_config.py:6 | int(env, "5") | env | global |
| C11 | max_content | agent/edit/edit_proposal_generator.py:231 | 8000 | inline_default | component |
| C12 | evidence truncation | agent/edit/edit_proposal_generator.py:246 | 1500 | inline_default | component |
| C13 | max_tokens (retry_planner) | agent/meta/retry_planner.py:131 | 1024 | inline_default | component |
| C14 | rewrite_query cap | agent/meta/retry_planner.py:148 | 500 | inline_default | component |
| C14b | suggestion fallback cap | agent/meta/retry_planner.py:160 | 200 | inline_default | component |
| C15 | reason truncation | agent/runtime/execution_loop.py:683,716,734,744 | 500 | inline_default | component |
| C16 | _MAX_SUMMARY_LEN | editing/semantic_feedback.py:11 | 500 | constant | component |
| C17 | evidence block | agent/edit/edit_proposal_generator.py:31,47,76,87 | 500 | inline_default | component |
| C18 | REPLANNER_LLM_DEADLINE_SECONDS | agent/orchestrator/replanner.py:315 | env | env | component |
| C19 | SERENA_PROJECT_DIR | multiple | env, getcwd fallback | env | global |
| C20 | RETRY_STRATEGIES | agent/meta/retry_planner.py:49-55 | frozenset | constant | component |
| C21 | STRATEGY_ALIASES | agent/meta/retry_planner.py:57-62 | dict | constant | component |
| C22 | FALLBACK_STRATEGY | agent/meta/retry_planner.py:64 | "generate_new_plan" | constant | component |
| C23 | MAX_CONTEXT_TOKENS | config/context_limits.py:3 | 8000 | constant | global |
| C24 | MAX_CONTEXT_SNIPPETS | config/context_limits.py:4 | 6 | constant | global |
| C25 | MAX_CONTEXT_FILES | config/context_limits.py:5 | 6 | constant | global |
| C26 | search_memory_snippet_max | config/policy_config.yaml:29 | 5000 | yaml | global |

---

## 2. Problems

### 2.1 Duplicated / Inconsistent Prompts

- **patch_generator vs PATCH_SYSTEM_PROMPT:** Registry has `patch_generator.yaml` (unified-diff format). Edit proposal generator has its own `PATCH_SYSTEM_PROMPT` (JSON text_sub/insert). Different output formats; registry `patch_generator` is effectively dead for the current edit flow. **Inconsistency:** two patch-generation concepts with the same registry name.

### 2.2 Hardcoded Values

- **edit_proposal_generator:** max_content=8000, evidence[:1500], content[:500] scattered.
- **retry_planner:** max_tokens=1024, rewrite_query[:500].
- **execution_loop:** reason[:500] in multiple places.
- **semantic_feedback:** _MAX_SUMMARY_LEN=500.

### 2.3 Config Leakage

- **MAX_SEMANTIC_RETRIES** defined in execution_loop.py, not in config/agent_runtime.py. Same domain as MAX_EDIT_ATTEMPTS, MAX_SAME_ERROR_RETRIES.
- **ENABLE_DIFF_PLANNER** in diff_planner.py; other flags (ENABLE_SANDBOX) live in config.
- **REPLANNER_LLM_DEADLINE_SECONDS** read inline; no config module.
- **SERENA_PROJECT_DIR** repeated in 6+ files with same pattern; should be centralized.

### 2.4 Env / Config Conflicts

- config/agent_runtime.py uses env with defaults.
- config/editing_config.py has MAX_FILES_EDITED; diff_planner uses it but also has ENABLE_DIFF_PLANNER inline.
- No YAML override layer for agent_runtime values (only env + code defaults).

---

## 3. Migration Mapping Table

| current_location | target_yaml_path | key | notes |
|------------------|------------------|-----|-------|
| agent/edit/edit_proposal_generator.py:PATCH_SYSTEM_PROMPT | agent/prompts/edit_proposal_system.yaml | system_prompt | New file; distinct from patch_generator |
| agent/edit/edit_proposal_generator.py:user prompt f-string | agent/prompts/edit_proposal_user.yaml | user_prompt | Template with {{instruction}}, {{target_file}}, {{symbol}}, {{evidence}}, {{full_content}} |
| agent/meta/retry_planner.py:user prompt f-string | agent/prompts/retry_planner_user.yaml | user_prompt | Template with {{goal}}, {{failure_type}}, {{affected_step}}, {{suggestion}}; or add to retry_planner_system.yaml as user_block |
| agent/runtime/execution_loop.py:MAX_SEMANTIC_RETRIES | config/agent_runtime.py | MAX_SEMANTIC_RETRIES | Add to agent_runtime; read from env with default 2 |
| agent/edit/edit_proposal_generator.py:max_content 8000 | config/editing_config.py or config/context_limits.py | EDIT_PROPOSAL_MAX_CONTENT | Add; env EDIT_PROPOSAL_MAX_CONTENT default 8000 |
| agent/edit/edit_proposal_generator.py:evidence[:1500] | config/editing_config.py | EDIT_PROPOSAL_EVIDENCE_MAX | Add; default 1500 |
| agent/meta/retry_planner.py:max_tokens 1024 | config/ or task_params | retry_planning.max_tokens | Could go in model router task_params |
| agent/meta/retry_planner.py:rewrite_query[:500] | config/agent_runtime.py | RETRY_QUERY_MAX_LEN | Add; default 500 |
| agent/meta/retry_planner.py:suggestion[:200] | config/agent_runtime.py | RETRY_SUGGESTION_FALLBACK_MAX | Add; default 200 |
| agent/runtime/execution_loop.py:reason[:500] | config/agent_runtime.py | REASON_TRUNCATE_LEN | Add; default 500 |
| editing/semantic_feedback.py:_MAX_SUMMARY_LEN | config/editing_config.py | SEMANTIC_FEEDBACK_MAX_SUMMARY | Add; default 500 |
| editing/diff_planner.py:ENABLE_DIFF_PLANNER | config/editing_config.py | ENABLE_DIFF_PLANNER | Move to editing_config; already uses env |
| agent/orchestrator/replanner.py:REPLANNER_LLM_DEADLINE | config/agent_runtime.py or router_config | REPLANNER_LLM_DEADLINE_SECONDS | Add to config |
| agent/meta/retry_planner.py:RETRY_STRATEGIES, STRATEGY_ALIASES, FALLBACK | config/policy_config.yaml or new retry_config.yaml | retry_planner.strategies, aliases, fallback | YAML array/dict; load at startup |

---

## 4. Final Target Structure (Existing Systems Only)

### 4.1 Prompts (agent/prompts/ and loader.py)

```
agent/prompts/
  edit_proposal_system.yaml    # NEW: system_prompt for edit_proposal_generator
  edit_proposal_user.yaml      # NEW: user_prompt template with {{vars}}
  retry_planner_system.yaml    # EXISTS
  retry_planner_user.yaml      # NEW: or extend retry_planner_system with user_template
  patch_generator.yaml         # EXISTS (different format; consider renaming to patch_generator_unified_diff or deprecating)
```

**Registry changes (loader.py _LEGACY_MAP):**
- Add `edit_proposal`: "edit_proposal_system" (or composite: system + user).
- retry_planner: keep; add user template support if needed.

**Template variables (normalized):**
- edit_proposal: `{{instruction}}`, `{{target_file}}`, `{{symbol}}`, `{{evidence}}`, `{{full_content}}`
- retry_planner user: `{{goal}}`, `{{failure_type}}`, `{{affected_step}}`, `{{suggestion}}`

### 4.2 Config (config/)

```
config/
  agent_runtime.py     # Add: MAX_SEMANTIC_RETRIES, REASON_TRUNCATE_LEN, REPLANNER_LLM_DEADLINE_SECONDS
  editing_config.py    # Add: EDIT_PROPOSAL_MAX_CONTENT, EDIT_PROPOSAL_EVIDENCE_MAX, SEMANTIC_FEEDBACK_MAX_SUMMARY, ENABLE_DIFF_PLANNER
  policy_config.yaml   # Add: retry_planner: { strategies: [...], aliases: {...}, fallback: "..." }
```

**Or** create `config/retry_planner_config.yaml` if policy_config is not the right home.

### 4.3 Loading Contract (Specification Only)

1. **Prompts:**
   - Load via `get_registry().get(name, variables={...})` or `get_instructions(name, variables={...})`.
   - Variables: `.format_map({k: v or "" for k, v in variables.items()})` (existing loader behavior).
   - Source order: `prompt_versions/{name}/{version}.yaml` → `prompts/{file_stem}.yaml` (existing).

2. **Config:**
   - Rule: **env > yaml > default**
   - agent_runtime, editing_config: keep current pattern (os.getenv with default).
   - For YAML overrides: config module reads policy_config.yaml (or equivalent) and applies. Env overrides YAML when set.
   - Centralize SERENA_PROJECT_DIR in one config accessor; callers import from config.

3. **No new abstractions:**
   - Use existing PromptRegistry, loader.load_prompt, load_from_legacy.
   - Use existing config/ Python modules; extend with new keys.
   - No new config framework.

---

## 5. Prompt Normalization (Step 4)

### edit_proposal_system.yaml (new)

```yaml
system_prompt: |
  You are editing code. Produce a minimal valid patch.

  Your goal is to move the code closer to satisfying the instruction...
  [full PATCH_SYSTEM_PROMPT content]

  Output exactly one JSON object with:
  - action: "text_sub" or "insert"
  ...
```

### edit_proposal_user.yaml (new)

```yaml
user_prompt: |
  Instruction:
  {{instruction}}

  You are editing file: {{target_file}}
  You MUST ONLY modify this file. Do not propose changes for other files.

  Target file: {{target_file}}
  Symbol: {{symbol}}

  Relevant context (when replacing, copy exact text from Full file content below):
  {{evidence}}

  Full file content:
  ```
  {{full_content}}
  ```

  Produce a minimal valid patch (JSON only). For text_sub: "old" must be an exact copy from the file above. If unsure, propose a minimal change that moves the code toward satisfying the instruction.
```

### retry_planner_user (add to retry_planner_system.yaml or separate)

```yaml
user_prompt: |
  Goal: {{goal}}
  Diagnosis:
    failure_type: {{failure_type}}
    affected_step: {{affected_step}}
    suggestion: {{suggestion}}

  Produce retry hints as JSON.
```

---

## 6. Summary

| Category | Count | Action |
|----------|-------|--------|
| Hardcoded prompts | 4 | Move to YAML; add edit_proposal_system, edit_proposal_user, retry_planner_user |
| Inline config | 12+ | Move to config/ modules or policy_config.yaml |
| Env vars | 6 | Document; some already in config, some scattered |
| Duplicated prompts | 1 | patch_generator vs edit_proposal — clarify/rename; edit_proposal is primary for current flow |

**Registry additions:** `edit_proposal` (system + user).  
**Config additions:** MAX_SEMANTIC_RETRIES, EDIT_PROPOSAL_MAX_CONTENT, EDIT_PROPOSAL_EVIDENCE_MAX, SEMANTIC_FEEDBACK_MAX_SUMMARY, ENABLE_DIFF_PLANNER, REASON_TRUNCATE_LEN, RETRY_QUERY_MAX_LEN, RETRY_SUGGESTION_FALLBACK_MAX, retry_planner strategies/aliases/fallback.
