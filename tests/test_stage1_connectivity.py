"""
Stage 1 Connectivity Smoke Test (Production Hardened).
- Requires non-empty response content to PASS
- Handles reasoning model token requirements (max_tokens=100)
- Extracts reasoning tokens if available
- Zero AFC warnings via chat.send_message
Runnable strictly from project root: python tests/test_stage1_connectivity.py
"""

import os
import sys
import time
from typing import Tuple, Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

load_dotenv(override=True)
console = Console()


def test_env_keys() -> Tuple[bool, str]:
    required_keys = [
        "ANALYST_PROVIDER",
        "ANALYST_API_KEY",
        "ANALYST_MODEL",
        "AUDITOR_PROVIDER",
        "AUDITOR_API_KEY",
        "AUDITOR_MODEL",
    ]
    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        return False, f"Missing required .env keys: {', '.join(missing)}"
    return True, "All required environment variables are set."


def test_llm_call(
    provider: str,
    api_key: str,
    model: str,
    base_url: Optional[str] = None
) -> Tuple[bool, float, str]:
    """Test LLM endpoints with validation for non-empty text generation."""
    start_time = time.perf_counter()
    provider_clean = provider.strip().lower()

    try:
        # 1. Google Gemini Native SDK
        if provider_clean == "gemini":
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            chat = client.chats.create(model=model)
            response = chat.send_message(
                "Reply with 'OK'.",
                config=types.GenerateContentConfig(max_output_tokens=50, temperature=0.0)
            )
            elapsed = (time.perf_counter() - start_time) * 1000
            text = (response.text or "").strip()
            
            if not text:
                return False, elapsed, "Model returned empty content."
            return True, elapsed, f"Response: '{text}'"

        # 2. Native Groq SDK
        elif provider_clean == "groq":
            from groq import Groq

            client = Groq(api_key=api_key)
            try:
                completion = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with 'READY'."}],
                    max_tokens=100,  # Accommodates reasoning models like gpt-oss-120b
                    temperature=0.0,
                )
                elapsed = (time.perf_counter() - start_time) * 1000
                choice = completion.choices[0]
                text = (choice.message.content or "").strip()
                
                # Check for empty content (reasoning models may swallow tokens)
                if not text:
                    finish = choice.finish_reason
                    return False, elapsed, f"Empty content! finish_reason='{finish}' (insufficient max_tokens?)"

                return True, elapsed, f"Response: '{text}'"
            except Exception as groq_err:
                elapsed = (time.perf_counter() - start_time) * 1000
                err_str = str(groq_err)
                if "404" in err_str or "does not exist" in err_str:
                    try:
                        available_models = [m.id for m in client.models.list().data if "whisper" not in m.id]
                        samples = ", ".join(available_models[:4])
                        return False, elapsed, f"Model '{model}' not found. Available: {samples}"
                    except Exception:
                        pass
                return False, elapsed, f"Groq Error: {err_str[:120]}"

        # 3. OpenAI / Generic OpenAI-Compatible
        elif provider_clean in ["openrouter", "github", "openai"]:
            from openai import OpenAI

            kwargs = {"api_key": api_key}
            if base_url and base_url.strip():
                kwargs["base_url"] = base_url.strip()

            client = OpenAI(**kwargs)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with 'OK'."}],
                max_tokens=50,
                temperature=0.0,
            )
            elapsed = (time.perf_counter() - start_time) * 1000
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return False, elapsed, "Model returned empty content."
            return True, elapsed, f"Response: '{text}'"

        else:
            return False, 0.0, f"Unsupported provider: '{provider}'"

    except Exception as exc:
        elapsed = (time.perf_counter() - start_time) * 1000
        return False, elapsed, f"Error: {str(exc)[:120]}"


def test_search() -> Tuple[bool, float, str]:
    """Test web search connectivity using native ddgs."""
    start_time = time.perf_counter()
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text("Python programming", max_results=1))
        elapsed = (time.perf_counter() - start_time) * 1000

        if results and len(results) > 0:
            first_title = results[0].get("title", "No title")[:35]
            return True, elapsed, f"Found: '{first_title}...'"
        return False, elapsed, "Query returned 0 results."
    except Exception as exc:
        elapsed = (time.perf_counter() - start_time) * 1000
        return False, elapsed, f"Search Error: {str(exc)[:120]}"


def main():
    console.print("\n", Panel.fit("[bold blue]Stage 1: System Scaffolding & Connectivity Smoke Test[/bold blue]"))

    table = Table(title="Connectivity Verification Matrix", show_header=True, header_style="bold magenta")
    table.add_column("Subsystem", style="cyan", width=22)
    table.add_column("Target / Provider", style="yellow", width=32)
    table.add_column("Status", width=10, justify="center")
    table.add_column("Latency (ms)", width=14, justify="right")
    table.add_column("Details", style="white")

    all_passed = True

    # 1. Environment Variables Check
    env_ok, env_msg = test_env_keys()
    if env_ok:
        table.add_row(".env Configuration", "Local Environment", "[green]PASS[/green]", "-", env_msg)
    else:
        table.add_row(".env Configuration", "Local Environment", "[red]FAIL[/red]", "-", env_msg)
        console.print(table)
        console.print("[bold red]Please configure your .env file with valid keys before proceeding.[/bold red]\n")
        sys.exit(1)

    # 2. Analyst LLM Connectivity
    analyst_prov = os.getenv("ANALYST_PROVIDER", "")
    analyst_model = os.getenv("ANALYST_MODEL", "")
    analyst_key = os.getenv("ANALYST_API_KEY", "")
    analyst_url = os.getenv("ANALYST_BASE_URL", "")

    a_ok, a_lat, a_msg = test_llm_call(analyst_prov, analyst_key, analyst_model, analyst_url)
    status_a = "[green]PASS[/green]" if a_ok else "[red]FAIL[/red]"
    if not a_ok:
        all_passed = False
    table.add_row(
        "Analyst LLM",
        f"{analyst_prov} ({analyst_model})",
        status_a,
        f"{a_lat:.1f}" if a_lat > 0 else "-",
        a_msg,
    )

    # 3. Auditor LLM Connectivity
    auditor_prov = os.getenv("AUDITOR_PROVIDER", "")
    auditor_model = os.getenv("AUDITOR_MODEL", "")
    auditor_key = os.getenv("AUDITOR_API_KEY", "")
    auditor_url = os.getenv("AUDITOR_BASE_URL", "")

    aud_ok, aud_lat, aud_msg = test_llm_call(auditor_prov, auditor_key, auditor_model, auditor_url)
    status_aud = "[green]PASS[/green]" if aud_ok else "[red]FAIL[/red]"
    if not aud_ok:
        all_passed = False
    table.add_row(
        "Auditor LLM",
        f"{auditor_prov} ({auditor_model})",
        status_aud,
        f"{aud_lat:.1f}" if aud_lat > 0 else "-",
        aud_msg,
    )

    # 4. Search Tool Dispatch
    s_ok, s_lat, s_msg = test_search()
    status_s = "[green]PASS[/green]" if s_ok else "[red]FAIL[/red]"
    if not s_ok:
        all_passed = False
    table.add_row("DuckDuckGo Tool", "Live Web Search API (ddgs)", status_s, f"{s_lat:.1f}", s_msg)

    console.print(table)

    if all_passed:
        console.print(
            "\n[bold green]✓ ALL CONNECTIVITY CHECKS PASSED. SYSTEM READY FOR STAGE 2.[/bold green]\n"
        )
        sys.exit(0)
    else:
        console.print(
            "\n[bold red]✗ CONNECTIVITY CHECKS FAILED. RESOLVE ERRORS BEFORE PROCEEDING.[/bold red]\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()