"""
Deterministic pricing engine and currency conversion for LLM token usage.
Supports Gemini Flash family, Groq reasoning models (gpt-oss-120b), Llama family, and safe defaults.
"""

import os
from typing import Tuple, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

DEFAULT_USD_TO_INR: float = float(os.getenv("USD_TO_INR_RATE", "85.0"))

# Price per token rate card (rates defined per 1,000,000 tokens)
RATE_CARD: Dict[str, Dict[str, float]] = {
    # Analyst Primary: Gemini Flash family
    "gemini": {
        "prompt": 0.50 / 1_000_000,          # $0.50 per 1M tokens
        "cached_prompt": 0.05 / 1_000_000,   # $0.05 per 1M tokens (90% cache discount)
        "completion": 3.00 / 1_000_000,      # $3.00 per 1M tokens
    },
    # Auditor Primary: OpenAI open-weight 120B reasoning model on Groq
    "openai/gpt-oss-120b": {
        "prompt": 0.15 / 1_000_000,          # $0.15 per 1M tokens
        "cached_prompt": 0.075 / 1_000_000,  # $0.075 per 1M tokens
        "completion": 0.60 / 1_000_000,      # $0.60 per 1M tokens (includes reasoning tokens)
    },
    # Auditor Alternative: Llama 3.3 70B on Groq
    "llama-3.3-70b-versatile": {
        "prompt": 0.59 / 1_000_000,
        "cached_prompt": 0.295 / 1_000_000,
        "completion": 0.79 / 1_000_000,
    },
    # Auditor Alternative: Llama 3.1 8B on Groq
    "llama-3.1-8b-instant": {
        "prompt": 0.05 / 1_000_000,
        "cached_prompt": 0.025 / 1_000_000,
        "completion": 0.08 / 1_000_000,
    },
    # Safe conservative fallback
    "default": {
        "prompt": 0.50 / 1_000_000,
        "cached_prompt": 0.10 / 1_000_000,
        "completion": 1.50 / 1_000_000,
    },
}

def get_model_rates(model_name: Optional[str]) -> Dict[str, float]:
    """Resolves rates defensively; routes missing or invalid model identifiers to fallback."""
    if not model_name or not isinstance(model_name, str):
        return RATE_CARD["default"]

    name_clean = model_name.strip().lower()

    if "gpt-oss-120b" in name_clean:
        return RATE_CARD["openai/gpt-oss-120b"]
    if "llama-3.3-70b" in name_clean:
        return RATE_CARD["llama-3.3-70b-versatile"]
    if "llama-3.1-8b" in name_clean:
        return RATE_CARD["llama-3.1-8b-instant"]
    if "gemini" in name_clean:
        return RATE_CARD["gemini"]

    return RATE_CARD["default"]


def calculate_cost(
    model_name: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
) -> Tuple[float, float]:
    """
    Computes exact (cost_usd, cost_inr) based on model rates.
    
    NOTE ON REASONING TOKENS:
    On OpenAI/Groq APIs, completion_tokens already includes reasoning tokens.
    Reasoning tokens are billed at standard completion rate and must NOT be added
    again to prevent double-billing.
    """
    rates = get_model_rates(model_name)

    # Segregate uncached vs cached prompt tokens
    cached_count = min(cached_tokens, prompt_tokens)
    regular_prompt_count = max(0, prompt_tokens - cached_count)

    prompt_cost = (regular_prompt_count * rates["prompt"]) + (cached_count * rates["cached_prompt"])
    completion_cost = completion_tokens * rates["completion"]

    cost_usd = prompt_cost + completion_cost
    cost_inr = cost_usd * usd_to_inr

    return cost_usd, cost_inr