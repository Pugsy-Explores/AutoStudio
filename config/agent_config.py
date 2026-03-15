"""Agent loop and controller configuration."""

import os


MAX_TASK_RUNTIME_SECONDS = int(os.getenv("MAX_TASK_RUNTIME_SECONDS", str(15 * 60)))
MAX_REPLAN_ATTEMPTS = int(os.getenv("MAX_REPLAN_ATTEMPTS", "5"))
MAX_STEP_TIMEOUT_SECONDS = int(os.getenv("MAX_STEP_TIMEOUT_SECONDS", "15"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "32000"))  # Hard cap before LLM reasoning call

# Phase 12 workflow safety limits
MAX_FILES_PER_PR = int(os.getenv("MAX_FILES_PER_PR", "10"))
MAX_PATCH_LINES = int(os.getenv("MAX_PATCH_LINES", "500"))
MAX_CI_RUNTIME_SECONDS = int(os.getenv("MAX_CI_RUNTIME_SECONDS", "600"))

# Phase 14 token budgeting and context control
MAX_PROMPT_TOKENS = int(os.getenv("MAX_PROMPT_TOKENS", "12000"))
OUTPUT_TOKEN_RESERVE = int(os.getenv("OUTPUT_TOKEN_RESERVE", "2000"))
MAX_REPO_SNIPPETS = int(os.getenv("MAX_REPO_SNIPPETS", "10"))
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))
MAX_REPO_CONTEXT_TOKENS = int(os.getenv("MAX_REPO_CONTEXT_TOKENS", "7200"))  # 60% of 12000
MAX_RETRIEVAL_RESULTS = int(os.getenv("MAX_RETRIEVAL_RESULTS", "20"))
HISTORY_WINDOW_TURNS = int(os.getenv("HISTORY_WINDOW_TURNS", "10"))  # last N turns kept raw
HISTORY_SUMMARY_TURNS = int(os.getenv("HISTORY_SUMMARY_TURNS", "30"))  # older turns summarized
