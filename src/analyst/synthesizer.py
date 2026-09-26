"""
Analyst Synthesizer.
Synthesizes factual draft answers with adjacent [^n] footnote citations,
cross-checks source discrepancies, enforces epistemic refusals, and supports revision feedback.
"""

import re
from typing import List, Tuple, Optional
from src.analyst.schemas import ResearchPlan
from src.analyst.llm_client import AnalystLLMClient
from src.tools.schemas import ScrapedChunk
from src.telemetry.schemas import ModelCallUsage


SYNTHESIZER_SYSTEM_PROMPT = """You are a Principal Financial and Research Intelligence Analyst.
Your mission is to produce an authoritative, bulletproof factual synthesis based EXCLUSIVELY on verified primary disclosures.

Strict Rules of Engagement:
1. GRANULAR FOOTNOTE CITATIONS:
   - Every factual claim, number, date, financial metric, or entity action MUST be immediately followed by a footnote citation, e.g., "Titan added 110 stores in FY24[^1] while Kalyan expanded by 71 showrooms[^2]."
   - Citations must attach directly to the atomic fact they justify.
2. MEMORY CITATION PROVENANCE:
   - Whenever you include a fact retrieved from "VERIFIED PRIOR KNOWLEDGE FROM MEMORY", you MUST cite the exact URL listed under its Source field.
   - That exact memory URL MUST appear in the "## Citations" bibliography at the bottom of your response.
3. STRICT EPISTEMIC REFUSAL (ANTI-HALLUCINATION GUARD):
   - If the user asks for "unreleased", "private", "confidential", or future internal metrics (e.g. internal forecasts, secret projections), you MUST NOT GUESS or treat third-party blog rumors / speculative projections as confirmed fact.
   - You MUST explicitly output the refusal tag:
     "[INSUFFICIENT EVIDENCE: The gathered primary sources do not contain verified data for <missing aspect>.]"
   - If no verified primary data exists for the query, state plainly that the information cannot be confirmed rather than providing an estimate.
4. REVISION & SELF-CORRECTION (IF FEEDBACK PROVIDED):
   - If Auditor Contradiction Feedback is present below, you MUST resolve the contradiction, correct disputed numbers using the provided quotes, and adjust citations accordingly.
5. BIBLIOGRAPHY SECTION:
   - At the bottom of your report, provide the exact numbered bibliography list:
     ## Citations
     [^1]: https://...
     [^2]: https://...
"""


class AnalystSynthesizer:
    """Synthesizes factual answers backed by granular markdown footnotes and revision feedback."""
    def __init__(self, llm_client: AnalystLLMClient):
        self.llm = llm_client

    async def synthesize(
        self,
        question_text: str,
        plan: ResearchPlan,
        memory_context_str: str,
        web_chunks: List[ScrapedChunk],
        revision_feedback: Optional[str] = None,
    ) -> Tuple[str, ModelCallUsage]:
        """Synthesizes a draft response with granular citations and optional revision feedback."""
        # Extract explicit memory URLs to instruct model
        memory_urls = re.findall(r"\*\s+\*\*Source:\*\*\s*(https?://[^\s\*\)\"]+)", memory_context_str)
        memory_url_instruction = ""
        if memory_urls:
            memory_url_instruction = (
                "\n=== MANDATORY MEMORY URLS TO INCLUDE IN ## Citations IF REFERENCING MEMORY FACTS ===\n"
                + "\n".join(f"- {u}" for u in set(memory_urls))
                + "\n"
            )

        refusal_directive = ""
        if any(term in question_text.lower() for term in ["private", "unreleased", "confidential", "secret", "future internal"]):
            refusal_directive = (
                "\n=== CRITICAL EPISTEMIC REFUSAL DIRECTIVE ===\n"
                "The user is asking for private/unreleased internal information.\n"
                "Unless an official verified corporate disclosure directly states this exact number, you MUST NOT report speculative estimates as facts.\n"
                "You MUST include the exact tag: [INSUFFICIENT EVIDENCE: The gathered primary sources do not contain verified data for this unreleased private metric.]\n"
            )

        revision_block = ""
        if revision_feedback:
            revision_block = (
                f"\n=== AUDITOR CONTRADICTION FEEDBACK (PRIOR DRAFT REJECTED) ===\n"
                f"{revision_feedback}\n"
                f"You MUST surgically revise your report to eliminate the contradictions above.\n"
            )

        # Assemble web chunk excerpts
        chunks_text_list = []
        for i, chunk in enumerate(web_chunks, start=1):
            chunks_text_list.append(
                f"[Source Chunk #{i}] (URL: {chunk.source_url})\n{chunk.text}\n"
            )
        chunks_str = "\n".join(chunks_text_list) if chunks_text_list else "No web chunks gathered."

        prompt = (
            f"{SYNTHESIZER_SYSTEM_PROMPT}\n\n"
            f"=== USER QUESTION ===\n{question_text}\n\n"
            f"=== RESEARCH PLAN ===\n"
            f"Memory Assessment: {plan.memory_assessment}\n"
            f"Known Facts: {', '.join(plan.known_facts) if plan.known_facts else 'None'}\n"
            f"Missing Information Goals: {', '.join(plan.missing_information_goals)}\n\n"
            f"=== VERIFIED PRIOR KNOWLEDGE FROM MEMORY ===\n"
            f"{memory_context_str or 'No memory entries.'}\n"
            f"{memory_url_instruction}\n"
            f"{refusal_directive}\n"
            f"{revision_block}\n"
            f"=== GATHERED WEB SOURCES ===\n"
            f"{chunks_str}\n\n"
            f"Synthesize the report with granular [^n] citations and a ## Citations bibliography:"
        )

        draft_answer, usage = await self.llm.generate(prompt, temperature=0.1)
        return draft_answer, usage