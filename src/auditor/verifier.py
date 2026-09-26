"""
Blinded NLI Verifier.
Conducts adversarial Natural Language Inference against focused 500-600 word source text passages,
assigns 5-state verdicts, and programmatically validates verbatim quotes against the full document.
"""

import re
import json
from typing import Tuple

from src.auditor.schemas import AtomicClaim, ClaimAuditResult, VerificationStatus
from src.auditor.llm_client import AuditorLLMClient
from src.telemetry.schemas import ModelCallUsage


VERIFIER_SYSTEM_PROMPT = """You are an Adversarial Natural Language Inference (NLI) Auditor.
Your job is to independently verify a specific factual claim against raw text extracted from a cited web page.

VERIFICATION RULES (NO CHARITY BIAS):
1. SUPPORTED:
   - The source text directly entails the claim. Numbers, entities, dates, and actions match.
   - You MUST extract the exact sentence from the source as verbatim_quote.
2. CONTRADICTED:
   - The source text directly clashes with or contradicts the claim (e.g. source says 95 stores, claim asserts 110 stores).
   - You MUST extract the contradictory sentence as verbatim_quote.
3. UNSUPPORTED:
   - The source text mentions the entity or general topic, but DOES NOT confirm the asserted metric, action, or fact.
   - Set verbatim_quote: null.

OUTPUT FORMAT:
Output ONLY a JSON object:
{
  "status": "SUPPORTED" | "CONTRADICTED" | "UNSUPPORTED",
  "verbatim_quote": "Exact verbatim excerpt from source text, or null",
  "confidence": 0.95,
  "auditor_rationale": "Clear 1-sentence forensic explanation"
}
"""


def normalize_text_for_matching(text: str) -> str:
    """Normalizes whitespace, smart quotes, dashes, and casing for robust substring matching."""
    if not text:
        return ""
    cleaned = text.strip().strip("\"'“”«»`")
    cleaned = cleaned.replace("\xa0", " ").replace("—", "-").replace("–", "-")
    cleaned = cleaned.replace("“", "\"").replace("”", "\"").replace("’", "'")
    return re.sub(r"\s+", " ", cleaned.lower()).strip()


def verify_quote_in_source(quote: str, source_text: str) -> bool:
    """Confirms verbatim quote exists in the source, resilient to quotes, dashes, and unicode."""
    if not quote or len(quote.strip()) < 10:
        return False
    norm_quote = normalize_text_for_matching(quote)
    norm_source = normalize_text_for_matching(source_text)

    if norm_quote in norm_source:
        return True

    # Secondary check: quote with trailing punctuation stripped
    stripped_punct = norm_quote.rstrip(".,;:!?")
    if len(stripped_punct) >= 15 and stripped_punct in norm_source:
        return True

    return False


class BlindedVerifier:
    """Evaluates atomic claims against high-density source text with programmatic quote verification."""
    def __init__(self, llm_client: AuditorLLMClient):
        self.llm = llm_client

    async def verify(
        self,
        claim: AtomicClaim,
        source_text: str,
    ) -> Tuple[ClaimAuditResult, ModelCallUsage]:
        """Verifies a claim against a high-density 500-600 word source passage."""
        words = source_text.split()
        target_window_size = 550  # 500-600 word budget preserves context while keeping tokens ~750

        if len(words) > target_window_size:
            # Extract key search tokens from claim
            claim_terms = [
                t.lower()
                for t in (claim.statement.split() + [claim.entity_hint or "", claim.value_hint or ""])
                if len(t) > 3 and t.isalnum()
            ]

            best_idx = 0
            best_hits = 0
            stride = 50

            # Find the window with highest keyword density
            for i in range(0, max(1, len(words) - target_window_size), stride):
                window_slice = " ".join(words[i:i + target_window_size]).lower()
                hits = sum(1 for term in claim_terms if term in window_slice)
                if hits > best_hits:
                    best_hits = hits
                    best_idx = i

            trimmed_source = " ".join(words[best_idx:best_idx + target_window_size])
        else:
            trimmed_source = source_text

        prompt = (
            f"{VERIFIER_SYSTEM_PROMPT}\n\n"
            f"=== CLAIM TO VERIFY ===\n"
            f"Statement: \"{claim.statement}\"\n"
            f"Entity: {claim.entity_hint or 'N/A'}\n"
            f"Asserted Value: {claim.value_hint or 'N/A'}\n"
            f"Temporal Scope: {claim.temporal_hint or 'N/A'}\n\n"
            f"=== RAW CITED SOURCE TEXT (FOCUSED PASSAGE) ===\n"
            f"{trimmed_source or 'NO SOURCE TEXT AVAILABLE.'}\n\n"
            f"Output your JSON verdict now:"
        )

        raw_text, usage = await self.llm.generate(prompt, temperature=0.0)

        # Robust outer JSON extraction
        json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if json_match:
            cleaned_json = json_match.group(0).strip()
        else:
            cleaned_json = raw_text.strip()

        try:
            data = json.loads(cleaned_json)
            status_str = data.get("status", "UNSUPPORTED").upper()
            status = VerificationStatus(status_str)
            quote = data.get("verbatim_quote")
            confidence = float(data.get("confidence", 0.8))
            rationale = str(data.get("auditor_rationale", "No rationale provided"))
        except Exception:
            status = VerificationStatus.UNSUPPORTED
            quote = None
            confidence = 0.5
            rationale = "Failed to parse auditor LLM output format"

        # Verify quote physically exists in the COMPLETE source text (not just trimmed window)
        quote_verified = False
        if quote and verify_quote_in_source(quote, source_text):
            quote_verified = True
        elif status == VerificationStatus.SUPPORTED:
            # Quote was fabricated or paraphrased -> Downgrade to UNSUPPORTED
            status = VerificationStatus.UNSUPPORTED
            quote_verified = False
            rationale += " [Downgraded to UNSUPPORTED: Verbatim quote not found in source text]"

        return (
            ClaimAuditResult(
                claim_id=claim.claim_id,
                statement=claim.statement,
                status=status,
                cited_url=claim.cited_url,
                verbatim_quote=quote if quote_verified else None,
                auditor_rationale=rationale,
                confidence=confidence,
                quote_verified_in_source=quote_verified,
            ),
            usage,
        )