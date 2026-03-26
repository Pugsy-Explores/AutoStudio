# Config Centralization Implementation Instructions

You are a senior staff engineer centralizing all configuration in Agent V2.

## Goal

Eliminate all hardcoded runtime/business behavior from the code and move it into the central config system.

---

## Non-negotiable outcomes

1. **Single source of truth**
   - Any behavior-affecting value must come from central config.
   - No hardcoded defaults in business logic unless they are explicitly fallback safety defaults in config loader.

2. **Config categories (must exist explicitly)**
   - Static config (model maps, prompt maps, task maps)
   - Runtime config (timeouts, retries, max contexts, search limits)
   - Behavioral config (read_only allowed actions, exploration gating policies, planner action policies)
   - Infra config (paths, artifact dirs, test discovery ignores)

3. **Config authority rule**
   - All code paths must read config via `get_config()` (or equivalent accessor).
   - Ban direct `os.getenv` or inline constants in business modules.

4. **Validation layer**
   - Introduce `validate_config(config)` at startup.
   - Validate required keys and ranges.
   - Validate policy consistency (for example read_only actions must exclude write actions).

5. **Policy extraction (very important from RCA)**
   - Extract these from code into config:
     - `planner.allowed_actions_read_only`
     - `exploration.max_steps`
     - `exploration.allow_partial_for_plan_mode`
     - `pytest.ignore_dirs` (or equivalent test discovery safety config)

6. **No behavior change guard**
   - Current behavior must remain identical unless explicitly changed in config.
   - Add a short parity check section to tests or validation.

---

## Implementation instructions

1. **First: inventory and classify**
   - Scan current hardcoded values and classify into static/runtime/behavioral/infra.
   - Put findings in the audit file section as "Before -> After mapping."

2. **Then: implement config schema updates**
   - Extend existing config files minimally (do not redesign whole architecture).
   - Add only missing keys needed for extracted policies and runtime constants.

3. **Then: wire code to config**
   - Replace direct constants/env reads in business logic with config accessor calls.
   - Keep fallbacks only in config loader.

4. **Then: add validation**
   - Add startup-time validation with clear failure messages.
   - Fail fast on invalid config.

5. **Then: tests**
   - Add/adjust tests for:
     - read_only action policy from config
     - exploration gating behavior from config
     - selector model/task config presence (already partly done; keep)
     - no behavior change parity for existing defaults

6. **Finally: update docs/backlog**
   - Update audit + plan sections with exact implemented file paths.
   - Update backlog row with what is done vs pending.

---

## Output format expected

Return:
1) changed files list  
2) key diffs summary  
3) validation/test evidence  
4) remaining hardcoding items (if any) with priority
