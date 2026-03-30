# Agent V2 — LLM input transformation (lossless)

Design reference for a **pure transformation layer**: raw objects → normalized LLM input blocks. **No upstream schema changes**; only structure/format for readability.

**Constraints:** do not drop fields; do not rename destructively; do not summarize away information; deterministic formatting.

---

## 1. Field enumeration (grounded in code)

**Scoper** (`exploration_scoper.py` → `candidates_json`):

- Global: `instruction`
- Per row (file-deduped): `index`, `file_path`, `sources`, `snippets`, `symbols`

**Selector batch** (`candidate_selector.py` → `_selector_candidate_payload` + batch variables):

- Global: `instruction`, `intent`, `explored_block`, `limit`
- Per item: `file_path`, `symbol`, `source`, `symbols`, `snippet_summary`, `source_channels`, optional `repo`, optional `outline_for_prompt`

**Analyzer** (`understanding_analyzer.py` → template variables):

- `instruction`, `task_intent_summary`, `intent`, `context_blocks` (JSON of `ContextBlock`), `symbol_relationships_block`, `file_path`, `snippet`

`ContextBlock` fields: `file_path`, `start`, `end`, `content`, `origin_reason`, `symbol`, `relationship_refs`

**Audit note:** `ExplorationCandidate` also has `snippet`, `discovery_max_score`, `discovery_rerank_score`; the current selector LLM payload does not expose all of these as separate keys. Lossless normalization operates on **whatever dicts upstream passes**; recovering fields never sent requires upstream to include them.

---

## 2. Canonical transformation strategy

```json
{
  "grouping_unit": "hybrid: scoper = one block per dedupe index (file-aggregated row); selector = one block per batch index; analyzer = global + relationships + one block per context_blocks[i]",
  "ordering_rules": [
    "Global envelope first (instruction, then layer-specific globals).",
    "Analyzer: [Global] → [Relationships] → [Context Block i] — never mix relationships into the global blob.",
    "List items: ascending by stable index (0..n-1).",
    "Selector: strict fixed field order on every item (see §4).",
    "Unknown keys: under extra_fields, rendered as structured text (not JSON blobs).",
    "Large text fields: bounded preview + full body in labeled sections (no token dropped)."
  ],
  "format_style": "text-native structured blocks; strong START/END delimiters per unit; scalars as key: value; lists as indented bullets; no JSON-in-text for extra_fields"
}
```

---

## 3. Normalized formats (per layer)

### 3.1 Scoper

```
[Global]
instruction: ...

----- CANDIDATE 0 START -----
index: ...
file_path: ...
sources:
  - ...
snippets_preview:
  first_20_lines: |
    ...
  last_10_lines: |
    ...
snippets_full:
  - ...   # full strings when under size threshold; else reference or inline per policy
symbols:
  - ...
extra_fields:
  <key>: <text-native value>
----- CANDIDATE 0 END -----
```

**Delimiter rule:** wrap each dedupe row in `----- CANDIDATE {i} START -----` / `----- CANDIDATE {i} END -----`.

**Snippets:** when aggregate snippet text exceeds thresholds, use `snippets_preview` (per-snippet or joined) + `snippets_full` preserving complete strings (see §5).

### 3.2 Selector batch

**Strict field order (always present; use empty string or `[]` when absent):**

`id` → `file_path` → `symbol` → `source` → `symbols` → `snippet_summary` → `source_channels` → `outline_for_prompt` → `repo` → `extra_fields`

```
[Global]
instruction: ...
intent: ...
limit: ...
explored_block: ...

----- ITEM 0 START -----
id: 0
file_path: ...
symbol: ...
source: ...
symbols:
  - ...
snippet_summary: ...
source_channels:
  - ...
outline_for_prompt:
  - name: ...
    type: ...
repo: ...
extra_fields:
  <key>: <value>
----- ITEM 0 END -----
```

### 3.3 Analyzer

**Do not overload global:** relationships live in their own section.

```
[Global]
instruction: ...
intent: ...
task_intent_summary: ...
file_path: ...

[Relationships]
symbol_relationships_block: |
  ...

----- CONTEXT BLOCK 0 START -----
file_path: ...
start: ...
end: ...
symbol: ...
origin_reason: ...
relationship_refs:
  - ...
content_preview:
  first_20_lines: |
    ...
  last_10_lines: |
    ...
content_full: |
  ...
extra_fields:
  ...
----- CONTEXT BLOCK 0 END -----
```

Global `snippet` (engine concatenation): either mirror in `[Global]` as `snippet_preview` + `snippet_full`, or only `snippet_preview` + full under `snippet_full` to avoid duplicating the same bytes twice in the prompt — **choose one policy; store nothing lossy.**

---

## 4. Field preservation mapping

| Source | Target |
|--------|--------|
| Known keys | Same names, first-class lines |
| Unknown keys | `extra_fields` as **text-native** key/value (nested dicts → indented blocks, lists → bullets, scalars → plain lines) — **no `json.dumps` in the prompt** |

---

## 5. Large fields — token control (no loss)

**Applies to:** scoper `snippets` (and joined text), analyzer `content` / global `snippet`.

**Policy:**

- Define thresholds: e.g. `PREVIEW_HEAD_LINES = 20`, `PREVIEW_TAIL_LINES = 10`.
- If line count ≤ head + tail + margin, emit **only** `content_full` (or `snippet_full`) without preview split.
- If larger:
  - `content_preview.first_20_lines` / `last_10_lines` (or `snippets_preview` analog)
  - `content_full` / `snippets_full` **must retain the entire original string** in the same logical item (or immediately following under an explicit `FULL` subsection).

The model is guided to read preview first; full data remains available for faithful reasoning.

---

## 6. Implementation: split functions (not one generic entry)

Replace `normalize_for_llm(layer, raw_items)` with:

```python
def normalize_scoper(
    *,
    instruction: str,
    rows: list[dict],
    preview_line_limits: tuple[int, int] = (20, 10),
) -> str: ...


def normalize_selector_batch(
    *,
    instruction: str,
    intent: str,
    limit: int,
    explored_block: str,
    items: list[dict],
    preview_line_limits: tuple[int, int] = (20, 10),
) -> str: ...


def normalize_analyzer(
    *,
    instruction: str,
    intent: str,
    task_intent_summary: str,
    file_path: str,
    snippet: str,
    symbol_relationships_block: str,
    context_blocks: list[dict],
    preview_line_limits: tuple[int, int] = (20, 10),
) -> str: ...
```

Each adapter owns delimiter strings, strict key order (selector), and preview/full rules.

**Shared helpers (private):**

- `_render_extra_fields_text_native(d: dict) -> str` — recursive text layout, no JSON.
- `_split_preview_full(text: str, head: int, tail: int) -> tuple[str, str, str]` — returns `(preview_block, full_block, mode)` where mode is `full_only` | `preview_plus_full`.
- `_delimiter(name: str, i: int, which: Literal["start", "end"]) -> str` — e.g. `----- ITEM {i} START -----`.

---

## 7. Optional future: stable secondary ordering

Preserve upstream list order by default. If needed later, add an optional `sort_symbols: bool = False` path that sorts lexicographically **after** copying originals into `extra_fields.original_order` — only if product accepts that tradeoff; **not required for v1**.

---

## 8. Before / after (illustrative)

### Scoper — BEFORE (raw JSON in prompt)

```json
[{"index": 0, "file_path": "src/a.py", "sources": ["graph"], "snippets": ["def x():"], "symbols": ["x"]}]
```

### Scoper — AFTER (text-native + delimiter)

```
[Global]
instruction: Find the entrypoint.

----- CANDIDATE 0 START -----
index: 0
file_path: src/a.py
sources:
  - graph
snippets_full:
  - |
    def x():
      ...
symbols:
  - x
----- CANDIDATE 0 END -----
```

### Selector — AFTER (strict order + delimiter)

```
----- ITEM 0 START -----
id: 0
file_path: src/a.py
symbol: x
source: graph
symbols:
  - x
snippet_summary: def x(): ...
source_channels:
  - graph
outline_for_prompt:
repo:
extra_fields:
----- ITEM 0 END -----
```

(`repo:` / `outline_for_prompt:` empty when absent — still parallel structure.)

### Analyzer — AFTER (sections separated)

```
[Global]
instruction: ...
intent: ...
task_intent_summary: ...
file_path: ...

[Relationships]
symbol_relationships_block: |
  (graph hints)

----- CONTEXT BLOCK 0 START -----
file_path: ...
...
----- CONTEXT BLOCK 0 END -----
```

---

## 9. Final implementation checklist

| Step | Action |
|------|--------|
| ✅ | Keep schema and lossless mapping |
| 🔧 | Replace JSON-in-text → **pure structured text** for `extra_fields` |
| 🔧 | Add **strong** `----- UNIT i START/END -----` delimiters (candidates, items, context blocks) |
| 🔧 | **Separate** analyzer: `[Global]` vs `[Relationships]` vs per-block sections |
| 🔧 | **Enforce strict field order** on selector items (always same keys) |
| 🔧 | **Preview + full** for large `snippets` / `content` / `snippet` (bounded display + raw fallback) |
| 🔧 | **Split** transformers: `normalize_scoper` / `normalize_selector_batch` / `normalize_analyzer` |

---

## 10. Rationale (7B-oriented)

1. **Text-native `extra_fields`** — avoids mixed “JSON thinking” + YAML; reduces parsing burden vs `json.dumps` in prose.
2. **Analyzer sectioning** — relationships are not buried in `[Global]`; blocks are delimited clearly.
3. **Delimiters** — explicit unit boundaries improve segmentation and consistent reasoning across items.
4. **Selector parallel structure** — fixed key order and empty placeholders keep rows comparable for ranking.
5. **Preview + full** — guides attention without dropping tokens; full text remains available.
6. **Per-layer functions** — clearer evolution, fewer generic-branch bugs.

---

*Document version: 2 — incorporates text-native extras, delimiters, analyzer split, selector ordering, preview/full, and split normalizers.*
