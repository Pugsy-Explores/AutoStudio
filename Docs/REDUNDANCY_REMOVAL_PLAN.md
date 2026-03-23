# Documentation Redundancy Removal Plan

**Date:** 2026-03-23  
**Goal:** Remove redundant code/structure info from READMEs and Docs; single source of truth per topic.

---

## Principles

1. **One canonical source per topic** — Link, don't duplicate.
2. **README = entry point** — Brief overview + links; no deep dives.
3. **Docs = detail** — Keep full content in one place only.
4. **Module READMEs** — Role + link to main doc; no inline architecture.

---

## Identified Redundancies

### 1. ReAct Mermaid diagram (3 copies)

| Location | Action |
|----------|--------|
| README.md §Architecture | **Remove** — Keep 1-sentence summary + link. |
| Docs/ARCHITECTURE.md | **Remove** — Link to REACT_ARCHITECTURE for ReAct diagram. |
| Docs/REACT_ARCHITECTURE.md | **Keep** — Canonical diagram. |

### 2. ReAct ASCII flow (2 copies)

| Location | Action |
|----------|--------|
| README.md §Architecture | **Remove** — Redundant with Mermaid in REACT_ARCHITECTURE. |
| Docs/REACT_ARCHITECTURE.md | **Keep** — Canonical overview. |

### 3. Primary vs Legacy summary (6+ copies)

| Location | Action |
|----------|--------|
| README, ARCHITECTURE, AGENT_LOOP_WORKFLOW, AGENT_CONTROLLER, orchestrator README | **Consolidate** — Keep single paragraph in README; others use "See README / REACT_ARCHITECTURE" or one-line. |
| PHASE_5_ATTEMPT_LOOP | **Keep** banner (legacy-only). |

### 4. Project structure tree (~330 lines)

| Location | Action |
|----------|--------|
| README.md §Project Structure | **Replace** with short bullet list + link to Docs/PROJECT_STRUCTURE.md. |
| Docs/PROJECT_STRUCTURE.md | **Keep** — Canonical structure. |

### 5. Run commands (3 copies)

| Location | Action |
|----------|--------|
| README §Quick Start | **Keep** — Primary entry (ReAct + live). |
| REACT_QUICK_START | **Keep** — Dedicated ReAct quick start; trim if overlaps. |
| REACT_ARCHITECTURE §Running ReAct | **Remove** — Link to REACT_QUICK_START. |

### 6. Model endpoints / env vars (2 copies)

| Location | Action |
|----------|--------|
| README §Model endpoints | **Keep** — Brief; link to CONFIGURATION. |
| REACT_QUICK_START §Model Endpoints | **Remove** — Redundant; link to CONFIGURATION. |

### 7. EDIT flow (ReAct) (4 copies)

| Location | Action |
|----------|--------|
| README §Execution Pipeline §EDIT pipeline | **Condense** — One line + link. |
| AGENT_CONTROLLER §EDIT Flow | **Condense** — ReAct one-liner + link to REACT_ARCHITECTURE; keep legacy block (or link to EDIT_PIPELINE_DETAILED_ANALYSIS). |
| REACT_ARCHITECTURE §EDIT Path | **Keep** — Canonical ReAct EDIT. |
| EDIT_PIPELINE_DETAILED_ANALYSIS | **Keep** — Canonical detailed analysis. |

### 8. SEARCH pipeline detail (2 copies)

| Location | Action |
|----------|--------|
| README §Execution Pipeline §SEARCH pipeline | **Condense** — High-level bullet + link to RETRIEVAL_ARCHITECTURE. |
| Docs/ARCHITECTURE §Data Flow §3 | **Keep** — Summary table; full detail in RETRIEVAL_ARCHITECTURE. |
| Docs/AGENT_LOOP_WORKFLOW | **Condense** retrieval section — link to RETRIEVAL_ARCHITECTURE. |

### 9. CLI commands (2 copies)

| Location | Action |
|----------|--------|
| README §Quick Start | **Keep** — Primary. |
| AGENT_CONTROLLER §CLI | **Condense** — One-line + link to README §Quick Start. |

### 10. Agent Controller pipeline flow

| Location | Action |
|----------|--------|
| README §Agent Controller | **Condense** — Short block + link to AGENT_CONTROLLER. |
| AGENT_CONTROLLER | **Keep** — Canonical; trim repeated ReAct/Legacy preamble. |

### 11. Core Components table

| Location | Action |
|----------|--------|
| README §Core Components | **Condense** — 3–4 key components + link to REACT_ARCHITECTURE §Key Files. |
| REACT_ARCHITECTURE §Key Files | **Keep** — Canonical. |

### 12. models_config.json sample

| Location | Action |
|----------|--------|
| README §Configuration | **Remove** or **shorten** — Link to CONFIGURATION.md; no full JSON. |

### 13. AGENT_LOOP_WORKFLOW legacy content

| Location | Action |
|----------|--------|
| AGENT_LOOP_WORKFLOW | **Trim** — Primary = ReAct (link). Legacy = link to PHASE_5; remove or shorten huge legacy Mermaid/ASCII (link to PHASE_5). |

### 14. orchestrator README

| Location | Action |
|----------|--------|
| agent/orchestrator/README.md | **Trim** — One para ReAct primary; link to REACT_ARCHITECTURE. Plan resolver table stays (legacy-specific). |

---

## Implementation Order

1. **README.md** — Project Structure (replace tree with link), ReAct diagrams (remove), EDIT/SEARCH (condense), Core Components (condense), models_config (shorten/remove).
2. **Docs/ARCHITECTURE.md** — Remove ReAct Mermaid; add link. Condense Primary/Legacy.
3. **Docs/REACT_ARCHITECTURE.md** — Remove "Running ReAct" block; link to REACT_QUICK_START.
4. **Docs/REACT_QUICK_START.md** — Remove redundant Model Endpoints; link to CONFIGURATION.
5. **Docs/AGENT_CONTROLLER.md** — Condense CLI; trim repeated flow; condense EDIT Flow.
6. **Docs/AGENT_LOOP_WORKFLOW.md** — Condense retrieval; trim or relocate legacy diagrams.
7. **agent/orchestrator/README.md** — Trim ReAct/legacy preamble.
8. **Verify** — Grep for duplicated phrases; ensure links work.

---

## Success Criteria

- [x] No ReAct Mermaid diagram outside REACT_ARCHITECTURE.
- [x] No full project tree in README; link to PROJECT_STRUCTURE.
- [x] Run commands in README Quick Start and REACT_QUICK_START; REACT_ARCHITECTURE links to QUICK_START.
- [x] Primary/Legacy summary in README; others link.
- [x] README significantly shorter; all detail reachable via links.
