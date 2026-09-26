"""
Pydantic v2 schemas for atomic claims, 5-state verification taxonomy,
forensic quote verification, and audit reports.
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNCITED = "UNCITED"
    DEAD_LINK = "DEAD_LINK"


class AtomicClaim(BaseModel):
    """An atomic, falsifiable proposition extracted from the Analyst's draft answer."""
    claim_id: str = Field(description="Unique claim identifier, e.g., 'claim_01'")
    statement: str = Field(description="Self-contained proposition with pronouns resolved to entity names")
    citation_index: Optional[int] = Field(default=None, description="Footnote index (e.g. 1 for [^1])")
    cited_url: Optional[str] = Field(default=None, description="URL corresponding to the footnote citation")
    is_cited: bool = Field(default=True, description="False if proposition was asserted without a citation")
    entity_hint: Optional[str] = Field(default=None, description="Target entity, e.g., 'Titan Company'")
    attribute_hint: Optional[str] = Field(default=None, description="Target property, e.g., 'fy24_store_additions'")
    value_hint: Optional[str] = Field(default=None, description="Extracted numerical or factual assertion")
    temporal_hint: Optional[str] = Field(default=None, description="Temporal anchor, e.g., 'FY24'")


class ClaimAuditResult(BaseModel):
    """Individual verification verdict emitted by the Blinded Verifier."""
    claim_id: str
    statement: str
    status: VerificationStatus
    cited_url: Optional[str] = None
    verbatim_quote: Optional[str] = None
    auditor_rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    quote_verified_in_source: bool = Field(
        default=False,
        description="True if verbatim_quote was verified via programmatic substring match"
    )


class AuditReport(BaseModel):
    """Comprehensive adversarial audit report for an Analyst draft response."""
    question_id: int
    total_claims: int
    claims_supported: int
    claims_contradicted: int
    claims_unsupported: int
    claims_uncited: int
    claims_dead_link: int
    pass_rate: float = Field(ge=0.0, le=1.0, description="claims_supported / total_claims")
    results: List[ClaimAuditResult] = Field(default_factory=list)
    committed_to_memory_count: int = Field(default=0, ge=0)
    auditor_summary: str