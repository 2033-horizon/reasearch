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
from .extraction import EvidenceExtractor
from .models import EvidenceArtifact, EvidenceItem, RejectedItem, SourceProfile
from .tiering import TierClassifier, TierRules

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
        rules_path: str | None = None,
        cache_path: str | None = None,
        cost_callback: Callable[[float], None] | None = None,
        on_event: EventCallback | None = None,
    ):
        self.config = config
        self.cost_callback = cost_callback
        self.on_event = on_event
        self.llm = llm if llm is not None else self._build_default_llm()
        self.extractor_name = self._extractor_name()

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
        )

    def _llm_selection(self) -> tuple[str | None, str | None]:
        level = str(_cfg(self.config, "evidence_llm", "fast") or "fast").strip().lower()
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

    def _build_default_llm(self) -> Callable[[str], Awaitable[str]] | None:
        provider, model = self._llm_selection()
        if not provider or not model:
            return None
        max_tokens = int(_cfg(self.config, "fast_token_limit", 4000) or 4000)

        async def call(prompt: str) -> str:
            return await create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                llm_provider=provider,
                max_tokens=max_tokens,
                llm_kwargs=getattr(self.config, "llm_kwargs", {}) or {},
                cost_callback=self.cost_callback,
            )

        return call

    async def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            await self.on_event(event)
        except Exception as exc:
            logger.warning("Evidence event callback failed: %s", exc)

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
        results = await asyncio.gather(
            *(
                self.extractor.extract(profile.id, profile.url, contents[profile.id])
                for profile in profiles
            )
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
        summary = artifact.summary
        await self._emit(
            {
                "status": "completed",
                "message": (
                    f"🧾 Evidence layer completed: {summary['evidence']} evidence / "
                    f"{summary['rejected']} rejected from {summary['sources']} sources"
                ),
                **summary,
            }
        )
        return artifact
