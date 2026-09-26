"""
Stage 2 Telemetry & Cost Engine Unit Test.
Verifies rate calculation math, null safety, usage extraction (including Gemini thought tokens),
tracer idempotency, evaluation metrics aggregation, rich rendering, and JSON artifact persistence.
Run from root: python tests/test_stage2_telemetry.py
"""

import math
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.telemetry.pricing import calculate_cost, get_model_rates, RATE_CARD
from src.telemetry.schemas import ModelCallUsage, QuestionRunTelemetry
from src.telemetry.tracer import TelemetryTracer, extract_usage
from rich.console import Console

console = Console()


def test_pricing_math_and_null_safety():
    console.print("[cyan]1. Verifying Pricing Math & Null Safety...[/cyan]")

    # Analyst: 10,000 prompt tokens + 2,000 completion tokens
    # Rates: $0.50/M prompt, $3.00/M completion
    # Expected: $0.005 + $0.006 = $0.011 USD -> ₹0.935 INR (@ 85.0)
    a_usd, a_inr = calculate_cost(
        model_name="gemini-3-flash-preview",
        prompt_tokens=10_000,
        completion_tokens=2_000,
        cached_tokens=0,
        usd_to_inr=85.0,
    )
    assert math.isclose(a_usd, 0.011, abs_tol=1e-8), f"Analyst USD mismatch: {a_usd} != 0.011"
    assert math.isclose(a_inr, 0.935, abs_tol=1e-6), f"Analyst INR mismatch: {a_inr} != 0.935"
    console.print("  [green]✓[/green] Analyst math verified: 10k prompt + 2k output = $0.011 -> ₹0.935")

    # Auditor: 5,000 prompt tokens + 1,000 completion tokens (including 400 reasoning)
    # Rates: $0.15/M prompt, $0.60/M completion
    # Expected: $0.00075 + $0.0006 = $0.00135 USD -> ₹0.11475 INR (@ 85.0)
    aud_usd, aud_inr = calculate_cost(
        model_name="openai/gpt-oss-120b",
        prompt_tokens=5_000,
        completion_tokens=1_000,
        cached_tokens=0,
        usd_to_inr=85.0,
    )
    assert math.isclose(aud_usd, 0.00135, abs_tol=1e-8), f"Auditor USD mismatch: {aud_usd} != 0.00135"
    assert math.isclose(aud_inr, 0.11475, abs_tol=1e-6), f"Auditor INR mismatch: {aud_inr} != 0.11475"
    console.print("  [green]✓[/green] Auditor math verified: 5k prompt + 1k output = $0.00135 -> ₹0.11475")

    # Null safety: None or non-string input safely falls back without raising AttributeError
    rates_none = get_model_rates(None)
    assert rates_none == RATE_CARD["default"], "get_model_rates(None) did not return default rate card"
    fallback_usd, fallback_inr = calculate_cost(None, 1000, 1000)
    assert fallback_usd > 0.0 and fallback_inr > 0.0
    console.print("  [green]✓[/green] Defensive null-safety verified: get_model_rates(None) returned fallback.")


def test_usage_extraction_with_thinking_tokens():
    console.print("\n[cyan]2. Verifying Safe Usage Extraction Adapter (with Thinking Tokens)...[/cyan]")

    # Gemini with thought_token_count
    class DummyGeminiMetaWithThoughts:
        prompt_token_count = 4000
        candidates_token_count = 1200
        cached_content_token_count = 1000
        thought_token_count = 500

    class DummyGeminiResponse:
        usage_metadata = DummyGeminiMetaWithThoughts()

    gemini_usage = extract_usage(DummyGeminiResponse(), provider="analyst", model_name="gemini-3-flash-preview")
    assert gemini_usage.prompt_tokens == 4000
    assert gemini_usage.completion_tokens == 1200
    assert gemini_usage.cached_tokens == 1000
    assert gemini_usage.reasoning_tokens == 500
    console.print("  [green]✓[/green] Gemini usage extracted successfully with 500 thinking tokens.")

    # Groq/OpenAI with reasoning tokens
    class DummyReasoningDetails:
        reasoning_tokens = 350

    class DummyGroqUsage:
        prompt_tokens = 3000
        completion_tokens = 700
        completion_tokens_details = DummyReasoningDetails()

    class DummyGroqResponse:
        usage = DummyGroqUsage()

    groq_usage = extract_usage(DummyGroqResponse(), provider="auditor", model_name="openai/gpt-oss-120b")
    assert groq_usage.prompt_tokens == 3000
    assert groq_usage.completion_tokens == 700
    assert groq_usage.reasoning_tokens == 350
    console.print("  [green]✓[/green] Groq usage extracted successfully without double-billing reasoning tokens.")


def test_tracer_idempotency_and_evaluation_metrics():
    console.print("\n[cyan]3. Verifying Finalize Idempotency & First-Class Evaluation Metrics...[/cyan]")

    tracer = TelemetryTracer(
        question_id=0,
        question_text="What was Nvidia's Q4 FY25 data center revenue?",
        usd_to_inr=85.0,
    )

    # Record evaluation metrics
    tracer.record_tool_call()
    tracer.record_tool_call()
    tracer.record_memory_hit()

    tracer.record_audit_verdict("SUPPORTED")
    tracer.record_audit_verdict("SUPPORTED")
    tracer.record_audit_verdict("CONTRADICTED")
    tracer.record_audit_verdict("UNSUPPORTED")

    # Step 1: Research step
    with tracer.track_step("analyst_research_and_synthesis") as step:
        time.sleep(0.02)
        cost_usd, cost_inr = calculate_cost("gemini-3-flash-preview", 10_000, 2_000, usd_to_inr=85.0)
        step.set_usage(
            ModelCallUsage(
                model_name="gemini-3-flash-preview",
                provider="analyst",
                prompt_tokens=10_000,
                completion_tokens=2_000,
                reasoning_tokens=0,
                cost_usd=cost_usd,
                cost_inr=cost_inr,
            )
        )

    # First call to finalize
    t1 = tracer.finalize()
    frozen_latency = t1.wall_clock_seconds

    # Simulate delay before second call
    time.sleep(0.05)
    t2 = tracer.finalize()

    # Assert clock is completely frozen (idempotent)
    assert t1.wall_clock_seconds == t2.wall_clock_seconds, (
        f"Latency drift detected! {t1.wall_clock_seconds} != {t2.wall_clock_seconds}"
    )
    console.print(f"  [green]✓[/green] Clock freezing verified: finalize() is idempotent ({frozen_latency:.3f}s).")

    # Assert evaluation metrics match
    assert t1.tools_called_count == 2
    assert t1.memory_hits_count == 1
    assert t1.claims_total == 4
    assert t1.claims_supported == 2
    assert t1.claims_contradicted == 1
    assert t1.claims_unsupported == 1
    assert math.isclose(t1.audit_pass_rate, 0.50, abs_tol=1e-4)
    console.print("  [green]✓[/green] First-class evaluation metrics verified: 2 tools, 1 memory hit, 50% pass rate.")

    # Render summary table live
    tracer.render_summary_table(t1)

    # Save artifact and verify persistence
    artifact_path = Path("benchmarks/runs/test_q0_telemetry_sample.json")
    saved_path = tracer.save_run_artifact(artifact_path, t1)

    assert saved_path.exists(), f"Artifact was not saved to {saved_path}"
    with open(saved_path, "r", encoding="utf-8") as f:
        loaded = QuestionRunTelemetry.model_validate_json(f.read())

    assert loaded.claims_total == 4
    assert loaded.tools_called_count == 2
    assert loaded.wall_clock_seconds == frozen_latency
    console.print(f"  [green]✓[/green] Artifact serialized and verified at [bold]{artifact_path}[/bold]")


def main():
    try:
        test_pricing_math_and_null_safety()
        test_usage_extraction_with_thinking_tokens()
        test_tracer_idempotency_and_evaluation_metrics()
        console.print("\n[bold green]✓ STAGE 2 HARDENED TELEMETRY SUITE PASSED (100%)[/bold green]\n")
        sys.exit(0)
    except AssertionError as err:
        console.print(f"\n[bold red]✗ ASSERTION FAILED:[/bold red] {err}\n")
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[bold red]✗ UNEXPECTED EXCEPTION:[/bold red] {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()