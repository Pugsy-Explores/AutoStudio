"""Compose prompt with optional skill and repo context."""


def build_context(
    base_instructions: str,
    skill_block: str | None = None,
    repo_context: str | None = None,
) -> str:
    """
    Compose: base_prompt + skill_block + repo_context_block.
    Used when combining a PromptTemplate with a skill and/or retrieved context.
    """
    parts = [base_instructions]
    if skill_block and skill_block.strip():
        parts.append("\n\n---\n\n")
        parts.append(skill_block.strip())
    if repo_context and repo_context.strip():
        parts.append("\n\n---\n\n")
        parts.append("REPOSITORY CONTEXT:\n\n")
        parts.append(repo_context.strip())
    return "".join(parts)
