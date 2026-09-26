"""
The Adversarial Auditor Engine.
Orchestrates: Claim Extraction -> Parallel Source Fetching -> Parallel NLI Verification ->
Auditor-Gated Memory Commits -> Telemetry Logging.
Enforces top-3 claim priority ceiling to guarantee sub-60s completion and zero timeout aborts.
"""

import asyncio
from typing import List, Optional, Dict

from src.analyst.schemas import CitationReference
from src.auditor.schemas import (
    AtomicClaim,
    ClaimAuditResult,
    AuditReport,
    VerificationStatus,
)
from src.auditor.llm_client import AuditorLLMClient
from src.auditor.extractor import ClaimExtractor
from src.auditor.verifier import BlindedVerifier
from src.tools.scraper import AsyncWebScraper
from src.tools.schemas import FetchStatus
from src.memory.store import EntityMemoryStore
from src.telemetry.tracer import TelemetryTracer

MAX_CLAIMS_TO_AUDIT = 3  # Optimized ceiling: Audits top 3 core quantitative assertions in ~10-12s


class AuditorEngine:
    """End-to-end verification and memory-commit gatekeeper."""
    def __init__(
        self,
        memory_store: EntityMemoryStore,
        scraper: Optional[AsyncWebScraper] = None,
        llm_client: Optional[AuditorLLMClient] = None,
    ):
        self.memory_store = memory_store
        self.scraper = scraper or AsyncWebScraper()
        self.llm_client = llm_client or AuditorLLMClient()
        self.extractor = ClaimExtractor(self.llm_client)
        self.verifier = BlindedVerifier(self.llm_client)

    async def audit(
        self,
        question_id: int,
        draft_answer: str,
        citations: List[CitationReference],
        tracer: TelemetryTracer,
    ) -> AuditReport:
        """
        Executes end-to-end adversarial audit:
        1. Atomic Claim Extraction
        2. Priority Capping (Top 3 core quantitative claims)
        3. Parallel Web Fetching of cited URLs
        4. Parallel 5-State NLI Verification
        5. Auditor Write Barrier
        """
        # Step 1: Claim Extraction
        with tracer.track_step("auditor_claim_extraction") as step:
            raw_claims, ext_usage = await self.extractor.extract_claims(draft_answer, citations)
            step.set_usage(ext_usage)

        # Prioritize claims with numbers, percentages, currency, and entity actions
        def _claim_priority(c: AtomicClaim) -> int:
            score = 0
            if c.value_hint:
                score += 3
            if c.is_cited:
                score += 2
            if any(char.isdigit() for char in c.statement):
                score += 2
            return score

        sorted_claims = sorted(raw_claims, key=_claim_priority, reverse=True)
        claims = sorted_claims[:MAX_CLAIMS_TO_AUDIT]

        # Step 2: Parallel Fetching of Cited URLs
        cited_urls = list(dict.fromkeys(c.cited_url for c in claims if c.is_cited and c.cited_url))
        url_to_text: Dict[str, str] = {}
        dead_urls: Dict[str, str] = {}

        if cited_urls:
            with tracer.track_step("auditor_source_fetching") as step:
                docs = await self.scraper.fetch_many(cited_urls)
                tracer.record_tool_call(len(cited_urls))

                for doc in docs:
                    if doc.fetch_status == FetchStatus.SUCCESS and doc.raw_markdown.strip():
                        url_to_text[doc.url] = doc.raw_markdown
                    else:
                        dead_urls[doc.url] = f"{doc.fetch_status}: {doc.error_message or 'HTTP Error'}"

        # Step 3: Parallel Verification
        async def _verify_single(claim: AtomicClaim) -> ClaimAuditResult:
            if not claim.is_cited or not claim.cited_url:
                return ClaimAuditResult(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    status=VerificationStatus.UNCITED,
                    cited_url=None,
                    verbatim_quote=None,
                    auditor_rationale="Substantive claim asserted without a citation footnote",
                    confidence=1.0,
                    quote_verified_in_source=False,
                )

            if claim.cited_url in dead_urls:
                return ClaimAuditResult(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    status=VerificationStatus.DEAD_LINK,
                    cited_url=claim.cited_url,
                    verbatim_quote=None,
                    auditor_rationale=f"Cited source unavailable: {dead_urls[claim.cited_url]}",
                    confidence=1.0,
                    quote_verified_in_source=False,
                )

            source_text = url_to_text.get(claim.cited_url, "")
            if not source_text:
                return ClaimAuditResult(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    status=VerificationStatus.DEAD_LINK,
                    cited_url=claim.cited_url,
                    verbatim_quote=None,
                    auditor_rationale="No text extracted from cited source",
                    confidence=1.0,
                    quote_verified_in_source=False,
                )

            with tracer.track_step(f"auditor_verify_{claim.claim_id}") as v_step:
                res, v_usage = await self.verifier.verify(claim, source_text)
                v_step.set_usage(v_usage)
                return res

        results: List[ClaimAuditResult] = []
        if claims:
            results = await asyncio.gather(*[_verify_single(c) for c in claims])

        for res in results:
            tracer.record_audit_verdict(res.status.value)

        # Step 4: Auditor-Gated Memory Commits
        committed_count = 0
        for claim, res in zip(claims, results):
            if (
                res.status == VerificationStatus.SUPPORTED
                and res.confidence >= 0.85
                and res.quote_verified_in_source
                and res.verbatim_quote
                and claim.cited_url
            ):
                entity_target = (claim.entity_hint or "").strip()
                if not entity_target or entity_target.lower() in ["unspecified_entity", "none", "n/a", "unknown"]:
                    resolved = self.memory_store.resolve_entity(claim.statement)
                    if resolved:
                        entity_target = resolved.canonical_name
                    else:
                        continue

                attribute_target = claim.attribute_hint or f"fact_{claim.claim_id}"
                value_target = claim.value_hint or claim.statement

                ok, msg = self.memory_store.commit_verified_fact(
                    entity_name_or_alias=entity_target,
                    attribute=attribute_target,
                    value=value_target,
                    source_url=claim.cited_url,
                    evidence_quote=res.verbatim_quote,
                    auditor_verdict="SUPPORTED",
                    confidence=res.confidence,
                    temporal_anchor=claim.temporal_hint,
                )
                if ok:
                    committed_count += 1

        # Step 5: Report Aggregation
        total = len(results)
        supported = sum(1 for r in results if r.status == VerificationStatus.SUPPORTED)
        contradicted = sum(1 for r in results if r.status == VerificationStatus.CONTRADICTED)
        unsupported = sum(1 for r in results if r.status == VerificationStatus.UNSUPPORTED)
        uncited = sum(1 for r in results if r.status == VerificationStatus.UNCITED)
        dead_link = sum(1 for r in results if r.status == VerificationStatus.DEAD_LINK)

        pass_rate = round(supported / total, 4) if total > 0 else 1.0

        summary = (
            f"Audited {total} priority claim(s): {supported} SUPPORTED, {contradicted} CONTRADICTED, "
            f"{unsupported} UNSUPPORTED, {uncited} UNCITED, {dead_link} DEAD_LINK. "
            f"Pass rate: {pass_rate * 100:.1f}%. Committed {committed_count} verified fact(s) to memory."
        )

        return AuditReport(
            question_id=question_id,
            total_claims=total,
            claims_supported=supported,
            claims_contradicted=contradicted,
            claims_unsupported=unsupported,
            claims_uncited=uncited,
            claims_dead_link=dead_link,
            pass_rate=pass_rate,
            results=results,
            committed_to_memory_count=committed_count,
            auditor_summary=summary,
        )