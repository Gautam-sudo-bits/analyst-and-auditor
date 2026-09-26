"""
Auditor-Gated Entity Memory Store.
Enforces write barrier, multi-source and multi-temporal retention, thread-safe atomic saves,
alias normalization, self-healing corrupted load recovery, and query-aware fact ranking.
"""

import os
import re
import json
import uuid
import shutil
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Union

from src.memory.schemas import EntityFact, CanonicalEntity, MemoryHit, MemoryInterrogationResult

DEFAULT_STORE_PATH = Path("data/memory/entity_store.json")

# Seed canonical entities & aliases for Indian business domain benchmark
SEED_ENTITIES = [
    {
        "entity_id": "titan_company",
        "canonical_name": "Titan Company Limited",
        "aliases": ["titan", "tanishq", "mia", "fastrack", "titan company", "titan ltd", "caratlane"],
    },
    {
        "entity_id": "kalyan_jewellers",
        "canonical_name": "Kalyan Jewellers India Limited",
        "aliases": ["kalyan", "kalyan jewellers", "candere"],
    },
    {
        "entity_id": "zepto",
        "canonical_name": "Zepto (Kiranakart Technologies)",
        "aliases": ["zepto", "kiranakart", "zepto cafe"],
    },
    {
        "entity_id": "blinkit",
        "canonical_name": "Blinkit (Zomato Quick Commerce)",
        "aliases": ["blinkit", "grofers", "zomato quick commerce"],
    },
    {
        "entity_id": "swiggy_instamart",
        "canonical_name": "Swiggy Instamart",
        "aliases": ["swiggy", "instamart", "swiggy instamart"],
    },
    {
        "entity_id": "nvidia",
        "canonical_name": "NVIDIA Corporation",
        "aliases": ["nvidia", "nvda", "geforce"],
    },
]


class EntityMemoryStore:
    """
    Persistence store protected by an Auditor-only write barrier.
    Guarantees thread-safe atomic serialization, multi-source retention,
    and self-healing backup recovery.
    """
    def __init__(self, storage_path: Optional[Union[str, Path]] = None):
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_STORE_PATH
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.entities: Dict[str, CanonicalEntity] = {}
        self.alias_index: Dict[str, str] = {}  # lower_alias -> entity_id
        self._load()

    def _load(self) -> None:
        """Loads store from disk with automated recovery backup and self-healing on corrupted JSON."""
        with self._lock:
            corrupted = False
            if self.storage_path.exists():
                try:
                    with open(self.storage_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for entity_id, entity_dict in data.items():
                            entity = CanonicalEntity.model_validate(entity_dict)
                            self.entities[entity_id] = entity
                except Exception:
                    corrupted = True
                    # Safe recovery: preserve damaged file before resetting
                    timestamp = int(datetime.now().timestamp())
                    backup_path = self.storage_path.with_name(
                        f"{self.storage_path.stem}_corrupted_{timestamp}.json"
                    )
                    try:
                        shutil.copyfile(self.storage_path, backup_path)
                    except Exception:
                        pass
                    self.entities = {}

            # Merge seed entities if not already registered
            now = datetime.now(timezone.utc).isoformat()
            for seed in SEED_ENTITIES:
                eid = seed["entity_id"]
                if eid not in self.entities:
                    self.entities[eid] = CanonicalEntity(
                        entity_id=eid,
                        canonical_name=seed["canonical_name"],
                        aliases=seed["aliases"],
                        facts=[],
                        created_at=now,
                        updated_at=now,
                    )

            self._rebuild_alias_index()

            # Self-heal: overwrite damaged disk file with clean seed state
            if corrupted:
                self._atomic_save()

    def _rebuild_alias_index(self) -> None:
        """Indexes aliases in lowercase for $O(1)$ canonical resolution."""
        self.alias_index.clear()
        for entity_id, entity in self.entities.items():
            self.alias_index[entity.canonical_name.lower().strip()] = entity_id
            self.alias_index[entity_id.lower().strip()] = entity_id
            for alias in entity.aliases:
                self.alias_index[alias.lower().strip()] = entity_id

    def _atomic_save(self) -> None:
        """
        Saves store atomically using a thread lock and unique temp file.
        Eliminates Windows [WinError 32] file sharing collisions under concurrency.
        """
        with self._lock:
            unique_id = uuid.uuid4().hex[:8]
            tmp_path = self.storage_path.with_name(f"{self.storage_path.stem}_{unique_id}.tmp")
            serializable = {
                eid: entity.model_dump()
                for eid, entity in self.entities.items()
            }
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(serializable, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.storage_path)
            except Exception as e:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise e

    def register_entity(
        self,
        entity_id: str,
        canonical_name: str,
        aliases: Optional[List[str]] = None,
    ) -> CanonicalEntity:
        """Registers a new entity or merges aliases onto an existing entity."""
        with self._lock:
            clean_eid = entity_id.strip().lower()
            now = datetime.now(timezone.utc).isoformat()

            if clean_eid in self.entities:
                entity = self.entities[clean_eid]
                if aliases:
                    for a in aliases:
                        if a.lower().strip() not in [x.lower() for x in entity.aliases]:
                            entity.aliases.append(a.strip())
                entity.updated_at = now
            else:
                all_aliases = [canonical_name.strip()]
                if aliases:
                    all_aliases.extend(aliases)
                entity = CanonicalEntity(
                    entity_id=clean_eid,
                    canonical_name=canonical_name.strip(),
                    aliases=list(dict.fromkeys(all_aliases)),
                    facts=[],
                    created_at=now,
                    updated_at=now,
                )
                self.entities[clean_eid] = entity

            self._rebuild_alias_index()
            self._atomic_save()
            return self.entities[clean_eid]

    def resolve_entity(self, name_or_alias: str) -> Optional[CanonicalEntity]:
        """Resolves a string to a CanonicalEntity using the alias index."""
        with self._lock:
            clean = name_or_alias.strip().lower()
            eid = self.alias_index.get(clean)
            return self.entities.get(eid) if eid else None

    def commit_verified_fact(
        self,
        entity_name_or_alias: str,
        attribute: str,
        value: str,
        source_url: str,
        evidence_quote: str,
        auditor_verdict: str,
        confidence: float,
        temporal_anchor: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        THE AUDITOR WRITE BARRIER:
        Strict gatekeeper that prevents unverified or hallucinated claims from entering memory.
        Uses (attribute, source_url, temporal_anchor) tuple for precise deduplication.
        Returns: (success: bool, message: str)
        """
        with self._lock:
            # Rule 1: Verdict MUST be SUPPORTED
            verdict_clean = auditor_verdict.strip().upper()
            if verdict_clean != "SUPPORTED":
                return False, f"Write rejected: Verdict must be 'SUPPORTED' (got '{auditor_verdict}')"

            # Rule 2: Minimum confidence threshold
            if confidence < 0.85:
                return False, f"Write rejected: Confidence must be >= 0.85 (got {confidence:.2f})"

            # Rule 3: Verbatim evidence quote length
            quote_clean = evidence_quote.strip()
            if len(quote_clean) < 15:
                return False, f"Write rejected: Evidence quote too short ({len(quote_clean)} chars < 15 required)"

            # Rule 4: Valid HTTP/HTTPS source URL
            url_clean = source_url.strip()
            if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
                return False, f"Write rejected: Source URL is invalid ('{source_url}')"

            # Resolve or auto-register canonical entity
            entity = self.resolve_entity(entity_name_or_alias)
            if not entity:
                slug = re.sub(r"[^a-z0-9_]+", "_", entity_name_or_alias.lower().strip()).strip("_")
                entity = self.register_entity(
                    entity_id=slug or f"entity_{uuid.uuid4().hex[:6]}",
                    canonical_name=entity_name_or_alias.strip().title(),
                    aliases=[entity_name_or_alias.strip()],
                )

            now = datetime.now(timezone.utc).isoformat()
            attr_clean = attribute.strip().lower()
            temp_clean = temporal_anchor.strip() if temporal_anchor else None

            # Multi-Temporal & Multi-Source Deduplication Logic
            existing_idx = -1
            for idx, f in enumerate(entity.facts):
                if (
                    f.attribute == attr_clean
                    and f.source_url == url_clean
                    and f.temporal_anchor == temp_clean
                ):
                    existing_idx = idx
                    break

            new_fact = EntityFact(
                fact_id=f"fact_{uuid.uuid4().hex[:10]}",
                attribute=attr_clean,
                value=value.strip(),
                temporal_anchor=temp_clean,
                source_url=url_clean,
                evidence_quote=quote_clean,
                confidence=round(confidence, 4),
                auditor_verdict="SUPPORTED",
                timestamp=now,
            )

            if existing_idx >= 0:
                entity.facts[existing_idx] = new_fact
                action = "updated"
            else:
                entity.facts.append(new_fact)
                action = "committed"

            entity.updated_at = now
            self._atomic_save()

            return True, f"Fact successfully {action} under entity '{entity.canonical_name}' (fact_id={new_fact.fact_id})"

    def interrogate(self, query_text: str) -> MemoryInterrogationResult:
        """
        Scans query_text for known entity names and aliases.
        Ranks facts by relevance to query terms and outputs a clean Markdown context block.
        """
        with self._lock:
            query_lower = query_text.lower()
            matched_hits: List[MemoryHit] = []
            seen_entity_ids = set()

            sorted_aliases = sorted(self.alias_index.keys(), key=lambda a: len(a), reverse=True)

            for alias in sorted_aliases:
                pattern = r"(?<![a-zA-Z0-9])" + re.escape(alias) + r"(?![a-zA-Z0-9])"
                if re.search(pattern, query_lower):
                    eid = self.alias_index[alias]
                    if eid not in seen_entity_ids:
                        seen_entity_ids.add(eid)
                        entity = self.entities[eid]
                        if entity.facts:
                            matched_hits.append(
                                MemoryHit(
                                    entity_id=entity.entity_id,
                                    canonical_name=entity.canonical_name,
                                    matched_alias=alias,
                                    facts=entity.facts,
                                )
                            )

            total_facts = sum(len(h.facts) for h in matched_hits)
            if total_facts == 0:
                return MemoryInterrogationResult(hits=[], total_facts_found=0, formatted_context_block="")

            lines = [
                "### [VERIFIED PRIOR KNOWLEDGE FROM MEMORY]",
                "The following facts were previously verified by the Adversarial Auditor from raw primary web sources.",
                "You may rely on these facts directly without issuing redundant web search queries:",
            ]

            def _fact_relevance(f: EntityFact) -> int:
                score = 0
                if f.attribute and f.attribute.lower() in query_lower:
                    score += 3
                if f.temporal_anchor and f.temporal_anchor.lower() in query_lower:
                    score += 2
                for word in f.value.lower().split():
                    if len(word) > 3 and word in query_lower:
                        score += 1
                return score

            for hit in matched_hits:
                sorted_facts = sorted(hit.facts, key=_fact_relevance, reverse=True)

                for fact in sorted_facts:
                    temporal_str = f" (Scope: {fact.temporal_anchor})" if fact.temporal_anchor else ""
                    lines.append(f"\n- **Entity:** {hit.canonical_name} | **Attribute:** `{fact.attribute}`{temporal_str}")
                    lines.append(f"  * **Value:** {fact.value}")
                    lines.append(f"  * **Source:** {fact.source_url}")
                    lines.append(f"  * **Evidence:** \"{fact.evidence_quote}\"")
                    lines.append(f"  * **Auditor Verification:** SUPPORTED (Confidence: {fact.confidence:.2f})")

            return MemoryInterrogationResult(
                hits=matched_hits,
                total_facts_found=total_facts,
                formatted_context_block="\n".join(lines),
            )