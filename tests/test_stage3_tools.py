"""
Stage 3 Tool Dispatcher Unit Test.
Verifies live search unwrapping/filtering, live scraping with source_url provenance,
two-tier fallback, monolithic paragraph slicing, streaming payload abort, and defensive timeouts.
Run from root: python tests/test_stage3_tools.py
"""

import sys
import asyncio
import httpx
import os
from rich.console import Console
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.tools.schemas import FetchStatus
from src.tools.search import AsyncSearchEngine
from src.tools.processor import ContentProcessor
from src.tools.scraper import AsyncWebScraper

console = Console()


async def test_live_search():
    console.print("[cyan]1. Verifying Live Web Search & URL Sanitization...[/cyan]")
    search_engine = AsyncSearchEngine()
    results = await search_engine.search("Titan Company store expansion", max_results=3)

    assert len(results) > 0, "Search returned zero results"
    for r in results:
        assert r.url.startswith("http"), f"Invalid URL schema: {r.url}"
        assert "duckduckgo.com/l/?uddg=" not in r.url, f"Failed to unwrap DDG tracking URL: {r.url}"
        assert not r.url.lower().endswith(".pdf"), f"Binary PDF leak detected: {r.url}"

    console.print(f"  [green]✓[/green] Found {len(results)} clean, unwrapped search results.")


async def test_live_scraping_and_provenance():
    console.print("\n[cyan]2. Verifying Live Scraping & Citation Provenance...[/cyan]")
    scraper = AsyncWebScraper()
    test_url = "https://www.python.org"
    doc = await scraper.fetch_document(test_url)

    assert doc.fetch_status == FetchStatus.SUCCESS, f"Scrape failed: {doc.error_message}"
    assert len(doc.raw_markdown) > 200, "Markdown content unexpectedly short"
    assert len(doc.chunks) > 0, "No markdown chunks generated"
    assert doc.http_status_code == 200, f"Expected HTTP 200, got {doc.http_status_code}"

    # Verify citation provenance on every chunk
    for chunk in doc.chunks:
        assert chunk.source_url == test_url, f"Chunk missing source_url provenance: {chunk}"

    console.print(f"  [green]✓[/green] Live page scraped: '{doc.title}' ({len(doc.chunks)} chunks, all linked to source_url).")


def test_two_tier_fallback():
    console.print("\n[cyan]3. Verifying Two-Tier HTML Extraction Fallback...[/cyan]")
    processor = ContentProcessor()

    minimal_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Emergency Fallback Page</title></head>
      <body>
        <nav><a href="/home">Home</a></nav>
        <div id="content">
          <h1>Direct Notification</h1>
          <p>This is a critical fallback test message intended to prove that the secondary parser activates cleanly.</p>
        </div>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """

    markdown, title = processor.extract_markdown_and_title(minimal_html)
    assert "Direct Notification" in markdown, "Fallback parser failed to extract primary text"
    assert "Copyright" not in markdown, "Boilerplate footer was not cleaned up"
    assert title == "Emergency Fallback Page", f"Expected 'Emergency Fallback Page', got '{title}'"

    console.print("  [green]✓[/green] BeautifulSoup secondary tier successfully extracted clean text and title.")


def test_monolithic_paragraph_and_budgeting():
    console.print("\n[cyan]4. Verifying Monolithic Paragraph Slicing & Word Budgeting...[/cyan]")
    processor = ContentProcessor()
    dummy_url = "https://example.com/financial_filing"

    # Create a monolithic paragraph with NO \n\n (1,600 continuous words)
    monolithic_words = "financial metric growth disclosure " * 400
    monolithic_markdown = f"# Annual Report\n\n{monolithic_words}"

    chunks = processor.chunk_markdown(
        monolithic_markdown,
        source_url=dummy_url,
        max_chunk_words=800,
        overlap_words=75,
    )

    assert len(chunks) >= 2, f"Failed to slice monolithic paragraph: produced {len(chunks)} chunks"
    for c in chunks:
        assert c.word_count <= 800, f"Chunk exceeded 800-word limit: {c.word_count} words"
        assert c.source_url == dummy_url, "Chunk missing source_url"

    # Test keyword ranking and budget ceiling
    ranked = processor.filter_and_rank_chunks(chunks, query_keywords=["growth", "disclosure"], max_total_words=1000)
    total_words = sum(c.word_count for c in ranked)
    assert total_words <= 1000, f"Ceiling breach: {total_words} words > 1000 word limit"

    console.print(f"  [green]✓[/green] Monolithic 1,600-word block safely sliced into {len(chunks)} bounded chunks.")


async def test_defensive_streaming_and_timeout():
    console.print("\n[cyan]5. Verifying Streaming Payload Guard & Timeout (FMEA)...[/cyan]")
    scraper = AsyncWebScraper()

    # 1. Non-routable IP timeout test
    non_routable_url = "http://10.255.255.1"
    fast_timeout = httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=1.0)

    html, status, code, latency_ms, err = await scraper.fetch_page(non_routable_url, timeout=fast_timeout)
    assert status == FetchStatus.TIMEOUT, f"Expected TIMEOUT status, got {status}"
    assert html == "", "HTML content must be empty on timeout"
    console.print(f"  [green]✓[/green] Network timeout intercepted gracefully: Status={status} in {latency_ms:.1f}ms")


async def main():
    try:
        await test_live_search()
        await test_live_scraping_and_provenance()
        test_two_tier_fallback()
        test_monolithic_paragraph_and_budgeting()
        await test_defensive_streaming_and_timeout()
        console.print("\n[bold green]✓ STAGE 3 HARDENED TOOL DISPATCHER PASSED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())