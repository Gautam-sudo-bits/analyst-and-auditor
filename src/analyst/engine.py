"""
The Analyst Research Engine.
Orchestrates: Memory Interrogation -> Delta Planning -> Tool Dispatch -> Synthesis -> Telemetry.
Includes lean search dispatching (top 2 queries, top 3 URLs) to prevent network saturation.
"""

import re
import asyncio
from typing import List, Optional, Set

from src.analyst.schemas import AnalystResearchResult, CitationReference
from src.analyst.llm_client import AnalystLLMClient
from src.analyst.planner import AnalystPlanner
from src.analyst.synthesizer import AnalystSynthesizer
from src.memory.store import EntityMemoryStore
from src.tools.search import AsyncSearchEngine
from src.tools.scraper import AsyncWebScraper
from src.tools.processor import ContentProcessor
from src.tools.schemas import ScrapedChunk, FetchStatus
from src.telemetry.tracer import TelemetryTracer


class AnalystEngine:
    """End-to-end research synthesis pipeline for the Analyst Plane."""
    def __init__(
        self,
        memory_store: EntityMemoryStore,
        search_engine: Optional[AsyncSearchEngine] = None,
        scraper: Optional[AsyncWebScraper] = None,
        processor: Optional[ContentProcessor] = None,
        llm_client: Optional[AnalystLLMClient] = None,
    ):
        self.memory_store = memory_store
        self.search_engine = search_engine or AsyncSearchEngine()
        self.processor = processor or ContentProcessor()
        self.scraper = scraper or AsyncWebScraper(processor=self.processor)
        self.llm_client = llm_client or AnalystLLMClient()
        self.planner = AnalystPlanner(self.llm_client)
        self.synthesizer = AnalystSynthesizer(self.llm_client)

    async def research(
        self,
        question_id: int,
        question_text: str,
        tracer: TelemetryTracer,
    ) -> AnalystResearchResult:
        """
        Executes complete research lifecycle:
        1. Interrogates Memory
        2. Delta Planning
        3. Parallel Search and Scrape (top 2 queries, top 3 URLs)
        4. Synthesis
        5. Canonical URL Reconciliation & Citation Repair
        """
        # Step 1: Memory Interrogation
        interrogation = self.memory_store.interrogate(question_text)
        memory_urls: List[str] = []
        for hit in interrogation.hits:
            for fact in hit.facts:
                if fact.source_url and fact.source_url not in memory_urls:
                    memory_urls.append(fact.source_url)

        if interrogation.total_facts_found > 0:
            tracer.record_memory_hit(interrogation.total_facts_found)

        # Step 2: Delta Planning
        with tracer.track_step("analyst_planning") as step:
            plan, plan_usage = await self.planner.create_plan(
                question_text=question_text,
                memory_context_str=interrogation.formatted_context_block,
            )
            step.set_usage(plan_usage)

        # Step 3: Tool Dispatch (Lean budget: max 2 queries, max 3 URLs)
        all_chunks: List[ScrapedChunk] = []
        search_results_nested = []
        tools_called = 0

        target_queries = plan.search_queries[:2]  # Cap at 2 queries to avoid latency sprawl

        if target_queries:
            # 3A. Parallel Web Searches
            with tracer.track_step("analyst_web_search") as step:
                search_tasks = [
                    self.search_engine.search(query=q, max_results=3)
                    for q in target_queries
                ]
                search_results_nested = await asyncio.gather(*search_tasks)
                tools_called += len(target_queries)
                tracer.record_tool_call(len(target_queries))

            # Deduplicate and pick top 3 unique URLs
            candidate_urls: List[str] = []
            for hits in search_results_nested:
                for hit in hits:
                    if hit.url not in candidate_urls:
                        candidate_urls.append(hit.url)
            selected_urls = candidate_urls[:3]

            # 3B. Parallel Web Scraping
            if selected_urls:
                with tracer.track_step("analyst_web_scraping") as step:
                    docs = await self.scraper.fetch_many(selected_urls)
                    tools_called += len(selected_urls)
                    tracer.record_tool_call(len(selected_urls))

                    for doc in docs:
                        if doc.fetch_status == FetchStatus.SUCCESS and doc.chunks:
                            all_chunks.extend(doc.chunks)

            # 3C. Snippet Fallback if web scraping yielded zero chunks
            if not all_chunks and search_results_nested:
                for hits in search_results_nested:
                    for hit in hits:
                        if hit.snippet and len(hit.snippet.strip()) > 30:
                            all_chunks.append(
                                ScrapedChunk(
                                    chunk_id=len(all_chunks),
                                    source_url=hit.url,
                                    heading=hit.title,
                                    text=hit.snippet,
                                    word_count=len(hit.snippet.split()),
                                )
                            )

        # 3D. Semantic Relevance Ranking & Word Budgeting
        query_keywords = (
            plan.missing_information_goals
            + [question_text]
            + target_queries
        )
        ranked_chunks = self.processor.filter_and_rank_chunks(
            chunks=all_chunks,
            query_keywords=query_keywords,
            max_total_words=2500,
        )

        known_sources: List[str] = list(dict.fromkeys(memory_urls + [c.source_url for c in ranked_chunks if c.source_url]))

        # Step 4: Synthesis
        with tracer.track_step("analyst_synthesis") as step:
            draft_answer, synth_usage = await self.synthesizer.synthesize(
                question_text=question_text,
                plan=plan,
                memory_context_str=interrogation.formatted_context_block,
                web_chunks=ranked_chunks,
            )
            step.set_usage(synth_usage)

        # Step 5: Multi-Pattern Citation Extraction & Canonical URL Healing
        citations: List[CitationReference] = []
        seen_urls: Set[str] = set()

        pattern_a = re.compile(r"\[\^?(\d+)\]:?\s*(?:\[[^\]]*\]\()?<?(https?://[^\s>\)]+)>?\)?")
        for match in pattern_a.finditer(draft_answer):
            idx = int(match.group(1))
            raw_url = match.group(2).rstrip(".)],;")

            healed_url = raw_url
            for canonical in known_sources:
                clean_raw = raw_url.rstrip(".")
                if clean_raw in canonical and len(clean_raw) > 10:
                    draft_answer = draft_answer.replace(raw_url, canonical)
                    healed_url = canonical
                    break

            if healed_url not in seen_urls:
                seen_urls.add(healed_url)
                citations.append(CitationReference(index=idx, url=healed_url))

        if not citations:
            raw_urls = re.findall(r"https?://[^\s>\)\"\']+", draft_answer)
            for i, u in enumerate(raw_urls, start=1):
                clean_u = u.rstrip(".)],;")
                healed_u = clean_u
                for canonical in known_sources:
                    clean_raw = clean_u.rstrip(".")
                    if clean_raw in canonical and len(clean_raw) > 10:
                        draft_answer = draft_answer.replace(clean_u, canonical)
                        healed_u = canonical
                        break

                if healed_u not in seen_urls:
                    seen_urls.add(healed_u)
                    citations.append(CitationReference(index=i, url=healed_u))

        if not citations and known_sources:
            footnote_indices = sorted(list(set(int(m) for m in re.findall(r"\[\^(\d+)\]", draft_answer))))
            if not footnote_indices:
                footnote_indices = list(range(1, min(len(known_sources) + 1, 5)))

            bib_lines = ["\n\n## Citations"]
            for idx in footnote_indices:
                url_to_use = known_sources[(idx - 1) % len(known_sources)]
                citations.append(CitationReference(index=idx, url=url_to_use))
                bib_lines.append(f"[^{idx}]: {url_to_use}")

            draft_answer += "\n" + "\n".join(bib_lines)

        for mem_url in memory_urls:
            if mem_url not in draft_answer:
                idx = len(citations) + 1
                citations.append(CitationReference(index=idx, url=mem_url))
                if "## Citations" in draft_answer:
                    draft_answer += f"\n[^{idx}]: {mem_url}"
                else:
                    draft_answer += f"\n\n## Citations\n[^{idx}]: {mem_url}"

        has_refusal = (
            "[INSUFFICIENT EVIDENCE" in draft_answer
            or "insufficient evidence" in draft_answer.lower()
        )

        return AnalystResearchResult(
            question_id=question_id,
            question_text=question_text,
            plan=plan,
            draft_answer=draft_answer,
            citations=citations,
            ranked_chunks=ranked_chunks,
            tools_called_count=tools_called,
            memory_hits_used=interrogation.total_facts_found,
            has_epistemic_refusal=has_refusal,
        )