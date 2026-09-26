"""
Pydantic v2 schemas for verified atomic facts, canonical entities, and memory interrogation.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class EntityFact(BaseModel):
    """An atomic factual claim verified against raw web evidence by the Auditor."""
    fact_id: str = Field(description="Unique fact identifier")
    attribute: str = Field(description="Normalized attribute or property name")
    value: str = Field(description="Factual value or quantified assertion")
    temporal_anchor: Optional[str] = Field(default=None, description="Time scope (e.g., 'FY24', 'Q3 2024')")
    source_url: str = Field(description="Source URL where evidence was fetched")
    evidence_quote: str = Field(description="Verbatim excerpt from the source proving the value")
    confidence: float = Field(ge=0.0, le=1.0, description="Auditor confidence score")
    auditor_verdict: str = Field(default="SUPPORTED", description="Verification verdict (must be SUPPORTED)")
    timestamp: str = Field(description="ISO 8601 UTC timestamp of verification")


class CanonicalEntity(BaseModel):
    """A business entity with alias mapping and verified facts."""
    entity_id: str = Field(description="Canonical slug (e.g., 'titan_company', 'blinkit')")
    canonical_name: str = Field(description="Official name (e.g., 'Titan Company Limited')")
    aliases: List[str] = Field(default_factory=list, description="Recognized aliases and brand names")
    facts: List[EntityFact] = Field(default_factory=list, description="Auditor-certified verified facts")
    created_at: str
    updated_at: str


class MemoryHit(BaseModel):
    """Result of an entity match during query interrogation."""
    entity_id: str
    canonical_name: str
    matched_alias: str
    facts: List[EntityFact] = Field(default_factory=list)


class MemoryInterrogationResult(BaseModel):
    """Complete bundle of retrieved memory hits formatted for the Analyst prompt."""
    hits: List[MemoryHit] = Field(default_factory=list)
    total_facts_found: int = 0
    formatted_context_block: str = Field(default="", description="Ready-to-inject Markdown context block")