"""
Sequential Benchmark Runner.
Executes Q1 through Q8, enforces clean-room memory state at start,
applies 8.0s pacing cooldowns to protect API rate limits, and triggers report generation.
"""

import sys
import json
import asyncio
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel

from src.orchestration.coordinator import ResearchAuditorPipeline
from src.orchestration.schemas import PipelineResult
from src.memory.store import EntityMemoryStore, DEFAULT_STORE_PATH
from benchmarks.report import generate_benchmark_report

console = Console()
BENCHMARK_QUESTIONS_PATH = Path("benchmarks/questions.json")


class BenchmarkRunner:
    """Manages clean-room execution of the 8-question benchmark progression."""
    def __init__(self, questions_path: Optional[Path] = None):
        self.questions_path = questions_path or BENCHMARK_QUESTIONS_PATH
        self.pipeline = ResearchAuditorPipeline()

    def _reset_clean_room(self) -> None:
        """Resets entity store to pristine seed state so Q1 starts cold."""
        if DEFAULT_STORE_PATH.exists():
            backup_path = DEFAULT_STORE_PATH.with_name(f"{DEFAULT_STORE_PATH.stem}_pre_benchmark_backup.json")
            try:
                DEFAULT_STORE_PATH.replace(backup_path)
            except Exception:
                pass

        # Write clean seed store
        store = EntityMemoryStore()
        store._atomic_save()
        self.pipeline.memory_store = store
        self.pipeline.analyst_engine.memory_store = store
        self.pipeline.auditor_engine.memory_store = store
        console.print("[bold green][OK] Clean-room environment established: Memory reset to pristine seed state.[/bold green]\n")

    async def run_all(self, enable_revision: bool = True) -> List[PipelineResult]:
        """Sequentially runs all 8 questions with rate-limit pacing and generates reports."""
        if not self.questions_path.exists():
            raise FileNotFoundError(f"Benchmark questions file not found at: {self.questions_path}")

        with open(self.questions_path, "r", encoding="utf-8") as f:
            questions_data = json.load(f)

        console.print("\n", Panel.fit(
            f"[bold cyan]Autonomous Research & Adversarial Audit Engine[/bold cyan]\n"
            f"[bold white]Executing Authoritative 8-Question Benchmark Suite[/bold white]\n"
            f"[yellow]Target: Proving >= 50% Cost Drop Across Questions while Accuracy Holds[/yellow]"
        ))

        # 1. Reset memory to pristine cold baseline
        self._reset_clean_room()

        results: List[PipelineResult] = []
        total_q = len(questions_data)

        # 2. Sequential Execution
        for item in questions_data:
            qid = item["question_id"]
            theme = item["theme"]
            q_text = item["question_text"]
            mem_type = item["memory_type"]

            console.print("\n" + "=" * 80)
            console.print(Panel(
                f"[bold cyan]Question #{qid} of {total_q}:[/bold cyan] [bold white]{theme}[/bold white]\n"
                f"[yellow]Memory Mode:[/yellow] {mem_type.upper()} | [yellow]Difficulty:[/yellow] {item['difficulty']}\n"
                f"[white]{q_text}[/white]",
                title=f"[bold green]Question #{qid}[/bold green]",
                border_style="cyan",
            ))

            result = await self.pipeline.run_question(
                question_id=qid,
                question_text=q_text,
                enable_revision=enable_revision,
            )
            results.append(result)

            # Print question summary
            console.print(
                f"[bold green]Completed Q#{qid} in {result.wall_clock_seconds:.1f}s | "
                f"Tokens: {result.telemetry.total_tokens:,} | "
                f"Cost: ₹{result.telemetry.total_cost_inr:.4f} | "
                f"Pass Rate: {result.audit_report.pass_rate * 100:.1f}%[/bold green]"
            )

            # Pacing delay between questions to replenish free-tier API rate limits
            if qid < total_q:
                console.print("[dim white]Cooldown: Waiting 8.0s to replenish API rate-limit tokens...[/dim white]")
                await asyncio.sleep(8.0)

        # 3. Generate Executive Benchmark Report & Markdown Documentation
        console.print("\n" + "=" * 80)
        console.print("[bold cyan]Aggregating Benchmark Telemetry & Generating Markdown Report...[/bold cyan]")
        generate_benchmark_report(results, Path("benchmarks/BENCHMARK_REPORT.md"))

        return results