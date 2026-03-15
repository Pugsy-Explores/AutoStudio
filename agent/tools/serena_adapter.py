"""Serena code search adapter via MCP. Uses symbol search, text search, and reference lookup."""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from agent.retrieval.retrieval_expander import normalize_file_path

# Optional MCP client; fallback to placeholder if unavailable
try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

MAX_RESULTS = 5
_VERBOSE = os.environ.get("SERENA_VERBOSE", "").lower() in ("1", "true", "yes")
_SUPPRESS_SERENA_DEBUG = os.environ.get("SERENA_SUPPRESS_DEBUG", "1").lower() in ("1", "true", "yes")
_ENABLE_GREP_FALLBACK = os.environ.get("SERENA_GREP_FALLBACK", "1").lower() in ("1", "true", "yes")
_log = logging.getLogger(__name__)


def _is_serena_debug_line(line: str) -> bool:
    """True if line looks like a DEBUG-level log from serena (suppress it)."""
    s = line.strip()
    if not s:
        return False
    return (
        "[DEBUG]" in line
        or " DEBUG " in line
        or " DEBUG:" in line
        or s.startswith("DEBUG ")
        or s.startswith("DEBUG:")
        or "level=DEBUG" in line
        or "levelname=DEBUG" in line
    )


class _FilterSerenaDebugStream:
    """File-like wrapper that forwards to stderr but drops lines that look like DEBUG."""

    def __init__(self, target):
        self._target = target
        self._buf = ""

    def write(self, data: str | bytes) -> int:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        self._buf += data
        while "\n" in self._buf or "\r" in self._buf:
            line, rest = self._buf.split("\n", 1) if "\n" in self._buf else self._buf.split("\r", 1)
            self._buf = rest
            if not _is_serena_debug_line(line):
                self._target.write(line + "\n")
                self._target.flush()
        return len(data)

    def flush(self) -> None:
        if self._buf and not _is_serena_debug_line(self._buf):
            self._target.write(self._buf)
            self._target.flush()
        self._buf = ""
        self._target.flush()

    def fileno(self) -> int:
        """Delegate to underlying stream so MCP stdio_client can use it."""
        return self._target.fileno()


def _serena_server_params(project_dir: str | None = None) -> "StdioServerParameters":
    """Build Serena MCP server parameters from env or defaults."""
    command = os.environ.get("SERENA_MCP_COMMAND", "uvx")
    args_str = os.environ.get("SERENA_MCP_ARGS", "")
    if args_str:
        args = [a.strip() for a in args_str.split() if a.strip()]
    else:
        project = project_dir or os.getcwd()
        args = [
            "--from", "git+https://github.com/oraios/serena",
            "serena", "start-mcp-server",
            "--context", "ide",
            "--project", project,
        ]
    return StdioServerParameters(command=command, args=args)


def _text_from_content(content: list) -> str:
    """Extract text from MCP CallToolResult content list."""
    parts = []
    for block in content:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _grep_fallback(query: str, project_dir: str | None) -> dict:
    """
    Fallback search using ripgrep when Serena MCP is unavailable.
    Returns {"results": [...], "query": query} in same format as search_code.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}
    root = Path(project_dir or os.getcwd()).resolve()
    if not root.is_dir():
        return {"results": [], "query": query}
    # Extract search pattern: use first token that looks like identifier
    pattern = query.strip()
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", pattern)
    if tokens:
        pattern = tokens[0] if len(tokens[0]) >= 2 else pattern
    try:
        proc = subprocess.run(
            ["rg", "-n", "--max-count", "1", "-g", "*.py", re.escape(pattern), str(root)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"results": [], "query": query}
    results = []
    seen: set[tuple[str, int]] = set()
    for line in (proc.stdout or "").splitlines()[:MAX_RESULTS * 2]:
        m = re.match(r"^([^:]+):(\d+):(.*)$", line)
        if m:
            path, ln, content = m.group(1), int(m.group(2)), m.group(3)
            try:
                rel = str(Path(path).relative_to(root))
            except ValueError:
                rel = path
            key = (rel, ln)
            if key not in seen:
                seen.add(key)
                # Use absolute path for consistency with graph_retriever (read_file resolves)
                abs_path = str((root / path).resolve())
                results.append({
                    "file": abs_path,
                    "symbol": "",
                    "line": ln,
                    "snippet": (content or "").strip()[:300],
                })
                if len(results) >= MAX_RESULTS:
                    break
    return {"results": results, "query": query}


def _parse_serena_json_results(text: str) -> list[dict]:
    """Parse Serena JSON array of symbol dicts into our format (file, symbol, line, snippet)."""
    results = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                rel = normalize_file_path(item.get("relative_path") or item.get("file") or "")
                if not rel:
                    continue
                name_path = item.get("name_path") or item.get("symbol") or ""
                body_loc = item.get("body_location") or item.get("location") or {}
                line = body_loc.get("line") if isinstance(body_loc, dict) else 0
                snippet = (item.get("body") or name_path or "")[:300]
                if isinstance(snippet, str) and len(snippet) > 300:
                    snippet = snippet[:300] + "..."
                results.append({
                    "file": rel,
                    "symbol": name_path,
                    "line": line,
                    "snippet": snippet,
                })
    except json.JSONDecodeError:
        pass
    return results


def _parse_serena_text_to_results(text: str) -> list[dict]:
    """Parse Serena tool output text into structured results (file, symbol, line, snippet)."""
    parsed = _parse_serena_json_results(text)
    if parsed:
        return parsed[:MAX_RESULTS]
    results = []
    path_line = re.compile(r"([^\s:]+\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|rb|php))(?::(\d+))?")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = path_line.search(line)
        if match:
            file_path = normalize_file_path(match.group(1))
            if not file_path:
                continue
            line_no = int(match.group(2)) if match.group(2) else 0
            snippet = line[:200] + ("..." if len(line) > 200 else "")
            results.append({
                "file": file_path,
                "symbol": "",
                "line": line_no,
                "snippet": snippet,
            })
    if not results and text.strip():
        results.append({
            "file": "",
            "symbol": "",
            "line": 0,
            "snippet": text.strip()[:500],
        })
    return results[:MAX_RESULTS]


async def _search_via_mcp(query: str, project_dir: str | None, tool_hint: str | None = None) -> dict:
    """Call Serena MCP find_symbol and/or search_for_pattern; return structured results.
    tool_hint: "find_symbol" | "search_for_pattern" | None (run both).
    """
    params = _serena_server_params(project_dir)
    all_results = []
    seen = set()
    run_find_symbol = tool_hint in (None, "find_symbol")
    run_search_pattern = tool_hint in (None, "search_for_pattern")

    # Show serena stderr when SERENA_VERBOSE=1; optionally suppress only DEBUG lines
    if not _VERBOSE:
        errlog = open(os.devnull, "w")
    elif _SUPPRESS_SERENA_DEBUG:
        errlog = _FilterSerenaDebugStream(sys.stderr)
    else:
        errlog = sys.stderr
    try:
        async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                if run_find_symbol:
                    try:
                        result = await session.call_tool(
                            "find_symbol",
                            arguments={"name_path_pattern": query, "substring_matching": True},
                        )
                        if not result.isError and result.content:
                            text = _text_from_content(result.content)
                            for r in _parse_serena_text_to_results(text):
                                key = (r.get("file"), r.get("line"))
                                if key not in seen:
                                    seen.add(key)
                                    all_results.append(r)
                    except Exception:
                        pass

                if run_search_pattern:
                    try:
                        result = await session.call_tool(
                            "search_for_pattern",
                            arguments={"substring_pattern": re.escape(query)},
                        )
                        if not result.isError and result.content:
                            text = _text_from_content(result.content)
                            for r in _parse_serena_text_to_results(text):
                                key = (r.get("file"), r.get("line"))
                                if key not in seen:
                                    seen.add(key)
                                    all_results.append(r)
                    except Exception:
                        pass

        return {"results": all_results[:MAX_RESULTS], "query": query}
    finally:
        if not _VERBOSE:
            errlog.close()
        elif _SUPPRESS_SERENA_DEBUG:
            errlog.flush()


def search_code(query: str, tool_hint: str | None = None) -> dict:
    """
    Search codebase via Serena MCP (find_symbol, search_for_pattern).
    tool_hint: "find_symbol" | "search_for_pattern" | None (both).
    Returns {"results": [{"file", "symbol", "line", "snippet"}, ...]}.
    If Serena is unavailable, returns {"results": [], "error": "Serena MCP not available"}.
    """
    if _VERBOSE:
        _log.info("[Serena] searching: %s", query)
    if not query or not query.strip():
        return {"results": [], "query": query}

    project_dir = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    if not _MCP_AVAILABLE:
        if _VERBOSE:
            _log.info("[Serena] results: 0 (MCP not available)")
        if _ENABLE_GREP_FALLBACK:
            return _grep_fallback(query, project_dir)
        return {"results": [], "error": "Serena MCP not available", "query": query}

    if os.environ.get("SERENA_USE_PLACEHOLDER", "").lower() in ("1", "true", "yes"):
        if _VERBOSE:
            _log.info("[Serena] results: 0 (placeholder mode)")
        if _ENABLE_GREP_FALLBACK:
            return _grep_fallback(query, project_dir)
        return {"results": [], "query": query}

    try:
        out = asyncio.run(_search_via_mcp(query.strip(), project_dir, tool_hint=tool_hint))
    except Exception as e:
        if _VERBOSE:
            _log.info("[Serena] results: 0 (%s)", e)
        if _ENABLE_GREP_FALLBACK:
            return _grep_fallback(query, project_dir)
        return {"results": [], "error": str(e), "query": query}

    results = out.get("results", [])
    if not results and _ENABLE_GREP_FALLBACK:
        return _grep_fallback(query, project_dir)

    if _VERBOSE:
        _log.info("[Serena] results: %d", len(results))
    return out
