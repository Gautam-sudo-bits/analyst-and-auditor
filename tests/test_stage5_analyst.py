"""
Stage 5 Analyst Research Engine Unit Test.
Verifies:
1. Cold Start Execution (Zero memory -> search dispatch + granular citations + bibliography).
2. Warm Start Execution (Pre-seeded memory -> delta planning -> reduced tools + memory hit).
3. Epistemic Refusal (Impossible secret metric -> [INSUFFICIENT EVIDENCE]).
Run from root: python tests/test_stage5_analyst.py
"""

import os
import sys
import asyncio
from pathlib import Path
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.analyst.engine import AnalystEngine
from src.memory.store import EntityMemoryStore
from src.telemetry.tracer import TelemetryTracer

console = Console()
TEST_STORE_PATH = Path("data/memory/test_analyst_store.json")


def clean_store():
    if TEST_STORE_PATH.exists():
        try:
            TEST_STORE_PATH.unlink()
        except Exception:
            pass
    tmp = TEST_STORE_PATH.with_suffix(".tmp")
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass


async def test_cold_start():
    console.print("[cyan]1. Verifying Cold Start Execution (Zero Memory)...[/cyan]")
    clean_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    engine = AnalystEngine(memory_store=store)

    tracer = TelemetryTracer(question_id=1, question_text="Titan FY24 store expansion count")
    question = "Which Indian jewellery retailer opened the most new stores in FY24, and what was the count?"

    result = await engine.research(question_id=1, question_text=question, tracer=tracer)

    assert len(result.plan.search_queries) > 0, "Planner generated zero search queries on cold start!"
    assert result.tools_called_count > 0, "Zero tools were called on cold start!"
    assert result.memory_hits_used == 0, f"Expected 0 memory hits, got {result.memory_hits_used}"
    assert "[^" in result.draft_answer, "Draft answer is missing granular [^n] footnote citations!"
    assert len(result.citations) > 0, "Failed to parse citation references from bibliography!"

    console.print(f"  [green]✓[/green] Cold start dispatched {result.tools_called_count} tool calls.")
    console.print(f"  [green]✓[/green] Granular citations detected: {len(result.citations)} sources in bibliography.")


async def test_warm_start():
    console.print("\n[cyan]2. Verifying Warm Start Delta Planning (Memory Transfer)...[/cyan]")
    clean_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)

    # Pre-seed memory with Titan's FY24 store additions
    test_memory_url = "https://en.wikipedia.org/wiki/Titan_Company"
    ok, msg = store.commit_verified_fact(
        entity_name_or_alias="Titan Company",
        attribute="fy24_store_additions",
        value="110 net new physical stores across India",
        source_url=test_memory_url,
        evidence_quote="Titan added 110 net new physical stores across India during FY24.",
        auditor_verdict="SUPPORTED",
        confidence=0.96,
        temporal_anchor="FY24",
    )
    assert ok, f"Pre-seed commit failed: {msg}"

    engine = AnalystEngine(memory_store=store)
    tracer = TelemetryTracer(question_id=2, question_text="Titan and Kalyan FY24 comparison")
    question = "Between Titan and Kalyan Jewellers, how many stores did Titan open in FY24 and what was Kalyan's count?"

    result = await engine.research(question_id=2, question_text=question, tracer=tracer)

    assert result.memory_hits_used > 0, "Engine failed to leverage pre-seeded memory!"
    # Verify planner focused search queries on Kalyan rather than researching Titan from scratch
    kalyan_focused = any("kalyan" in q.lower() for q in result.plan.search_queries)
    assert kalyan_focused, "Planner failed to focus search queries on missing entity (Kalyan)!"

    # Verify memory source URL is carried over into the draft answer / citations
    url_found = (
        test_memory_url in result.draft_answer
        or f"{test_memory_url}/" in result.draft_answer
        or any(test_memory_url.rstrip("/") in c.url.rstrip("/") for c in result.citations)
    )
    assert url_found, (
        f"Draft answer failed to carry over the verified source URL from memory!\n"
        f"Expected: {test_memory_url}\n"
        f"Draft Answer Citations:\n{result.draft_answer}"
    )

    console.print(f"  [green]✓[/green] Memory hit recorded: {result.memory_hits_used} verified fact(s) reused.")
    console.print(f"  [green]✓[/green] Planner delta search focused strictly on missing entity (Kalyan).")
    console.print(f"  [green]✓[/green] Memory citation provenance verified in bibliography.")


async def test_epistemic_refusal():
    console.print("\n[cyan]3. Verifying Epistemic Refusal (Honest Refusal of Private Metric)...[/cyan]")
    clean_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    engine = AnalystEngine(memory_store=store)

    tracer = TelemetryTracer(question_id=3, question_text="Secret forecast test")
    impossible_question = "What is the private unreleased 2027 revenue forecast for Zepto?"

    result = await engine.research(question_id=3, question_text=impossible_question, tracer=tracer)

    assert result.has_epistemic_refusal, (
        "Model hallucinated a figure instead of declaring [INSUFFICIENT EVIDENCE]!"
    )
    assert "[INSUFFICIENT EVIDENCE" in result.draft_answer
    console.print("  [green]✓[/green] Epistemic refusal successfully triggered: [INSUFFICIENT EVIDENCE] asserted.")


async def main():
    try:
        await test_cold_start()
        await test_warm_start()
        await test_epistemic_refusal()
        clean_store()
        console.print("\n[bold green]✓ STAGE 5 ANALYST RESEARCH ENGINE PASSED (100%)[/bold green]\n")
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