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

import difflib
import json
import logging
import re
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
from .parsing import parse_json_object

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

REPOST_SIMILARITY = 0.9
REPOST_UNCERTAIN_SIMILARITY = 0.75

_ATTRIBUTION_RE = re.compile(r"(?:来源|转自|摘自|据)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9.]{2,30})")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
LLMCall = Callable[[str], Awaitable[str]]


def build_metric_synonym_prompt(metric_a: str, metric_b: str) -> str:
    return (
        "你是市场调研的指标归并助手（指标归并）。判断下面两个指标名是否为同一"
        "指标的近义表述（如“销量”与“销售量”）。统计对象、口径不同的指标不得归并；"
        "数值接近不能作为归并理由。\n"
        '只输出 JSON：{"same": true 或 false, "reason": "简短依据"}\n'
        f"指标A：{metric_a}\n"
        f"指标B：{metric_b}\n"
    )


def build_scope_relation_prompt(scope_a: str | None, scope_b: str | None) -> str:
    return (
        "你是市场调研的口径判定助手（口径判定）。判断两个统计口径的关系：\n"
        "same（同一口径）/ compatible（兼容，可合并）/ different（不同，不能合并）。\n"
        '只输出 JSON：{"relation": "same|compatible|different", "reason": "简短依据"}\n'
        f"口径A：{scope_a or '（无）'}\n"
        f"口径B：{scope_b or '（无）'}\n"
    )


def build_text_relation_prompt(text_a: str, text_b: str) -> str:
    return (
        "你是市场调研的事实一致性判定助手（事实判定）。判断两条文本型事实的关系：\n"
        "same（同一事实的近义表述）/ conflict（语义冲突，如“已完成”与“计划中”）/ "
        "different（描述不同且不冲突）。\n"
        '只输出 JSON：{"relation": "same|conflict|different", "reason": "简短依据"}\n'
        f"事实A：{text_a}\n"
        f"事实B：{text_b}\n"
    )


def _norm(text: Any) -> str:
    return fold_text(str(text or "")).casefold()


def scope_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


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


def _recency_date(item: EvidenceItem) -> str:
    """Day-granularity recency key (extracted_at is the best signal captured)."""
    return (item.extracted_at or "")[:10]


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
                matched, entry = await self._match_metric(item.metric, candidate["metrics"])
                if matched is not None:
                    bucket = candidate
                    if entry is not None:
                        bucket["log"].append(entry)
                    break
            if bucket is None:
                bucket = {"metrics": [item.metric], "log": []}
                buckets.append(bucket)
            elif _norm(item.metric) not in {_norm(m) for m in bucket["metrics"]}:
                bucket["metrics"].append(item.metric)

            partitions = bucket.setdefault("partitions", [])
            partition, logs = await self._find_partition(item.scope, partitions)
            for entry in logs:
                bucket["log"].append(entry)
            if partition is None:
                partitions.append({"scope": item.scope, "items": [item]})
            else:
                partition["items"].append(item)

        groups: list[EvidenceGroup] = []
        for bucket in buckets:
            for partition in bucket["partitions"]:
                groups.append(self._new_group(partition["items"], bucket["log"]))
        return groups

    async def _find_partition(
        self,
        scope: str | None,
        partitions: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Match a scope to an existing partition; exact text first, then LLM.

        Returns the matched partition (or None) and merge/split log entries.
        """
        normalized = _norm(scope)
        for partition in partitions:
            if normalized == _norm(partition["scope"]):
                return partition, []

        logs: list[dict[str, Any]] = []
        if self.llm is None:
            return None, logs
        for partition in partitions:
            other = partition["scope"]
            key = "|".join(sorted([normalized, _norm(other)]))
            result, from_cache = await self._judge(
                "scope_relation", key, build_scope_relation_prompt(scope, other)
            )
            relation = str((result or {}).get("relation") or "").lower()
            if relation in {"same", "compatible"}:
                logs.append(
                    {
                        "type": "scope_merged",
                        "scopes": [scope_or_none(scope), scope_or_none(other)],
                        "relation": relation,
                        "by": "cache" if from_cache else "llm",
                        "reason": str((result or {}).get("reason") or ""),
                    }
                )
                return partition, logs
            if relation == "different":
                logs.append(
                    {
                        "type": "scope_split",
                        "scopes": [scope_or_none(scope), scope_or_none(other)],
                        "relation": "different",
                        "by": "cache" if from_cache else "llm",
                        "reason": str((result or {}).get("reason") or ""),
                    }
                )
        return None, logs

    async def _match_metric(
        self, metric: str, existing: list[str]
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Exact normalized match first, then cached LLM synonym judgements.

        Returns the matched metric and an optional audit entry.
        """
        normalized = _norm(metric)
        for other in existing:
            if normalized == _norm(other):
                return other, None
        if self.llm is None:
            return None, None
        for other in existing:
            key = "|".join(sorted([normalized, _norm(other)]))
            result, from_cache = await self._judge(
                "metric_synonym", key, build_metric_synonym_prompt(metric, other)
            )
            if (result or {}).get("same") is True:
                return other, {
                    "type": "metric_merged",
                    "metrics": [metric, other],
                    "by": "cache" if from_cache else "llm",
                    "reason": str((result or {}).get("reason") or ""),
                }
        return None, None

    async def _judge(
        self, kind: str, key: str, prompt: str
    ) -> tuple[dict[str, Any] | None, bool]:
        """Return (judgement, from_cache); caches successful LLM judgements."""
        cached = self.cache.get(kind, key) if self.cache is not None else None
        if isinstance(cached, dict):
            return cached, True
        if self.llm is None:
            return None, False
        try:
            response = await self.llm(prompt)
        except Exception as exc:
            logger.warning("Adjudication LLM call failed (%s): %s", kind, exc)
            return None, False
        parsed = parse_json_object(response)
        if not parsed:
            return None, False
        result = {"by": "llm", **parsed}
        if self.cache is not None:
            try:
                self.cache.set(kind, key, result)
            except Exception as exc:
                logger.warning("Failed to cache adjudication judgement: %s", exc)
        return result, False

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

        clusters = await self._value_clusters(items)
        resolution: str | None = None
        conflicts: list[dict[str, Any]] = []
        winner: list[EvidenceItem] | None = None
        if len(clusters) <= 1:
            winner = clusters[0]
        else:
            conflicts = [
                {
                    "members": [item.id for item in cluster],
                    "values": [item.value_raw or item.value for item in cluster],
                }
                for cluster in clusters
            ]
            # Text facts never resolve through tier/recency: a semantic
            # conflict ("已完成" vs "计划中") always goes to human review.
            if any(item.value_type == "text" for item in items):
                winner, resolution = None, None
            else:
                winner, resolution = self._resolve_conflict(clusters, source_by_id)
                if winner is not None:
                    conflicts = [entry for entry in conflicts if entry["members"] != [item.id for item in winner]]

        # Repost log covers every member of the group for audit; the verdict
        # count is computed over the winning cluster only.
        _, repost_log = self._independent_sources(items, source_by_id)
        group.merge_log.extend(repost_log)
        if winner is None:
            group.independent_sources, _ = self._independent_sources(items, source_by_id)
            group.representative = self._pick_representative(items, source_by_id)
            group.verdict = {
                "status": VERDICT_CONFLICT_PENDING,
                "rule": "resolution:unresolved",
                "reason": "数值或语义冲突且等级与时效均无法裁定，进入待审",
                "resolution": None,
                "conflicts": conflicts,
                "values": _unique(
                    [item.value_raw or str(item.value) for item in items]
                ),
            }
            return

        independent, _ = self._independent_sources(winner, source_by_id)
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

    async def _value_clusters(self, items: list[EvidenceItem]) -> list[list[EvidenceItem]]:
        clusters: list[list[EvidenceItem]] = []
        for item in items:
            for cluster in clusters:
                if await self._values_consistent(item, cluster[0]):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        return clusters

    async def _values_consistent(self, left: EvidenceItem, right: EvidenceItem) -> bool:
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
        if _norm(left.value) == _norm(right.value):
            return True
        # Text facts: near-synonym phrasing counts as the same fact; anything
        # else stays a conflict (LLM-judged, cached, conservative on failure).
        if left.value_type == "text" and self.llm is not None:
            key = "|".join(sorted([_norm(left.value), _norm(right.value)]))
            result, _ = await self._judge(
                "text_relation",
                key,
                build_text_relation_prompt(str(left.value), str(right.value)),
            )
            return str((result or {}).get("relation") or "").lower() == "same"
        return False

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
                # Day granularity: items extracted in the same run share the
                # date, so same-tier conflicts stay pending (ticket 08) instead
                # of resolving on arbitrary per-chunk timestamps.
                dates = [
                    max(_recency_date(item) for item in cluster) for cluster in clusters
                ]
                newest = max(dates)
                winners = [
                    cluster for cluster, date in zip(clusters, dates) if date == newest
                ]
                if len(winners) == 1:
                    return winners[0], "recency"
        return None, None

    def _publisher_key(
        self, item: EvidenceItem, source_by_id: dict[str, SourceProfile]
    ) -> str:
        source = source_by_id.get(item.source_id)
        if source is None:
            return item.source_id
        return _norm(source.publisher) or _norm(source.domain) or source.id

    def _independent_sources(
        self,
        items: list[EvidenceItem],
        source_by_id: dict[str, SourceProfile],
    ) -> tuple[int, list[dict[str, Any]]]:
        """Count independent sources (ADR-0004).

        One count per publisher (domain fallback: same subject, many domains
        still one), reposts merged into the original and never counted anew;
        uncertain reposts are conservatively merged (not independent).
        """
        if not items:
            return 0, []
        publishers = [self._publisher_key(item, source_by_id) for item in items]
        parent = list(range(len(items)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left_index: int, right_index: int) -> None:
            left_root, right_root = find(left_index), find(right_index)
            if left_root != right_root:
                parent[right_root] = left_root

        log: list[dict[str, Any]] = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if publishers[i] == publishers[j]:
                    union(i, j)
                    continue
                pair = self._repost_pair(items[i], items[j], source_by_id)
                if pair is None:
                    continue
                reposted, original, reason = pair
                repost_index = i if reposted is items[i] else j
                original_index = j if repost_index == i else i
                if find(repost_index) == find(original_index):
                    continue
                union(repost_index, original_index)
                log.append(
                    {
                        "type": "repost",
                        "evidence_id": reposted.id,
                        "source_id": reposted.source_id,
                        "repost_of": self._publisher_key(original, source_by_id),
                        "reason": reason,
                        "by": "rule",
                    }
                )
        return len({find(index) for index in range(len(items))}), log

    def _repost_pair(
        self,
        left: EvidenceItem,
        right: EvidenceItem,
        source_by_id: dict[str, SourceProfile],
    ) -> tuple[EvidenceItem, EvidenceItem, str] | None:
        """Detect that one item reposts the other's data, with the reason."""
        left_source = source_by_id.get(left.source_id)
        right_source = source_by_id.get(right.source_id)

        for candidate, target in ((left, right_source), (right, left_source)):
            attribution = _attribution_target(candidate.quote)
            if attribution and _attribution_hits(attribution, target):
                original = right if candidate is left else left
                return candidate, original, "explicit_attribution"

        left_quote, right_quote = _norm(left.quote), _norm(right.quote)
        if not left_quote or not right_quote:
            return None
        ratio = difflib.SequenceMatcher(None, left_quote, right_quote).ratio()
        if ratio >= REPOST_SIMILARITY:
            reason = "quote_similarity"
        elif ratio >= REPOST_UNCERTAIN_SIMILARITY:
            reason = "quote_similarity_uncertain"
        else:
            return None
        original = self._pick_original(left, right, source_by_id)
        reposted = right if original is left else left
        return reposted, original, reason

    def _pick_original(
        self,
        left: EvidenceItem,
        right: EvidenceItem,
        source_by_id: dict[str, SourceProfile],
    ) -> EvidenceItem:
        left_rank = _tier_rank(source_by_id.get(left.source_id))
        right_rank = _tier_rank(source_by_id.get(right.source_id))
        if left_rank != right_rank:
            return left if left_rank < right_rank else right
        left_time, right_time = _recency_date(left), _recency_date(right)
        if left_time != right_time:
            return left if left_time < right_time else right
        return left if left.id <= right.id else right

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
        newest = max(_recency_date(item) for item in candidates)
        picked = min(
            (item for item in candidates if _recency_date(item) == newest),
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


def _attribution_target(quote: str) -> str | None:
    """Extract an explicit source annotation ("来源：XX", "转自 XX") if any."""
    match = _ATTRIBUTION_RE.search(fold_text(quote))
    return match.group(1).strip() if match else None


def _attribution_hits(target: str, source: SourceProfile | None) -> bool:
    if source is None:
        return False
    target_norm = _norm(target)
    for name in (_norm(source.publisher), _norm(source.domain)):
        if not name or not target_norm:
            continue
        if target_norm in name or name in target_norm:
            return True
    return False


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
