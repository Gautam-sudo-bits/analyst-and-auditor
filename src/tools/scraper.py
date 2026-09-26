"""
Async HTTP scraping engine with streaming payload ceiling enforcement,
SSL resilience, browser headers, and batch fetching.
"""

import time
import httpx
import asyncio
import warnings
from typing import List, Tuple, Optional

from src.tools.schemas import FetchStatus, ScrapedDocument
from src.tools.processor import ContentProcessor

# Suppress insecure SSL warnings for misconfigured public enterprise servers
warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}

MAX_CONTENT_LENGTH = 2_500_000  # 2.5MB payload ceiling


class AsyncWebScraper:
    """
    Streaming HTTP web scraper with strict payload ceiling and failure taxonomy.
    """
    def __init__(self, processor: Optional[ContentProcessor] = None):
        self.processor = processor or ContentProcessor()
        self.default_timeout = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)

    async def fetch_page(
        self,
        url: str,
        timeout: Optional[httpx.Timeout] = None,
    ) -> Tuple[str, FetchStatus, Optional[int], float, Optional[str]]:
        """
        Fetches an individual web page via HTTP streaming.
        Aborts early if Content-Length or streaming body exceeds 2.5MB.
        Returns: (html_content, fetch_status, http_status_code, latency_ms, error_message)
        """
        start_time = time.perf_counter()
        req_timeout = timeout or self.default_timeout

        try:
            async with httpx.AsyncClient(
                headers=BROWSER_HEADERS,
                timeout=req_timeout,
                follow_redirects=True,
                max_redirects=5,
                verify=False,  # Resilient against misconfigured SSL on Indian business portals
            ) as client:
                async with client.stream("GET", url) as response:
                    latency_ms = (time.perf_counter() - start_time) * 1000.0

                    # 1. HTTP Status Code Checks
                    if response.status_code in (401, 403):
                        return "", FetchStatus.BLOCKED_403, response.status_code, latency_ms, f"HTTP {response.status_code} Blocked"
                    
                    if response.status_code >= 400:
                        return "", FetchStatus.FETCH_FAILED, response.status_code, latency_ms, f"HTTP Error {response.status_code}"

                    # 2. Content-Type Inspection
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(t in content_type for t in ["text/html", "application/xhtml+xml"]):
                        return "", FetchStatus.UNSUPPORTED_TYPE, response.status_code, latency_ms, f"Unsupported Content-Type: {content_type}"

                    # 3. Content-Length Header Guard
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_CONTENT_LENGTH:
                        return "", FetchStatus.UNSUPPORTED_TYPE, response.status_code, latency_ms, "Payload exceeds 2.5MB ceiling"

                    # 4. Stream Read with Hard 2.5MB Ceiling
                    body_bytes = bytearray()
                    async for chunk in response.aiter_bytes():
                        body_bytes.extend(chunk)
                        if len(body_bytes) > MAX_CONTENT_LENGTH:
                            latency_ms = (time.perf_counter() - start_time) * 1000.0
                            return "", FetchStatus.UNSUPPORTED_TYPE, response.status_code, latency_ms, "Body stream exceeded 2.5MB ceiling"

                    html = body_bytes.decode(response.encoding or "utf-8", errors="replace")
                    return html, FetchStatus.SUCCESS, response.status_code, latency_ms, None

        except httpx.TimeoutException:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return "", FetchStatus.TIMEOUT, None, latency_ms, "Connection or Read Timeout"

        except httpx.HTTPStatusError as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            code = e.response.status_code if e.response else None
            status = FetchStatus.BLOCKED_403 if code in (401, 403) else FetchStatus.FETCH_FAILED
            return "", status, code, latency_ms, str(e)

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return "", FetchStatus.FETCH_FAILED, None, latency_ms, str(e)[:120]

    async def fetch_document(self, url: str) -> ScrapedDocument:
        """Fetches a URL, extracts markdown, and preserves source_url across all chunks."""
        html, status, code, latency_ms, err = await self.fetch_page(url)

        if status != FetchStatus.SUCCESS:
            return ScrapedDocument(
                url=url,
                fetch_status=status,
                http_status_code=code,
                latency_ms=round(latency_ms, 2),
                error_message=err,
            )

        markdown, title = self.processor.extract_markdown_and_title(html, fallback_url=url)
        chunks = self.processor.chunk_markdown(markdown, source_url=url)

        return ScrapedDocument(
            url=url,
            title=title,
            raw_markdown=markdown,
            chunks=chunks,
            fetch_status=status,
            http_status_code=code,
            latency_ms=round(latency_ms, 2),
            error_message=None,
        )

    async def fetch_many(self, urls: List[str]) -> List[ScrapedDocument]:
        """Fetches multiple URLs in parallel using asyncio.gather."""
        tasks = [self.fetch_document(url) for url in urls]
        return await asyncio.gather(*tasks)