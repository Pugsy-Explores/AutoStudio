# Intelligence Subsystem (`agent/intelligence/`)

Experience and learning utilities that make the agent better over time without changing the core execution engine. This package manages **solution memory**, **developer preferences**, **repo learning artifacts**, and **similar-solution retrieval**.

## Responsibilities

- **Solution memory**: store/retrieve prior accepted solutions.
- **Similarity search**: index solutions and retrieve nearby examples for hints.
- **Developer model**: persist user preferences and learn from accepted outcomes.
- **Repo learning**: persist repo-specific “knowledge” extracted from successful work.

## Public API

Exports from `agent/intelligence/__init__.py`:

- Solution memory: `save_solution`, `load_solution`, `list_solutions`, `mark_accepted`
- Embeddings search: `index_solution`, `search_similar_solutions`
- Experience hints: `ExperienceHints`, `retrieve`
- Developer model: `load_profile`, `save_profile`, `update_from_solution`
- Repo learning: `load_knowledge`, `update_repo_from_solution`

## Integration points

- Orchestrator may pull “experience hints” during planning/retry.
- Workflow layers can record accepted solutions to improve future runs.

