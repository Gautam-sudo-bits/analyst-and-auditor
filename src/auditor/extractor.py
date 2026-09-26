"""
Atomic Claim Extractor.
Decomposes Analyst prose into isolated, falsifiable factual propositions,
resolves co-references, maps footnotes to citation URLs, and handles partial refusals.
Includes multi-format JSON parsing and programmatic sentence-level fallback.
"""

import re
import json
from typing import List, Tuple, Dict, Any

from src.auditor.schemas import AtomicClaim
from src.analyst.schemas import CitationReference
from src.auditor.llm_client import AuditorLLMClient
from src.telemetry.schemas import ModelCallUsage


EXTRACTOR_SYSTEM_PROMPT = """You are a Principal Forensic Claim Extractor.
Your mission is to decompose an Analyst's drafted report into atomic, falsifiable factual claims.

Strict Decomposition Rules:
1. ATOMICITY:
   - Split complex or compound sentences into indivisible single facts.
   - Example: "Titan opened 110 stores in FY24[^1] and generated $5B revenue[^2]" MUST become two separate claims.
2. CO-REFERENCE RESOLUTION:
   - Resolve every pronoun ("it", "they", "the firm", "this brand") to its canonical entity name (e.g. "Titan Company Limited").
3. CITATION FOOTNOTE MAPPING (NO INHERITANCE):
   - A claim is ONLY cited ("is_cited": true) if a footnote tag like [^1] or [^2] was DIRECTLY attached to that specific sentence or clause in the text.
   - If a sentence makes an assertion with NO footnote citation attached directly to it, set "is_cited": false and "citation_index": null.
4. EXTRACT ALL FACTUAL PROPOSITIONS:
   - You MUST extract all factual assertions, counts, store numbers, revenues, and dates, EVEN IF the report also contains an [INSUFFICIENT EVIDENCE] tag for other aspects.
   - Do NOT return an empty array if factual claims exist in the text.
   - ONLY return [] if the text is EXCLUSIVELY a 1-sentence refusal with zero metrics, zero entities, and zero citations.

Output ONLY a JSON array of objects:
[
  {
    "claim_id": "claim_01",
    "statement": "Titan Company added 110 net new physical stores across India during FY24.",
    "citation_index": 1,
    "is_cited": true,
    "entity_hint": "Titan Company",
    "attribute_hint": "fy24_store_additions",
    "value_hint": "110 stores",
    "temporal_hint": "FY24"
  }
]
"""


class ClaimExtractor:
    """Decomposes narrative answers into atomic claims with citation mapping."""
    def __init__(self, llm_client: AuditorLLMClient):
        self.llm = llm_client

    async def extract_claims(
        self,
        draft_answer: str,
        citations: List[CitationReference],
    ) -> Tuple[List[AtomicClaim], ModelCallUsage]:
        """Extracts atomic claims and maps citations to their destination URLs."""
        citation_map: Dict[int, str] = {c.index: c.url for c in citations}
        citations_list_str = "\n".join(f"[^{c.index}]: {c.url}" for c in citations)

        prompt = (
            f"{EXTRACTOR_SYSTEM_PROMPT}\n\n"
            f"=== CITATION BIBLIOGRAPHY ===\n"
            f"{citations_list_str or 'No citations provided.'}\n\n"
            f"=== DRAFT ANSWER TO DECOMPOSE ===\n"
            f"{draft_answer}\n\n"
            f"Output the JSON array of atomic claims:"
        )

        raw_text, usage = await self.llm.generate(prompt, temperature=0.0)

        # 1. Clean markdown fences
        cleaned = raw_text.strip()
        if "```" in cleaned:
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE).strip()

        # 2. Multi-Format JSON Parsing
        parsed: Any = None
        try:
            parsed = json.loads(cleaned)
        except Exception:
            # Try isolating outer JSON array
            array_match = re.search(r"\[\s*\{.*\}\s*\]", raw_text, flags=re.DOTALL)
            if array_match:
                try:
                    parsed = json.loads(array_match.group(0))
                except Exception:
                    pass

            # Try isolating outer JSON object (e.g. {"claims": [...]})
            if not parsed:
                obj_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
                if obj_match:
                    try:
                        obj = json.loads(obj_match.group(0))
                        if isinstance(obj, dict):
                            for val in obj.values():
                                if isinstance(val, list):
                                    parsed = val
                                    break
                    except Exception:
                        pass

        # If parsed as a dictionary, unwrap the list
        if isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list):
                    parsed = val
                    break

        claims: List[AtomicClaim] = []
        if isinstance(parsed, list):
            for item in parsed:
                try:
                    claim = AtomicClaim.model_validate(item)
                    if claim.citation_index and claim.citation_index in citation_map:
                        claim.cited_url = citation_map[claim.citation_index]
                        claim.is_cited = True
                    else:
                        claim.is_cited = False
                        claim.cited_url = None
                    claims.append(claim)
                except Exception:
                    pass

        # 3. Programmatic Safety Net
        # If the LLM returned empty claims but the draft answer contains substantive cited sentences,
        # decompose cited sentences programmatically so claims are never dropped
        is_pure_refusal = (
            draft_answer.strip().startswith("[INSUFFICIENT EVIDENCE")
            and len(draft_answer.split()) < 35
        )

        if not claims and not is_pure_refusal:
            sentences = [
                s.strip()
                for s in re.split(r"(?<=[.!?])\s+", draft_answer)
                if len(s.strip()) > 20 and not s.strip().startswith("#")
            ]
            for i, s in enumerate(sentences, start=1):
                if "[INSUFFICIENT EVIDENCE" in s:
                    continue
                fn_match = re.search(r"\[\^?(\d+)\]", s)
                c_idx = int(fn_match.group(1)) if fn_match else None
                c_url = citation_map.get(c_idx) if c_idx else None
                clean_stmt = re.sub(r"\[\^?\d+\]", "", s).strip()

                claims.append(
                    AtomicClaim(
                        claim_id=f"claim_{i:02d}",
                        statement=clean_stmt,
                        citation_index=c_idx,
                        cited_url=c_url,
                        is_cited=bool(c_url),
                    )
                )

        return claims, usage