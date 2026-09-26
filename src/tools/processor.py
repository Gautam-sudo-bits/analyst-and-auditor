"""
Two-tier HTML-to-Markdown extractor and hierarchical chunker with source_url provenance,
monolithic paragraph slicing, and budget-constrained relevance ranking.
"""

import re
from typing import List, Optional
from bs4 import BeautifulSoup
import trafilatura

from src.tools.schemas import ScrapedChunk


class ContentProcessor:
    """Handles HTML parsing, markdown extraction, heading-aware chunking, and relevance ranking."""

    def extract_markdown_and_title(self, html: str, fallback_url: str = "") -> tuple[str, str]:
        """
        Two-tier extraction:
        1. Trafilatura: Strips boilerplate and preserves tables/links.
        2. BeautifulSoup Fallback: Activated if Trafilatura yields < 150 characters.
        """
        title = "Untitled"
        markdown = ""

        if not html or not html.strip():
            return "", title

        # Tier 1: Trafilatura
        try:
            extracted = trafilatura.extract(
                html,
                output_format="markdown",
                include_tables=True,
                include_links=True,
                favor_recall=True,
            )
            if extracted and len(extracted.strip()) >= 150:
                markdown = extracted.strip()
                meta = trafilatura.extract_metadata(html)
                if meta and meta.title:
                    title = meta.title.strip()
        except Exception:
            pass

        # Tier 2: BeautifulSoup Fallback
        if not markdown or len(markdown) < 150:
            try:
                soup = BeautifulSoup(html, "html.parser")
                
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
                elif soup.find("h1"):
                    title = soup.find("h1").get_text(strip=True)

                for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe"]):
                    tag.decompose()

                text = soup.get_text(separator="\n\n", strip=True)
                clean_text = re.sub(r"\n{3,}", "\n\n", text)
                if clean_text:
                    markdown = clean_text
            except Exception:
                pass

        return markdown, title

    def chunk_markdown(
        self,
        markdown: str,
        source_url: str = "",
        max_chunk_words: int = 800,
        overlap_words: int = 75,
    ) -> List[ScrapedChunk]:
        """
        Splits markdown into logical chunks:
        1. Splits on headings (#, ##, ###).
        2. Sub-splits long sections by paragraphs.
        3. Slices monolithic paragraphs (> max_chunk_words with no breaks) using word windows.
        Guarantees source_url is populated on every chunk.
        """
        if not markdown.strip():
            return []

        chunks: List[ScrapedChunk] = []
        chunk_id = 0

        # Regex split by markdown headings
        heading_pattern = re.compile(r"(?=(?:^|\n)#{1,3}\s+)")
        raw_sections = [s.strip() for s in heading_pattern.split(markdown) if s.strip()]

        for section in raw_sections:
            lines = section.splitlines()
            current_heading: Optional[str] = None

            if lines and re.match(r"^#{1,3}\s+", lines[0].strip()):
                current_heading = lines[0].strip().lstrip("#").strip()
                body = "\n".join(lines[1:]).strip()
            else:
                body = section

            words = body.split()
            word_count = len(words)

            if word_count <= max_chunk_words:
                chunks.append(
                    ScrapedChunk(
                        chunk_id=chunk_id,
                        source_url=source_url,
                        heading=current_heading,
                        text=body,
                        word_count=word_count,
                    )
                )
                chunk_id += 1
            else:
                # Sub-split long section with paragraph awareness and monolithic paragraph defense
                paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
                current_sub_words: List[str] = []

                for para in paragraphs:
                    p_words = para.split()
                    
                    # Defend against monolithic paragraph exceeding max_chunk_words
                    if len(p_words) > max_chunk_words:
                        # Flush current buffer first
                        if current_sub_words:
                            chunks.append(
                                ScrapedChunk(
                                    chunk_id=chunk_id,
                                    source_url=source_url,
                                    heading=current_heading,
                                    text=" ".join(current_sub_words),
                                    word_count=len(current_sub_words),
                                )
                            )
                            chunk_id += 1
                            current_sub_words = []

                        # Slice monolithic paragraph by word window
                        step = max_chunk_words - overlap_words
                        for i in range(0, len(p_words), step):
                            slice_words = p_words[i:i + max_chunk_words]
                            if slice_words:
                                chunks.append(
                                    ScrapedChunk(
                                        chunk_id=chunk_id,
                                        source_url=source_url,
                                        heading=current_heading,
                                        text=" ".join(slice_words),
                                        word_count=len(slice_words),
                                    )
                                )
                                chunk_id += 1
                        continue

                    # Standard accumulation
                    if len(current_sub_words) + len(p_words) <= max_chunk_words:
                        current_sub_words.extend(p_words)
                    else:
                        if current_sub_words:
                            chunks.append(
                                ScrapedChunk(
                                    chunk_id=chunk_id,
                                    source_url=source_url,
                                    heading=current_heading,
                                    text=" ".join(current_sub_words),
                                    word_count=len(current_sub_words),
                                )
                            )
                            chunk_id += 1
                            current_sub_words = current_sub_words[-overlap_words:] + p_words
                        else:
                            current_sub_words.extend(p_words)

                if current_sub_words:
                    chunks.append(
                        ScrapedChunk(
                            chunk_id=chunk_id,
                            source_url=source_url,
                            heading=current_heading,
                            text=" ".join(current_sub_words),
                            word_count=len(current_sub_words),
                        )
                    )
                    chunk_id += 1

        return chunks

    def filter_and_rank_chunks(
        self,
        chunks: List[ScrapedChunk],
        query_keywords: List[str],
        max_total_words: int = 2500,
    ) -> List[ScrapedChunk]:
        """
        Scores chunks using keyword frequency (with heading multiplier),
        ranks them, and packs the top-scoring chunks up to the max_total_words ceiling.
        Selected chunks are returned in their original reading sequence.
        """
        if not chunks:
            return []

        clean_keywords = [kw.lower().strip() for kw in query_keywords if kw.strip()]

        for chunk in chunks:
            text_lower = chunk.text.lower()
            heading_lower = (chunk.heading or "").lower()

            raw_hits = sum(text_lower.count(kw) for kw in clean_keywords)
            heading_hits = sum(heading_lower.count(kw) for kw in clean_keywords)

            # 2.0x weight for heading matches
            chunk.relevance_score = float(raw_hits + (heading_hits * 2.0))

        # Sort descending by relevance score
        ranked = sorted(chunks, key=lambda c: c.relevance_score, reverse=True)

        selected_chunks: List[ScrapedChunk] = []
        cumulative_words = 0

        for chunk in ranked:
            if cumulative_words + chunk.word_count <= max_total_words:
                selected_chunks.append(chunk)
                cumulative_words += chunk.word_count
            elif not selected_chunks:
                selected_chunks.append(chunk)
                break

        # Re-sort into original document sequence for narrative coherence
        return sorted(selected_chunks, key=lambda c: c.chunk_id)