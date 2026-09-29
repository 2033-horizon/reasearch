"""Data models and artifact serialization for the evidence layer."""

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1

TIERS = ("A", "B", "C", "D")
VALUE_TYPES = ("number", "range", "ratio", "text")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourceProfile:
    """Metadata record for one source: tier, publisher and how it was decided."""

    id: str
    url: str
    domain: str
    tier: str
    assigned_by: str
    title: str | None = None
    publisher: str | None = None
    org_type: str | None = None
    matched_rule: str | None = None
    scraped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "domain": self.domain,
            "title": self.title or "",
            "publisher": self.publisher,
            "tier": self.tier,
            "org_type": self.org_type,
            "assigned_by": self.assigned_by,
            "matched_rule": self.matched_rule,
            "scraped": self.scraped,
        }


@dataclass
class EvidenceItem:
    """One extracted, quote-verified data point or text fact."""

    id: str
    source_id: str
    metric: str
    value_type: str
    value: Any
    value_raw: str
    quote: str
    extractor: str
    extracted_at: str = field(default_factory=_now_iso)
    entity: str | None = None
    unit: str | None = None
    period: dict[str, Any] | None = None
    region: str | None = None
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "entity": self.entity,
            "metric": self.metric,
            "value_type": self.value_type,
            "value": self.value,
            "value_raw": self.value_raw,
            "unit": self.unit,
            "period": self.period,
            "region": self.region,
            "scope": self.scope,
            "quote": self.quote,
            "extracted_at": self.extracted_at,
            "extractor": self.extractor,
        }


@dataclass
class RejectedItem:
    """An evidence candidate dropped during extraction, with the reason why."""

    source_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "reason": self.reason,
            "detail": self.detail,
        }


def compute_summary(
    sources: list[SourceProfile],
    evidence: list[EvidenceItem],
    rejected: list[RejectedItem],
) -> dict[str, Any]:
    by_tier = {tier: 0 for tier in TIERS}
    for profile in sources:
        if profile.tier in by_tier:
            by_tier[profile.tier] += 1
    return {
        "sources": len(sources),
        "sources_scraped": sum(1 for p in sources if p.scraped),
        "evidence": len(evidence),
        "rejected": len(rejected),
        "by_tier": by_tier,
    }


def _quote_path(path: str) -> str:
    return urllib.parse.quote(path.replace("\\", "/"))


def _table_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


@dataclass
class EvidenceArtifact:
    """The evidence product: profiles, verified evidence, rejects and summary."""

    research_id: str
    query: str
    sources: list[SourceProfile]
    evidence: list[EvidenceItem]
    rejected: list[RejectedItem]
    generated_at: str = field(default_factory=_now_iso)
    schema_version: int = SCHEMA_VERSION

    @property
    def summary(self) -> dict[str, Any]:
        return compute_summary(self.sources, self.evidence, self.rejected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "research_id": self.research_id,
            "query": self.query,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "sources": [profile.to_dict() for profile in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "rejected": [item.to_dict() for item in self.rejected],
        }

    def to_markdown(self) -> str:
        summary = self.summary
        lines = [
            f"# 证据产物：{self.query}",
            "",
            f"- research_id: `{self.research_id}`",
            f"- generated_at: {self.generated_at}",
            f"- schema_version: {self.schema_version}",
            "",
            "## 概览",
            "",
            "| 来源数 | 已抓取 | 证据条数 | 丢弃条数 | A | B | C | D |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| {sources} | {sources_scraped} | {evidence} | {rejected} | {A} | {B} | {C} | {D} |".format(
                **summary, **summary["by_tier"]
            ),
            "",
            "## 来源档案（来源等级）",
            "",
            "| ID | 来源 | 域名 | 来源等级 | 判定方式 | 发布主体 | 命中规则 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for profile in self.sources:
            lines.append(
                "| {id} | {url} | {domain} | {tier} | {assigned_by} | {publisher} | {matched_rule} |".format(
                    id=profile.id,
                    url=_table_cell(profile.url),
                    domain=_table_cell(profile.domain),
                    tier=profile.tier,
                    assigned_by=profile.assigned_by,
                    publisher=_table_cell(profile.publisher),
                    matched_rule=_table_cell(profile.matched_rule),
                )
            )

        lines += [
            "",
            "## 证据表",
            "",
            "| ID | 来源 | 主体 | 指标 | 数值 | 单位 | 期间 | 口径 | 原文引用 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in self.evidence:
            period = item.period or {}
            period_text = period.get("raw") or period.get("type") or ""
            value_text = (
                f"{item.value_raw}（{item.value}）" if item.value_raw else str(item.value)
            )
            lines.append(
                "| {id} | {source_id} | {entity} | {metric} | {value} | {unit} | {period} | {scope} | {quote} |".format(
                    id=item.id,
                    source_id=item.source_id,
                    entity=_table_cell(item.entity),
                    metric=_table_cell(item.metric),
                    value=_table_cell(value_text),
                    unit=_table_cell(item.unit),
                    period=_table_cell(period_text),
                    scope=_table_cell(item.scope),
                    quote=_table_cell(item.quote),
                )
            )

        if self.rejected:
            lines += [
                "",
                "## 丢弃记录（原因）",
                "",
                "| 来源 | 原因 | 详情 |",
                "| --- | --- | --- |",
            ]
            for rejected in self.rejected:
                lines.append(
                    f"| {rejected.source_id} | {rejected.reason} | {_table_cell(rejected.detail)} |"
                )

        lines.append("")
        return "\n".join(lines)

    def save(self, output_dir: str = "outputs") -> dict[str, str]:
        """Write the artifact as JSON + Markdown; return quoted download paths."""
        os.makedirs(output_dir, exist_ok=True)
        safe_id = re.sub(r"[^\w.\-]", "_", self.research_id or "evidence")
        base = f"{safe_id}.evidence"
        json_path = os.path.join(output_dir, f"{base}.json")
        md_path = os.path.join(output_dir, f"{base}.md")

        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(self.to_markdown())

        return {"json": _quote_path(json_path), "md": _quote_path(md_path)}
