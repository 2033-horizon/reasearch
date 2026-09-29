"""Ticket 10 regression suite: evidence-driven writing.

Asserts that the writing context only contains effective conclusions, that
finalize keeps only valid [^n] markers (with footnote definitions matching the
artifact citation map), that pending data lands in the configurable appendix,
and that an empty effective conclusion set abstains instead of inventing a
report. Also covers the GPTResearcher integration path.
"""

from types import SimpleNamespace

import pytest

from gpt_researcher import GPTResearcher
from gpt_researcher.evidence import (
    EMPTY_EVIDENCE_MESSAGE,
    PENDING_BLOCKED_MESSAGE,
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    SourceProfile,
    build_citations,
    build_writing_plan,
    finalize_report,
    footnote_text,
)
from gpt_researcher.utils.enum import ReportType


def _source(source_id, domain, tier, publisher, title):
    return SourceProfile(
        id=source_id,
        url=f"https://{domain}/x",
        domain=domain,
        tier=tier,
        assigned_by="rule",
        publisher=publisher,
        title=title,
        scraped=True,
    )


def _evidence(item_id, source_id, metric, value_raw, extracted_at="2026-09-29T10:00:00+00:00"):
    return EvidenceItem(
        id=item_id,
        source_id=source_id,
        metric=metric,
        value_type="number",
        value=100,
        value_raw=value_raw,
        quote=f"{metric}的原文引用",
        extractor="fake:model",
        entity="某公司",
        unit="万辆",
        period={"type": "year", "raw": "2024年"},
        scope="全年累计",
        extracted_at=extracted_at,
    )


def _group(group_id, status, members, source_ids, evidence_id, source_id):
    return EvidenceGroup(
        id=group_id,
        key={"entity": "某公司", "metric": "新能源汽车销量"},
        scope="全年累计",
        members=members,
        source_ids=source_ids,
        independent_sources=2,
        verdict={"status": status, "reason": f"{status} 说明"},
        representative={
            "evidence_id": evidence_id,
            "source_id": source_id,
            "value_raw": "100万辆",
        },
    )


def _artifact(*, with_pending=True) -> EvidenceArtifact:
    sources = [
        _source("S-001", "a.example", "C", "甲媒体", "甲媒体页面"),
        _source("S-002", "stats.gov.cn", "A", "国家统计局", "统计公报"),
        _source("S-003", "b.example", "C", "乙媒体", "乙媒体页面"),
    ]
    evidence = [
        _evidence("E-001", "S-001", "新能源汽车销量", "100万辆"),
        _evidence("E-002", "S-002", "新能源汽车销量", "100万辆"),
        _evidence("E-003", "S-003", "出口量", "50万辆"),
    ]
    groups = [
        _group("G-001", "accepted", ["E-002"], ["S-002"], "E-002", "S-002"),
        _group("G-002", "cross_validated", ["E-001"], ["S-001"], "E-001", "S-001"),
    ]
    if with_pending:
        groups.append(
            _group("G-003", "insufficient_pending", ["E-003"], ["S-003"], "E-003", "S-003")
        )
    artifact = EvidenceArtifact(
        research_id="research_w10",
        query="新能源",
        sources=sources,
        evidence=evidence,
        rejected=[],
        schema_version=2,
        groups=groups,
    )
    artifact.citations = build_citations(artifact)
    return artifact


# ------------------------------------------------------------ writing plan


def test_context_contains_only_effective_conclusions():
    artifact = _artifact()

    plan = build_writing_plan(artifact)

    assert "[^1]" in plan.context and "[^2]" in plan.context
    assert "[^3]" not in plan.context
    assert "出口量" not in plan.context
    assert plan.citations["1"]["group_id"] == "G-001"
    assert plan.citations["1"]["url"] == "https://stats.gov.cn/x"
    assert plan.citations["2"]["group_id"] == "G-002"


def test_footnote_text_follows_contract():
    artifact = _artifact()
    source = artifact.sources[1]
    evidence = artifact.evidence[1]

    text = footnote_text(source, evidence)

    assert text == "统计公报 — 国家统计局（A）· 2026-09-29 · https://stats.gov.cn/x"


def test_finalize_keeps_valid_markers_and_appends_definitions():
    artifact = _artifact()
    plan = build_writing_plan(artifact)
    markdown = "销量为100万辆 [^1]，再次出现 [^1]，出口数据 [^3]，未知编号 [^9]。"

    report = finalize_report(markdown, plan)

    assert "[^1]" in report
    assert "[^3]" not in report
    assert "[^9]" not in report
    assert "[^1]: 统计公报 — 国家统计局（A）· 2026-09-29 · https://stats.gov.cn/x" in report
    # Pending data appears in the appendix, not the body.
    assert "附录：待审数据（未采信）" in report
    assert "G-003" in report


def test_appendix_can_be_disabled():
    artifact = _artifact()

    plan = build_writing_plan(artifact, pending_appendix=False)
    report = finalize_report("销量为100万辆 [^1]。", plan)

    assert "附录" not in report
    assert "[^1]:" in report


def test_empty_effective_conclusions_abstain():
    artifact = _artifact(with_pending=True)
    artifact.groups = [group for group in artifact.groups if group.status != "accepted"]
    artifact.groups[0].verdict["status"] = "insufficient_pending"
    artifact.citations = build_citations(artifact)

    plan = build_writing_plan(artifact)

    assert plan.blocked
    assert plan.blocked_message == EMPTY_EVIDENCE_MESSAGE


def test_pending_can_block_report_by_config():
    artifact = _artifact()

    plan = build_writing_plan(artifact, pending_blocks_report=True)

    assert plan.blocked
    assert PENDING_BLOCKED_MESSAGE.format(pending=1) == plan.blocked_message


# ------------------------------------------------------- researcher wiring


def _make_researcher(monkeypatch, tmp_path, artifact, **flags):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    researcher = GPTResearcher(
        query="新能源行业",
        report_type=ReportType.ResearchReport.value,
        agent="agent",
        role="role",
    )
    researcher.cfg.adjudication_enabled = flags.get("adjudication_enabled", True)
    researcher.cfg.report_pending_appendix = flags.get("report_pending_appendix", True)
    researcher.cfg.adjudication_pending_blocks_report = flags.get(
        "pending_blocks_report", False
    )
    researcher.evidence_artifact = artifact
    return researcher


@pytest.mark.asyncio
async def test_write_report_uses_evidence_context_and_finalizes(monkeypatch, tmp_path):
    artifact = _artifact()
    researcher = _make_researcher(monkeypatch, tmp_path, artifact)
    captured = {}

    async def fake_write_report(**kwargs):
        captured["context"] = kwargs.get("ext_context")
        return "销量为100万辆 [^1]，补充说明。"

    researcher.report_generator.write_report = fake_write_report

    report = await researcher.write_report()

    assert "[^1]" in report
    assert "[^1]: 统计公报" in report
    assert "附录：待审数据" in report
    context = captured["context"]
    assert "[^1]" in context and "[^2]" in context
    assert "原文引用" in context


@pytest.mark.asyncio
async def test_write_report_abstains_without_valid_conclusions(monkeypatch, tmp_path):
    artifact = _artifact(with_pending=True)
    artifact.groups = [artifact.groups[2]]
    artifact.citations = build_citations(artifact)
    researcher = _make_researcher(monkeypatch, tmp_path, artifact)

    async def fail_write_report(**kwargs):
        raise AssertionError("report must not be written without valid conclusions")

    researcher.report_generator.write_report = fail_write_report

    report = await researcher.write_report()

    assert report == EMPTY_EVIDENCE_MESSAGE


@pytest.mark.asyncio
async def test_write_report_blocks_on_pending_when_configured(monkeypatch, tmp_path):
    artifact = _artifact()
    researcher = _make_researcher(
        monkeypatch, tmp_path, artifact, pending_blocks_report=True
    )

    async def fail_write_report(**kwargs):
        raise AssertionError("pending groups must block the report body")

    researcher.report_generator.write_report = fail_write_report

    report = await researcher.write_report()

    assert report == PENDING_BLOCKED_MESSAGE.format(pending=1)


@pytest.mark.asyncio
async def test_write_report_unchanged_when_adjudication_disabled(monkeypatch, tmp_path):
    artifact = _artifact()
    researcher = _make_researcher(
        monkeypatch, tmp_path, artifact, adjudication_enabled=False
    )
    captured = {}

    async def fake_write_report(**kwargs):
        captured["context"] = kwargs.get("ext_context")
        return "原始报告内容 [^1] 不做任何处理。"

    researcher.report_generator.write_report = fake_write_report

    report = await researcher.write_report()

    assert report == "原始报告内容 [^1] 不做任何处理。"
    assert captured["context"] == researcher.context or captured["context"] == []


def test_introduction_uses_evidence_context_when_available(monkeypatch, tmp_path):
    artifact = _artifact()
    researcher = _make_researcher(monkeypatch, tmp_path, artifact)
    researcher._evidence_writing_context = "证据上下文"
    captured = {}

    async def fake_introduction(query, context, agent_role_prompt, config, websocket=None, cost_callback=None, **kwargs):
        captured["context"] = context
        return "引言"

    monkeypatch.setattr(
        "gpt_researcher.skills.writer.write_report_introduction", fake_introduction
    )

    import asyncio

    result = asyncio.run(researcher.report_generator.write_introduction())

    assert result == "引言"
    assert captured["context"] == "证据上下文"
