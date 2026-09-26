"""
Autonomous Research Analyst & Adversarial Auditor Engine
Main CLI Driver.
Usage:
  python run.py --query "Which Indian jewellery retailer opened the most new stores in FY24, and what was the count?"
  python run.py --show-memory
  python run.py --reset-memory
  python run.py --benchmark
"""

import sys
import os
import asyncio
import argparse
from pathlib import Path

# Force UTF-8 encoding on Windows console to prevent cp1252 charmap crashes
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.orchestration.coordinator import ResearchAuditorPipeline
from src.memory.store import EntityMemoryStore, DEFAULT_STORE_PATH
from src.telemetry.tracer import TelemetryTracer

console = Console()


def show_memory():
    """Inspects verified persistent entity memory store."""
    store = EntityMemoryStore()
    table = Table(title="Verified Prior Knowledge (Entity Memory Store)", show_header=True, header_style="bold cyan")
    table.add_column("Entity ID", style="cyan", width=18)
    table.add_column("Canonical Name", style="white", width=26)
    table.add_column("Attribute", style="yellow", width=22)
    table.add_column("Verified Value", style="green", width=30)
    table.add_column("Scope", style="magenta", width=10)
    table.add_column("Cited Source URL", style="blue")

    total_facts = 0
    for entity in store.entities.values():
        if entity.facts:
            for fact in entity.facts:
                total_facts += 1
                table.add_row(
                    entity.entity_id,
                    entity.canonical_name,
                    fact.attribute,
                    fact.value[:28] + "..." if len(fact.value) > 28 else fact.value,
                    fact.temporal_anchor or "-",
                    fact.source_url[:35] + "..." if len(fact.source_url) > 35 else fact.source_url,
                )

    if total_facts > 0:
        console.print("\n", table)
        console.print(f"[bold green][OK] Loaded {total_facts} verified fact(s) across {len(store.entities)} registered entities.[/bold green]\n")
    else:
        console.print(Panel("[yellow]Memory store is clean and contains 0 verified facts. Ready for cold benchmark run.[/yellow]"))


def reset_memory():
    """Resets memory store to pristine seed state with backup and disk write."""
    store_path = DEFAULT_STORE_PATH
    if store_path.exists():
        backup_path = store_path.with_name(f"{store_path.stem}_backup.json")
        try:
            store_path.replace(backup_path)
            console.print(f"[yellow]Existing store backed up to: {backup_path}[/yellow]")
        except Exception:
            pass

    # Initialize and explicitly write pristine seed store to disk
    store = EntityMemoryStore()
    store._atomic_save()
    console.print("[bold green][OK] Entity Memory Store reset to pristine seed state.[/bold green]\n")


async def run_pipeline(query: str, no_revision: bool):
    """Executes full live research and audit pipeline."""
    console.print("\n", Panel.fit(
        f"[bold cyan]Autonomous Research & Adversarial Audit Pipeline[/bold cyan]\n"
        f"[white]Query: {query}[/white]"
    ))

    pipeline = ResearchAuditorPipeline()
    result = await pipeline.run_question(
        question_id=1,
        question_text=query,
        enable_revision=not no_revision,
    )

    # 1. Print Final Research Answer
    console.print("\n", Panel(
        result.final_answer,
        title="[bold green]Final Research Synthesis (Audited)[/bold green]",
        border_style="green",
    ))

    # 2. Render Audit Table
    pipeline.render_audit_report_table(result.audit_report)

    # 3. Render Telemetry Summary
    tracer = TelemetryTracer(question_id=result.question_id, question_text=result.question_text)
    tracer.render_summary_table(result.telemetry)

    # 4. Confirmation
    if result.was_revised:
        console.print("[bold yellow][REVISED] 1-Turn Feedback Loop Activated: Analyst self-corrected contradicted claims.[/bold yellow]")

    if result.artifact_path:
        console.print(f"\n[bold white]Artifact Serialized:[/bold white] [cyan]{result.artifact_path}[/cyan]\n")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Research Analyst & Adversarial Auditor CLI")
    parser.add_argument("--query", type=str, help="Research question to process")
    parser.add_argument("--show-memory", action="store_true", help="Display verified entity memory store")
    parser.add_argument("--reset-memory", action="store_true", help="Reset memory store to pristine seed state")
    parser.add_argument("--benchmark", action="store_true", help="Execute the complete 8-question benchmark suite")
    parser.add_argument("--no-revision", action="store_true", help="Disable 1-turn self-correction feedback loop")

    args = parser.parse_args()

    if args.show_memory:
        show_memory()
    elif args.reset_memory:
        reset_memory()
    elif args.benchmark:
        from benchmarks.runner import BenchmarkRunner
        runner = BenchmarkRunner()
        asyncio.run(runner.run_all(enable_revision=not args.no_revision))
    elif args.query:
        asyncio.run(run_pipeline(args.query, args.no_revision))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()