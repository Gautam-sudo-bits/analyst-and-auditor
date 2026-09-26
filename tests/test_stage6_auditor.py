"""
Stage 6 Adversarial Auditor Engine Unit Test.
Verifies:
1. Atomic Claim Extraction & Empty Refusal Handling
2. 5-State Entailment Matrix & Fuzzy Quote Normalization (smart quotes, em-dashes, non-breaking spaces)
3. Memory Gatekeeper & Ambiguous Entity Filtering
4. Telemetry Metric Synchronization
Run from root: python tests/test_stage6_auditor.py
"""

import os
import sys
import asyncio
from pathlib import Path
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.auditor.schemas import VerificationStatus, AtomicClaim
from src.analyst.schemas import CitationReference
from src.auditor.engine import AuditorEngine
from src.auditor.extractor import ClaimExtractor
from src.auditor.verifier import BlindedVerifier, verify_quote_in_source
from src.auditor.llm_client import AuditorLLMClient
from src.memory.store import EntityMemoryStore
from src.telemetry.tracer import TelemetryTracer

console = Console()
TEST_STORE_PATH = Path("data/memory/test_auditor_store.json")


def clean_store():
    if TEST_STORE_PATH.exists():
        try:
            TEST_STORE_PATH.unlink()
        except Exception:
            pass


async def test_atomic_extraction_and_refusals():
    console.print("[cyan]1. Verifying Atomic Claim Extraction & Pure Refusal Handling...[/cyan]")
    llm = AuditorLLMClient()
    extractor = ClaimExtractor(llm)

    # 1A. Standard Decomposition
    draft_answer = (
        "Titan Company added 110 physical stores in FY24[^1]. "
        "The company generated ₹40,000 crore in total jewelry sales[^2]. "
        "Titan operates 500 retail stores in southern India."
    )
    citations = [
        CitationReference(index=1, url="https://example.com/stores"),
        CitationReference(index=2, url="https://example.com/sales"),
    ]

    claims, usage = await extractor.extract_claims(draft_answer, citations)
    assert len(claims) >= 3, f"Expected at least 3 atomic claims, got {len(claims)}"

    uncited_claims = [c for c in claims if not c.is_cited]
    assert len(uncited_claims) >= 1, "Failed to identify uncited assertion in draft answer!"
    console.print(f"  [green]✓[/green] Decomposed narrative into {len(claims)} atomic claims with footnote mapping.")

    # 1B. Pure Epistemic Refusal (Zero claims)
    refusal_text = "[INSUFFICIENT EVIDENCE: The gathered primary sources do not contain verified data for this unreleased private metric.]"
    refusal_claims, _ = await extractor.extract_claims(refusal_text, [])
    assert len(refusal_claims) == 0, f"Expected 0 claims for pure refusal, got {len(refusal_claims)}"
    console.print("  [green]✓[/green] Pure epistemic refusal correctly emitted an empty claim array.")


def test_quote_normalization_resilience():
    console.print("\n[cyan]2. Verifying Quote Normalization (Smart Quotes, Dashes, Unicode)...[/cyan]")
    source_html = "Titan Company added 110 net new physical stores—expanding its domestic presence—in FY24."

    # Quote extracted with smart quotes and standard hyphens
    quote_with_smart_quotes = "“Titan Company added 110 net new physical stores-expanding its domestic presence-in FY24.”"
    assert verify_quote_in_source(quote_with_smart_quotes, source_html), (
        "Failed to match quote containing smart quotes and dash substitutions!"
    )

    # Quote with trailing period omitted in source
    quote_with_punct = "Titan Company added 110 net new physical stores-expanding its domestic presence-in FY24."
    assert verify_quote_in_source(quote_with_punct, source_html), (
        "Failed to match quote with punctuation differences!"
    )

    console.print("  [green]✓[/green] Fuzzy quote normalization verified against unicode dashes and smart quotes.")


async def test_5_state_verification_matrix():
    console.print("\n[cyan]3. Verifying 5-State Entailment Matrix & Quote Verification...[/cyan]")
    llm = AuditorLLMClient()
    verifier = BlindedVerifier(llm)

    source_text = (
        "Titan Company Limited reported its annual performance for the fiscal year 2024. "
        "During FY24, Titan added exactly 110 net new physical stores across its retail network in India. "
        "The jewelry division recorded domestic revenue growth of 20 percent."
    )

    # State A: SUPPORTED
    claim_supported = AtomicClaim(
        claim_id="claim_sup",
        statement="Titan Company added 110 net new physical stores across India in FY24.",
        is_cited=True,
        cited_url="https://example.com/titan",
        entity_hint="Titan Company",
        attribute_hint="fy24_store_additions",
        value_hint="110 stores",
        temporal_hint="FY24",
    )
    res_sup, _ = await verifier.verify(claim_supported, source_text)
    assert res_sup.status == VerificationStatus.SUPPORTED, f"Expected SUPPORTED, got {res_sup.status}"
    assert res_sup.quote_verified_in_source, "Verbatim quote failed programmatic verification!"
    console.print("  [green]✓[/green] SUPPORTED: Direct entailment verified with programmatic quote match.")

    # State B: CONTRADICTED
    claim_contradicted = AtomicClaim(
        claim_id="claim_con",
        statement="Titan Company added 250 net new stores in FY24.",
        is_cited=True,
        cited_url="https://example.com/titan",
        entity_hint="Titan Company",
        attribute_hint="fy24_store_additions",
        value_hint="250 stores",
    )
    res_con, _ = await verifier.verify(claim_contradicted, source_text)
    assert res_con.status == VerificationStatus.CONTRADICTED, f"Expected CONTRADICTED, got {res_con.status}"
    console.print("  [green]✓[/green] CONTRADICTED: Discrepancy (250 vs 110) correctly flagged.")

    # State C: UNSUPPORTED
    claim_unsupported = AtomicClaim(
        claim_id="claim_unsup",
        statement="Titan Company opened 40 stores in London in FY24.",
        is_cited=True,
        cited_url="https://example.com/titan",
        entity_hint="Titan Company",
        attribute_hint="london_stores",
    )
    res_unsup, _ = await verifier.verify(claim_unsupported, source_text)
    assert res_unsup.status == VerificationStatus.UNSUPPORTED, f"Expected UNSUPPORTED, got {res_unsup.status}"
    console.print("  [green]✓[/green] UNSUPPORTED: Unsubstantiated London expansion identified.")


async def test_memory_gatekeeper_and_entity_hygiene():
    console.print("\n[cyan]4. Verifying Memory Gatekeeper & Entity Hygiene...[/cyan]")
    clean_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    engine = AuditorEngine(memory_store=store)

    tracer = TelemetryTracer(question_id=99, question_text="Audit validation test")

    # Draft answer containing:
    # 1. Fact backed by valid URL (will be mocked as SUPPORTED)
    # 2. Fact backed by unreachable URL (DEAD_LINK)
    # 3. Uncited assertion (UNCITED)
    draft_answer = (
        "Titan Company added 110 net new physical stores across India in FY24[^1]. "
        "Titan reported international operations on an external server[^2]. "
        "Titan operates 500 retail stores in southern India."
    )
    citations = [
        CitationReference(index=1, url="https://en.wikipedia.org/wiki/Titan_Company"),
        CitationReference(index=2, url="http://10.255.255.1/dead_link"),
    ]

    report = await engine.audit(
        question_id=99,
        draft_answer=draft_answer,
        citations=citations,
        tracer=tracer,
    )

    # Verify 5-state presence
    statuses = {r.status for r in report.results}
    assert VerificationStatus.UNCITED in statuses, (
        f"UNCITED claim was not captured in audit report!\n"
        f"Extracted results: {[r.model_dump() for r in report.results]}"
    )
    assert VerificationStatus.DEAD_LINK in statuses, (
        f"DEAD_LINK claim was not captured in audit report!\n"
        f"Extracted results: {[r.model_dump() for r in report.results]}"
    )

    # Verify Memory Gatekeeper: Only SUPPORTED claims enter memory
    entity = store.resolve_entity("titan")
    assert report.committed_to_memory_count == len(entity.facts), (
        f"Memory write barrier breached! Report committed {report.committed_to_memory_count} facts, but store has {len(entity.facts)}"
    )
    assert all(f.auditor_verdict == "SUPPORTED" for f in entity.facts), "Non-SUPPORTED fact found in entity store!"

    # Ensure store is not polluted with "unspecified_entity"
    assert "unspecified_entity" not in store.entities

    # Verify Telemetry Audit Counts
    assert tracer.audit_counts["UNCITED"] >= 1
    assert tracer.audit_counts["DEAD_LINK"] >= 1

    console.print(f"  [green]✓[/green] Audit Report: {report.auditor_summary}")
    console.print(f"  [green]✓[/green] Memory Gatekeeper: {report.committed_to_memory_count} fact(s) committed, non-supported rejected.")
    console.print("  [green]✓[/green] Telemetry audit metrics successfully synchronized.")


async def main():
    try:
        await test_atomic_extraction_and_refusals()
        test_quote_normalization_resilience()
        await test_5_state_verification_matrix()
        await test_memory_gatekeeper_and_entity_hygiene()
        clean_store()
        console.print("\n[bold green]✓ STAGE 6 HARDENED AUDITOR SUITE PASSED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        clean_store()
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        clean_store()
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())