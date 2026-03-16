# Documentation Audit Plan

**PROJECT:** AutoStudio Documentation Audit

**ROLE:** Principal Engineer performing a documentation audit.

**GOAL:**
Ensure that all documentation (README, architecture docs, module docs) accurately reflect the current codebase.

We recently implemented major changes including:

- hybrid retrieval pipeline
- cross-encoder reranker infrastructure
- graph expansion
- reference lookup
- call-chain context
- deduplication pipeline stage
- telemetry metrics
- configuration additions

The documentation likely does NOT reflect the current architecture.

Your task is to:

1. audit the codebase
2. audit the documentation
3. produce a gap report
4. update documentation

**DO NOT modify code.**
**ONLY update documentation.**

---

## STEP 1 — Inventory All Documentation

Scan repository for all documentation sources:

- README.md
- Docs/
- docs/
- architecture docs
- component READMEs
- module docstrings

Create an index: **doc_inventory.md**

List:

- file path
- document purpose
- last update indicators
- modules described

---

## STEP 2 — Inventory System Components

Analyze the codebase and list major subsystems.

Expected subsystems include:

- retrieval pipeline
- graph index
- vector retrieval
- BM25 or lexical retrieval
- reranker subsystem
- context builder
- execution path analyzer
- symbol expansion
- reference lookup
- prompt system
- token budgeting
- trajectory loop
- failure mining

Create: **system_components.md**

Each component must include:

- component name
- file location
- purpose
- key entry points

---

## STEP 3 — Detect Documentation Drift

Compare documentation against the code.

Find cases where:

- documentation describes removed components
- documentation misses new modules
- README pipeline diagrams are outdated
- config variables are undocumented
- telemetry fields are undocumented
- architecture diagrams no longer match code

Create report: **docs_gap_report.md**

Include sections:

- Missing documentation
- Outdated documentation
- Incorrect architecture diagrams
- Undocumented configuration
- Undocumented metrics
- Undocumented subsystems

---

## STEP 4 — Identify Components Requiring Deep Explanation

Mark components that need dedicated documentation pages.

Examples likely include:

- retrieval pipeline
- reranker architecture
- symbol graph
- execution path analyzer
- context building
- failure mining system
- trajectory retry loop

For each:

- explain what the component does
- why it exists
- how it integrates into pipeline

---

## STEP 5 — Update Architecture Documentation

Update or create: **Docs/ARCHITECTURE.md**

Include:

- system overview
- pipeline diagram
- component descriptions
- data flow
- config sections

Architecture diagram should reflect:

```
AST parsing
↓
symbol graph
↓
vector retrieval
BM25 / lexical retrieval
↓
rank fusion
↓
graph expansion
↓
reference lookup
↓
call-chain builder
↓
deduplication
↓
cross-encoder reranker
↓
context pruning
↓
LLM
```

---

## STEP 6 — Update README

README.md must contain:

- project overview
- system architecture diagram
- installation
- quick start
- high-level component overview

Avoid deep implementation details here.

---

## STEP 7 — Document Configuration

Create: **Docs/CONFIGURATION.md**

Document all config keys including:

- retrieval_config
- reranker_config
- graph_config
- prompt_system_config

Include:

- default value
- purpose
- impact

---

## STEP 8 — Document Observability

Create: **Docs/OBSERVABILITY.md**

Document telemetry fields:

- rerank_latency_ms
- rerank_cache_hits
- rerank_cache_misses
- graph_nodes_expanded
- dedupe_removed_count
- candidate_budget_applied

Explain what each metric means.

---

## STEP 9 — Create Component READMEs

For complex subsystems create module docs:

- agent/retrieval/reranker/README.md
- agent/retrieval/README.md
- agent/meta/README.md
- agent/failure_mining/README.md

Each README should contain:

- purpose
- architecture
- key classes
- data flow

---

## STEP 10 — Produce Final Report

Create: **docs_audit_report.md**

Sections:

- documentation coverage
- missing explanations
- outdated sections
- architecture corrections
- files updated

This report must be generated BEFORE documentation updates.

Then update the docs.
