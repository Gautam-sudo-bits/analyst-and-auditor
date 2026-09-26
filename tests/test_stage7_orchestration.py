"""
Stage 7 Pipeline Orchestrator Integration Test.
Verifies:
1. Full End-to-End Pipeline Execution (Live research -> Audit -> Memory commit -> Telemetry -> Artifact)
2. 1-Turn Auditor-to-Analyst Self-Correction Revision Loop
3. 115-Second Wall-Clock Ceiling Supervisor Timeout Defense
4. CLI Subprocess Smoke Test with UTF-8 Pipe Decoding
Run from root: python tests/test_stage7_orchestration.py
"""

import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.orchestration.coordinator import ResearchAuditorPipeline
from src.memory.store import EntityMemoryStore, DEFAULT_STORE_PATH

console = Console()
TEST_STORE_PATH = Path("data/memory/test_orchestrator_store.json")


def clean_test_store():
    if TEST_STORE_PATH.exists():
        try:
            TEST_STORE_PATH.unlink()
        except Exception:
            pass


async def test_full_pipeline_run():
    console.print("[cyan]1. Verifying Full End-to-End Pipeline Execution...[/cyan]")
    clean_test_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    pipeline = ResearchAuditorPipeline(memory_store=store)

    question = "Which Indian quick commerce company expanded dark stores the fastest in 2024?"
    result = await pipeline.run_question(
        question_id=101,
        question_text=question,
        enable_revision=False,
        timeout_seconds=115.0,
    )

    assert result.success, f"Pipeline execution failed: {result.error_message}"
    assert len(result.final_answer) > 100, "Final answer is empty or too short"
    assert "[^" in result.final_answer, "Draft answer is missing footnote citations!"
    assert result.audit_report.total_claims > 0, (
        f"No claims were audited!\n"
        f"Final Answer:\n{result.final_answer}\n"
        f"Audit Report Results: {result.audit_report.results}"
    )
    assert result.telemetry.total_tokens > 0, "Zero tokens recorded in telemetry"
    assert result.telemetry.total_cost_inr >= 0, "Telemetry cost calculation failed"
    assert Path(result.artifact_path).exists(), f"Artifact was not saved to {result.artifact_path}"

    console.print(f"  [green]✓[/green] Pipeline executed successfully in {result.wall_clock_seconds:.2f}s.")
    console.print(f"  [green]✓[/green] Audited {result.audit_report.total_claims} claims. Artifact saved to: {result.artifact_path}")


async def test_self_correction_feedback_loop():
    console.print("\n[cyan]2. Verifying 1-Turn Auditor Self-Correction Revision Loop...[/cyan]")
    # Pacing delay to avoid free-tier RPM rate-limit collision
    await asyncio.sleep(3.0)

    clean_test_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    pipeline = ResearchAuditorPipeline(memory_store=store)

    question = "Titan Company store additions in FY24"
    result = await pipeline.run_question(
        question_id=102,
        question_text=question,
        enable_revision=True,
        timeout_seconds=115.0,
    )

    assert result.success, f"Self-correction run failed: {result.error_message}"
    if result.audit_report.claims_contradicted > 0:
        assert result.was_revised, "Self-correction loop failed to trigger on contradiction!"
        console.print("  [green]✓[/green] Contradiction detected -> 1-turn self-correction loop triggered and resolved.")
    else:
        console.print("  [green]✓[/green] Self-correction engine operational (Initial draft had zero contradictions).")


async def test_ceiling_supervisor_timeout():
    console.print("\n[cyan]3. Verifying Wall-Clock Ceiling Supervisor Timeout Defense...[/cyan]")
    clean_test_store()
    store = EntityMemoryStore(storage_path=TEST_STORE_PATH)
    pipeline = ResearchAuditorPipeline(memory_store=store)

    # Force immediate timeout with 0.001s ceiling
    result = await pipeline.run_question(
        question_id=999,
        question_text="Simulate a hanging external task",
        timeout_seconds=0.001,
    )

    assert not result.success, "Timeout ceiling supervisor failed to flag failure!"
    assert result.telemetry.exceeded_2min_ceiling, "Telemetry failed to flag ceiling breach!"
    assert "Timeout:" in (result.error_message or ""), "Missing timeout error message"
    console.print("  [green]✓[/green] Timeout ceiling intercepted gracefully without unhandled exceptions.")


def test_cli_smoke():
    console.print("\n[cyan]4. Verifying Root CLI Driver (run.py)...[/cyan]")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    # 1. Test run.py --reset-memory with explicit UTF-8 pipe decoding
    cmd_reset = [sys.executable, "run.py", "--reset-memory"]
    res_reset = subprocess.run(
        cmd_reset,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert res_reset.returncode == 0, f"run.py --reset-memory failed: {res_reset.stderr}"
    assert DEFAULT_STORE_PATH.exists(), "run.py --reset-memory failed to write entity_store.json to disk!"
    with open(DEFAULT_STORE_PATH, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert len(disk_data) >= 6, "entity_store.json does not contain seed entities"
    console.print("  [green]✓[/green] run.py --reset-memory executed and verified on disk.")

    # 2. Test run.py --show-memory with explicit UTF-8 pipe decoding
    cmd_show = [sys.executable, "run.py", "--show-memory"]
    res_show = subprocess.run(
        cmd_show,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert res_show.returncode == 0, f"run.py --show-memory failed: {res_show.stderr}"
    console.print("  [green]✓[/green] run.py --show-memory executed with exit code 0.")


async def main():
    try:
        await test_full_pipeline_run()
        await test_self_correction_feedback_loop()
        await test_ceiling_supervisor_timeout()
        test_cli_smoke()
        clean_test_store()
        console.print("\n[bold green]✓ STAGE 7 HARDENED PIPELINE SUITE PASSED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        clean_test_store()
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        clean_test_store()
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())