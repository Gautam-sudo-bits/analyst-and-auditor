"""
Resilient LLM invocation adapter for the Analyst Plane.
Features fast 2-attempt retry with 1.5s base delay to prevent cumulative latency inflation.
"""

import os
import random
import asyncio
from typing import Tuple, List
from dotenv import load_dotenv

from src.telemetry.schemas import ModelCallUsage
from src.telemetry.tracer import extract_usage

load_dotenv(override=True)


class AnalystLLMClient:
    """Dispatches calls to the configured Analyst model with fast retries and fallback."""
    def __init__(self):
        self.provider = os.getenv("ANALYST_PROVIDER", "gemini").strip().lower()
        self.api_key = os.getenv("ANALYST_API_KEY", "").strip()

        primary_model = os.getenv("ANALYST_MODEL", "gemini-3.5-flash").strip()
        fallback_model = os.getenv("ANALYST_FALLBACK_MODEL", "gemini-3.5-flash-lite").strip()

        self.models_to_try: List[str] = [primary_model]
        if fallback_model and fallback_model != primary_model:
            self.models_to_try.append(fallback_model)

        self.model = primary_model
        self.base_url = os.getenv("ANALYST_BASE_URL", "").strip()

    async def generate(self, prompt: str, temperature: float = 0.1, max_retries: int = 2) -> Tuple[str, ModelCallUsage]:
        """Executes prompt via chat session with fast backoff on 503/429."""
        last_exception = None

        for current_model in self.models_to_try:
            base_delay = 1.5  # Snappy base delay prevents accumulating 40+ seconds of sleep

            for attempt in range(max_retries):
                try:
                    if self.provider == "gemini":
                        from google import genai
                        from google.genai import types

                        client = genai.Client(api_key=self.api_key)
                        config = types.GenerateContentConfig(
                            temperature=temperature,
                            max_output_tokens=4096,
                        )

                        def _call():
                            chat = client.chats.create(model=current_model, config=config)
                            return chat.send_message(prompt)

                        response = await asyncio.to_thread(_call)
                        text = (response.text or "").strip()
                        usage = extract_usage(response, provider="analyst", model_name=current_model)
                        return text, usage

                    elif self.provider in ["groq", "openrouter", "github", "openai"]:
                        from openai import AsyncOpenAI

                        kwargs = {"api_key": self.api_key}
                        if self.base_url:
                            kwargs["base_url"] = self.base_url

                        client = AsyncOpenAI(**kwargs)
                        response = await client.chat.completions.create(
                            model=current_model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=temperature,
                            max_tokens=4096,
                        )
                        text = (response.choices[0].message.content or "").strip()
                        usage = extract_usage(response, provider="analyst", model_name=current_model)
                        return text, usage

                    else:
                        raise ValueError(f"Unsupported Analyst provider: {self.provider}")

                except Exception as e:
                    last_exception = e
                    err_str = str(e).lower()
                    is_transient = any(k in err_str for k in ["503", "429", "rate limit", "resource_exhausted", "unavailable", "high demand", "overloaded"])

                    if is_transient and attempt < max_retries - 1:
                        sleep_time = (base_delay * (1.8 ** attempt)) + random.uniform(0.3, 0.8)
                        await asyncio.sleep(sleep_time)
                        continue
                    elif is_transient and current_model != self.models_to_try[-1]:
                        await asyncio.sleep(1.5)
                        break
                    else:
                        raise e

        raise last_exception or RuntimeError("AnalystLLMClient failed across all models in fallback cascade.")