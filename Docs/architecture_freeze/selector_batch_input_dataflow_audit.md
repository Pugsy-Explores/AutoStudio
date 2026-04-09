# Selector Batch Input Dataflow Audit

## Scope

This audit traces only the data flow that builds Selector Batch prompt input:

- `candidates_json`
- normalized batch text (`normalize_selector_batch(...)`)

No code changes proposed in this document.

---

## 1) Data Flow (Step-by-Step)

1. **Discovery builds `ExplorationCandidate` rows**
   - `ExplorationEngineV2._discovery(...)`
   - Merges multi-channel search results by canonical file path.
   - Creates per-file `ExplorationCandidate` with:
     - `file_path`, `symbol`, `symbols`
     - `snippet`, `snippet_summary`
     - `source`, `source_channels`
2. **Scoper may reduce candidate set**
   - `ExplorationEngineV2._enqueue_ranked(...)` calls scoper when enabled.
   - Output `scoped` candidates feed selector.
3. **Selector top slice**
   - `CandidateSelector.select_batch(...)`
   - `top = candidates[:EXPLORATION_SELECTOR_TOP_K]`
4. **Outline rows generated per top candidate**
   - Engine builds `outline_rows` via:
     - `_outline_rows_for_selector_batch(...)`
     - `load_python_file_outline(...)`
     - `rank_outline_for_selector_query(...)`
5. **Per-item selector payload assembled**
   - `_selector_candidate_payload(...)`
   - attaches:
     - candidate metadata + summaries
     - `outline_for_prompt` (if available)
6. **Code in outline is bounded/trimmed**
   - `prepare_outline_for_selector_prompt(...)`
7. **Explored locations block serialized**
   - `format_explored_locations_for_prompt(...)`
8. **Final normalized selector batch text built**
   - `normalize_selector_batch(...)`
9. **Prompt template renders final message payload**
   - system/user prompt from `exploration.selector.batch` template
   - normalized batch injected as `{candidates_json}`

---

## 2) Field-Wise Origin Map

## A) `snippet_summary`

`snippet_summary` in selector payload comes from:

- `_selector_candidate_payload(...)`:
  - `c.snippet_summary or c.snippet`

Upstream construction:

- In `_discovery(...)`, rows from all channels ingest:
  - `row["snippet"]` or `row["content"]`
- merged per file via `_merge_discovery_snippets(...)`:
  - dedupe exact snippet strings
  - join with `\n---\n`
  - truncate to `EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS`
- assigned to candidate as:
  - `snippet_summary = summary`
  - `snippet = summary[:MAX_SNIPPET_CHARS]` (legacy short field)

Normalization behavior:

- `normalize_selector_batch(...)` calls `_emit_preview_then_full(...)`
- `snippet_summary` may emit:
  - `snippet_summary_preview` (head/tail)
  - `snippet_summary_full` (full text)
- For short summaries, emits single `snippet_summary`.

Sources represented:

- symbol query channel (`graph`)
- regex channel (`grep`)
- text channel (`vector`)

## B) `outline_for_prompt`

Origin:

- `ExplorationEngineV2._outline_rows_for_selector_batch(...)`
  - per candidate file:
    - `load_python_file_outline(file_path)`
    - `rank_outline_for_selector_query(...)`

`load_python_file_outline(...)` details:

- parses local `.py` file via repo parser/symbol extractor
- emits rows:
  - `name`, `type` (`function|class|method`)
  - `start_line`, `end_line`
  - `code` (exact source lines for symbol range)

Selector transform:

- `prepare_outline_for_selector_prompt(...)`
  - deterministic sort by name/type/start_line
  - full bodies until budget
  - overflow as signatures only
  - overflow signatures get `[trimmed]` prefixes
  - first overflow row gets trim notice marker

Final form in normalized batch:

- `outline_for_prompt:` list of dict entries
- each entry includes keys emitted in sorted order (e.g. code/end_line/name/start_line/type)

## C) `symbol` / `symbols`

Upstream:

- `_discovery(...)` ingests `row["symbol"]` from search results.
- Per-file merge stores ordered unique symbols in `symbols_order`.
- Candidate fields:
  - `symbol = symbols_order[0]` (primary symbol)
  - `symbols = symbols_order` (all merged per file)

Selector payload:

- `symbol`: single scalar
- `symbols`: list copy

## D) `source` / `source_channels`

Source assignment:

- channel mapping in `_discovery_query_channel_to_source(...)`:
  - `symbol` query -> `graph`
  - `regex` query -> `grep`
  - `text` query -> `vector`

Merge behavior:

- per-file ordered unique `sources_order` across hits/channels

Candidate fields:

- `source = sources_order[0]` (primary source)
- `source_channels = sources_order` (all contributing channels)

Selector payload:

- both fields are included.

## E) `explored_locations` (`explored_block`)

Origin:

- `ex_state.explored_location_keys` (set of `(canonical_file_path, symbol)`).

Serialization:

- `format_explored_locations_for_prompt(...)`
  - sorts by `(file_path, symbol)`
  - caps rows by `EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K`
  - emits verbose human-readable nested list.

## F) `normalize_selector_batch(...)`

Assembly:

- global header:
  - instruction, intent, limit, explored_block
- per item:
  - `id`, `file_path`, `symbol`, `source`
  - `symbols`, `snippet_summary` (preview/full mode), `source_channels`
  - `outline_for_prompt`, `repo`, optional extras

Formatting overhead:

- delimiter wrappers for each item
- YAML-like nested indentation
- preview + full duplication for large fields

---

## 3) Redundancy Findings (By Field)

## `snippet_summary`

- Classification: **duplicated signal** (in many cases)
- Why:
  - summary can overlap code shown in `outline_for_prompt.code`
  - preview + full mode duplicates same content in two sections
- Unique value:
  - non-symbol snippets from retrieval can contain context not in outlines

## `outline_for_prompt`

- Classification: **high-value unique signal**
- Why:
  - direct symbol-level code/signature grounding
  - deterministic structure and bounded code path
- Redundancy:
  - overlaps with snippet summaries when snippets include same source regions

## `symbol` vs `symbols`

- Classification:
  - `symbol`: **partially duplicated**
  - `symbols`: **high-value list**
- Why:
  - `symbol` is often first entry of `symbols` (same signal repeated)

## `source` vs `source_channels`

- Classification:
  - `source`: **partially duplicated**
  - `source_channels`: **unique aggregated provenance**
- Why:
  - primary source duplicates first channel in many rows

## `explored_block`

- Classification: **low/medium-value verbose signal**
- Why:
  - can be long and structurally repetitive
  - mostly anti-repeat guardrail text, not core target content

## Normalization scaffolding (delimiters/labels)

- Classification: **format overhead**
- Why:
  - repeated structural text per item and per section
  - aids parseability but adds tokens without new semantic payload

---

## 4) FIELD -> SOURCE -> TRANSFORMATIONS -> FINAL FORM

- `snippet_summary`
  - source: retrieval rows (`snippet` / `content`) across graph+grep+vector
  - transforms: dedupe + join + merge-cap truncation; then selector preview/full expansion
  - final: `snippet_summary` or (`snippet_summary_preview` + `snippet_summary_full`)

- `outline_for_prompt`
  - source: file AST/symbol extraction (`load_python_file_outline`)
  - transforms: query relevance ranking; deterministic trim/full-signature selection
  - final: list of symbol dict rows including bounded `code`

- `symbol`
  - source: first merged symbol for file
  - transforms: first-of-list projection from `symbols_order`
  - final: scalar item field

- `symbols`
  - source: merged unique symbols per file
  - transforms: ordered dedupe
  - final: list item field

- `source`
  - source: first contributing channel mapped to literal
  - transforms: first-of-list projection from `sources_order`
  - final: scalar item field (`graph|grep|vector`)

- `source_channels`
  - source: all contributing channels
  - transforms: ordered dedupe
  - final: list item field

- `explored_block`
  - source: `ex_state.explored_location_keys`
  - transforms: sort + top-k cap + verbose serialization
  - final: multiline global block

---

## 5) Top 3 Root Causes of Prompt Bloat

1. **`snippet_summary` preview + full duplication**
   - same content represented twice for long fields.
2. **Parallel inclusion of retrieval summaries and symbol code**
   - `snippet_summary` and `outline_for_prompt.code` frequently overlap semantically.
3. **Repeated structural verbosity**
   - per-item delimiters, labels, nested formatting, and explored-block prose add significant non-content tokens.

---

## 6) Gaps / Control Blind Spots (Audit-Only)

- Global selector prompt budget control is not present at normalized-batch composition level.
- Code budget exists for `outline_for_prompt.code`, but no equivalent global cap for total selector payload text.
- Multiple partially duplicated fields (`symbol`/`symbols`, `source`/`source_channels`) are always emitted.

