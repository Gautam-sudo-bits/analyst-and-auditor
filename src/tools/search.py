"""
Asynchronous search engine with concurrency guards, exponential backoff outside lock,
DuckDuckGo redirect unwrapping, and binary/PDF filtering.
"""

import asyncio
import random
import urllib.parse
from typing import List, Optional

from src.tools.schemas import SearchResult

# Non-HTML extensions to filter out to prevent parser hangs
BLOCKED_EXTENSIONS = (
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".docx", ".doc",
    ".pptx", ".xlsx", ".csv", ".mp4", ".mp3", ".avi", ".jpg",
    ".jpeg", ".png", ".gif", ".webp"
)


def _unwrap_ddg_url(raw_url: str) -> str:
    """Unwraps DuckDuckGo tracking redirects (e.g. /l/?uddg=<ENCODED_URL>)."""
    if "duckduckgo.com/l/?" in raw_url or "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params and params["uddg"]:
                return urllib.parse.unquote(params["uddg"][0])
        except Exception:
            pass
    return raw_url


def _is_binary_url(url: str) -> bool:
    """Checks if the URL targets a non-HTML or binary document."""
    try:
        path = urllib.parse.urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in BLOCKED_EXTENSIONS)
    except Exception:
        return False


class AsyncSearchEngine:
    """
    Search dispatcher with strict concurrency throttling (2 concurrent searches),
    inter-request jitter, and exponential backoff released from semaphore lock.
    """
    def __init__(self, max_concurrent: int = 2):
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _sync_ddg_search(self, query: str, max_results: int) -> List[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        results: List[SearchResult] = []
        with DDGS() as ddgs:
            raw_hits = list(ddgs.text(query, max_results=max_results + 5))
            for hit in raw_hits:
                raw_url = hit.get("href") or hit.get("url") or ""
                clean_url = _unwrap_ddg_url(raw_url)

                if not clean_url or _is_binary_url(clean_url):
                    continue

                title = hit.get("title", "").strip()
                snippet = hit.get("body", "").strip()

                results.append(
                    SearchResult(
                        title=title or "Untitled",
                        url=clean_url,
                        snippet=snippet,
                    )
                )

                if len(results) >= max_results:
                    break

        return results

    async def search(
        self,
        query: str,
        max_results: int = 5,
        max_retries: int = 2,
    ) -> List[SearchResult]:
        """
        Executes a search query. Semaphore lock is held ONLY during the request;
        exponential backoff sleep is executed outside the lock.
        """
        for attempt in range(max_retries + 1):
            try:
                async with self._semaphore:
                    # Jitter to avoid burst triggers
                    await asyncio.sleep(random.uniform(0.3, 0.6))
                    return await asyncio.to_thread(self._sync_ddg_search, query, max_results)
            except Exception:
                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0.1, 0.4)
                    # Release lock BEFORE sleeping
                    await asyncio.sleep(backoff)

        return []