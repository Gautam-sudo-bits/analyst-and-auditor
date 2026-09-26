"""
Stage 8 Benchmark Suite & Reporting Unit Test.
Verifies:
1. Schema & integrity of benchmarks/questions.json (8 questions, cold vs warm progression)
2. Mathematical cost drop percentage calculation & report generator with synthetic metrics
3. CLI smoke test verifying python run.py --help registers --benchmark
Run from root: python tests/test_stage8_benchmark.py
"""

import os
import sys
import json
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
from src.orchestration.schemas import PipelineResult
from src.auditor.schemas import AuditReport, ClaimAuditResult, VerificationStatus
from src.telemetry.schemas import QuestionRunTelemetry
from benchmarks.report import generate_benchmark_report

console = Console()
QUESTIONS_PATH = Path("benchmarks/questions.json")
TEST_REPORT_PATH = Path("benchmarks/test_benchmark_report_sample.md")


def test_questions_json_integrity():
    console.print("[cyan]1. Verifying Benchmark Questions Specification...[/cyan]")
    assert QUESTIONS_PATH.exists(), f"Missing {QUESTIONS_PATH}"

    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        questions = json.load(f)

    assert len(questions) == 8, f"Expected exactly 8 benchmark questions, found {len(questions)}"

    # Validate structure and progression
    for i, q in enumerate(questions, start=1):
        assert q["question_id"] == i, f"Question ID mismatch at index {i}"
        assert len(q["question_text"].strip()) > 20, f"Question #{i} text is too short"
        assert len(q["target_entities"]) > 0, f"Question #{i} missing target entities"
        if i <= 4:
            assert q["memory_type"] == "cold_start", f"Question #{i} should be cold_start"
        else:
            assert q["memory_type"] == "warm_memory", f"Question #{i} should be warm_memory"

    console.print("  [green]✓[/green] All 8 questions verified: Q1-Q4 (Cold Exploration) -> Q5-Q8 (Warm Transfer).")


def test_report_generation_and_cost_drop_math():
    console.print("\n[cyan]2. Verifying Mathematical Cost Drop Calculation & Report Generator...[/cyan]")

    # Create 8 synthetic PipelineResult objects:
    # Q1-Q4 (Cold): Average cost = ₹1.40 (12,000 tokens)
    # Q5-Q8 (Warm): Average cost = ₹0.56 (4,800 tokens) -> 60.0% cost reduction!
    mock_results: list[PipelineResult] = []

    for qid in range(1, 9):
        is_cold = qid <= 4
        cost_inr = 1.40 if is_cold else 0.56
        tokens = 12000 if is_cold else 4800
        hits = 0 if is_cold else 3
        tools = 4 if is_cold else 1

        mock_telemetry = QuestionRunTelemetry(
            question_id=qid,
            question_text=f"Question #{qid}",
            wall_clock_seconds=30.0 if is_cold else 14.0,
            exceeded_2min_ceiling=False,
            total_prompt_tokens=int(tokens * 0.75),
            total_completion_tokens=int(tokens * 0.25),
            total_reasoning_tokens=0,
            total_tokens=tokens,
            total_cost_usd=cost_inr / 85.0,
            total_cost_inr=cost_inr,
            tools_called_count=tools,
            memory_hits_count=hits,
            claims_total=4,
            claims_supported=3,
            claims_contradicted=0,
            claims_unsupported=1,
            claims_uncited=0,
            audit_pass_rate=0.75,
            step_traces=[],
            timestamp="2026-09-26T20:00:00Z",
        )

        mock_audit = AuditReport(
            question_id=qid,
            total_claims=4,
            claims_supported=3,
            claims_contradicted=0,
            claims_unsupported=1,
            claims_uncited=0,
            claims_dead_link=0,
            pass_rate=0.75,
            results=[],
            committed_to_memory_count=3,
            auditor_summary="Mock audit complete",
        )

        mock_results.append(
            PipelineResult(
                question_id=qid,
                question_text=f"Question #{qid}",
                initial_answer=f"Draft answer for #{qid}",
                was_revised=False,
                final_answer=f"Final answer for #{qid}",
                audit_report=mock_audit,
                telemetry=mock_telemetry,
                artifact_path=f"benchmarks/runs/run_q{qid}.json",
                wall_clock_seconds=mock_telemetry.wall_clock_seconds,
                success=True,
            )
        )

    # Generate Report
    generate_benchmark_report(mock_results, TEST_REPORT_PATH)

    assert TEST_REPORT_PATH.exists(), "Markdown report was not created"
    with open(TEST_REPORT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify mathematical reduction: ((1.40 - 0.56) / 1.40) * 100 = 60.0%
    assert "60.0%" in content, "Failed to compute exact 60.0% cost drop!"
    assert "Cold Baseline" in content
    assert "Warm Transfer" in content
    assert "Architectural Proof: Why Cost Dropped by >= 50%" in content

    # Clean up test artifact
    if TEST_REPORT_PATH.exists():
        TEST_REPORT_PATH.unlink()

    console.print("  [green]✓[/green] Cost drop formula verified: 60.0% reduction accurately calculated and documented.")


def test_cli_benchmark_flag():
    console.print("\n[cyan]3. Verifying CLI Registration (--benchmark)...[/cyan]")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, "run.py", "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    assert res.returncode == 0, f"run.py --help failed: {res.stderr}"
    assert "--benchmark" in res.stdout, "--benchmark flag was not registered in run.py!"
    console.print("  [green]✓[/green] CLI entry point registered: 'python run.py --benchmark' available.")


def main():
    try:
        test_questions_json_integrity()
        test_report_generation_and_cost_drop_math()
        test_cli_benchmark_flag()
        console.print("\n[bold green]✓ STAGE 8 BENCHMARK SUITE VALIDATED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        if TEST_REPORT_PATH.exists():
            TEST_REPORT_PATH.unlink()
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        if TEST_REPORT_PATH.exists():
            TEST_REPORT_PATH.unlink()
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()