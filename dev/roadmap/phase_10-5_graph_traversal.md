Phase 10.5 — Graph-Guided Localization
Objective

Improve the AutoStudio retrieval pipeline from:

semantic retrieval

to:

structural repository navigation

Goal metrics:

file localization ≥ 85%
function localization ≥ 75%
retry depth ↓
patch success rate ↑
Architectural Principle

Never replace your current pipeline.

Extend it.

Current pipeline:

query rewrite
→ repo_map lookup
→ anchor detection
→ graph expansion
→ regex search
→ vector search
→ context ranking
→ pruning

Phase-10.5 pipeline:

query rewrite
↓
repo_map anchor
↓
dependency traversal
↓
execution path expansion
↓
symbol expansion
↓
vector search
↓
context ranking
↓
context pruning

The key change:

vector search becomes the last step.

New Modules

Add folder:

agent/retrieval/localization/

Files:

dependency_traversal.py
execution_path_analyzer.py
symbol_ranker.py
localization_engine.py
Step 1 — Dependency Traversal

Purpose:

Traverse the call graph + import graph starting from an anchor.

Module:

dependency_traversal.py

Core function:

def traverse_dependencies(symbol, depth=3):
    """
    Walk dependency graph:
    callers
    callees
    imports
    dependents
    """

Inputs:

symbol_graph
repo_map
GraphStorage

Output:

candidate_symbols
candidate_files

This performs multi-hop graph reasoning across dependencies.

Step 2 — Execution Path Analyzer

Purpose:

Reconstruct likely execution paths.

Module:

execution_path_analyzer.py

Example path:

router
→ planner
→ executor
→ policy_engine
→ retry logic

Function:

def build_execution_paths(anchor_symbol):
    return paths

Paths represent runtime logic chains.

This avoids isolated file search.

Step 3 — Symbol Ranker

Purpose:

Rank candidate code locations.

Module:

symbol_ranker.py

Ranking score:

0.4 dependency distance
0.25 call graph relevance
0.2 symbol name similarity
0.15 semantic similarity

Output:

top_k_symbols
top_k_files

This decides which files are sent to the context builder.

Step 4 — Localization Engine

Purpose:

Combine all localization stages.

Module:

localization_engine.py

Pipeline:

def localize_issue(query):

    anchor = detect_anchor_symbol(query)

    graph_nodes = traverse_dependencies(anchor)

    execution_paths = build_execution_paths(anchor)

    candidates = merge(graph_nodes, execution_paths)

    ranked = rank_symbols(candidates)

    return ranked
Step 5 — Retrieval Pipeline Integration

Modify:

agent/retrieval/retrieval_pipeline.py

Add stage:

localization_engine

New pipeline:

query rewrite
↓
repo_map anchor
↓
localization_engine
↓
symbol expansion
↓
vector search
↓
context ranking
↓
pruning
Step 6 — Trace Observability

Add trace events:

localization_anchor_detected
dependency_traversal_complete
execution_paths_built
localization_ranked

Trace example:

query
→ anchor: StepExecutor
→ dependency nodes: 14
→ execution paths: 3
→ ranked candidates: 5

This keeps your system fully inspectable.

Step 7 — Localization Evaluation Dataset

Add:

tests/localization_tasks.json

Examples:

Find retry logic bug
Locate patch validator
Find symbol graph builder
Find configuration loader
Locate dependency resolver

Each test records:

correct_file
correct_symbol
Step 8 — Localization Eval Script

Add:

scripts/run_localization_eval.py

Metrics:

file_accuracy
function_accuracy
top_k_recall
avg_graph_nodes
avg_tool_calls

Output:

reports/localization_report.json
Step 9 — Metrics Integration

Update:

dev/evaluation/metrics.md

Add Phase-10.5 metrics:

file localization accuracy
function localization accuracy
average graph traversal depth
average candidate files
retry reduction
Step 10 — Safety Limits

Reuse repo intelligence limits.

Add localization limits:

MAX_GRAPH_DEPTH = 3
MAX_DEPENDENCY_NODES = 100
MAX_EXECUTION_PATHS = 10

Prevents runaway traversal.

Phase 10.5 Exit Criteria

Phase-10.5 is complete when:

file localization ≥ 85%
function localization ≥ 75%
retry depth reduced by ≥ 30%
patch success rate improved
Architecture After Phase-10.5

AutoStudio becomes:

Interface
CLI / session

Orchestration
supervisor
role agents

Reflection
critic
retry planner

Repository Intelligence
repo_summary
architecture_map
impact_analyzer

Localization Layer
dependency_traversal
execution_path_analyzer
symbol_ranker

Knowledge Layer
repo_map
symbol_graph

Execution Layer
editing pipeline
terminal tools

Observability
trace logger
metrics
replay
Principal Engineer Advice

At this stage do not add more agents.

Instead focus on:

localization precision
evaluation rigor
deterministic debugging

Almost all strong coding agents succeed because they edit the correct file on the first attempt.