"""Re-export Phase 11 Langfuse module (singleton + facades)."""

from agent_v2.observability.langfuse_client import (  # noqa: F401
    LFGenerationHandle,
    LFSpanHandle,
    LFTraceHandle,
    create_agent_trace,
    finalize_agent_trace,
    langfuse,
)
