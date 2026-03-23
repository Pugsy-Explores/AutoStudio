# REACT PROMPT AUDIT — BEHAVIORAL FAILURE ANALYSIS

**Scope:** Audit current ReAct prompts for behavioral failures. Diagnosis only; no fixes or redesign.

---

## 1. PROMPT LOCATIONS + TEXT

### 1.1 System prompt (main instructions)

**File:** `agent/orchestrator/execution_loop.py` (lines 80–161)

**Variable:** `_REACT_SYSTEM_PROMPT`

**Full prompt text:**

```
You are an expert software engineer working on a codebase.

Your goal is to solve the given task by interacting with tools.

You must follow a strict loop:

1. Think about the problem
2. Choose ONE action
3. Provide arguments
4. Wait for observation
5. Repeat until solved

---

## RULES (VERY IMPORTANT)

- You can only take ONE action at a time
- Always use tools — do not guess code without reading it
- Prefer reading files before editing
- After making an edit, ALWAYS run tests
- Use search if you don't know where to look
- Do NOT repeat the same action more than 2–3 times
- If stuck, change strategy (e.g., search → read → edit)
- Do not assume correctness — verify using tools.
- Before editing, make sure you have identified the correct file and location.

---

## WHEN TO USE EACH TOOL

- search:
  Use when you don't know where the relevant code is

- open_file:
  Use to inspect code before making changes

- edit:
  Use to fix or modify code based on findings

- run_tests:
  Use after editing to verify correctness

- finish:
  Use ONLY when:
    - tests pass
    - task is fully solved

---

## EDITING GUIDELINES

- Make minimal, precise changes
- Fix root cause, not symptoms
- Do NOT rewrite entire files unless necessary
- If a test fails, use the error message to guide the fix

---

## OUTPUT FORMAT (STRICT)

You MUST respond in this exact format:

Thought: <your reasoning>
Action: <one of [search, open_file, edit, run_tests, finish]>
Args: <valid JSON>

---

## TASK

{instruction}

---

## HISTORY (previous steps)

{react_history}
```

### 1.2 Action prompt (continuation)

**File:** `agent/orchestrator/execution_loop.py` (lines 202–207)

The system prompt is concatenated with `"\n\nThought:"` and sent as a single user message. There is no separate action prompt. The model is expected to complete the Thought and then emit `Action:` and `Args:`.

```python
prompt = prompt.rstrip() + "\n\nThought:"
out = call_reasoning_model(prompt, task_name="REACT_ACTION")
```

### 1.3 Retry / failure prompt injections

**File:** `agent/orchestrator/execution_loop.py` (lines 210–228)

Retries are implemented by appending observations to `react_history` and re-calling the LLM. No separate retry prompt text is injected.

| Condition | Observation appended | Retry behavior |
|-----------|----------------------|----------------|
| Parse failed (no Action) | `"Parse error: could not extract Action. Use format: Thought: ... Action: <tool> Args: <json>"` | Retry once |
| Invalid action name | `"Invalid action '<action>'. Use one of: search, open_file, edit, run_tests, finish"` | Retry once |

### 1.4 Observation formatting

**File:** `agent/orchestrator/execution_loop.py` (lines 183–197, 253–286)

- **History format:** `_format_react_history()` outputs:
  ```
  Thought: <thought>
  Action: <action>
  Args: <json>
  Observation: <observation>
  ```

- **Observation builders (`_build_react_observation`):**
  - SEARCH: JSON `{success, results_count, preview}` or raw output (truncated)
  - READ: file content (truncated to 8000 chars)
  - EDIT: JSON `{success, error, test_output}` or similar
  - RUN_TEST: "All tests passed. If task is complete, call finish." or "Tests failed:\n\n{output}\n\nUse this to fix the issue."

- **Repeated-action warning (`_repeated_action_guard`):** `"You are repeating the same action. Try a different approach."` appended to observation when same action repeated 3+ times.

---

## 2. SCHEMA ENFORCEMENT AUDIT

| Tool | Required args (system) | Is schema explicitly defined in prompt? | Strict or suggestive? |
|------|------------------------|----------------------------------------|------------------------|
| **search** | `query` (non-empty) | **no** | — |
| **open_file** | `path` | **no** | — |
| **edit** | `instruction` | **no** | — |
| **run_tests** | none | **no** | — |
| **finish** | none | **no** | — |

The prompt says only:

> `Args: <valid JSON>`

No per-tool schema, no `query`/`path`/`instruction` specification. The model has no prompt-level guidance on what keys to use.

**System-level validation (in `_dispatch_react`):**

- SEARCH: rejects empty query with `_obs("SEARCH requires a query. Use Args: {\"query\": \"<search terms>\"}")`
- READ: rejects empty path with `_obs("READ requires path. Use Args: {\"path\": \"<file path>\"}")`
- EDIT: no validation of `instruction`; no rejection of `path`/`content` or other wrong schemas

---

## 3. WORKFLOW ENFORCEMENT AUDIT

| Aspect | Explicit workflow in prompt? | Mandatory or optional? |
|--------|-----------------------------|-------------------------|
| search → open_file → edit → run_tests | **Partially** | **Optional** |

**Relevant text:**

- RULES: "Prefer reading files before editing" — suggestive, not mandatory
- RULES: "After making an edit, ALWAYS run tests" — strong for post-edit, weak on pre-edit flow
- RULES: "Use search if you don't know where to look" — conditional
- RULES: "If stuck, change strategy (e.g., search → read → edit)" — example only, not a required sequence

**Conclusion:** No mandatory workflow. The prompt does not state that EDIT must be preceded by search and/or open_file, nor that a strict order is required.

---

## 4. FAILURE HANDLING AUDIT

| Failure type | Present in prompt? | Quality of guidance |
|--------------|--------------------|---------------------|
| Search fails | **no** | — |
| Edit fails | **no** | — |
| Tests fail | **weak** | "If a test fails, use the error message to guide the fix" (EDITING GUIDELINES) |

The prompt does not tell the model:

- What to do when search returns no or poor results
- What to do when edit fails (e.g., patch rejection, syntax error)
- How to iterate after test failure beyond "use error message"
- That empty search query will produce an error observation and that a new, non-empty query is needed

Observations are returned by the system (e.g., "SEARCH requires a query...") but the prompt never instructs the model to fix invalid args or retry with correct schema.

---

## 5. ARGUMENT VALIDATION GAP

### Failure examples (from user report and code analysis)

| Failure | Observed | Root cause in prompt |
|---------|----------|----------------------|
| SEARCH with empty args | Yes | No instruction that `query` must be non-empty; no example of `Args` for search |
| EDIT with `path`/`content` instead of `instruction` | Yes | No per-tool schema; no mention of `instruction` for edit; `path`/`content` aligns with common edit APIs |
| EDIT with empty instruction | Yes | No validation text; no warning that edit needs a clear instruction |
| run_tests with args | Possible | No explicit "run_tests takes no args" or `Args: {}` |

### Prompt-level gaps

- `Args: <valid JSON>` gives no structure. The model can choose arbitrary keys.
- "edit: Use to fix or modify code based on findings" does not specify `instruction`.
- No contrast with incorrect schemas (e.g., "Do NOT use path/content for edit; use instruction").
- Schema hints exist only in system-generated error observations, not in the initial instructions.

---

## 6. ACTION MISUSE CAUSES (PROMPT-LEVEL)

### Why model used edit with path/content

- Prompt never states that edit expects `instruction`.
- Common pattern in tools: edit = (path, content) or (file, diff).
- No explicit "edit uses instruction, not path/content" in RULES or WHEN TO USE EACH TOOL.

### Why search used empty args

- Prompt does not say search requires a non-empty `query`.
- "Use search if you don't know where to look" is use-case only, not arg spec.
- `Args: <valid JSON>` allows `{}`; no example like `{"query": "..."}`.
- Model can infer that search is needed but not what to pass.

### Why workflow was skipped

- No required sequence; only "Prefer" and "If stuck, change strategy".
- Model can rationally try edit first, especially for simple tasks.
- No statement that edit depends on prior search/open_file for correct behavior.

---

## 7. TOP 5 PROMPT WEAKNESSES

| # | Observed failure | Exact missing or weak instruction |
|---|------------------|-----------------------------------|
| 1 | SEARCH called with empty args | No instruction that search requires non-empty `query`; no example `Args: {"query": "..."}`. Only generic `Args: <valid JSON>`. |
| 2 | EDIT called with path/content instead of instruction | No schema for edit. No statement that edit takes `instruction` (natural-language change description), not `path`/`content`. |
| 3 | Model not following search → open_file → edit → run_tests | Workflow described only as "Prefer" and "If stuck" example. No mandatory order; no statement that edit needs prior search/open_file. |
| 4 | No patches applied (edit fails or produces no changes) | No guidance on edit failure; no instruction that edit needs a clear, specific instruction; no handling for empty/invalid instruction. |
| 5 | Unclear how to react to failure observations | No instruction to correct invalid args after observations like "SEARCH requires a query" or schema errors. Retries occur but model is not told to fix the schema. |

---

## APPENDIX: REFERENCE

**Step→Args mapping in code** (`execution_loop.py` lines 234–245):

- search: `step["query"] = args.get("query", "")`
- open_file: `step["path"] = args.get("path") or args.get("file", "")`
- edit: `step["description"] = args.get("instruction", "")`
- run_tests: `step["description"] = ""` (no args read)

**Dispatcher validation** (`step_dispatcher.py` `_dispatch_react`):

- SEARCH: rejects empty `query` via `step.get("query") or step.get("description")`
- READ: rejects empty `path`
- EDIT: no validation
- RUN_TEST: no validation
