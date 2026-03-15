AutoStudio AI Assistant — Architecture Guardrails
Purpose of This Document

This document defines the design goals, architectural constraints, patterns, and anti-patterns for the AutoStudio AI coding assistant.

Any AI assistant or engineer modifying this repository must follow these constraints.

The purpose is to prevent:

architectural drift

unnecessary complexity

hallucinated “optimizations”

uncontrolled agent behavior

The system prioritizes clarity, determinism, and debuggability over novelty.

Project Goal

The goal of this system is to build a modular AI coding assistant that can:

understand developer queries

navigate large codebases

retrieve the correct code context

explain system behavior

safely modify code

The assistant should approach the capabilities of tools like:

Cursor

Claude Code

Devin-style agents

But with one key difference:

The system must remain fully inspectable and deterministic.

This means every stage of the pipeline is visible and testable.

Core Architectural Philosophy

The system follows four core principles.

1. Retrieval Before Reasoning

The agent must never reason about code without context.

Correct architecture:

query
→ retrieval
→ context building
→ reasoning

Incorrect architecture:

query
→ reasoning
→ hallucinated explanation

All reasoning must operate on retrieved repository context.

2. Deterministic Pipelines

The agent must follow a structured execution pipeline, not free-form decision making.

Current architecture:

User Query
↓
Context Grounder
↓
Intent Router
↓
Planner
↓
Execution Loop
↓
Retrieval Pipeline
↓
Context Ranking
↓
Reasoning

Each stage has a clear responsibility.

3. Repository Intelligence

The system builds structural understanding of the repository through:

symbol indexing

dependency graphs

repository maps

This allows the assistant to navigate the codebase structurally, not just through text search.

4. Observability First

Every stage of the agent pipeline must be observable and debuggable.

The system includes:

trace logging

replay scripts

structured stage logging

Agent runs must be replayable step-by-step.

Major Components Already Implemented

The following systems are already implemented and must not be redesigned.

Repository Indexing

Parses source code using AST and extracts:

classes

functions

modules

dependencies

Used to build the symbol graph.

Symbol Graph

The symbol graph represents relationships between code elements.

Nodes:

functions
classes
modules
files

Edges:

calls
imports
inheritance
references

The graph is used for retrieval expansion and navigation.

Repository Map

The repository map provides a fast structural index of the codebase.

It maps:

symbols → files
modules → symbols
functions → callers

This allows the agent to locate important code structures without scanning the entire repo.

Retrieval Pipeline

The retrieval system follows this pipeline:

query rewrite
→ repo map lookup
→ anchor detection
→ graph expansion
→ regex search
→ vector search
→ context ranking
→ context pruning

This pipeline must remain intact.

Context Ranking

Retrieved context is ranked using a hybrid scoring system:

0.6 LLM relevance
0.2 symbol match
0.1 filename match
0.1 reference score

Only the top context snippets are passed to reasoning.

Editing System

The editing pipeline uses structured patching:

diff planner
→ patch generator
→ AST patching
→ validation
→ patch execution

Edits must always be:

syntactically valid

bounded

reversible

Observability System

The system logs structured traces of agent runs.

Trace includes:

planner decisions
retrieval steps
context ranking
model calls
edit results

These traces allow debugging and replay.

Patterns to Follow

Any new code must follow these patterns.

Pattern: Single Responsibility Modules

Each module should perform one task.

Examples:

context_grounder
intent_router
repo_map_lookup
anchor_detector
context_ranker

Avoid mixing responsibilities.

Pattern: Deterministic Tool Pipelines

Tools must operate in a fixed order.

Correct pattern:

retrieval
→ expansion
→ ranking
→ pruning

Avoid dynamic tool selection by LLMs.

Pattern: Explicit Data Flow

Data should move explicitly between stages.

Example:

state.context
state.search_memory
state.ranked_context

Avoid hidden global state.

Pattern: Incremental Intelligence

Capabilities must be layered gradually.

Correct approach:

search
→ retrieval expansion
→ symbol graph
→ repo map
→ context ranking

Do not skip layers.

Anti-Patterns (Must Avoid)

The following behaviors are not allowed in this project.

Anti-Pattern: LLM-Controlled Execution

The LLM must not decide the entire agent workflow.

Incorrect:

LLM decides which tools to run

Correct:

planner generates steps
dispatcher executes them deterministically
Anti-Pattern: Hidden Agent Behavior

All agent decisions must be visible in traces.

Avoid systems where behavior cannot be reproduced.

Anti-Pattern: Premature Optimization

Do not add:

caching layers

async frameworks

distributed components

unless performance becomes a real problem.

Anti-Pattern: Over-Engineering

Avoid introducing:

new frameworks

complex abstractions

unnecessary design patterns

The system should remain understandable.

Anti-Pattern: Replacing Existing Architecture

Do not replace existing components such as:

planner

execution loop

retrieval pipeline

patch executor

trace system

Extensions should integrate into the existing system.

Anti-Pattern: Context Explosion

Never feed entire files or repositories directly to the model.

Context must always be:

retrieved
ranked
pruned

before reasoning.

Development Strategy

When modifying the system, engineers should follow this testing order.

repository indexing

symbol graph generation

repository map generation

retrieval pipeline

context ranking

planner execution

editing pipeline

full agent loop

Each module should be validated independently.

What This System Is NOT

This system is not intended to be:

a chatbot

a fully autonomous agent

a black-box AI system

It is an engineering tool designed for code understanding and safe automation.

What Future Assistants Should Stick To

When working on this repository, assistants should:

preserve the pipeline architecture

extend existing modules instead of replacing them

maintain observability

prioritize retrieval quality over reasoning tricks

keep the system simple and inspectable

Final Guidance for AI Assistants

If you are modifying this repository:

do not redesign the system

do not introduce new frameworks

do not replace existing modules

Instead:

analyze the current architecture

extend it carefully

ensure tests pass

maintain deterministic behavior

This project values clarity and reliability over novelty.