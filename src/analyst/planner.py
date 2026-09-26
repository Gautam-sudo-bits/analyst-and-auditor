"""
Analyst Delta Planner.
Interrogates verified memory, identifies factual deficits, and formulates 0 to 3 search queries
with robust regex JSON extraction.
"""

import re
from typing import Tuple

from src.analyst.schemas import ResearchPlan
from src.analyst.llm_client import AnalystLLMClient
from src.telemetry.schemas import ModelCallUsage


PLANNER_SYSTEM_PROMPT = """You are a Senior Strategic Research Planner.
Your role is to formulate a lean, targeted search plan to answer the user question.

You will be given:
1. The USER QUESTION.
2. VERIFIED PRIOR KNOWLEDGE FROM MEMORY (facts previously verified by the Auditor).

Strict Operational Directives:
1. Examine what is already known from memory.
2. If memory COMPLETELY answers the user question, set "search_queries": [] (do NOT dispatch redundant web searches).
3. If memory is partially helpful, list what is known and formulate queries ONLY for the missing information goals.
4. Queries must be concise, high-entropy keyword searches (e.g., "Kalyan Jewellers FY24 net store additions count"). Never write conversational questions.
5. Maximum 3 search queries.

Output ONLY a JSON object adhering to this exact schema:
{
  "memory_assessment": "Analysis of what memory provides vs what is missing",
  "known_facts": ["fact 1", "fact 2"],
  "missing_information_goals": ["goal 1", "goal 2"],
  "search_queries": ["query 1", "query 2"]
}
"""


class AnalystPlanner:
    """Generates a delta research plan before any search execution."""
    def __init__(self, llm_client: AnalystLLMClient):
        self.llm = llm_client

    async def create_plan(
        self,
        question_text: str,
        memory_context_str: str,
    ) -> Tuple[ResearchPlan, ModelCallUsage]:
        """Formulates a minimal search plan based on existing memory."""
        prompt = (
            f"{PLANNER_SYSTEM_PROMPT}\n\n"
            f"=== VERIFIED PRIOR KNOWLEDGE FROM MEMORY ===\n"
            f"{memory_context_str or 'No prior verified knowledge in memory for this query.'}\n\n"
            f"=== USER QUESTION ===\n"
            f"{question_text}\n\n"
            f"Generate your JSON research plan now:"
        )

        raw_text, usage = await self.llm.generate(prompt, temperature=0.0)

        # Robust outer JSON object extraction
        json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if json_match:
            cleaned_json = json_match.group(0).strip()
        else:
            cleaned_json = raw_text.strip()

        try:
            plan = ResearchPlan.model_validate_json(cleaned_json)
        except Exception:
            plan = ResearchPlan(
                memory_assessment="Fallback parsing plan",
                known_facts=[],
                missing_information_goals=[question_text],
                search_queries=[question_text[:80]],
            )

        plan.search_queries = plan.search_queries[:3]
        return plan, usage