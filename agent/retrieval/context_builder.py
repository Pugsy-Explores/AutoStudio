"""Context builder: turn search results into files/snippets context. Stub."""


def build_context(search_results) -> dict:
    """
    Build context dict from search results. Returns {"files": [], "snippets": []}.
    If search_results has "results" list, populate from it; else return empty.
    """
    out = {"files": [], "snippets": []}
    if not search_results or not isinstance(search_results, dict):
        return out
    results = search_results.get("results")
    if not isinstance(results, list):
        return out
    for r in results:
        if isinstance(r, dict):
            if r.get("file"):
                out["files"].append(r.get("file"))
            if "snippet" in r:
                out["snippets"].append(r.get("snippet", ""))
    return out
