"""Editing and patch configuration."""

import os

MAX_PATCH_SIZE = int(os.getenv("MAX_PATCH_SIZE", "200"))
MAX_FILES_EDITED = int(os.getenv("MAX_FILES_EDITED", "5"))

# Edit proposal generator truncation limits
EDIT_PROPOSAL_MAX_CONTENT = int(os.getenv("EDIT_PROPOSAL_MAX_CONTENT", "20000"))
EDIT_PROPOSAL_EVIDENCE_MAX = int(os.getenv("EDIT_PROPOSAL_EVIDENCE_MAX", "5000"))
EDIT_PROPOSAL_SYMBOL_BLOCK_MAX = int(os.getenv("EDIT_PROPOSAL_SYMBOL_BLOCK_MAX", "2000"))

# Semantic feedback extraction limits
SEMANTIC_FEEDBACK_MAX_SUMMARY = int(os.getenv("SEMANTIC_FEEDBACK_MAX_SUMMARY", "500"))
