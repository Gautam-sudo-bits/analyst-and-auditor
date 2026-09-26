"""
Pydantic v2 schemas for research plans, citation anchors, and research outcomes.
"""

from typing import List
from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    """Structured plan identifying known prior facts and missing information deltas."""
    memory_assessment: str = Field(description="Summary of facts already available in verified memory")
    known_facts: List[str] = Field(default_factory=list, description="List of relevant facts already known")
    missing_information_goals: List[str] = Field(default_factory=list, description="Targeted facts that must be searched")
    search_queries: List[str] = Field(
        default_factory=list,
        description="0 to 3 high-entropy search queries; empty if memory is sufficient"
    )


class CitationReference(BaseModel):
    """An atomic citation reference linking a footnote index to a verified source URL."""
    index: int = Field(ge=1, description="Footnote number (e.g. 1 for [^1])")
    url: str = Field(description="Direct source URL")
    title: str = Field(default="", description="Title of the source publication")


class AnalystResearchResult(BaseModel):
    """Final artifact emitted by the Analyst Engine for the Auditor Plane."""
    question_id: int
    question_text: str
    plan: ResearchPlan
    draft_answer: str = Field(description="Synthesized markdown with [^n] citations and bibliography")
    citations: List[CitationReference] = Field(default_factory=list)
    tools_called_count: int = Field(default=0, ge=0)
    memory_hits_used: int = Field(default=0, ge=0)
    has_epistemic_refusal: bool = Field(default=False, description="True if answer flagged [INSUFFICIENT EVIDENCE]")