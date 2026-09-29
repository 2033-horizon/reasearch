"""Ticket 08 regression suite: synonym merging, independence and conflicts.

Covers the LLM-assisted parts of the adjudication pipeline through
``Adjudicator.adjudicate`` with fake LLMs: metric synonyms, scope relations,
text fact relations, judgement caching, publisher-based independence, repost
merging (similarity / explicit attribution / conservative grey zone) and the
conflict resolution rules from ADR-0004.
"""

import json

import pytest

from gpt_researcher.evidence import (
    AdjudicationRules,
    Adjudicator,
    EvidenceItem,
    JudgementCache,
    SourceProfile,
)


def _source(source_id, domain, tier, publisher=None):
    return SourceProfile(
        id=source_id,
        url=f"https://{domain}/article",
        domain=domain,
        tier=tier,
        assigned_by="rule",
        publisher=publisher,
        scraped=True,
    )


def _item(
    item_id,
    source_id,
    *,
    metric="新能源汽车销量",
    value=100,
    value_type="number",
    scope="全年累计",
    entity="新能源汽车",
    unit="辆",
    quote="独立报道引文",
    extracted_at="2026-09-29T00:00:00+00:00",
):
    return EvidenceItem(
        id=item_id,
        source_id=source_id,
        metric=metric,
        value_type=value_type,
        value=value,
        value_raw=str(value),
        quote=quote,
        extractor="fake:model",
        entity=entity,
        unit=unit,
        period={"type": "year", "raw": "2024年"},
        scope=scope,
        extracted_at=extracted_at,
    )


class FakeAdjudicationLLM:
    """Answers the three adjudication prompt kinds with configurable verdicts."""

    def __init__(self, *, metric_same=False, scope_relation="different", text_relation="conflict"):
        self.metric_same = metric_same
        self.scope_relation = scope_relation
        self.text_relation = text_relation
        self.calls = []

    async def __call__(self, prompt):
        self.calls.append(prompt)
        if "指标归并" in prompt:
            return json.dumps({"same": self.metric_same, "reason": "测试判定"})
        if "口径判定" in prompt:
            return json.dumps({"relation": self.scope_relation, "reason": "测试判定"})
        if "事实判定" in prompt:
            return json.dumps({"relation": self.text_relation, "reason": "测试判定"})
        raise AssertionError(f"unexpected adjudication prompt: {prompt[:80]}")

    def count(self, marker):
        return sum(1 for prompt in self.calls if marker in prompt)


async def _adjudicate(items, sources, *, llm=None, cache=None):
    adjudicator = Adjudicator(
        AdjudicationRules.load(), llm=llm, cache=cache if cache is not None else JudgementCache()
    )
    return await adjudicator.adjudicate(items, sources)


# ------------------------------------------------------------------ synonyms


@pytest.mark.asyncio
async def test_metric_synonyms_merge_and_leave_audit_trail():
    llm = FakeAdjudicationLLM(metric_same=True)
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", metric="销量", quote="引文甲"),
        _item("E-002", "S-002", metric="销售量", quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources, llm=llm)

    assert len(groups) == 1
    assert groups[0].members == ["E-001", "E-002"]
    assert groups[0].verdict["status"] == "cross_validated"
    merges = [entry for entry in groups[0].merge_log if entry["type"] == "metric_merged"]
    assert merges and merges[0]["metrics"] == ["销售量", "销量"]
    assert merges[0]["by"] == "llm"


@pytest.mark.asyncio
async def test_metric_synonym_judgement_is_cached():
    llm = FakeAdjudicationLLM(metric_same=True)
    cache = JudgementCache()
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", metric="销量", quote="引文甲"),
        _item("E-002", "S-002", metric="销售量", quote="引文乙"),
    ]

    await _adjudicate(items, sources, llm=llm, cache=cache)
    assert llm.count("指标归并") == 1

    fresh_llm = FakeAdjudicationLLM(metric_same=True)
    groups = await _adjudicate(items, sources, llm=fresh_llm, cache=cache)

    assert fresh_llm.count("指标归并") == 0
    assert len(groups) == 1
    merges = [entry for entry in groups[0].merge_log if entry["type"] == "metric_merged"]
    assert merges and merges[0]["by"] == "cache"


@pytest.mark.asyncio
async def test_metric_synonym_false_never_merges():
    llm = FakeAdjudicationLLM(metric_same=False)
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001", metric="销量", quote="引文甲"),
        _item("E-002", "S-002", metric="销售量", quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources, llm=llm)

    assert len(groups) == 2


# -------------------------------------------------------------------- scopes


@pytest.mark.asyncio
async def test_scope_same_or_compatible_merges():
    for relation in ("same", "compatible"):
        llm = FakeAdjudicationLLM(scope_relation=relation)
        sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
        items = [
            _item("E-001", "S-001", scope="零售额", quote="引文甲"),
            _item("E-002", "S-002", scope="零售金额", quote="引文乙"),
        ]

        groups = await _adjudicate(items, sources, llm=llm)

        assert len(groups) == 1, relation
        entries = [entry for entry in groups[0].merge_log if entry["type"] == "scope_merged"]
        assert entries and entries[0]["relation"] == relation


@pytest.mark.asyncio
async def test_scope_different_always_splits_and_is_cached():
    llm = FakeAdjudicationLLM(scope_relation="different")
    cache = JudgementCache()
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", scope="零售额", quote="引文甲"),
        _item("E-002", "S-002", scope="出货额", quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources, llm=llm, cache=cache)

    assert len(groups) == 2
    splits = [entry for entry in groups[1].merge_log if entry["type"] == "scope_split"]
    assert splits and splits[0]["relation"] == "different"
    assert llm.count("口径判定") == 1

    fresh_llm = FakeAdjudicationLLM(scope_relation="different")
    groups = await _adjudicate(items, sources, llm=fresh_llm, cache=cache)
    assert len(groups) == 2
    assert fresh_llm.count("口径判定") == 0


# --------------------------------------------------------------------- texts


@pytest.mark.asyncio
async def test_text_near_synonyms_merge_then_are_adjudicated():
    llm = FakeAdjudicationLLM(text_relation="same")
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item(
            "E-001",
            "S-001",
            value_type="text",
            value="已完成对乙公司的收购",
            quote="引文甲",
        ),
        _item(
            "E-002",
            "S-002",
            value_type="text",
            value="完成收购乙公司",
            quote="引文乙",
        ),
    ]

    groups = await _adjudicate(items, sources, llm=llm)

    assert len(groups) == 1
    assert not groups[0].verdict["conflicts"]
    assert groups[0].verdict["status"] == "cross_validated"


@pytest.mark.asyncio
async def test_text_semantic_conflict_pends_even_when_tier_could_win():
    llm = FakeAdjudicationLLM(text_relation="conflict")
    sources = [_source("S-001", "stats.gov.cn", "A"), _source("S-002", "blog.example", "D")]
    items = [
        _item("E-001", "S-001", value_type="text", value="已完成", quote="引文甲"),
        _item("E-002", "S-002", value_type="text", value="计划中", quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources, llm=llm)

    assert groups[0].verdict["status"] == "conflict_pending"
    assert groups[0].verdict["resolution"] is None
    assert groups[0].verdict["conflicts"]


@pytest.mark.asyncio
async def test_llm_failure_is_conservative():
    class BrokenLLM:
        async def __call__(self, prompt):
            raise RuntimeError("no network")

    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", metric="销量", scope="零售额", quote="引文甲"),
        _item("E-002", "S-002", metric="销售量", scope="出货额", quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources, llm=BrokenLLM())

    # Metric/scope judgements never merge on failure: every item its own group.
    assert len(groups) == 2
    assert all(group.verdict["status"] in {"insufficient_pending", "conflict_pending"} for group in groups)


# --------------------------------------------------------------- independence


@pytest.mark.asyncio
async def test_same_publisher_multiple_domains_counts_once():
    sources = [
        _source("S-001", "news.example", "C", publisher="某财经媒体"),
        _source("S-002", "m.example", "C", publisher="某财经媒体"),
    ]
    items = [
        _item("E-001", "S-001", quote="引文甲"),
        _item("E-002", "S-002", value=101, quote="引文乙"),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].independent_sources == 1
    assert groups[0].verdict["status"] == "insufficient_pending"


@pytest.mark.asyncio
async def test_repost_by_quote_similarity_is_merged_conservatively():
    sources = [
        _source("S-001", "a.example", "C", publisher="甲媒体"),
        _source("S-002", "b.example", "C", publisher="乙媒体"),
    ]
    shared_quote = "2024年某公司新能源汽车销量为100万辆"
    items = [
        _item("E-001", "S-001", quote=shared_quote),
        _item("E-002", "S-002", quote=shared_quote),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].independent_sources == 1
    reps = [entry for entry in groups[0].merge_log if entry["type"] == "repost"]
    assert reps and reps[0]["reason"] == "quote_similarity"
    assert reps[0]["repost_of"] in {"甲媒体", "乙媒体"}


@pytest.mark.asyncio
async def test_uncertain_quote_similarity_does_not_count_as_independent():
    sources = [
        _source("S-001", "a.example", "C", publisher="甲媒体"),
        _source("S-002", "b.example", "C", publisher="乙媒体"),
    ]
    items = [
        _item(
            "E-001",
            "S-001",
            quote="2024年某公司新能源汽车销量为100万辆，同比增长显著",
        ),
        _item(
            "E-002",
            "S-002",
            quote="2024年该企业新能源汽车销量约100万辆，同比明显增长",
        ),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].independent_sources == 1
    reps = [entry for entry in groups[0].merge_log if entry["type"] == "repost"]
    assert reps and reps[0]["reason"] == "quote_similarity_uncertain"


@pytest.mark.asyncio
async def test_explicit_attribution_marks_repost():
    sources = [
        _source("S-001", "a.example", "C", publisher="某小站"),
        _source("S-002", "people.com.cn", "B", publisher="人民网"),
    ]
    items = [
        _item(
            "E-001",
            "S-001",
            quote="据人民网报道，全年新能源车销量为100万辆，市场保持增长",
        ),
        _item(
            "E-002",
            "S-002",
            quote="全年新能源车销量为100万辆，市场保持增长态势",
        ),
    ]

    groups = await _adjudicate(items, sources)

    reps = [entry for entry in groups[0].merge_log if entry["type"] == "repost"]
    assert reps and reps[0]["reason"] == "explicit_attribution"
    # The reposted item does not add independence; the original is 人民网 (B),
    # whose single-source threshold is met.
    assert groups[0].independent_sources == 1
    assert groups[0].verdict["status"] == "accepted"
    assert groups[0].verdict["best_tier"] == "B"


@pytest.mark.asyncio
async def test_distinct_publishers_and_quotes_count_as_independent():
    sources = [
        _source("S-001", "a.example", "C", publisher="甲媒体"),
        _source("S-002", "b.example", "C", publisher="乙媒体"),
    ]
    items = [
        _item("E-001", "S-001", quote="甲媒体独立采写的报道文本"),
        _item("E-002", "S-002", value=101, quote="乙媒体从另一角度给出的独立数据"),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].independent_sources == 2
    assert groups[0].verdict["status"] == "cross_validated"
