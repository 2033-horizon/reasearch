"""Evidence adjudication: grouping and rule-based verdicts (ADR-0004).

Deterministic blocking on entity/period/region/unit, then metric + scope
grouping inside the block; scope mismatch always splits. Requirements per
tier, the value tolerance and the conflict resolution order live in the
packaged adjudication rule table (data, not code, path overridable).

Ticket 07 delivers the deterministic baseline (exact metric/scope match,
independence by domain, tier thresholds, representative = best tier + newest).
Ticket 08 layers LLM synonym/scope judgements, publisher-based independence,
conservative repost merging and conflict resolution on top.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .extraction import fold_text
from .models import (
    PENDING_STATUSES,
    EvidenceGroup,
    EvidenceItem,
    SourceProfile,
)

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).with_name("rules") / "adjudication_rules.yaml"

VERDICT_ACCEPTED = "accepted"
VERDICT_CROSS_VALIDATED = "cross_validated"
VERDICT_CONFLICT_PENDING = "conflict_pending"
VERDICT_INSUFFICIENT_PENDING = "insufficient_pending"

DEFAULT_TIER_THRESHOLDS = {"A": 1, "B": 1, "C": 2, "D": 3}
DEFAULT_TOLERANCE = 0.05
DEFAULT_RESOLUTION_ORDER = ("tier", "recency")
_TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
_UNKNOWN_RANK = len(_TIER_RANK)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
LLMCall = Callable[[str], Awaitable[str]]


def _norm(text: Any) -> str:
    return fold_text(str(text or "")).casefold()


def _period_key(period: dict[str, Any] | None) -> str:
    if not period:
        return ""
    raw = _norm(period.get("raw"))
    if raw:
        return raw
    return _norm(
        f"{period.get('type') or ''}|{period.get('start') or ''}|{period.get('end') or ''}"
    )


def _tier_rank(source: SourceProfile | None) -> int:
    return _TIER_RANK.get(source.tier, _UNKNOWN_RANK) if source else _UNKNOWN_RANK


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


@dataclass
class AdjudicationRules:
    """Tier thresholds, tolerance and resolution order (ADR-0004)."""

    thresholds: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_TIER_THRESHOLDS)
    )
    tolerance: float = DEFAULT_TOLERANCE
    resolution_order: tuple[str, ...] = DEFAULT_RESOLUTION_ORDER
    version: int = 1

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AdjudicationRules":
        rule_path = Path(path) if path else DEFAULT_RULES_PATH
        if not rule_path.exists():
            if path:
                logger.warning(
                    "Adjudication rules not found at %s; falling back to packaged defaults.",
                    rule_path,
                )
                return cls.load(None)
            logger.error("Packaged adjudication rules missing at %s", rule_path)
            return cls()
        try:
            with open(rule_path, "r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except Exception as exc:
            logger.error("Failed to load adjudication rules from %s: %s", rule_path, exc)
            if rule_path != DEFAULT_RULES_PATH:
                return cls.load(None)
            return cls()

        if not isinstance(payload, dict):
            logger.warning(
                "Adjudication rules at %s are not a mapping; using defaults.", rule_path
            )
            return cls()

        thresholds = dict(DEFAULT_TIER_THRESHOLDS)
        tiers = payload.get("tiers")
        if isinstance(tiers, dict):
            for tier, config in tiers.items():
                if tier not in thresholds or not isinstance(config, dict):
                    continue
                try:
                    minimum = int(config.get("min_independent_sources"))
                except (TypeError, ValueError):
                    logger.warning("Ignoring non-numeric threshold for tier %s", tier)
                    continue
                if minimum >= 1:
                    thresholds[tier] = minimum

        tolerance = DEFAULT_TOLERANCE
        raw_tolerance = payload.get("value_tolerance")
        if isinstance(raw_tolerance, dict):
            try:
                candidate = float(raw_tolerance.get("relative"))
            except (TypeError, ValueError):
                candidate = 0.0
            if 0 < candidate < 1:
                tolerance = candidate
            else:
                logger.warning(
                    "Invalid relative tolerance %r; keeping %.2f",
                    raw_tolerance.get("relative"),
                    DEFAULT_TOLERANCE,
                )

        order = tuple(
            entry
            for entry in (payload.get("resolution_order") or [])
            if entry in {"tier", "recency"}
        ) or DEFAULT_RESOLUTION_ORDER

        try:
            version = int(payload.get("version") or 1)
        except (TypeError, ValueError):
            version = 1

        return cls(
            thresholds=thresholds,
            tolerance=tolerance,
            resolution_order=order,
            version=version,
        )


class JudgementCache:
    """In-memory fallback cache for LLM merge judgements.

    The SQLite evidence store implements the same ``get``/``set`` interface so
    judgements survive across runs without re-spending LLM calls.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Any] = {}

    def get(self, kind: str, key: str) -> Any:
        return self._data.get((kind, key))

    def set(self, kind: str, key: str, value: Any) -> None:
        self._data[(kind, key)] = value


class Adjudicator:
    """Clusters verified evidence items and adjudicates each group."""

    def __init__(
        self,
        rules: AdjudicationRules | None = None,
        *,
        llm: LLMCall | None = None,
        cache: Any | None = None,
        on_event: EventCallback | None = None,
    ):
        self.rules = rules or AdjudicationRules.load()
        self.llm = llm
        self.cache = cache if cache is not None else JudgementCache()
        self.on_event = on_event

    async def _emit(self, event: dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            await self.on_event(event)
        except Exception as exc:
            logger.warning("Adjudication event callback failed: %s", exc)

    async def adjudicate(
        self,
        evidence: list[EvidenceItem],
        sources: list[SourceProfile],
    ) -> list[EvidenceGroup]:
        source_by_id = {profile.id: profile for profile in sources}
        items_by_id = {item.id: item for item in evidence}
        candidates = [item for item in evidence if item.source_id in source_by_id]

        await self._emit(
            {
                "status": "clustering",
                "message": f"🔎 聚类开始：{len(candidates)} 条证据",
                "evidence": len(candidates),
            }
        )
        groups: list[EvidenceGroup] = []
        for block in self._blocks(candidates):
            groups.extend(await self._cluster_block(block))

        for position, group in enumerate(groups, start=1):
            group.id = f"G-{position:03d}"

        await self._emit(
            {
                "status": "clustered",
                "message": f"🔎 聚类完成：{len(groups)} 个证据组",
                "groups": len(groups),
            }
        )

        for group in groups:
            await self._adjudicate_group(group, items_by_id, source_by_id)

        verdicts = {status: 0 for status in (
            VERDICT_ACCEPTED,
            VERDICT_CROSS_VALIDATED,
            VERDICT_CONFLICT_PENDING,
            VERDICT_INSUFFICIENT_PENDING,
        )}
        for group in groups:
            if group.status in verdicts:
                verdicts[group.status] += 1
        pending = sum(verdicts[status] for status in PENDING_STATUSES)
        await self._emit(
            {
                "status": "adjudicated",
                "message": (
                    f"⚖️ 裁决完成：{len(groups)} 个证据组，待审 {pending} 个"
                ),
                "groups": len(groups),
                "pending": pending,
                "verdicts": verdicts,
            }
        )
        return groups

    # ------------------------------------------------------------------ blocks

    def _block_key(self, item: EvidenceItem) -> tuple:
        return (
            _norm(item.entity),
            _period_key(item.period),
            _norm(item.region),
            _norm(item.unit),
        )

    def _blocks(self, candidates: list[EvidenceItem]) -> list[list[EvidenceItem]]:
        blocks: dict[tuple, list[EvidenceItem]] = {}
        order: list[tuple] = []
        for item in candidates:
            key = self._block_key(item)
            if key not in blocks:
                blocks[key] = []
                order.append(key)
            blocks[key].append(item)
        return [blocks[key] for key in order]

    async def _cluster_block(self, block: list[EvidenceItem]) -> list[EvidenceGroup]:
        buckets: list[dict[str, Any]] = []
        for item in sorted(block, key=lambda entry: entry.id):
            bucket = None
            for candidate in buckets:
                if await self._same_metric(item.metric, candidate["metrics"]):
                    bucket = candidate
                    break
            if bucket is None:
                bucket = {"metrics": [item.metric], "log": []}
                buckets.append(bucket)
            elif _norm(item.metric) not in {_norm(m) for m in bucket["metrics"]}:
                bucket["log"].append(
                    {
                        "type": "metric_merged",
                        "detail": f"“{item.metric}”并入“{bucket['metrics'][0]}”",
                        "by": "llm",
                    }
                )

            partitions = bucket.setdefault("partitions", [])
            for partition in partitions:
                if await self._same_scope(item.scope, partition["scope"]):
                    partition["items"].append(item)
                    break
            else:
                partitions.append({"scope": item.scope, "items": [item]})

        groups: list[EvidenceGroup] = []
        for bucket in buckets:
            for partition in bucket["partitions"]:
                groups.append(self._new_group(partition["items"], bucket["log"]))
        return groups

    async def _same_metric(self, metric: str, existing: list[str]) -> bool:
        """Baseline: exact normalized match only (ticket 08 adds LLM synonyms)."""
        normalized = _norm(metric)
        return any(normalized == _norm(other) for other in existing)

    async def _same_scope(self, scope: str | None, existing: str | None) -> bool:
        """Baseline: identical scope text (or both empty) merges; else splits."""
        return _norm(scope) == _norm(existing)

    def _new_group(
        self, items: list[EvidenceItem], merge_log: list[dict[str, Any]]
    ) -> EvidenceGroup:
        primary = items[0]
        return EvidenceGroup(
            id="",
            key={
                "entity": primary.entity or None,
                "metric": primary.metric,
                "period": primary.period,
                "region": primary.region or None,
                "unit": primary.unit or None,
            },
            scope=primary.scope,
            members=[item.id for item in items],
            source_ids=_unique([item.source_id for item in items]),
            independent_sources=0,
            verdict={},
            merge_log=[dict(entry) for entry in merge_log],
        )

    # ------------------------------------------------------------ adjudication

    async def _adjudicate_group(
        self,
        group: EvidenceGroup,
        items_by_id: dict[str, EvidenceItem],
        source_by_id: dict[str, SourceProfile],
    ) -> None:
        items = [items_by_id[item_id] for item_id in group.members if item_id in items_by_id]
        if not items:
            group.verdict = {
                "status": VERDICT_INSUFFICIENT_PENDING,
                "rule": "no_evidence",
                "reason": "证据组没有可裁决的条目，进入待审",
            }
            group.independent_sources = 0
            return

        clusters = self._value_clusters(items)
        resolution: str | None = None
        conflicts: list[dict[str, Any]] = []
        if len(clusters) <= 1:
            winner = clusters[0]
        else:
            winner, resolution = self._resolve_conflict(clusters, source_by_id)
            conflicts = [
                {
                    "members": [item.id for item in cluster],
                    "values": [item.value_raw or item.value for item in cluster],
                }
                for cluster in clusters
                if cluster is not winner
            ]
        if winner is None:
            group.independent_sources = self._independent_sources(items, source_by_id)
            group.representative = self._pick_representative(items, source_by_id)
            group.verdict = {
                "status": VERDICT_CONFLICT_PENDING,
                "rule": "resolution:unresolved",
                "reason": "数值超出容差且等级与时效均无法裁定，进入待审",
                "resolution": None,
                "conflicts": conflicts,
                "values": _unique(
                    [item.value_raw or str(item.value) for item in items]
                ),
            }
            return

        independent = self._independent_sources(winner, source_by_id)
        group.independent_sources = independent
        group.representative = self._pick_representative(winner, source_by_id)

        best_tier = self._best_tier(winner, source_by_id)
        required = self.rules.thresholds.get(best_tier, DEFAULT_TIER_THRESHOLDS["D"])
        verdict: dict[str, Any] = {
            "independent_sources": independent,
            "required": required,
            "best_tier": best_tier,
            "resolution": resolution,
            "conflicts": conflicts,
            "values": _unique([item.value_raw or str(item.value) for item in winner]),
        }

        if resolution is not None:
            verdict["rule"] = f"resolution:{resolution}"
        else:
            verdict["rule"] = f"tier_minimum:{best_tier}={required}"

        if independent < required:
            verdict["status"] = VERDICT_INSUFFICIENT_PENDING
            verdict["reason"] = (
                f"独立来源 {independent} 条未达 {best_tier} 级门槛 {required} 条，进入待审"
            )
        elif resolution is not None:
            verdict["status"] = (
                VERDICT_ACCEPTED
                if best_tier in {"A", "B"}
                else VERDICT_CROSS_VALIDATED
            )
            verdict["reason"] = (
                f"数值超出容差，按{'等级' if resolution == 'tier' else '时效'}裁定，"
                f"采用 {best_tier} 级来源"
            )
        elif best_tier in {"A", "B"}:
            verdict["status"] = VERDICT_ACCEPTED
            verdict["reason"] = (
                f"{best_tier} 级来源单条达标（门槛 {required} 条），数据一致"
            )
        else:
            verdict["status"] = VERDICT_CROSS_VALIDATED
            verdict["reason"] = (
                f"{best_tier} 级来源独立 {independent} 条达到门槛 {required} 条，数据一致"
            )
        group.verdict = verdict

    def _value_clusters(self, items: list[EvidenceItem]) -> list[list[EvidenceItem]]:
        clusters: list[list[EvidenceItem]] = []
        for item in items:
            for cluster in clusters:
                if self._values_consistent(item, cluster[0]):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        return clusters

    def _values_consistent(self, left: EvidenceItem, right: EvidenceItem) -> bool:
        if left.value_type != right.value_type:
            return False
        left_numbers = _numeric_values(left.value)
        right_numbers = _numeric_values(right.value)
        if left_numbers is not None and right_numbers is not None:
            if len(left_numbers) != len(right_numbers):
                return False
            return all(
                _within_tolerance([a, b], self.rules.tolerance)
                for a, b in zip(left_numbers, right_numbers)
            )
        return _norm(left.value) == _norm(right.value)

    def _resolve_conflict(
        self,
        clusters: list[list[EvidenceItem]],
        source_by_id: dict[str, SourceProfile],
    ) -> tuple[list[EvidenceItem] | None, str | None]:
        for rule in self.rules.resolution_order:
            if rule == "tier":
                ranks = [
                    min(_tier_rank(source_by_id.get(item.source_id)) for item in cluster)
                    for cluster in clusters
                ]
                best = min(ranks)
                winners = [
                    cluster for cluster, rank in zip(clusters, ranks) if rank == best
                ]
                if len(winners) == 1:
                    return winners[0], "tier"
            elif rule == "recency":
                dates = [
                    max(item.extracted_at or "" for item in cluster)
                    for cluster in clusters
                ]
                newest = max(dates)
                winners = [
                    cluster for cluster, date in zip(clusters, dates) if date == newest
                ]
                if len(winners) == 1:
                    return winners[0], "recency"
        return None, None

    def _independent_sources(
        self,
        items: list[EvidenceItem],
        source_by_id: dict[str, SourceProfile],
    ) -> int:
        """Baseline independence: one count per distinct domain (ticket 08 refines)."""
        keys: set[str] = set()
        for item in items:
            source = source_by_id.get(item.source_id)
            if source is None:
                continue
            keys.add(source.domain or source.id)
        return len(keys)

    def _best_tier(
        self, items: list[EvidenceItem], source_by_id: dict[str, SourceProfile]
    ) -> str:
        rank = min(_tier_rank(source_by_id.get(item.source_id)) for item in items)
        for tier, tier_rank in _TIER_RANK.items():
            if tier_rank == rank:
                return tier
        return "D"

    def _pick_representative(
        self, items: list[EvidenceItem], source_by_id: dict[str, SourceProfile]
    ) -> dict[str, Any] | None:
        if not items:
            return None
        best_rank = min(_tier_rank(source_by_id.get(item.source_id)) for item in items)
        candidates = [
            item
            for item in items
            if _tier_rank(source_by_id.get(item.source_id)) == best_rank
        ]
        newest = max(item.extracted_at or "" for item in candidates)
        picked = min(
            (item for item in candidates if (item.extracted_at or "") == newest),
            key=lambda item: item.id,
        )
        return {
            "evidence_id": picked.id,
            "source_id": picked.source_id,
            "metric": picked.metric,
            "value": picked.value,
            "value_raw": picked.value_raw,
            "unit": picked.unit,
            "period": picked.period,
            "scope": picked.scope,
            "quote": picked.quote,
        }


def _numeric_values(value: Any) -> list[float] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list) and value and all(
        isinstance(entry, (int, float)) and not isinstance(entry, bool)
        for entry in value
    ):
        return [float(entry) for entry in value]
    return None


def _within_tolerance(values: list[float], tolerance: float) -> bool:
    low, high = min(values), max(values)
    if low == high:
        return True
    scale = max(abs(low), abs(high))
    if scale == 0:
        return True
    return (high - low) <= tolerance * scale
