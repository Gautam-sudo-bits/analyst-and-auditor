"""
Telemetry Tracer with idempotent clock freezing, multi-vendor token parsing,
wall-clock ceiling supervisor, audit metric tracking, Rich visualization, and JSON artifact export.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Dict, Any, List, Union

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.telemetry.pricing import calculate_cost, DEFAULT_USD_TO_INR
from src.telemetry.schemas import ModelCallUsage, StepTrace, QuestionRunTelemetry

console = Console()


class StepContext:
    """Helper passed to track_step context manager to allow attaching usage and metadata."""
    def __init__(self, step_name: str, metadata: Optional[Dict[str, Any]] = None):
        self.step_name = step_name
        self.metadata = metadata or {}
        self.usage: Optional[ModelCallUsage] = None

    def set_usage(self, usage: ModelCallUsage) -> None:
        self.usage = usage

    def add_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value


def extract_usage(
    response: Any,
    provider: str,
    model_name: str,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
) -> ModelCallUsage:
    """
    Vendor-agnostic adapter: safely extracts token counts from Google GenAI
    and Groq / OpenAI response objects.
    """
    if isinstance(response, ModelCallUsage):
        return response

    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    cached_tokens = 0

    # 1. Google GenAI SDK (usage_metadata)
    if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
        meta = response.usage_metadata
        prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
        completion_tokens = getattr(meta, "candidates_token_count", 0) or 0
        cached_tokens = getattr(meta, "cached_content_token_count", 0) or 0
        
        # Safe inspection for Gemini thinking/reasoning tokens
        reasoning_tokens = (
            getattr(meta, "thought_token_count", 0)
            or getattr(getattr(meta, "candidates_tokens_details", None), "thought_tokens", 0)
            or 0
        )

    # 2. Groq / OpenAI SDK (response.usage)
    elif hasattr(response, "usage") and response.usage is not None:
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Check prompt caching details
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

        # Check reasoning tokens without double-counting
        if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
            reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

    # 3. Dict fallback (for mocks or deserialized payloads)
    elif isinstance(response, dict):
        prompt_tokens = response.get("prompt_tokens", 0)
        completion_tokens = response.get("completion_tokens", 0)
        reasoning_tokens = response.get("reasoning_tokens", 0)
        cached_tokens = response.get("cached_tokens", 0)

    cost_usd, cost_inr = calculate_cost(
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        usd_to_inr=usd_to_inr,
    )

    return ModelCallUsage(
        model_name=model_name,
        provider=provider,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        cost_inr=cost_inr,
    )


class TelemetryTracer:
    """
    Manages live step tracking, time supervision, first-class metric aggregation,
    and token accounting for a single question.
    """
    def __init__(
        self,
        question_id: int,
        question_text: str,
        usd_to_inr: float = DEFAULT_USD_TO_INR,
    ):
        self.question_id = question_id
        self.question_text = question_text
        self.usd_to_inr = usd_to_inr
        self.start_wall_time = time.perf_counter()
        self.end_wall_time: Optional[float] = None
        self.step_traces: List[StepTrace] = []
        self._finalized_telemetry: Optional[QuestionRunTelemetry] = None

        # First-Class Evaluation Metric State
        self.tools_called_count: int = 0
        self.memory_hits_count: int = 0
        self.audit_counts: Dict[str, int] = {
            "SUPPORTED": 0,
            "CONTRADICTED": 0,
            "UNSUPPORTED": 0,
            "UNCITED": 0,
            "DEAD_LINK": 0,
        }

    def record_tool_call(self, count: int = 1) -> None:
        """Records web search or HTTP fetch invocations."""
        self.tools_called_count += count

    def record_memory_hit(self, count: int = 1) -> None:
        """Records instances where pre-verified entity memory answered claims without web calls."""
        self.memory_hits_count += count

    def record_audit_verdict(self, status: str) -> None:
        """Records an atomic claim verification outcome."""
        status_upper = status.upper().strip()
        if status_upper in self.audit_counts:
            self.audit_counts[status_upper] += 1
        else:
            self.audit_counts["UNSUPPORTED"] += 1

    @contextmanager
    def track_step(self, step_name: str, metadata: Optional[Dict[str, Any]] = None):
        """Context manager to measure step latency and record model usage."""
        ctx = StepContext(step_name=step_name, metadata=metadata)
        step_start = time.perf_counter()
        try:
            yield ctx
        finally:
            step_latency_ms = (time.perf_counter() - step_start) * 1000.0
            self.step_traces.append(
                StepTrace(
                    step_name=ctx.step_name,
                    latency_ms=round(step_latency_ms, 2),
                    usage=ctx.usage,
                    metadata=ctx.metadata,
                )
            )

    def finalize(self) -> QuestionRunTelemetry:
        """
        Idempotent finalization: freezes the clock on first call,
        aggregates token economics and audit pass metrics.
        """
        if self._finalized_telemetry is not None:
            return self._finalized_telemetry

        if self.end_wall_time is None:
            self.end_wall_time = time.perf_counter()

        total_seconds = self.end_wall_time - self.start_wall_time
        exceeded_ceiling = total_seconds > 120.0

        total_prompt = 0
        total_completion = 0
        total_reasoning = 0
        total_cost_usd = 0.0
        total_cost_inr = 0.0

        for trace in self.step_traces:
            if trace.usage:
                total_prompt += trace.usage.prompt_tokens
                total_completion += trace.usage.completion_tokens
                total_reasoning += trace.usage.reasoning_tokens
                total_cost_usd += trace.usage.cost_usd
                total_cost_inr += trace.usage.cost_inr

        # Calculate claim metrics
        claims_total = sum(self.audit_counts.values())
        claims_supported = self.audit_counts.get("SUPPORTED", 0)
        claims_contradicted = self.audit_counts.get("CONTRADICTED", 0)
        claims_unsupported = self.audit_counts.get("UNSUPPORTED", 0)
        claims_uncited = self.audit_counts.get("UNCITED", 0)
        audit_pass_rate = round(claims_supported / claims_total, 4) if claims_total > 0 else 0.0

        self._finalized_telemetry = QuestionRunTelemetry(
            question_id=self.question_id,
            question_text=self.question_text,
            wall_clock_seconds=round(total_seconds, 3),
            exceeded_2min_ceiling=exceeded_ceiling,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_reasoning_tokens=total_reasoning,
            total_tokens=total_prompt + total_completion,
            total_cost_usd=round(total_cost_usd, 6),
            total_cost_inr=round(total_cost_inr, 4),
            tools_called_count=self.tools_called_count,
            memory_hits_count=self.memory_hits_count,
            claims_total=claims_total,
            claims_supported=claims_supported,
            claims_contradicted=claims_contradicted,
            claims_unsupported=claims_unsupported,
            claims_uncited=claims_uncited,
            audit_pass_rate=audit_pass_rate,
            step_traces=self.step_traces,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        return self._finalized_telemetry

    def render_summary_table(self, telemetry: Optional[QuestionRunTelemetry] = None) -> Table:
        """Renders live terminal execution table with latency, token economics, and audit metrics."""
        t = telemetry or self._finalized_telemetry or self.finalize()

        # Latency styling
        if t.wall_clock_seconds < 60.0:
            latency_style = "[bold green]"
        elif t.wall_clock_seconds <= 120.0:
            latency_style = "[bold yellow]"
        else:
            latency_style = "[bold red blink]"

        table = Table(
            title=f"Telemetry Summary: Question #{t.question_id}",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Step / Component", style="white", width=34)
        table.add_column("Latency (ms)", justify="right", width=14)
        table.add_column("Prompt Tok", justify="right", width=12)
        table.add_column("Output Tok", justify="right", width=12)
        table.add_column("Reasoning", justify="right", width=11)
        table.add_column("Cost (INR)", justify="right", width=14)

        for step in t.step_traces:
            if step.usage:
                table.add_row(
                    f"{step.step_name} ({step.usage.provider})",
                    f"{step.latency_ms:.1f}",
                    f"{step.usage.prompt_tokens:,}",
                    f"{step.usage.completion_tokens:,}",
                    f"{step.usage.reasoning_tokens:,}" if step.usage.reasoning_tokens else "-",
                    f"₹{step.usage.cost_inr:.4f}",
                )
            else:
                table.add_row(
                    step.step_name,
                    f"{step.latency_ms:.1f}",
                    "-",
                    "-",
                    "-",
                    "-",
                )

        table.add_section()
        table.add_row(
            "[bold white]TOTAL RUN[/bold white]",
            f"{latency_style}{t.wall_clock_seconds:.2f}s[/]",
            f"[bold]{t.total_prompt_tokens:,}[/bold]",
            f"[bold]{t.total_completion_tokens:,}[/bold]",
            f"[bold]{t.total_reasoning_tokens:,}[/bold]",
            f"[bold green]₹{t.total_cost_inr:.4f}[/bold green] (${t.total_cost_usd:.5f})",
        )

        console.print("\n", table)

        # Print Evaluation Summary Banner
        eval_panel = Panel(
            f"[bold]Tools Dispatched:[/bold] {t.tools_called_count}  |  "
            f"[bold]Memory Hits:[/bold] {t.memory_hits_count}  |  "
            f"[bold]Claims Evaluated:[/bold] {t.claims_total}  |  "
            f"[bold green]Supported:[/bold green] {t.claims_supported}  |  "
            f"[bold red]Contradicted:[/bold red] {t.claims_contradicted}  |  "
            f"[bold yellow]Unsupported:[/bold yellow] {t.claims_unsupported}  |  "
            f"[bold cyan]Pass Rate:[/bold cyan] {t.audit_pass_rate * 100:.1f}%",
            title="[bold blue]Evaluation Metrics[/bold blue]",
            expand=False,
        )
        console.print(eval_panel)

        if t.exceeded_2min_ceiling:
            console.print(
                Panel.fit(
                    f"[bold red]CRITICAL WARNING: Wall-clock time ({t.wall_clock_seconds:.2f}s) "
                    "exceeded the 120-second evaluation ceiling![/bold red]"
                )
            )

        return table

    def save_run_artifact(
        self,
        filepath: Union[str, Path],
        telemetry: Optional[QuestionRunTelemetry] = None,
    ) -> Path:
        """Serializes telemetry data into a JSON artifact."""
        t = telemetry or self._finalized_telemetry or self.finalize()
        out_path = Path(filepath)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(t.model_dump_json(indent=2))

        return out_path