"""Evidence-driven report writing (ADR-0004, ticket 10).

The writing context only contains evidence groups whose *effective* verdict
(verdict, or the latest human review) is accepted/cross-validated; every data
point carries a ``[^n]`` footnote marker that maps back to the group/source
through the artifact's citation map. Pending groups never enter the body and
optionally land in the report appendix. No valid conclusions means the report
abstains with an explicit "insufficient evidence" message instead of writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import (
    PENDING_STATUSES,
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    SourceProfile,
)

CITATION_TOKEN_RE = re.compile(r"\[\^(\d+)\]")

EMPTY_EVIDENCE_MESSAGE = (
    "本次调研没有产生任何已采纳或交叉验证通过的证据，无法生成可靠报告（证据不足）。"
    "请在复核页面处置待审数据后重生成。"
)

PENDING_BLOCKED_MESSAGE = (
    "本次调研存在 {pending} 个待审证据组（数据冲突或证据不足），"
    "按配置（ADJUDICATION_PENDING_BLOCKS_REPORT=true）已暂停报告生成。"
    "请在复核页面处置后重生成。"
)

WRITING_INSTRUCTIONS = """以下证据组均通过裁决（有效结论），是本次报告唯一可用的数据来源。
写作要求：
1. 正文中出现的每个数据点都必须来自下面的证据条目；数据之后紧跟 Markdown 脚注语法编号，如 [^1]、[^2]。
2. 一律使用 [^n] 形式，禁止使用 [1] 这样的普通引用编号；不要自行编写脚注定义（定义由系统生成）。
3. 不得引用未列出的数据，也不得编造任何数字或事实；没有证据支撑的内容不要写入。
4. 同一数据点如多处出现，每一处都要标注对应编号。

可用证据（编号 → 数据）："""


@dataclass
class WritingPlan:
    context: str
    citations: dict[str, dict[str, Any]] = field(default_factory=dict)
    appendix: str = ""
    blocked_message: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_message)


def footnote_text(source: SourceProfile | None, evidence: EvidenceItem | None) -> str:
    """The Word footnote contract: 标题 — 发布主体（等级）· 日期 · URL."""
    title = (source.title or "").strip() if source else ""
    if not title and evidence is not None:
        title = evidence.metric
    publisher = ""
    if source is not None:
        publisher = (source.publisher or source.domain or "").strip()
    tier = source.tier if source else ""
    date = (evidence.extracted_at or "")[:10] if evidence is not None else ""
    url = source.url if source else ""
    return f"{title} — {publisher}（{tier}）· {date} · {url}"


def _representative_entry(
    group: EvidenceGroup, artifact: EvidenceArtifact
) -> tuple[EvidenceItem, SourceProfile] | None:
    evidence_by_id = {item.id: item for item in artifact.evidence}
    source_by_id = {profile.id: profile for profile in artifact.sources}

    review = group.review or {}
    if review.get("action") == "set_representative" and review.get(
        "representative_source_id"
    ):
        for member_id in group.members:
            evidence = evidence_by_id.get(member_id)
            if evidence is not None and evidence.source_id == review["representative_source_id"]:
                source = source_by_id.get(evidence.source_id)
                if source is not None:
                    return evidence, source

    representative = group.representative or {}
    evidence = evidence_by_id.get(representative.get("evidence_id"))
    if evidence is None:
        return None
    source = source_by_id.get(evidence.source_id)
    if source is None:
        return None
    return evidence, source


def build_citations(artifact: EvidenceArtifact) -> dict[str, dict[str, Any]]:
    """Number effective-conclusion groups 1..N and map them to source detail."""
    citations: dict[str, dict[str, Any]] = {}
    if not artifact.groups:
        return citations
    number = 1
    for group in artifact.groups:
        if group.effective_status not in {"accepted", "cross_validated"}:
            continue
        entry = _representative_entry(group, artifact)
        if entry is None:
            continue
        evidence, source = entry
        citations[str(number)] = {
            "group_id": group.id,
            "evidence_id": evidence.id,
            "source_id": source.id,
            "url": source.url,
            "metric": evidence.metric,
            "value": evidence.value_raw or evidence.value,
            "footnote": footnote_text(source, evidence),
        }
        number += 1
    return citations


def pending_appendix_markdown(artifact: EvidenceArtifact) -> str:
    """The appendix listing unreviewed pending groups (未采信数据)."""
    pending = [
        group
        for group in artifact.groups or []
        if group.effective_status in PENDING_STATUSES
    ]
    if not pending:
        return ""
    source_by_id = {profile.id: profile for profile in artifact.sources}
    lines = [
        "## 附录：待审数据（未采信）",
        "",
        "以下证据组为冲突待审或证据不足待审，未纳入正文；复核处置后可重生成报告。",
        "",
    ]
    for group in pending:
        representative = group.representative or {}
        value = representative.get("value_raw") or representative.get("value")
        metric = (group.key or {}).get("metric") or ""
        parts = [f"- {group.id} {metric}"]
        if value not in (None, ""):
            parts.append(f"：代表值 {value}")
        reason = group.verdict.get("reason") or ""
        parts.append(f"—— {reason}")
        urls = [
            source_by_id[source_id].url
            for source_id in group.source_ids
            if source_id in source_by_id
        ]
        if urls:
            parts.append("；来源：" + "、".join(urls))
        lines.append("".join(parts))
    lines.append("")
    return "\n".join(lines)


def build_writing_plan(
    artifact: EvidenceArtifact,
    *,
    pending_appendix: bool = True,
    pending_blocks_report: bool = False,
) -> WritingPlan:
    """Build the evidence-only writing context + citation map for the report."""
    citations = artifact.citations or build_citations(artifact)
    context_lines: list[str] = []
    if citations:
        evidence_by_id = {item.id: item for item in artifact.evidence}
        context_lines.append(WRITING_INSTRUCTIONS)
        for number in sorted(citations, key=int):
            entry = citations[number]
            evidence = evidence_by_id.get(entry.get("evidence_id"))
            detail = []
            if evidence is not None:
                if evidence.entity:
                    detail.append(f"主体：{evidence.entity}")
                value = evidence.value_raw or evidence.value
                detail.append(f"代表值：{value}")
                if evidence.unit:
                    detail.append(f"单位：{evidence.unit}")
                period = (evidence.period or {}).get("raw")
                if period:
                    detail.append(f"期间：{period}")
                if evidence.scope:
                    detail.append(f"口径：{evidence.scope}")
            context_lines.append(
                f"[^{number}] 指标：{entry.get('metric') or ''} | "
                + " | ".join(detail)
            )
            if evidence is not None and evidence.quote:
                context_lines.append(f"     原文引用：{evidence.quote}")
            context_lines.append(f"     来源：{entry.get('footnote') or ''}")
    context = "\n".join(context_lines)

    appendix = ""
    if pending_appendix:
        appendix = pending_appendix_markdown(artifact)

    pending_count = sum(
        1
        for group in artifact.groups or []
        if group.effective_status in PENDING_STATUSES
    )
    blocked_message = None
    if not citations:
        blocked_message = EMPTY_EVIDENCE_MESSAGE
    elif pending_blocks_report and pending_count:
        blocked_message = PENDING_BLOCKED_MESSAGE.format(pending=pending_count)

    return WritingPlan(
        context=context,
        citations=citations,
        appendix=appendix,
        blocked_message=blocked_message,
    )


def build_plan_for_config(artifact: EvidenceArtifact, config: Any) -> WritingPlan:
    """The single place mapping report settings onto the writing plan."""
    return build_writing_plan(
        artifact,
        pending_appendix=bool(getattr(config, "report_pending_appendix", True)),
        pending_blocks_report=bool(
            getattr(config, "adjudication_pending_blocks_report", False)
        ),
    )


def finalize_report(markdown: str, plan: WritingPlan) -> str:
    """Keep only valid citation markers, then append appendix + definitions."""
    used: set[int] = set()

    def _replace(match: re.Match) -> str:
        number = int(match.group(1))
        if str(number) not in plan.citations:
            return ""
        used.add(number)
        return match.group(0)

    body = CITATION_TOKEN_RE.sub(_replace, markdown or "")

    chunks = [body.rstrip()]
    if plan.appendix:
        chunks.append(plan.appendix.rstrip())
    if used:
        definitions = [
            f"[^{number}]: {plan.citations[str(number)].get('footnote') or ''}"
            for number in sorted(used)
        ]
        chunks.append("\n".join(definitions))
    return "\n\n".join(chunk for chunk in chunks if chunk) + "\n"
