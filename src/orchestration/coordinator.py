"""
The Research & Adversarial Audit Pipeline Orchestrator.
Coordinating: Telemetry -> Memory Interrogation -> Delta Planning -> Web Tools ->
Analyst Synthesis -> Adversarial Audit -> 1-Turn Self-Correction -> Ceiling Supervisor.
Features a tightened 40s revision gate to prevent wall-clock ceiling timeouts.
"""

import time
import asyncio
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table

from src.orchestration.schemas import PipelineResult
from src.analyst.engine import AnalystEngine
from src.analyst.schemas import CitationReference
from src.auditor.engine import AuditorEngine
from src.auditor.schemas import AuditReport, VerificationStatus
from src.memory.store import EntityMemoryStore
from src.telemetry.tracer import TelemetryTracer

console = Console()


class ResearchAuditorPipeline:
    """End-to-end multi-agent research and adversarial auditing coordinator."""
    def __init__(
        self,
        memory_store: Optional[EntityMemoryStore] = None,
        analyst_engine: Optional[AnalystEngine] = None,
        auditor_engine: Optional[AuditorEngine] = None,
    ):
        self.memory_store = memory_store or EntityMemoryStore()
        self.analyst_engine = analyst_engine or AnalystEngine(memory_store=self.memory_store)
        self.auditor_engine = auditor_engine or AuditorEngine(memory_store=self.memory_store)

    async def run_question(
        self,
        question_id: int,
        question_text: str,
        enable_revision: bool = True,
        timeout_seconds: float = 118.0,  # Strict ceiling under 120s
    ) -> PipelineResult:
        """
        Executes end-to-end question research with wall-clock supervisor and self-correction loop.
        """
        tracer = TelemetryTracer(question_id=question_id, question_text=question_text)
        start_time = time.perf_counter()

        try:
            return await asyncio.wait_for(
                self._execute_pipeline(
                    question_id=question_id,
                    question_text=question_text,
                    enable_revision=enable_revision,
                    tracer=tracer,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            wall_clock = time.perf_counter() - start_time
            telemetry = tracer.finalize()
            telemetry.exceeded_2min_ceiling = True
            artifact_path = tracer.save_run_artifact(
                f"benchmarks/runs/run_q{question_id}.json", telemetry
            )

            empty_audit = AuditReport(
                question_id=question_id,
                total_claims=0,
                claims_supported=0,
                claims_contradicted=0,
                claims_unsupported=0,
                claims_uncited=0,
                claims_dead_link=0,
                pass_rate=0.0,
                results=[],
                committed_to_memory_count=0,
                auditor_summary="Execution aborted: Exceeded 118s wall-clock ceiling supervisor",
            )

            return PipelineResult(
                question_id=question_id,
                question_text=question_text,
                initial_answer="[EXECUTION ABORTED: Wall-clock ceiling exceeded]",
                was_revised=False,
                final_answer="[EXECUTION ABORTED: Wall-clock ceiling exceeded]",
                audit_report=empty_audit,
                telemetry=telemetry,
                artifact_path=str(artifact_path),
                wall_clock_seconds=round(wall_clock, 2),
                success=False,
                error_message=f"Timeout: Wall-clock exceeded {timeout_seconds}s ceiling",
            )

    async def _execute_pipeline(
        self,
        question_id: int,
        question_text: str,
        enable_revision: bool,
        tracer: TelemetryTracer,
    ) -> PipelineResult:
        """Core DAG execution."""
        # 1. Analyst Research Phase
        analyst_res = await self.analyst_engine.research(
            question_id=question_id,
            question_text=question_text,
            tracer=tracer,
        )

        # 2. Adversarial Audit Phase (Initial)
        audit_report = await self.auditor_engine.audit(
            question_id=question_id,
            draft_answer=analyst_res.draft_answer,
            citations=analyst_res.citations,
            tracer=tracer,
        )

        final_answer = analyst_res.draft_answer
        final_audit = audit_report
        was_revised = False

        # 3. 1-Turn Auditor -> Analyst Self-Correction Loop
        elapsed = time.perf_counter() - tracer.start_wall_time
        # TIGHTENED GATE: Only trigger revision if under 40s to guarantee < 118s completion
        needs_revision = (
            enable_revision
            and audit_report.claims_contradicted > 0
            and elapsed < 40.0
        )

        if needs_revision:
            feedback_lines = [
                "The Adversarial Auditor detected the following contradiction(s) in your report:"
            ]
            for r in audit_report.results:
                if r.status == VerificationStatus.CONTRADICTED:
                    feedback_lines.append(
                        f'- Contradicted Claim: "{r.statement}"\n'
                        f'  Source Verbatim Quote: "{r.verbatim_quote}"\n'
                        f'  Forensic Rationale: "{r.auditor_rationale}"'
                    )
            feedback_lines.append(
                "\nUpdate the contested metrics according to the primary quote and re-issue footnotes."
            )
            feedback_str = "\n".join(feedback_lines)

            # Revision step with preserved web chunks
            with tracer.track_step("analyst_self_correction_revision") as step:
                interrogation = self.memory_store.interrogate(question_text)
                revised_answer, rev_usage = await self.analyst_engine.synthesizer.synthesize(
                    question_text=question_text,
                    plan=analyst_res.plan,
                    memory_context_str=interrogation.formatted_context_block,
                    web_chunks=analyst_res.ranked_chunks,
                    revision_feedback=feedback_str,
                )
                step.set_usage(rev_usage)

            revised_citations = self._extract_citations_from_text(revised_answer, analyst_res.citations)

            # Reset audit verdict counters on tracer to prevent double-counting
            tracer.audit_counts = {k: 0 for k in tracer.audit_counts}

            # Re-Audit Phase
            with tracer.track_step("auditor_re_audit") as step:
                re_audit_report = await self.auditor_engine.audit(
                    question_id=question_id,
                    draft_answer=revised_answer,
                    citations=revised_citations,
                    tracer=tracer,
                )

            final_answer = revised_answer
            final_audit = re_audit_report
            was_revised = True

        # 4. Finalize Telemetry & Save Artifact
        telemetry = tracer.finalize()
        artifact_path = tracer.save_run_artifact(
            f"benchmarks/runs/run_q{question_id}.json", telemetry
        )

        return PipelineResult(
            question_id=question_id,
            question_text=question_text,
            initial_answer=analyst_res.draft_answer,
            was_revised=was_revised,
            final_answer=final_answer,
            audit_report=final_audit,
            telemetry=telemetry,
            artifact_path=str(artifact_path),
            wall_clock_seconds=telemetry.wall_clock_seconds,
            success=True,
            error_message=None,
        )

    def _extract_citations_from_text(
        self,
        text: str,
        fallback_citations: List[CitationReference],
    ) -> List[CitationReference]:
        """Extracts citations from text or falls back to previous citation registry."""
        import re
        citations: List[CitationReference] = []
        pattern = re.compile(r"\[\^?(\d+)\]:?\s*<?(https?://[^\s>\)]+)>?")
        seen = set()

        for match in pattern.finditer(text):
            idx = int(match.group(1))
            url = match.group(2).rstrip(".)],;")
            if url not in seen:
                seen.add(url)
                citations.append(CitationReference(index=idx, url=url))

        return citations if citations else fallback_citations

    def render_audit_report_table(self, report: AuditReport) -> Table:
        """Renders live colorized audit findings table in terminal."""
        table = Table(
            title=f"Adversarial Audit Matrix (Pass Rate: {report.pass_rate * 100:.1f}%)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Claim ID", style="cyan", width=10)
        table.add_column("Atomic Proposition", style="white", width=42)
        table.add_column("Audit Verdict", justify="center", width=14)
        table.add_column("Quote / Forensic Rationale", style="yellow")

        status_colors = {
            VerificationStatus.SUPPORTED: "[bold green]SUPPORTED[/bold green]",
            VerificationStatus.CONTRADICTED: "[bold red]CONTRADICTED[/bold red]",
            VerificationStatus.UNSUPPORTED: "[bold yellow]UNSUPPORTED[/bold yellow]",
            VerificationStatus.UNCITED: "[bold magenta]UNCITED[/bold magenta]",
            VerificationStatus.DEAD_LINK: "[dim white]DEAD_LINK[/dim white]",
        }

        for r in report.results:
            status_badge = status_colors.get(r.status, str(r.status))
            detail = r.verbatim_quote if r.verbatim_quote else r.auditor_rationale
            clean_stmt = r.statement[:40] + "..." if len(r.statement) > 40 else r.statement
            table.add_row(r.claim_id, clean_stmt, status_badge, detail[:80])

        console.print("\n", table)
        return table