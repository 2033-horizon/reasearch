"""Evidence layer entry point: aggregated sources -> evidence artifact.

Independent of orchestration (ADR-0001): the top-level researcher calls
``EvidenceLayer.build`` with the aggregated source list and gets back an
in-memory artifact; serialization/persistence stays in ``EvidenceArtifact``.
LLM, rule table path and cache path are injectable for offline tests.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..utils.domains import normalize_domain
from ..utils.llm import create_chat_completion
from .adjudication import AdjudicationRules, Adjudicator, JudgementCache
from .extraction import EvidenceExtractor
from .store import EvidenceStore
from .models import (
    SCHEMA_VERSION_ADJUDICATED,
    EvidenceArtifact,
    EvidenceItem,
    RejectedItem,
    SourceProfile,
)
from .tiering import TierClassifier, TierRules
from .writing import build_citations

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _cfg(config: Any, name: str, default: Any = None) -> Any:
    value = getattr(config, name, None)
    return default if value is None else value


def _collect_sources(sources: list[Any], max_sources: int) -> list[dict[str, str]]:
    """Deduplicate by URL (keeping the longest content) and cap the list."""
    collected: list[dict[str, str]] = []
    index: dict[str, int] = {}
    for raw in sources or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or raw.get("href") or "").strip()
        if not url:
            continue
        content = raw.get("raw_content") or raw.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        title = str(raw.get("title") or "").strip()
        if url in index:
            existing = collected[index[url]]
            if len(content) > len(existing["content"]):
                existing["content"] = content
                existing["title"] = title or existing["title"]
            continue
        index[url] = len(collected)
        collected.append({"url": url, "title": title, "content": content})
    return collected[: max(0, int(max_sources))]


class EvidenceLayer:
    """Builds an :class:`EvidenceArtifact` from an aggregated source list."""

    def __init__(
        self,
        config: Any,
        *,
        llm: Callable[[str], Awaitable[str]] | None = None,
        adjudication_llm: Callable[[str], Awaitable[str]] | None = None,
        rules_path: str | None = None,
        cache_path: str | None = None,
        cost_callback: Callable[[float], None] | None = None,
        adjudication_cost_callback: Callable[[float], None] | None = None,
        on_event: EventCallback | None = None,
        on_adjudication_event: EventCallback | None = None,
        judgement_cache: Any | None = None,
        store_path: str | None = None,
    ):
        self.config = config
        self.cost_callback = cost_callback
        self.adjudication_cost_callback = adjudication_cost_callback or cost_callback
        self.on_event = on_event
        self.on_adjudication_event = on_adjudication_event
        self.llm = llm if llm is not None else self._build_default_llm()
        self.extractor_name = self._extractor_name()

        db_path = (
            store_path
            if store_path is not None
            else str(_cfg(config, "evidence_db_path", "") or "")
        )
        self.store: EvidenceStore | None = None
        if db_path:
            try:
                self.store = EvidenceStore(db_path)
            except Exception as exc:
                logger.error("Evidence store unavailable at %s: %s", db_path, exc)
        self.last_run_id: int | None = None

        if adjudication_llm is not None:
            self.adjudication_llm = adjudication_llm
        elif llm is not None:
            self.adjudication_llm = llm
        else:
            self.adjudication_llm = self._build_default_llm(
                "adjudication_llm", cost_callback=self.adjudication_cost_callback
            )

        effective_rules_path = rules_path or _cfg(config, "reliability_rules_path", "") or None
        rules = TierRules.load(effective_rules_path)
        effective_cache_path = cache_path or _cfg(config, "tier_cache_path", "") or None
        self.classifier = TierClassifier(rules, llm=self.llm, cache_path=effective_cache_path)
        self.extractor = EvidenceExtractor(
            llm=self.llm,
            max_chars=int(_cfg(config, "evidence_max_chars_per_source", 30000)),
            chunk_size=int(_cfg(config, "evidence_chunk_size", 8000)),
            chunk_overlap=int(_cfg(config, "evidence_chunk_overlap", 400)),
            concurrency=int(_cfg(config, "evidence_concurrency", 4)),
            extractor_name=self.extractor_name,
            cache=self.store,
        )

        adjudication_rules_path = (
            _cfg(config, "adjudication_rules_path", "") or None
        )
        self.adjudication_rules = AdjudicationRules.load(adjudication_rules_path)
        if judgement_cache is not None:
            cache = judgement_cache
        elif self.store is not None:
            cache = self.store
        else:
            cache = JudgementCache()
        self.adjudicator = Adjudicator(
            self.adjudication_rules,
            llm=self.adjudication_llm,
            cache=cache,
            on_event=self._emit_adjudication,
        )

    def close(self) -> None:
        """Release the SQLite connection (safe to call more than once)."""
        if self.store is not None:
            try:
                self.store.close()
            except Exception as exc:
                logger.warning("Failed to close evidence store: %s", exc)
            self.store = None

    def _llm_selection(self, config_key: str = "evidence_llm") -> tuple[str | None, str | None]:
        level = str(_cfg(self.config, config_key, "fast") or "fast").strip().lower()
        if level == "smart":
            return (
                getattr(self.config, "smart_llm_provider", None),
                getattr(self.config, "smart_llm_model", None),
            )
        return (
            getattr(self.config, "fast_llm_provider", None),
            getattr(self.config, "fast_llm_model", None),
        )

    def _extractor_name(self) -> str:
        provider, model = self._llm_selection()
        return f"{provider}:{model}" if provider and model else "unknown"

    def _build_default_llm(
        self,
        config_key: str = "evidence_llm",
        cost_callback: Callable[[float], None] | None = None,
    ) -> Callable[[str], Awaitable[str]] | None:
        provider, model = self._llm_selection(config_key)
        if not provider or not model:
            return None
        level = str(_cfg(self.config, config_key, "fast") or "fast").strip().lower()
        token_key = "smart_token_limit" if level == "smart" else "fast_token_limit"
        max_tokens = int(_cfg(self.config, token_key, 4000) or 4000)
        callback = cost_callback or self.cost_callback

        async def call(prompt: str) -> str:
            return await create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                llm_provider=provider,
                max_tokens=max_tokens,
                llm_kwargs=getattr(self.config, "llm_kwargs", {}) or {},
                cost_callback=callback,
            )

        return call

    async def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            await self.on_event(event)
        except Exception as exc:
            logger.warning("Evidence event callback failed: %s", exc)

    async def _emit_adjudication(self, event: dict[str, Any]) -> None:
        if not self.on_adjudication_event:
            return
        try:
            await self.on_adjudication_event(event)
        except Exception as exc:
            logger.warning("Adjudication event callback failed: %s", exc)

    async def build(
        self,
        sources: list[Any],
        research_id: str,
        query: str = "",
    ) -> EvidenceArtifact:
        collected = _collect_sources(sources, int(_cfg(self.config, "evidence_max_sources", 100)))
        await self._emit(
            {
                "status": "started",
                "message": f"🧾 Evidence layer started for {len(collected)} sources",
                "sources": len(collected),
            }
        )

        profiles: list[SourceProfile] = []
        contents: dict[str, str] = {}
        for position, source in enumerate(collected, start=1):
            assignment = await self.classifier.classify(source["url"], source["title"])
            profile = SourceProfile(
                id=f"S-{position:03d}",
                url=source["url"],
                domain=normalize_domain(source["url"]),
                tier=assignment.tier,
                assigned_by=assignment.assigned_by,
                title=source["title"] or None,
                publisher=assignment.publisher,
                org_type=assignment.org_type,
                matched_rule=assignment.matched_rule,
                scraped=bool(source["content"].strip()),
            )
            profiles.append(profile)
            contents[profile.id] = source["content"]

        evidence: list[EvidenceItem] = []
        rejected: list[RejectedItem] = []
        # One semaphore shared by every source so EVIDENCE_CONCURRENCY bounds
        # in-flight LLM calls for the whole run, not per source.
        gate = asyncio.Semaphore(self.extractor.concurrency)
        completed_sources = 0

        async def _extract_source(profile: SourceProfile):
            nonlocal completed_sources
            result = await self.extractor.extract(
                profile.id, profile.url, contents[profile.id], semaphore=gate
            )
            completed_sources += 1
            await self._emit(
                {
                    "status": "extracting",
                    "message": (
                        f"🧾 证据抽取中：已完成 {completed_sources}/{len(profiles)} 个来源"
                    ),
                    "sources_done": completed_sources,
                    "sources_total": len(profiles),
                }
            )
            return result

        results = await asyncio.gather(
            *(_extract_source(profile) for profile in profiles)
        )
        for source_items, source_rejected in results:
            evidence.extend(source_items)
            rejected.extend(source_rejected)

        for position, item in enumerate(evidence, start=1):
            item.id = f"E-{position:03d}"

        artifact = EvidenceArtifact(
            research_id=research_id,
            query=query,
            sources=profiles,
            evidence=evidence,
            rejected=rejected,
        )

        # Phase 2 (ADR-0004/0005): cluster + adjudicate when the switch is on.
        # Off (default) the artifact stays schema v1 and byte-for-byte the same.
        if bool(_cfg(self.config, "adjudication_enabled", False)):
            artifact.groups = await self.adjudicator.adjudicate(evidence, profiles)
            artifact.schema_version = SCHEMA_VERSION_ADJUDICATED
            # Number the valid conclusions once, so the artifact mapping and
            # the report's [^n] markers always agree (ticket 10).
            artifact.citations = build_citations(artifact)

        if self.store is not None:
            try:
                self.last_run_id = self.store.record_run(artifact)
            except Exception as exc:
                logger.error("Failed to record evidence run: %s", exc)

        summary = artifact.summary
        await self._emit(
            {
                "status": "completed",
                "message": (
                    f"🧾 Evidence layer completed: {summary['evidence']} evidence / "
                    f"{summary['rejected']} rejected from {summary['sources']} sources"
                ),
                "cached_sources": self.extractor.cache_hits,
                **summary,
            }
        )
        return artifact
