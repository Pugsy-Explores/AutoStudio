# Phase 15 — Replay mode (step-by-step execution)

**Scope:** This document is the authoritative Phase 15 specification. It defines **deterministic, step-by-step playback** of a **completed** agent run from **recorded trace + artifacts** — *not* re-execution. It builds on **Phase 9** (trace), **Phase 12** (graph + UI), **Phase 13** (LLM visibility), and **Phase 14** (diff nodes). This file is **not** executable.

**Principal framing**

```text
Replay Mode is NOT a UI feature.
It is a time-travel debugging system.
```

Implemented shallowly, it is useless. Implemented correctly, it becomes the **most powerful debugging + evaluation tool** in the stack — Cursor/Devin-level **controllability** over a past run.

---

## Objective

```text
Allow deterministic, step-by-step playback of a completed agent run,
including LLM decisions, tool calls, diffs, and state transitions (as reconstructable from the trace).
```

---

## Why this is critical

You can already:

```text
see trace ✅
see graph ✅
see diffs ✅   (Phase 14)
```

You **cannot** yet:

```text
pause
step forward
inspect intermediate state
re-run from a specific step   (that is Phase 16+ — see below)
```

**Today**

```text
Debugging = passive (scroll, guess)
```

**After Phase 15**

```text
Debugging = interactive + controllable (within recorded data)
```

---

## Design principle

```text
Replay is NOT re-execution.
Replay is deterministic reconstruction from recorded trace (+ stored artifacts).
```

---

## Hard rule (non-negotiable)

```text
Replay must NOT call LLMs or tools
```

Replay **only** uses:

```text
Trace + stored artifacts (prompts, outputs, diffs, etc. as already recorded)
```

Any path that invokes models or side-effecting tools during “replay” violates this phase.

---

## Foundation already in place

| Piece | Phase |
|-------|--------|
| Structured trace | 9 |
| LLM nodes / reasoning visibility | 13 |
| Diff nodes | 14 |
| Execution graph | 12 |

That is enough to implement replay **cleanly** if steps are well-identified and payloads are complete.

---

## Core design

### 1. Replay input

```python
class ReplayInput:
    trace: Trace
```

(v1: `ReplayEngine` may take `Trace` directly; `ReplayInput` is the conceptual contract.)

---

### 2. Replay state

```python
class ReplayState:
    current_step_index: int
    steps: List[TraceStep]
```

(Index semantics: document whether “current” means *last completed* or *cursor before next* — pick one and test it.)

---

### 3. Replay engine

**New file (illustrative):**

```text
agent_v2/runtime/replay_engine.py
```

**Core API (illustrative):**

```python
class ReplayEngine:
    def __init__(self, trace: Trace):
        self.trace = trace
        self.index = 0

    def step_forward(self) -> TraceStep:
        step = self.trace.steps[self.index]
        self.index += 1
        return step

    def step_back(self) -> TraceStep:
        self.index = max(0, self.index - 1)
        return self.trace.steps[self.index]

    def seek(self, index: int) -> TraceStep:
        self.index = index
        return self.trace.steps[index]

    def reset(self):
        self.index = 0
```

**Invariants**

```text
NO side effects
NO execution (no LLM, no tools, no patch application)
PURE playback / navigation over recorded steps
```

**Implementation note:** Treat the source `Trace` as **immutable** — replay must not mutate original step lists or payloads.

---

### 4. State reconstruction (critical)

**Problem:** The trace lists steps, but “replay” UX often needs **conceptual state at step k** (e.g. what the UI should show as “world state” at that point).

**Two approaches**

| Approach | Description |
|----------|-------------|
| **A — Reconstruct (recommended for v1)** | Build state incrementally by folding `trace.steps[0..index)` with pure `apply(step, state)` rules. |
| **B — Snapshots** | Persist optional snapshots at key boundaries (heavier schema; use if reconstruction is insufficient). |

**Illustrative reconstruction**

```python
def build_state_until(index):
    state = empty_state()

    for i in range(index):
        step = trace.steps[i]
        apply(step, state)

    return state
```

**Illustrative apply rules (tune to real `TraceStep` types)**

| Step type | Effect on reconstructed state |
|-----------|------------------------------|
| `llm` | No filesystem mutation; surface prompt/output from `input`/`output` for UI |
| `tool` | Update *display* history / tool transcript (from recorded fields only) |
| `diff` | Optionally reflect file change in a **virtual** file map for UI (from stored diff — **do not** write to disk) |

**Boundary:** Reconstructed state is for **inspection and UI**, not for executing the agent again.

---

### 5. Trace extension (minor)

Ensure each `TraceStep` reliably exposes (aligned with **SCHEMAS.md**):

```text
step.id
step.type
step.name
step.input
step.output
```

If gaps exist, fix in trace emission — replay should not guess missing fields.

---

### 6. Graph integration

Attach replay position to graph projection so the UI can sync:

```python
node.replay_index = i
```

**UI:** highlight **active** node for current step; keep timeline and graph in sync.

---

## UI design (where the value lands)

### Controls (must have)

```text
▶ Play
⏸ Pause
⏭ Step forward
⏮ Step backward
🔁 Restart
```

(“Play” may auto-advance on a timer for demo; core value is **single-step** + **seek**.)

### View at each step

```text
- current node (highlighted)
- step details
- diff (if step is diff / or diff attached to edit)
- LLM prompt/output (if step is LLM)
```

### Critical UX: timeline slider

```text
[====●======]
```

Drag to **jump** to any step index (same as `seek`).

### Step panel (example)

```text
Step 5: LLM (Planner)

Prompt:
...

Output:
...
```

### Optional (powerful; defer)

**“Replay from here” → clone run → resume execution**

That implies **new** execution from a forked state — **not** Phase 15. Treat as **Phase 16+** (fork/resume product feature).

---

## Testing

**Add:** `tests/test_replay_engine.py`

**Validate:**

```text
- step_forward works
- step_back works
- seek works
- bounds handled (no IndexError; define behavior at ends)
- no mutation of original trace
```

---

## Edge cases

| Case | Behavior |
|------|----------|
| **Empty trace** | Safe no-op; define UI empty state |
| **Large trace** | Lazy-load step payloads in UI if needed; engine may keep only indices in memory |
| **Diff steps** | Show stored diff; **never** re-apply to workspace destructively during replay |

---

## Implementation plan (ordered)

1. Create `ReplayEngine` (navigation only).
2. Add state reconstruction helper (pure fold over recorded steps).
3. Add `replay_index` (or equivalent) to graph nodes for UI binding.
4. Optional: minimal CLI replay for dogfooding.
5. UI: controls + slider + step panel + graph highlight.
6. Tests.

---

## Expected impact

**Before**

```text
“Why did step 7 fail?”
→ scroll logs
→ guess
```

**After**

```text
Step → Step → Step
→ see exact failure point
→ inspect inputs/outputs at that index
→ fast root cause analysis (within recorded data)
```

---

## Principal engineer note

```text
Trace = logs
Replay = debugger (over those logs)
```

**Stack**

```text
Phase 13 → reasoning visibility
Phase 14 → action / diff visibility
Phase 15 → execution control (playback + inspection)
```

```text
You now own the inspection lifecycle of a completed run.
```

---

## Next phases (preview)

After Phase 15, the system is **debuggable enough** to improve intelligence with evidence.

**Candidate next work (not this spec):**

- **Phase 16** — Memory layer  
- **Phase 17** — Multi-agent separation  
- **Phase 18** — Evaluation harness  

**First validation deliverable**

```text
- implement replay
- one replay session output (e.g. CLI or UI capture)
```

Then refine toward a **true debugging system** (not trivial “next line” playback): reconstruction accuracy, large traces, and UX for diffs + LLM payloads.
