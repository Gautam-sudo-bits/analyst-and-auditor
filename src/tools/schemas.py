"""
Pydantic v2 schemas for search results, document fetching status, and markdown chunks.
Includes source_url provenance tracking on every chunk.
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class FetchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    BLOCKED_403 = "BLOCKED_403"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    FETCH_FAILED = "FETCH_FAILED"


class SearchResult(BaseModel):
    """Represents a clean, unwrapped web search hit."""
    title: str = Field(description="Title of the search result")
    url: str = Field(description="Direct destination URL (unwrapped)")
    snippet: str = Field(description="Search snippet or summary")


class ScrapedChunk(BaseModel):
    """A semantic chunk of extracted markdown content with full citation provenance."""
    chunk_id: int
    source_url: str = Field(default="", description="Origin URL of this chunk")
    heading: Optional[str] = Field(default=None, description="Section heading context")
    text: str = Field(description="Extracted markdown text")
    word_count: int = Field(ge=0)
    relevance_score: float = Field(default=0.0, description="Keyword relevance rank score")


class ScrapedDocument(BaseModel):
    """Full lifecycle document containing raw markdown and semantic chunks."""
    url: str
    title: str = Field(default="Untitled")
    raw_markdown: str = Field(default="")
    chunks: List[ScrapedChunk] = Field(default_factory=list)
    fetch_status: FetchStatus
    http_status_code: Optional[int] = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    error_message: Optional[str] = None