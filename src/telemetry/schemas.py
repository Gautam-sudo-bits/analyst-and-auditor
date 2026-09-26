"""
Strict Pydantic v2 schemas for token usage, step tracing, and run telemetry.
Includes first-class evaluation metrics for memory hits, tool reduction, and audit pass rates.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class ModelCallUsage(BaseModel):
    """Token and cost telemetry for a single LLM invocation."""
    model_name: str
    provider: str  # "analyst" | "auditor" | "system"
    prompt_tokens: int = Field(ge=0, description="Total prompt/input tokens")
    completion_tokens: int = Field(ge=0, description="Total output tokens (content + reasoning)")
    reasoning_tokens: int = Field(default=0, ge=0, description="Subset of completion tokens spent on reasoning")
    cached_tokens: int = Field(default=0, ge=0, description="Tokens read from cache")
    cost_usd: float = Field(ge=0.0, description="Nominal cost in USD")
    cost_inr: float = Field(ge=0.0, description="Nominal cost in INR")


class StepTrace(BaseModel):
    """Telemetry captured for a single execution step within a question run."""
    step_name: str
    latency_ms: float = Field(ge=0.0, description="Step duration in milliseconds")
    usage: Optional[ModelCallUsage] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QuestionRunTelemetry(BaseModel):
    """Complete serialized telemetry artifact for one benchmark question."""
    question_id: int
    question_text: str
    wall_clock_seconds: float = Field(ge=0.0, description="Total end-to-end execution time")
    exceeded_2min_ceiling: bool = Field(default=False, description="True if run exceeded 120.0s")

    # Token & Cost Metrics
    total_prompt_tokens: int = Field(ge=0)
    total_completion_tokens: int = Field(ge=0)
    total_reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    total_cost_inr: float = Field(ge=0.0)

    # First-Class Evaluation Metrics (Rubric Requirements)
    tools_called_count: int = Field(default=0, ge=0, description="Total web searches and fetches executed")
    memory_hits_count: int = Field(default=0, ge=0, description="Verified facts reused from memory without web calls")
    claims_total: int = Field(default=0, ge=0, description="Total atomic claims extracted")
    claims_supported: int = Field(default=0, ge=0)
    claims_contradicted: int = Field(default=0, ge=0)
    claims_unsupported: int = Field(default=0, ge=0)
    claims_uncited: int = Field(default=0, ge=0)
    audit_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="claims_supported / claims_total")

    step_traces: List[StepTrace] = Field(default_factory=list)
    timestamp: str