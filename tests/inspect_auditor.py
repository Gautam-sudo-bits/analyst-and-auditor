"""
Auditor Raw Response Inspector.
Inspects choices[0], finish_reason, reasoning tokens, and content payload.
Run from root: python tests/inspect_auditor.py
"""

import os
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from groq import Groq

load_dotenv(override=True)
console = Console()

api_key = os.getenv("AUDITOR_API_KEY")
model = os.getenv("AUDITOR_MODEL", "openai/gpt-oss-120b")

if not api_key:
    console.print("[red]Missing AUDITOR_API_KEY in .env[/red]")
    exit(1)

client = Groq(api_key=api_key)

console.print(Panel(f"[bold cyan]Auditor Diagnostics: Model = {model}[/bold cyan]"))

# Test 1: Constrained tokens (max_tokens=5) to reproduce the empty string
console.print("\n[bold yellow]--- Test 1: max_tokens=5 (Recreating smoke test conditions) ---[/bold yellow]")
try:
    resp_5 = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the word READY"}],
        max_tokens=5,
        temperature=0.0,
    )
    choice_5 = resp_5.choices[0]
    console.print(f"[bold]finish_reason:[/bold] {choice_5.finish_reason}")
    console.print(f"[bold]content:[/bold] repr={repr(choice_5.message.content)}")
    if hasattr(choice_5.message, "reasoning"):
        console.print(f"[bold]reasoning:[/bold] repr={repr(choice_5.message.reasoning)}")
    console.print(f"[bold]usage:[/bold] {resp_5.usage}")
except Exception as e:
    console.print(f"[red]Error:[/red] {e}")

# Test 2: Sufficient token budget (max_tokens=150)
console.print("\n[bold green]--- Test 2: max_tokens=150 (Sufficient reasoning + answer budget) ---[/bold green]")
try:
    resp_150 = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the word READY"}],
        max_tokens=150,
        temperature=0.0,
    )
    choice_150 = resp_150.choices[0]
    console.print(f"[bold]finish_reason:[/bold] {choice_150.finish_reason}")
    console.print(f"[bold]content:[/bold] repr={repr(choice_150.message.content)}")
    if hasattr(choice_150.message, "reasoning"):
        console.print(f"[bold]reasoning:[/bold] repr={repr(choice_150.message.reasoning)}")
    console.print(f"[bold]usage:[/bold] {resp_150.usage}")
    console.print("\n[bold]Raw Choice Object Inspection:[/bold]")
    console.print(Pretty(choice_150.model_dump()))
except Exception as e:
    console.print(f"[red]Error:[/red] {e}")