"""Context7 documentation lookup adapter. Mock until real API is wired."""


def lookup_docs(query: str) -> dict:
    """Look up documentation. Placeholder: return mock results."""
    return {
        "query": query,
        "docs": [
            {"title": "Mock doc", "snippet": f"Documentation for: {query}"},
        ],
    }
