Cursor Implementation Plan

Token Budgeting & Context Control Framework

Add this to your repo as a task file:

dev/tasks/token_budgeting_framework.md
Objective

Implement a token budgeting system that prevents prompt/context explosion.

The system must enforce:

context ranking
context compression
context pruning
hard prompt guardrails
token telemetry

before every LLM call.

This ensures the prompt always fits within the model context window and contains only high-signal information.

Step 1 — Audit Existing Context Systems

Before implementing anything, Cursor must inspect the repository for existing modules.

Search for:

context_ranker
context_pruner
context_summarizer
retrieval_pipeline
PromptRegistry
prompt_context_builder

If these modules already exist:

extend them

Do not create duplicate systems.

If they do not exist:

create new modules under agent/prompt_system/context/
Step 2 — Implement Token Counting Utility

Create:

agent/prompt_system/context/token_counter.py

Responsibilities:

estimate token counts
support multiple tokenizers
provide prompt token statistics

Example API:

count_tokens(text)
count_prompt_tokens(prompt_parts)

Prompt parts:

system
skills
repo_context
history
user_input
Step 3 — Implement Prompt Budget Manager

Create:

agent/prompt_system/context/prompt_budget_manager.py

Responsibilities:

enforce token budget
trigger pruning if needed
return safe prompt

Config:

MAX_CONTEXT_TOKENS
MAX_PROMPT_TOKENS
OUTPUT_TOKEN_RESERVE

Example flow:

PromptBuilder
   ↓
compose prompt parts
   ↓
token_counter
   ↓
PromptBudgetManager.enforce_budget()
   ↓
safe prompt

Budget rule:

prompt_tokens + output_tokens <= model_context_limit
Step 4 — Implement Context Ranking

Check if context_ranker.py already exists.

If it exists:

extend it to support token budgets

If not:

Create:

agent/prompt_system/context/context_ranker.py

Responsibilities:

score retrieved snippets
rank by relevance
limit number of snippets

Ranking signals:

embedding similarity
symbol match
file match
reference frequency

Limits:

MAX_REPO_FILES = 5
MAX_SNIPPETS = 10
MAX_CODE_LINES = 300

Reason: irrelevant context harms model reasoning quality.

Step 5 — Implement Context Compression

Check for:

context_summarizer.py

If it exists:

extend it

If not:

Create:

agent/prompt_system/context/context_compressor.py

Responsibilities:

summarize large files
remove comments
keep signatures
extract relevant blocks

Compression strategy:

large file
↓
function signatures
class definitions
relevant blocks

Compression triggers when:

context_tokens > threshold

Compression is widely used to reduce prompt size while preserving meaning.

Step 6 — Implement Context Pruning

Check for:

context_pruner.py

If missing:

Create:

agent/prompt_system/context/context_pruner.py

Responsibilities:

remove low priority prompt parts
truncate large sections

Pruning priority order:

repo_context
conversation_history
skills

Never prune:

system instructions
tool schema
output schema
Step 7 — Integrate Budget Manager into Prompt Builder

Locate:

prompt_context_builder.py

Update pipeline to:

retrieve context
↓
rank context
↓
compress context
↓
prune context
↓
budget manager
↓
prompt registry
↓
LLM call

Final architecture:

PromptBuilder
  ↓
ContextRanker
  ↓
ContextCompressor
  ↓
ContextPruner
  ↓
PromptBudgetManager
  ↓
PromptRegistry
  ↓
Model
Step 8 — Add Hard Guardrails

Inside:

PromptRegistry.get_guarded()

Add check:

if prompt_tokens > MAX_PROMPT_TOKENS:
    trigger pruning

If still too large:

fallback to compressed prompt

Fallback strategy:

planner_prompt_compact
Step 9 — Add Token Telemetry

Extend:

agent/prompt_system/observability/prompt_metrics.py

Add metrics:

prompt_tokens
repo_context_tokens
skills_tokens
history_tokens
system_tokens

Example log:

planner_prompt
system_tokens = 400
skills_tokens = 200
repo_tokens = 5200
total_tokens = 5800

This allows monitoring of context growth over time.

Step 10 — Add Context Limits to Config

Update:

config/agent_config.py

Add:

MAX_CONTEXT_TOKENS
MAX_PROMPT_TOKENS
MAX_REPO_SNIPPETS
MAX_HISTORY_TOKENS

Example values:

MAX_CONTEXT_TOKENS = 16000
MAX_PROMPT_TOKENS = 12000
OUTPUT_TOKEN_RESERVE = 2000
Step 11 — Add CI Tests

Update:

scripts/run_prompt_ci.py

Add test:

assert prompt_tokens < MAX_PROMPT_TOKENS

Add worst-case scenario tests:

large repo context
multi-file retrieval
long conversation
Step 12 — Add Failure Logging

Update:

failure_logger.py

Add fields:

prompt_tokens
context_tokens
pruning_triggered
compression_triggered

This helps detect context explosion failures.

Step 13 — Add Stress Test

Create:

tests/test_prompt_budgeting.py

Scenarios:

very large repo context
multi-file edit task
long conversation history
large skill composition

Verify:

prompt_tokens <= MAX_PROMPT_TOKENS
Expected Result

After implementing this system:

prompt size always bounded
context contains only relevant code
model reasoning improves
latency decreases

Large contexts increase cost, latency, and reduce accuracy, so production agents rely on context ranking and compression to remain reliable.