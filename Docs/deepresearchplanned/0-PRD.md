🧠 PRODUCT REQUIREMENTS DOCUMENT (PRD)
Product: AutoStudio (Local-first Agent Runtime)
1. 🎯 PRODUCT VISION
Core Goal

Build a local-first, cost-optimized AI agent runtime that can:

Run coding + research agents
Dynamically route between:
local open-source models (7B–70B)
hosted open-source (vLLM, GPUs)
frontier APIs (GPT-class)
Achieve Cursor-level capability at lower cost
Key Differentiator (IMPORTANT)

Dynamic model routing + cost optimization across heterogeneous models

Not:

just agents
just orchestration

👉 This is your moat.

2. 🧩 CORE CAPABILITIES
Must-have system capabilities
1. Planning Layer
Task decomposition
Dependency mapping
Structured plan output

✅ Status: DONE (v1 working)

2. Execution Engine (CRITICAL GAP)
Task scheduling
Dependency resolution
Retry + failure handling
State management

❌ Status: NOT PROPERLY BUILT

3. Tooling Layer
Typed tools (Pydantic)
Execution normalization
External integrations

✅ Status: STRONG

4. Model Routing Engine (CORE FEATURE)
Task-aware routing
Cost-aware routing
Context-aware routing
Local + remote model abstraction

⚠️ Status: PARTIAL (static mapping only)

5. Observability Layer
Step tracing
LLM tracing
Debug logs

✅ Status: GOOD (Langfuse integration)

6. Memory System
Short-term (task/session)
Long-term (optional later)

⚠️ Status: BASIC

7. Orchestration Layer (Future)
Parallel execution
Branching
Multi-agent

❌ Status: NOT BUILT (and should not be yet)

3. 🏗️ CURRENT SYSTEM STATE (HONEST)

From your report :

What AutoStudio is today:

Deterministic plan executor with retries and replan loop

What it is NOT:
not multi-agent
not graph runtime
not cost-optimized system (yet)
4. 🚨 CORE PROBLEMS
Problem 1: Missing Execution Engine

You have:

planner
tools

But missing:

the system that actually runs work properly

Problem 2: Model Routing Not Realized

Your main idea:

❌ not implemented yet

Problem 3: Over-abstracted Planner

Too much focus on:

controller / engine

Too little on:

execution
Problem 4: No Runtime Foundation

No:

scheduler
task abstraction
concurrency model
5. 🧭 PRODUCT STRATEGY (CRITICAL)

We simplify your vision into:

🎯 Phase 1 Goal (FOCUS HERE)

Build the best single-agent coding runtime with smart model routing

❌ NOT in Phase 1:
multi-agent
complex orchestration
distributed system
6. 📦 PHASED IMPLEMENTATION PLAN
🟢 PHASE 1 — EXECUTION CORE (2–3 weeks)
Goal:

Replace PlanExecutor with real execution engine

Deliverables:
1. Task Abstraction
class ExecutionTask:
    id
    dependencies
    tool
    inputs
    state
    retry_policy
2. Scheduler (MANDATORY)
ready queue
dependency tracking
execution loop
3. Execution Isolation
no global mutation
per-task context
4. Stable Retry System
idempotent retries
structured failure
Outcome:

👉 solid, predictable execution

🟡 PHASE 2 — MODEL ROUTING ENGINE (YOUR MOAT)
Goal:

intelligent model selection

Build:
Routing Inputs:
task type
context size
complexity
latency budget
cost budget
Routing Output:
model = select_model(task, context, constraints)
Features:
fallback models
cost tracking
token tracking
Outcome:

👉 THIS is what makes you different

🔵 PHASE 3 — PERFORMANCE + LOCAL OPTIMIZATION
Add:
local model adapters (llama.cpp, vLLM)
batching
caching
streaming
🟣 PHASE 4 — ORCHESTRATION (OPTIONAL)
Only after everything works:
parallel execution
DAG / graph runtime
or integrate LangGraph
🔴 PHASE 5 — MULTI-AGENT (LAST)

Only when:

execution is stable
routing is smart
7. 🧠 ARCHITECTURE (TARGET)
User Task
   ↓
Planner
   ↓
Execution Engine (scheduler)
   ↓
Task
   ↓
Model Router → Model
   ↓
Tool Execution
   ↓
Trace + Memory
8. 🧨 PRIORITY ORDER (NO DEVIATION)
Execution Engine
Model Routing
Stability + Debugging
Performance
Orchestration
Multi-agent
9. ⚠️ HARD TRUTHS
You’re not behind — you’re just misfocused
Your tooling work = ✅ valuable
Your planner work = ⚠️ slightly overdone
Your execution layer = ❌ bottleneck