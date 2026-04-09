# Selector Batch Prompt Audit Report

## Purpose

Detailed audit of everything included in the Selector Batch prompt, where each part is built, and how it contributes to total prompt size.

Scope covers:

- `agent_v2/exploration/candidate_selector.py`
- `agent_v2/exploration/llm_input_normalize.py`
- `agent/prompt_versions/exploration.selector.batch/models/qwen2.5-coder-7b/v1.yaml`

---

## 1) End-to-End Construction Path

Prompt build path in `CandidateSelector.select_batch(...)`:

1. Start from top candidates:
   - `top = candidates[:EXPLORATION_SELECTOR_TOP_K]`
2. Build per-item payload:
   - `_selector_candidate_payload(...)`
3. Inject outline code (bounded per candidate):
   - `prepare_outline_for_selector_prompt(ol, MAX_SELECTOR_CODE_CHARS)`
4. Build explored locations block:
   - `format_explored_locations_for_prompt(...)`
5. Normalize batch text:
   - `normalize_selector_batch(...)`
6. Render model prompt template:
   - `get_registry().render_prompt_parts(...)` with `candidates_json`
7. Send as messages:
   - system prompt = full template instructions
   - user prompt = normalized batch text (the `{candidates_json}` content)

Final payload to model is effectively:

- system instructions text (large static block)
- normalized dynamic batch text (potentially very large)

---

## 2) What Is Included in Selector Batch Prompt

## A. System Instructions (static, from prompt YAML)

From `exploration.selector.batch ... v1.yaml`:

- role/identity text
- selection principles and strict rules
- schema requirements and uncertainty rules
- symbol-grounding constraints
- output JSON contract

This static section is non-trivial in length and always present.

Example excerpt:

- `"You MUST first output brief reasoning, then output JSON."`
- `"Never omit selected_indices or selected_symbols."`
- strict output schema for `selected_indices`, `selected_symbols`, `selection_confidence`

## B. Global Batch Header (dynamic)

Produced by `normalize_selector_batch(...)`:

- `[Global]`
- `instruction: <instruction>`
- `intent: <intent>`
- `limit: <limit>`
- `explored_block: | ...`

Example:

```text
[Global]
instruction: How does exploration scope layer works in agent v2 in autostudio
intent: architecture, control-flow
limit: 2
explored_block: |
  Locations already inspected in this run...
```

## C. Explored Locations Block (dynamic)

From `format_explored_locations_for_prompt(...)`:

- human-readable list of `(file_path, symbol)` already inspected
- up to `EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K`

Example:

```text
Locations already inspected in this run...
  -
      file_path: /abs/path/agent_v2/exploration/exploration_scoper.py
      symbol: ExplorationScoper.scope
```

## D. Per-Item Blocks (dynamic, repeated for each top candidate)

For each item, `normalize_selector_batch(...)` emits:

- delimiter start/end
- `id`
- `file_path`
- `symbol`
- `source`
- `symbols` list
- `snippet_summary` (preview + full)
- `source_channels` list
- `outline_for_prompt` entries
- optional `repo`
- optional `extra_fields`

Example skeleton:

```text
----- ITEM 0 START ---
id: 0
file_path: agent_v2/exploration/exploration_scoper.py
symbol: ExplorationScoper.scope
source: grep
symbols:
  - ExplorationScoper.scope
snippet_summary_preview:
  first_20_lines: |
    ...
snippet_summary_full: |
  ...
source_channels:
  - grep
outline_for_prompt:
  -
      code: ...
      end_line: ...
      name: ...
      start_line: ...
      type: function
repo: AutoStudio
----- ITEM 0 END ---
```

## E. `snippet_summary` uses preview + full mode

Important detail from `_emit_preview_then_full(...)`:

- if snippet is long, prompt includes both:
  - `snippet_summary_preview`
  - `snippet_summary_full`

This materially increases token count.

## F. `outline_for_prompt` contains code/signatures

Each outline entry may include:

- `name`
- `type`
- `start_line`
- `end_line`
- `code` (full body or trimmed signature form)

`MAX_SELECTOR_CODE_CHARS` applies to this `code` aggregate **inside each candidate row only**.

---

## 3) Per-Candidate Payload Fields (Exact)

`_selector_candidate_payload(...)` contributes:

- `file_path`
- `symbol`
- `source`
- `symbols`
- `snippet_summary` (`candidate.snippet_summary or candidate.snippet`)
- `source_channels`
- optional `repo`
- optional `outline_for_prompt`

So selector prompt includes both:

- retrieval summary text (`snippet_summary`)
- symbol-level code/signature context (`outline_for_prompt`)

---

## 4) Why Prompt Can Still Exceed Model Context

Even when code trim threshold is applied:

- `MAX_SELECTOR_CODE_CHARS` only bounds `outline_for_prompt.code`
- it does not bound:
  - total prompt chars/tokens
  - static system instructions
  - `snippet_summary_full`
  - number of candidates (`EXPLORATION_SELECTOR_TOP_K`)
  - explored locations block

So total prompt can exceed model context size despite per-candidate code trimming.

---

## 5) Token/Size Risk Contributors (Highest -> Lowest)

1. `snippet_summary_full` per item (often largest text)
2. `outline_for_prompt` multiplied by item count
3. number of item blocks (`top_k`)
4. static system instruction length
5. explored locations block

---

## 6) Concrete Inclusion Checklist

Selector Batch prompt currently includes:

- static model instructions from YAML
- instruction text
- intent text
- selection limit
- explored locations list
- for each candidate:
  - file metadata
  - symbols list
  - snippet summary (preview + full)
  - source channels
  - outline entries with code/signatures
  - optional repo and extras

No hidden payload fields beyond above surfaces.

---

## 7) Operational Implication

Current design is rich/contextual but prompt-size fragile for small context windows unless there is an additional **global prompt budget controller** across all components (not only per-item code trim).

