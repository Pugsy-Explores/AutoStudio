# Phase 14 — Diff viewer (patch visualization)

**Scope:** This document is the authoritative Phase 14 specification. It exposes **code changes (diffs)** as first-class, inspectable artifacts in the **execution graph** and **UI**. It builds on **Phase 9** (trace), **Phase 12** (graph + UI), and assumes **Phase 13** (LLM visibility — *why* it decided). This file is **not** executable.

**Relationship to Phase 13**

```text
Phase 13 → "why it decided"
Phase 14 → "what it actually changed"
```

Without Phase 14 you cannot trust the agent on real codebases.

---

## Objective

```text
Expose code changes (diffs) as first-class, inspectable artifacts in the execution graph + UI
```

---

## Why this is critical

Today the system may:

```text
execute edit → apply patch → maybe run tests
```

…but you **cannot easily inspect**:

```text
What exactly changed?
Was it correct?
Did it touch unintended code?
```

### Current visibility gap

```text
[edit] → success
```

### Needed visibility

```text
[edit]
  ↓
[DIFF NODE]
  ↓
[file before → file after]
```

This is how Cursor / Devin-style products build trust.

---

## Design principle

```text
Diff is NOT metadata
Diff is a PRIMARY artifact of execution
```

---

## Target graph (after Phase 14)

### Example

```text
[LLM:arg_gen]
      ↓
[edit]
      ↓
[DIFF]
      ↓
[run_tests]
```

The **diff node** sits **between edit and the next step** (`edit → diff → next`).

---

## What to capture

### For every successful `edit` step (per touched file or aggregate policy)

```text
- file path
- before content (trimmed)   [optional in trace — see storage rule below]
- after content (trimmed)    [optional]
- unified diff (preferred)
```

**Minimum required in persisted trace:**

```text
- unified diff
- file path
```

**Optional (nice to have):**

```text
- lines added / removed
- patch size
```

---

## Backend design

### 1. Schema extension

Extend trace / graph step typing so steps can represent a diff artifact.

**Trace:** extend **`TraceStep.kind`** (see `agent_v2/schemas/trace.py` — **not** a separate `type` field on `TraceStep`).

```python
kind: Literal["tool", "llm", "diff", "memory"]  # diff when this step is a patch artifact
```

**Graph:** extend **`GraphNode.type`** with **`"diff"`** (projection layer; Phase 12 graph model).

**Diff-specific payload (illustrative):**

```python
{
  "path": "agent_v2/runtime/plan_executor.py",
  "diff": "... unified diff ...",
  "added": 12,
  "removed": 4
}
```

Amend **`SCHEMAS.md`** / **`TraceStep`** contracts when implementing; do not invent parallel schema files.

---

### 2. Where to hook (critical)

**In the edit execution path**, after a patch is applied successfully.

You already have something like:

```text
editing.patch_executor.execute_patch(...)
```

**Illustrative flow:**

```python
before = read_file(path)

result = execute_patch(...)

after = read_file(path)
```

**Generate unified diff:**

```python
import difflib

diff = "\n".join(difflib.unified_diff(
    before.splitlines(),
    after.splitlines(),
    fromfile="before",
    tofile="after",
    lineterm=""
))
```

Hook placement must respect architecture: **all edits go through the editing pipeline / patch executor**; the diff is recorded **after** the patch is applied and **only** on success.

---

### 3. Trace emitter extension

**Add:**

```python
def record_diff(self, path, diff, added, removed):
    self.steps.append(
        TraceStep(
            step_id=...,
            plan_step_index=...,
            action="diff",
            target=path,
            success=True,
            duration_ms=0,
            kind="diff",
            input={"path": path},
            output={
                "diff": truncate(diff),
                "added": added,
                "removed": removed,
            },
        )
    )
```

**Order (non-negotiable):**

```text
edit → diff → next step
```

---

### 4. Plan executor integration

Inside `_run_with_retry` or the edit handler (exact location follows existing `plan_executor` structure):

```python
if step.action == "edit" and result.success:
    before = ...
    after = ...
    diff = ...

    trace_emitter.record_diff(...)
```

Emit **one diff step per policy** (e.g. per file vs single aggregated node); document the chosen policy in implementation notes.

---

### 5. Graph builder

**Node type** (`GraphNode` — projection layer; not `TraceStep`):

```python
node.type = "diff"
```

**Edge logic:**

```text
edit → diff → next step
```

Align with **`ExecutionGraph` / `GraphNode`** from Phase 12; extend literals consistently.

---

## UI design

### Node style

```text
Diff node:
- color: orange
- icon: file/code
- label: filename (or basename + path tooltip)
```

### Detail panel (most important)

**Must show:**

```text
File path
Unified diff (syntax highlighted)
Added lines (+)
Removed lines (-)
```

### UX requirements

1. **Syntax highlighting:** green = added, red = removed.
2. **Collapsible diff:** e.g. `[+ show diff]` default collapsed for large payloads.
3. **Side-by-side toggle:** optional later.
4. **Copy patch** button.

---

## Testing

**Add:** `tests/test_diff_visualization.py` (path may follow repo test layout).

**Validate:**

```text
- diff generated correctly
- empty diff not recorded (or explicitly no-op — pick one policy and test it)
- diff node inserted after edit in trace order
- serialization works (Trace / JSON round-trip)
```

---

## Edge cases

| Case | Behavior |
|------|----------|
| **Large diffs** | Truncate to N lines (e.g. 200) in trace output; full diff optional behind dev flag or file artifact — default must stay bounded. |
| **Binary files** | Skip unified diff; show **binary file changed** (or skip diff node). |
| **Failed edit** | **No** diff node. |

---

## Storage rule (non-negotiable)

**Do not** store full file contents in the trace.

**Only store:**

```text
diff
```

(and path, optional added/removed counts)

This prevents memory explosion and UI slowdown.

---

## Expected impact

**Before**

```text
"edit succeeded" → unclear what changed
```

**After**

```text
"edit succeeded" → SEE EXACT CHANGE → validate instantly
```

### Debugging power unlocked

- Catch bad patches quickly.
- See unintended edits.
- Debug failed tests with visual context.
- Verify planner correctness against actual file deltas.

---

## Implementation plan (ordered)

1. Hook into patch execution (post-success).
2. Generate unified diff (`difflib` or equivalent).
3. Extend `TraceEmitter` with `record_diff`.
4. Update graph builder (`type="diff"`, edges `edit → diff → next`).
5. Update UI (node + detail panel).
6. Add tests.

---

## Principal engineer note

```text
Phase 13 = Why it thought
Phase 14 = What it did
```

Together:

```text
Full system transparency: reasoning (13) + mechanical change (14)
```

---

## Next (Phase 15 — preview)

After Phase 14, the next logical phase is **Phase 15 — Replay mode** (step-by-step execution).

**First deliverable suggestion for validation:**

```text
- one diff node example
- one graph snapshot
```

Then refine (especially large-diff handling + UI ergonomics).
