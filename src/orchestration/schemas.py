"""
Pydantic v2 schemas for pipeline execution results.
Captures initial draft, revision state, final answer, audit verdict, and telemetry.
"""

from typing import Optional
from pydantic import BaseModel, Field

from src.auditor.schemas import AuditReport
from src.telemetry.schemas import QuestionRunTelemetry


class PipelineResult(BaseModel):
    """Complete end-to-end execution outcome for a question run."""
    question_id: int
    question_text: str
    initial_answer: str = Field(description="Draft answer produced on first analyst pass")
    was_revised: bool = Field(default=False, description="True if self-correction loop was executed")
    final_answer: str = Field(description="Final delivered answer (initial or revised)")
    audit_report: AuditReport = Field(description="Audit verdicts for final answer")
    telemetry: QuestionRunTelemetry = Field(description="Complete token, cost, and latency metrics")
    artifact_path: Optional[str] = Field(default=None, description="Path to saved JSON artifact")
    wall_clock_seconds: float = Field(ge=0.0)
    success: bool = Field(default=True, description="False if ceiling timeout or catastrophic crash occurred")
    error_message: Optional[str] = None