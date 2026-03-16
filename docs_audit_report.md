# AutoStudio Documentation Audit Report

Final audit report produced before documentation updates. Summarizes documentation coverage, missing explanations, outdated sections, architecture corrections, and planned file updates.

## Documentation Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| Root README | Partial | Good overview; pipeline diagram and SEARCH description outdated |
| Docs/ index | Good | Docs/README.md comprehensive; links to most docs |
| Agent loop workflow | Good | AGENT_LOOP_WORKFLOW.md detailed |
| Agent controller | Good | AGENT_CONTROLLER.md covers full pipeline |
| Configuration | Partial | CONFIGURATION.md exists; retrieval_config missing Phase 17/18 keys |
| Retrieval architecture | Good | RETRIEVAL_ARCHITECTURE.md most current; covers BM25, RRF, reranker, dedup, telemetry |
| Symbol graph | Good | REPOSITORY_SYMBOL_GRAPH.md covers indexing, graph, repo map |
| Prompt system | Good | PROMPT_ARCHITECTURE.md, prompt_engineering_rules.md |
| Failure mining | Good | FAILURE_MINING.md dedicated doc |
| Routing | Good | ROUTING_ARCHITECTURE_REPORT.md |
| Workflow | Good | WORKFLOW.md Phase 12 |
| Architecture (single doc) | Missing | No Docs/ARCHITECTURE.md |
| Observability | Missing | No Docs/OBSERVABILITY.md; telemetry scattered |
| Component READMEs | Missing | retrieval, reranker, meta, failure_mining have no READMEs |

## Missing Explanations

1. **System architecture overview** — No single doc that describes the full pipeline from user instruction to LLM with all stages (query rewrite, parallel search, RRF, anchor detection, graph expansion, reference lookup, call-chain, dedup, reranker, pruning).
2. **Observability reference** — No consolidated list of telemetry fields, trace events, and metrics with meanings.
3. **Retrieval module** — No README explaining retrieval subsystem structure, data flow, and key classes.
4. **Reranker module** — No README explaining cross-encoder architecture, cache, dedup, symbol bypass.
5. **Meta module** — No README explaining trajectory loop, critic, retry planner integration.
6. **Failure mining module** — No README at component level (FAILURE_MINING.md exists at Docs/).

## Outdated Sections

| Document | Section | Correction |
|----------|---------|------------|
| README.md | Architecture Overview (Mermaid) | Add BM25, RRF, dedup, reranker to pipeline |
| README.md | Architecture Overview (ASCII) | Same |
| README.md | SEARCH pipeline description | Add BM25, RRF, deduplication, cross-encoder reranker |
| README.md | Tools table | Update find_referencing_symbols: uses symbol graph, not Serena stub |
| README.md | Project structure | Add bm25_retriever, rank_fusion, reranker/ |
| Docs/CONFIGURATION.md | retrieval_config table | Add RERANKER_GPU_MODEL, RERANKER_CPU_MODEL, MAX_RERANK_SNIPPET_TOKENS, MAX_RERANK_PAIR_TOKENS, RERANK_CACHE_SIZE, MAX_RERANK_CANDIDATES, RETRIEVAL_GRAPH_*, RERANK_FUSION_WEIGHT, RETRIEVER_FUSION_WEIGHT |

## Architecture Corrections

The canonical retrieval pipeline order (immutable per Rule 11) is:

1. Query rewrite
2. Repo map lookup
3. Anchor detection
4. **Parallel search**: BM25, graph, vector, grep
5. **RRF rank fusion** (or concat)
6. Graph expansion (expand_from_anchors; expand_symbol_dependencies)
7. **Reference lookup** (find_referencing_symbols)
8. **Call-chain context** (build_call_chain_context)
9. **Deduplication** (deduplicate_candidates)
10. **Candidate budget** (MAX_RERANK_CANDIDATES)
11. **Cross-encoder reranker** (Qwen3-Reranker-0.6B; GPU/CPU)
12. Context pruning
13. LLM

README.md and any architecture diagram must reflect this order.

## Files to Create

| File | Purpose |
|------|---------|
| Docs/ARCHITECTURE.md | Authoritative system architecture; pipeline diagram; component descriptions; data flow |
| Docs/OBSERVABILITY.md | Telemetry fields; trace events; metrics reference |
| agent/retrieval/README.md | Retrieval subsystem: purpose, architecture, key classes, data flow |
| agent/retrieval/reranker/README.md | Cross-encoder reranker: purpose, architecture, key classes |
| agent/meta/README.md | Trajectory loop, critic, retry planner: purpose, integration |
| agent/failure_mining/README.md | Failure mining: purpose, architecture, key classes |

## Files to Update

| File | Changes |
|------|---------|
| README.md | Update pipeline diagram (Mermaid + ASCII) to include BM25, RRF, graph expansion, reference lookup, call-chain, dedup, reranker |
| Docs/CONFIGURATION.md | Add Phase 17/18 config keys to retrieval_config table |
| Docs/README.md | Add links to ARCHITECTURE.md, OBSERVABILITY.md, and component READMEs |

## Audit Artifacts

| File | Purpose |
|------|---------|
| doc_inventory.md | Index of all documentation files |
| system_components.md | Inventory of codebase components |
| docs_gap_report.md | Gap analysis (missing, outdated, incorrect) |
| docs_audit_report.md | This report |
