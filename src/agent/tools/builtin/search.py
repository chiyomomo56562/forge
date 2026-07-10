"""Web search tool — read-only web search via configurable backend.

Supports two backends:
    - ``requests`` — generic HTTP-based search API (default)
    - ``mock`` — returns canned results for testing without network

The tool is classified as ``autonomous`` in tool_policy.yml with a
rate limit of 10 per minute.
"""

from __future__ import annotations

import time
from typing import Any

from ..base import Tool, ToolClass, ToolContext, ToolParameter, ToolResult
from ...utils.logging import get_logger

logger = get_logger("agent.tools.builtin.search")


class WebSearchTool(Tool):
    """Web search tool (read-only).

    Performs a web search and returns a list of result snippets.
    No network calls are made if ``backend`` is ``"mock"``.

    Args:
        backend: Search backend — ``"requests"`` or ``"mock"``.
        api_endpoint: API endpoint for the requests backend.
        rate_limit_per_min: Maximum calls per minute (from tool_policy).
    """

    name = "web_search"
    description = "Search the web for information. Returns a list of result titles, URLs, and snippets."
    tool_class = ToolClass.AUTONOMOUS
    parameters = [
        ToolParameter(
            name="query",
            type="string",
            description="The search query string.",
            required=True,
        ),
        ToolParameter(
            name="max_results",
            type="int",
            description="Maximum number of results to return.",
            required=False,
            default=5,
        ),
    ]

    def __init__(
        self,
        backend: str = "mock",
        api_endpoint: str = "",
        rate_limit_per_min: int = 10,
    ):
        self.backend = backend
        self.api_endpoint = api_endpoint
        self.rate_limit_per_min = rate_limit_per_min
        self._call_timestamps: list[float] = []

    def _check_rate_limit(self) -> bool:
        """Return ``True`` if the call is within the rate limit."""
        now = time.time()
        # Prune timestamps older than 60 seconds
        self._call_timestamps = [t for t in self._call_timestamps if now - t < 60.0]
        if len(self._call_timestamps) >= self.rate_limit_per_min:
            return False
        self._call_timestamps.append(now)
        return True

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)

        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        if not self._check_rate_limit():
            return ToolResult(
                success=False,
                error=f"Rate limit exceeded ({self.rate_limit_per_min}/min)",
            )

        if self.backend == "mock":
            return self._mock_search(query, max_results)
        return self._requests_search(query, max_results)

    def _mock_search(self, query: str, max_results: int) -> ToolResult:
        """Return canned results for testing."""
        results = [
            {
                "title": f"Search result {i+1} for '{query}'",
                "url": f"https://example.com/search?q={query.replace(' ', '+')}&page={i+1}",
                "snippet": f"This is a mock search result snippet for query '{query}', result #{i+1}.",
            }
            for i in range(min(max_results, 3))
        ]
        return ToolResult(
            success=True,
            output=results,
            metadata={"backend": "mock", "query": query, "count": len(results)},
        )

    def _requests_search(self, query: str, max_results: int) -> ToolResult:
        """Perform a real web search via HTTP."""
        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False,
                error="requests library not installed — install with: pip install requests",
            )

        if not self.api_endpoint:
            return ToolResult(
                success=False,
                error="No API endpoint configured for web search",
            )

        try:
            resp = requests.get(
                self.api_endpoint,
                params={"q": query, "count": max_results},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            # Normalise response — adapt to common search API formats
            results = []
            items = data.get("results", data.get("items", data.get("organic", [])))
            for item in items[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", item.get("link", "")),
                    "snippet": item.get("snippet", item.get("description", "")),
                })

            return ToolResult(
                success=True,
                output=results,
                metadata={"backend": "requests", "query": query, "count": len(results)},
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Web search failed: {type(e).__name__}: {e}",
            )