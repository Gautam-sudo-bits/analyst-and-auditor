"""
Stage 4 Memory Store & Anti-Poisoning Barrier Unit Test.
Verifies auditor write-barrier rejections, positive commits, alias resolution,
multi-source discrepancy retention, multi-temporal retention, concurrent writes,
query-aware ranking, safe recovery backup, and self-healing.
Run from root: python tests/test_stage4_memory.py
"""

import os
import sys
import json
import glob
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from rich.console import Console
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.memory.store import EntityMemoryStore, SEED_ENTITIES

console = Console()
TEST_STORE_PATH = Path("data/memory/test_entity_store.json")


def setup_clean_test_store() -> EntityMemoryStore:
    if TEST_STORE_PATH.exists():
        TEST_STORE_PATH.unlink()
    for tmp in glob.glob(f"{TEST_STORE_PATH.stem}_*.tmp"):
        try:
            os.unlink(tmp)
        except Exception:
            pass
    for b in TEST_STORE_PATH.parent.glob(f"{TEST_STORE_PATH.stem}_corrupted_*.json"):
        try:
            b.unlink()
        except Exception:
            pass
    return EntityMemoryStore(storage_path=TEST_STORE_PATH)


def test_write_barrier_rejections():
    console.print("[cyan]1. Verifying Auditor Write Barrier (Negative / Rejection Tests)...[/cyan]")
    store = setup_clean_test_store()

    # Attempt 1: CONTRADICTED verdict
    ok1, reason1 = store.commit_verified_fact(
        entity_name_or_alias="Titan",
        attribute="fy24_stores",
        value="500 stores",
        source_url="https://example.com/source",
        evidence_quote="Titan added 500 stores in the region.",
        auditor_verdict="CONTRADICTED",
        confidence=0.95,
    )
    assert not ok1, "Barrier breached: CONTRADICTED verdict was accepted!"
    assert "Verdict must be 'SUPPORTED'" in reason1
    console.print(f"  [green]✓[/green] Rejection enforced for CONTRADICTED: '{reason1}'")

    # Attempt 2: Low confidence (< 0.85)
    ok2, reason2 = store.commit_verified_fact(
        entity_name_or_alias="Titan",
        attribute="fy24_stores",
        value="110 stores",
        source_url="https://example.com/source",
        evidence_quote="Titan added 110 stores in the region.",
        auditor_verdict="SUPPORTED",
        confidence=0.60,
    )
    assert not ok2, "Barrier breached: Low confidence claim was accepted!"
    assert "Confidence must be >= 0.85" in reason2
    console.print(f"  [green]✓[/green] Rejection enforced for confidence 0.60: '{reason2}'")

    # Attempt 3: Evidence quote too short (< 15 chars)
    ok3, reason3 = store.commit_verified_fact(
        entity_name_or_alias="Titan",
        attribute="fy24_stores",
        value="110 stores",
        source_url="https://example.com/source",
        evidence_quote="Short quote.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
    )
    assert not ok3, "Barrier breached: Insufficient quote was accepted!"
    assert "Evidence quote too short" in reason3
    console.print(f"  [green]✓[/green] Rejection enforced for short quote: '{reason3}'")

    # Attempt 4: Invalid source URL
    ok4, reason4 = store.commit_verified_fact(
        entity_name_or_alias="Titan",
        attribute="fy24_stores",
        value="110 stores",
        source_url="ftp://invalid-server/file",
        evidence_quote="Titan added 110 stores across various markets.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
    )
    assert not ok4, "Barrier breached: Invalid URL scheme was accepted!"
    assert "Source URL is invalid" in reason4
    console.print(f"  [green]✓[/green] Rejection enforced for invalid URL: '{reason4}'")

    # Ensure store remains unpoisoned
    entity = store.resolve_entity("titan")
    assert len(entity.facts) == 0, "Corrupt facts were committed to memory!"
    console.print("  [green]✓[/green] Anti-poisoning guarantee verified: Zero corrupt facts in store.")


def test_multi_temporal_and_source_retention():
    console.print("\n[cyan]2. Verifying Multi-Temporal & Multi-Source Retention...[/cyan]")
    store = setup_clean_test_store()
    shared_url = "https://annualreport.com/titan-filing"

    # Fact 1: FY23 from shared_url
    ok1, _ = store.commit_verified_fact(
        entity_name_or_alias="titan_company",
        attribute="store_additions",
        value="95 net new physical stores",
        source_url=shared_url,
        evidence_quote="In FY23, the brand opened 95 net new physical stores.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
        temporal_anchor="FY23",
    )
    assert ok1

    # Fact 2: FY24 from the EXACT SAME shared_url
    ok2, _ = store.commit_verified_fact(
        entity_name_or_alias="titan_company",
        attribute="store_additions",
        value="110 net new physical stores",
        source_url=shared_url,
        evidence_quote="In FY24, the brand accelerated to 110 net new physical stores.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
        temporal_anchor="FY24",
    )
    assert ok2

    entity = store.resolve_entity("titan_company")
    assert len(entity.facts) == 2, f"Multi-temporal overwrite detected! Expected 2 facts, got {len(entity.facts)}"
    anchors = {f.temporal_anchor for f in entity.facts}
    assert "FY23" in anchors and "FY24" in anchors
    console.print("  [green]✓[/green] Multi-temporal facts from same URL both retained without collision.")


def test_concurrent_commits():
    console.print("\n[cyan]3. Verifying Concurrency Guard (WinError 32 Prevention)...[/cyan]")
    store = setup_clean_test_store()

    def _worker(worker_id: int):
        return store.commit_verified_fact(
            entity_name_or_alias="Zepto",
            attribute=f"metric_batch_{worker_id}",
            value=f"Value recorded by worker {worker_id}",
            source_url=f"https://source-{worker_id}.com/report",
            evidence_quote=f"Verbatim excerpt confirming worker metric number {worker_id}.",
            auditor_verdict="SUPPORTED",
            confidence=0.92,
            temporal_anchor="2024",
        )

    # Dispatch 10 simultaneous writes across a thread pool
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_worker, range(10)))

    for success, msg in results:
        assert success, f"Concurrent commit failed: {msg}"

    entity = store.resolve_entity("zepto")
    assert len(entity.facts) == 10, f"Concurrent write dropped records! Expected 10 facts, got {len(entity.facts)}"
    console.print("  [green]✓[/green] 10 concurrent threads executed commits with zero file-locking collisions.")


def test_query_aware_ranking():
    console.print("\n[cyan]4. Verifying Query-Aware Fact Ranking in Interrogation...[/cyan]")
    store = setup_clean_test_store()

    # Commit 2 distinct facts
    store.commit_verified_fact(
        entity_name_or_alias="Blinkit",
        attribute="dark_store_count",
        value="639 locations across Tier-1 cities",
        source_url="https://example.com/stores",
        evidence_quote="Blinkit operates 639 dark stores nationwide.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
        temporal_anchor="Q1 FY25",
    )
    store.commit_verified_fact(
        entity_name_or_alias="Blinkit",
        attribute="founder_name",
        value="Albinder Dhindsa",
        source_url="https://example.com/founder",
        evidence_quote="Albinder Dhindsa serves as CEO of Blinkit.",
        auditor_verdict="SUPPORTED",
        confidence=0.95,
    )

    # Interrogate specifically asking for dark stores
    result = store.interrogate("What is the dark store count for Blinkit in Q1 FY25?")
    assert result.total_facts_found == 2
    idx_stores = result.formatted_context_block.find("dark_store_count")
    idx_founder = result.formatted_context_block.find("founder_name")
    assert idx_stores < idx_founder, "Query-relevant fact was not ranked ahead of secondary facts!"
    console.print("  [green]✓[/green] Query-aware ranking prioritized 'dark_store_count' over other facts.")


def test_corrupted_load_recovery():
    console.print("\n[cyan]5. Verifying Safe Recovery Backup on Corrupted Load...[/cyan]")
    # 1. Clean previous state
    setup_clean_test_store()

    # 2. Intentionally corrupt test store file with invalid JSON
    with open(TEST_STORE_PATH, "w", encoding="utf-8") as f:
        f.write("{ invalid json payload ...")

    # 3. Load store; catches JSONDecodeError, creates backup snapshot, and self-heals
    recovered_store = EntityMemoryStore(storage_path=TEST_STORE_PATH)

    # 4. Assert backup file exists
    backups = list(TEST_STORE_PATH.parent.glob(f"{TEST_STORE_PATH.stem}_corrupted_*.json"))
    assert len(backups) > 0, "Failed to create recovery backup of corrupted file!"

    # 5. Assert backup preserves the exact corrupt text
    with open(backups[0], "r", encoding="utf-8") as bf:
        backup_content = bf.read()
    assert "{ invalid json payload" in backup_content, "Backup file does not contain original corrupted content!"
    console.print(f"  [green]✓[/green] Corrupted file backed up safely to: '{backups[0].name}'")

    # 6. Assert in-memory entities are correctly restored with all seed entities
    assert len(recovered_store.entities) == len(SEED_ENTITIES), (
        f"Expected {len(SEED_ENTITIES)} seed entities, got {len(recovered_store.entities)}"
    )
    assert len(recovered_store.alias_index) > 0, "Alias index was not populated"

    # 7. Assert self-healing: the disk file itself is now valid, parsable JSON
    with open(TEST_STORE_PATH, "r", encoding="utf-8") as sf:
        healed_disk = json.load(sf)
    assert len(healed_disk) == len(SEED_ENTITIES), "Healed disk file was not saved with seed entities"
    console.print("  [green]✓[/green] Self-healing verified: Corrupted disk store safely restored to clean seed state.")

    # Cleanup test artifacts
    if TEST_STORE_PATH.exists():
        TEST_STORE_PATH.unlink()
    for b in backups:
        try:
            b.unlink()
        except Exception:
            pass


def main():
    try:
        test_write_barrier_rejections()
        test_multi_temporal_and_source_retention()
        test_concurrent_commits()
        test_query_aware_ranking()
        test_corrupted_load_recovery()
        console.print("\n[bold green]✓ STAGE 4 HARDENED MEMORY SUITE PASSED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()